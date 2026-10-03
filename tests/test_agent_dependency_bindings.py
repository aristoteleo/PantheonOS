"""Scoped Agent tool assembly through real HTTPS, without a model API call.

The HTTP server is a deterministic grant/provider boundary fixture. It does not
replace the separate Fleet gateway authorization tests or prove live deployment.
Agent routing, schema generation, factory, SDK and TLS are the real implementations.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import ssl
import threading
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
from pantheon.apps.runtime_config import RuntimeCredential, load_runtime_configuration
from pantheon.chatroom.lifecycle import AgentLifetime
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory import create_agent, create_agents_from_template
from pantheon.factory.bindings import AgentToolBindings, bindings_from_runtime_configuration


FUNCTION = {"name": "execute", "description": "Execute a command in the bound session",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                           "required": ["command"]}}
CONFIG = dict(name="Tester", instructions="Test bound tools", icon="test", model="openai/gpt-4o-mini")


@pytest.fixture
def endpoint(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    certpath, keypath = tmp_path / "cert.pem", tmp_path / "key.pem"
    certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    state = SimpleNamespace(calls=[], status=200, release=threading.Event(), entered=threading.Event(),
                            hold=False, raw=None, applied=[], grants={"a" * 64: "session-a", "b" * 64: "session-b"})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            state.calls.append((self.path, token, request))
            state.entered.set()
            if state.hold:
                assert state.release.wait(5), "test did not release the accepted RPC"
            status = state.status
            if token not in state.grants:
                status = 401
            if request["method"] != "execute" or set(request["args"]) != {"command"}:
                status = 403
            if status == 200:
                result = {"session": state.grants[token], "command": request["args"]["command"]}
                state.applied.append(result)
                raw = state.raw if state.raw is not None else json.dumps({"success": True, "result": result}).encode()
            else:
                raw = b"private provider details must not escape"
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certpath, keypath)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"https://127.0.0.1:{server.server_port}/rpc"
    state.tls = ssl.create_default_context(cafile=str(certpath))
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture(autouse=True)
def forbid_ambient_tools(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Explicit tool assembly used ambient service discovery/settings")
    monkeypatch.setattr("pantheon.factory.get_settings", forbidden)
    monkeypatch.setattr("pantheon.factory._resolve_toolset_proxy", forbidden)
    monkeypatch.setattr("pantheon.apps.resolver.get_shared_resolver", forbidden)
    monkeypatch.setenv("HTTPS_PROXY", "https://do-not-use.invalid")
    monkeypatch.setenv("FLEET_KEY", "owner-key-must-not-escape")


def provider(endpoint, name="shell", token="a" * 64, **kwargs):
    client = DependencyClient(RuntimeCredential(endpoint.url, token), tls_context=endpoint.tls)
    return DependencyToolProvider(name, client, [FUNCTION], timeout_seconds=2, **kwargs)


async def agent(endpoint, name="shell", token="a" * 64):
    p = provider(endpoint, name, token)
    a = await create_agent(**CONFIG, toolsets=[name], tool_bindings=AgentToolBindings({name: p}))
    return a, p


@pytest.mark.asyncio
async def test_real_agent_menu_and_tls_call_never_discover_or_send_context(endpoint):
    a, p = await agent(endpoint)
    menu = await a.get_tools_for_llm()
    bound_menu = [tool for tool in menu if "__" in tool["function"]["name"]]
    assert [tool["function"]["name"] for tool in bound_menu] == ["shell__execute"]
    assert set(bound_menu[0]["function"]["parameters"]["properties"]) == {"command", "_background"}
    assert not endpoint.calls  # No list_tools or discovery HTTP request.
    for _ in range(2):
        await a.get_tools_for_llm()
    assert (await p.list_tools())[0].inputSchema == {
        **FUNCTION, "parameters": {**FUNCTION["parameters"], "additionalProperties": False}}
    result = await a.call_tool("shell__execute", {"command": "pwd"},
                              {"workspace": "must-not-send", "_call_agent": lambda: None})
    assert result == {"session": "session-a", "command": "pwd"}
    assert endpoint.calls == [("/rpc", "a" * 64,
                              {"method": "execute", "args": {"command": "pwd"}, "timeout_seconds": 2})]
    await p.shutdown()


@pytest.mark.asyncio
async def test_bindings_admit_only_exact_names_and_caller_arguments(endpoint):
    a, p = await agent(endpoint)
    for name, args in [("execute", {"command": "pwd"}), ("missing__execute", {"command": "pwd"}),
                       ("shell__delete", {}), ("shell__execute", {"command": "pwd", "session": "other"}),
                       ("shell__execute", {"command": "pwd", "context_variables": {}}),
                       ("shell__execute", {})]:
        with pytest.raises(ValueError):
            await a.call_tool(name, args)
    assert not endpoint.calls
    await p.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 409, 307, 502])
async def test_denial_or_transport_failure_never_replays_or_falls_back(endpoint, status):
    a, p = await agent(endpoint)
    endpoint.status = status
    with pytest.raises(DependencyCallError) as failure:
        await a.call_tool("shell__execute", {"command": "write"})
    assert failure.value.status == status
    assert failure.value.outcome_unknown is (status in (307, 502))
    assert "private provider" not in str(failure.value)
    assert len(endpoint.calls) == 1
    await p.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"not a JSON response", b'{"success":false,"error":"private details"}',
                                 b'{"success":true}', b'{"success":1,"result":"invalid"}'])
async def test_lost_result_of_applied_mutation_remains_unknown(endpoint, raw):
    a, p = await agent(endpoint)
    endpoint.raw = raw
    with pytest.raises(DependencyCallError) as failure:
        await a.call_tool("shell__execute", {"command": "write"})
    assert failure.value.outcome_unknown
    assert "private details" not in str(failure.value)
    assert len(endpoint.calls) == len(endpoint.applied) == 1
    await p.shutdown()


@pytest.mark.asyncio
async def test_team_binding_is_by_config_identity_and_mcp_has_no_uri_handoff(endpoint):
    pa, pb = provider(endpoint), provider(endpoint, token="b" * 64)
    pmcp = provider(endpoint, "docs")
    configs = {"id-a": {**CONFIG, "toolsets": ["shell", "mcp:docs"]},
               "id-b": {**CONFIG, "toolsets": ["shell"]}}
    agents = await create_agents_from_template(configs, tool_bindings={
        "id-a": AgentToolBindings({"shell": pa}, {"docs": pmcp}),
        "id-b": AgentToolBindings({"shell": pb})})
    assert list(agents[0].providers) == ["shell", "docs"]
    assert list(agents[1].providers) == ["shell"]
    results = await asyncio.gather(*(a.call_tool("shell__execute", {"command": "pwd"}) for a in agents))
    assert [r["session"] for r in results] == ["session-a", "session-b"]
    assert (await agents[0].call_tool("docs__execute", {"command": "read"}))["command"] == "read"
    assert all(call[2]["method"] == "execute" for call in endpoint.calls)
    for p in (pa, pb, pmcp):
        await p.shutdown()


@pytest.mark.asyncio
async def test_missing_required_binding_is_not_a_partial_team_or_legacy_fallback(endpoint):
    configs = {"first": {**CONFIG, "toolsets": ["shell"]}, "second": {**CONFIG, "toolsets": ["shell"]}}
    p = provider(endpoint)
    with pytest.raises(ValueError, match="every Agent"):
        await create_agents_from_template(configs, tool_bindings={"first": AgentToolBindings({"shell": p})})
    with pytest.raises(RuntimeError, match="assembly failed"):
        await create_agents_from_template(configs, tool_bindings={
            "first": AgentToolBindings({"shell": p}), "second": AgentToolBindings()})
    with pytest.raises(ValueError, match="composition root"):
        await create_agents_from_template({"first": {**CONFIG, "tool_bindings": None}})
    empty = await create_agent(**CONFIG, tool_bindings=AgentToolBindings())
    assert not empty.providers and not endpoint.calls  # No implicit global MCP.
    await p.shutdown()


@pytest.mark.asyncio
async def test_repeated_cancel_and_shutdown_drain_transport_and_reject_queued_work(endpoint):
    p = provider(endpoint, max_inflight=1)
    endpoint.hold = True
    call = asyncio.create_task(p.call_tool("execute", {"command": "write"}))
    assert await asyncio.to_thread(endpoint.entered.wait, 2)
    queued = asyncio.create_task(p.call_tool("execute", {"command": "must-not-start"}))
    await asyncio.sleep(0)
    call.cancel()
    await asyncio.sleep(0)
    call.cancel()
    closing = asyncio.create_task(p.shutdown())
    await asyncio.sleep(.02)
    assert not call.done() and not closing.done()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(RuntimeError, match="closed"):
        await p.call_tool("execute", {"command": "late"})
    endpoint.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 2)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, 2)
    with pytest.raises(RuntimeError, match="closed"):
        await asyncio.wait_for(queued, 2)
    await p.shutdown()
    assert len(endpoint.applied) == len(endpoint.calls) == 1 and not p._pending


@pytest.mark.asyncio
async def test_runtime_cleanup_closes_owned_bindings_after_plugin_and_once(endpoint):
    a, p = await agent(endpoint)
    events = []
    real_close = p.shutdown
    async def close():
        events.append("provider")
        await real_close()
    p.shutdown = close
    async def plugin_close():
        assert (await a.call_tool("shell__execute", {"command": "final-save"}))["command"] == "final-save"
        events.append("plugin")
    async def stream_close():
        events.append("stream")
    runtime = AgentLifetime()
    team = SimpleNamespace(agents={"one": a, "alias": a})
    runtime.chat_teams = {"first": team, "second": team}
    runtime._default_team = team
    runtime._plugins = [SimpleNamespace(on_shutdown=plugin_close)]
    runtime._nats_adapter = SimpleNamespace(close=stream_close)
    await runtime.cleanup()
    await runtime.cleanup()
    assert events == ["plugin", "provider", "stream"]
    with pytest.raises(RuntimeError, match="closed"):
        await a.call_tool("shell__execute", {"command": "late"})


@pytest.fixture
def configured_bindings(endpoint, monkeypatch, tmp_path):
    identity = {"owner": ("PANTHEON_FLEET_ID", "owner"), "node_id": ("PANTHEON_NODE_ID", "node"),
                "instance_id": ("PANTHEON_INSTANCE_ID", "instance"),
                "revision": ("PANTHEON_APP_REVISION", "c" * 64),
                "component": ("PANTHEON_COMPONENT_NAME", "backend")}
    for env, value in identity.values():
        monkeypatch.setenv(env, value)
    monkeypatch.setenv("PANTHEON_INSTANCE_GENERATION", "3")
    path = tmp_path / "config.json"
    monkeypatch.setenv("PANTHEON_APP_CONFIG", str(path))
    body = {"protocol": 1, "generation": 3, **{key: value for key, (_, value) in identity.items()},
            "credentials": {"shell_a": {"endpoint": endpoint.url, "key": "a" * 64}},
            "values": {"agent_tools": {"protocol": 1, "agents": {"config-id": {"toolsets": {
                "shell": {"credential": "shell_a", "functions": [FUNCTION], "timeout_seconds": 2}}}}}}}
    path.write_text(json.dumps(body))
    return path, body


@pytest.mark.asyncio
async def test_configuration_file_to_agent_to_rpc_uses_component_credentials(endpoint, configured_bindings):
    configuration = load_runtime_configuration(required=True)
    bindings = bindings_from_runtime_configuration(configuration, tls_context=endpoint.tls)
    agents = await create_agents_from_template({"config-id": {**CONFIG, "toolsets": ["shell"]}},
                                               tool_bindings=bindings)
    assert (await agents[0].call_tool("shell__execute", {"command": "snapshot"}))["session"] == "session-a"
    assert "a" * 64 not in repr(bindings)
    assert configuration.values["agent_tools"]["agents"]["config-id"]["toolsets"]["shell"]["functions"][0]["name"] == "execute"
    await bindings["config-id"].toolsets["shell"].shutdown()


@pytest.mark.parametrize("change", ["missing_credential", "extra_endpoint", "protocol", "private_argument"])
def test_invalid_component_tool_binding_never_uses_ambient_credentials(endpoint, configured_bindings, change):
    path, body = configured_bindings
    root = body["values"]["agent_tools"]
    entry = root["agents"]["config-id"]["toolsets"]["shell"]
    if change == "missing_credential":
        entry["credential"] = "owner-key-must-not-escape"
    elif change == "extra_endpoint":
        entry["endpoint"] = "https://other-host.invalid/rpc"
    elif change == "protocol":
        root["protocol"] = True
    else:
        # Do not mutate the shared schema fixture.
        entry["functions"] = json.loads(json.dumps(entry["functions"]))
        entry["functions"][0]["parameters"]["properties"]["context_variables"] = {"type": "object"}
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="invalid or incomplete") as failure:
        bindings_from_runtime_configuration(load_runtime_configuration(required=True), tls_context=endpoint.tls)
    assert "owner-key-must-not-escape" not in str(failure.value)
    assert not endpoint.calls
