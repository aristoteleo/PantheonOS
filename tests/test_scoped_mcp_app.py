"""Real MCP sessions and an isolated ordinary App host; no live owner deployment."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastmcp import Client, FastMCP
import httpx
import pytest

from pantheon.apps.builtin.mcp.scoped import ScopedMCP, validate_exports
from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.platform.mcp_package import build_package


@pytest.fixture
def mcp():
    server = FastMCP('Fixture')
    state = SimpleNamespace(count=0, entered=asyncio.Event(), release=asyncio.Event(), hold=False)

    @server.tool
    async def increment(amount: int) -> dict:
        state.entered.set()
        if state.hold:
            await state.release.wait()
        state.count += amount
        return {'count': state.count}

    @server.tool
    def forbidden() -> str:
        raise AssertionError('Unexported MCP tool executed')

    @server.tool
    def fail() -> str:
        raise ValueError('tool deliberately failed')

    return server, state


async def contract(server, tools=('increment',)):
    async with Client(server) as client:
        available = {t.name: t for t in await client.list_tools()}
    return {name: {'server': 'docs', 'tool': name, 'description': available[name].description or '',
                   'parameters': available[name].inputSchema} for name in tools}


def config(servers=None, credentials=None):
    return RuntimeConfiguration(values={'mcp': {'protocol': 1, 'servers': servers or {
        'docs': {'transport': 'http', 'url': 'http://127.0.0.1:1/mcp'}}}},
        credentials=credentials or {}, instance_id='mcp', revision='a' * 64, generation=1,
        component='backend', owner='owner', node_id='node')


@pytest.mark.asyncio
async def test_real_mcp_persistent_session_and_scoped_call(mcp, monkeypatch):
    server, state = mcp
    def forbidden(*args, **kwargs):
        raise AssertionError('Prepared MCP accessed ambient settings')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    host = ScopedMCP(await contract(server), config(), client_factory=lambda _: Client(server))
    await host.start()
    try:
        assert (await host.call('increment', {'amount': 3}))['structuredContent'] == {'count': 3}
        assert (await host.call('increment', {'amount': 4}))['structuredContent'] == {'count': 7}
        for name, args in [('forbidden', {}), ('increment', {'amount': '3'}),
                           ('increment', {'amount': 1, 'server': 'other'}), ('increment', {})]:
            with pytest.raises(ValueError, match='reviewed contract'):
                await host.call(name, args)
        assert state.count == 7
    finally:
        await host.close()
    with pytest.raises(RuntimeError, match='not accepting'):
        await host.call('increment', {'amount': 1})
    await host.close()


@pytest.mark.asyncio
async def test_consumer_cancel_and_app_stop_drain_accepted_call_once(mcp):
    server, state = mcp
    host = ScopedMCP(await contract(server), config(), client_factory=lambda _: Client(server))
    await host.start()
    state.hold = True
    call = asyncio.create_task(host.call('increment', {'amount': 1}))
    await asyncio.wait_for(state.entered.wait(), 2)
    call.cancel()
    close = asyncio.create_task(host.close())
    await asyncio.sleep(.05)
    assert not call.done() and not close.done()
    with pytest.raises(RuntimeError, match='not accepting'):
        await host.call('increment', {'amount': 10})
    state.release.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    await asyncio.wait_for(close, 3)
    assert state.count == 1 and not host._clients and not host._active


@pytest.mark.asyncio
async def test_changed_schema_fails_before_admission_and_closes(mcp):
    server, state = mcp
    exports = await contract(server)
    exports['increment']['parameters']['properties']['amount']['type'] = 'string'
    host = ScopedMCP(exports, config(), client_factory=lambda _: Client(server))
    with pytest.raises(RuntimeError, match='reviewed tool contract'):
        await host.start()
    assert host._session.done() and not host._clients and state.count == 0


@pytest.mark.asyncio
async def test_mcp_tool_error_is_preserved_without_retry(mcp):
    server, _ = mcp
    host = ScopedMCP(await contract(server, ('fail',)), config(), client_factory=lambda _: Client(server))
    await host.start()
    try:
        result = await host.call('fail', {})
        assert result['isError'] is True
        assert result['content'][0]['type'] == 'text'
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['credential_endpoint', 'extra_server', 'unused_credential', 'query', 'ambient_command', 'extra_values'])
async def test_invalid_configuration_rejected_before_connection(mcp, change):
    server, _ = mcp
    cfg = config()
    if change == 'credential_endpoint':
        cfg.values['mcp']['servers']['docs']['credential'] = 'api'
        cfg.credentials['api'] = RuntimeCredential('https://elsewhere.invalid', 'do-not-leak')
    elif change == 'extra_server':
        cfg.values['mcp']['servers']['other'] = {'transport': 'http', 'url': 'http://localhost:1'}
    elif change == 'unused_credential':
        cfg.credentials['extra'] = RuntimeCredential('https://elsewhere.invalid', 'do-not-leak')
    elif change == 'query':
        cfg.values['mcp']['servers']['docs']['url'] += '?token=do-not-leak'
    elif change == 'ambient_command':
        cfg.values['mcp']['servers']['docs'] = {'transport': 'stdio', 'command': ['python', 'server.py'], 'cwd': '.', 'env': {}}
    else:
        cfg.values['other'] = 'do-not-leak'
    with pytest.raises(ValueError) as error:
        ScopedMCP(await contract(server), cfg)
    assert 'do-not-leak' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['external_ref', 'base_uri', 'extra_arguments', 'private', 'management'])
async def test_invalid_contract_is_not_published(mcp, tmp_path, change):
    exports = await contract(mcp[0])
    schema = exports['increment']['parameters']
    if change == 'external_ref':
        schema['properties']['amount'] = {'$ref': 'https://untrusted.invalid/schema'}
    elif change == 'base_uri':
        schema['$id'] = 'https://untrusted.invalid/schema'
    elif change == 'extra_arguments':
        schema['additionalProperties'] = True
    elif change == 'private':
        schema['properties']['context_variables'] = {'type': 'object'}
    else:
        exports['get_uri'] = exports.pop('increment')
    with pytest.raises(ValueError, match='export contract'):
        build_package(tmp_path / 'bad', 'darwin-arm64', exports=exports)
    assert not (tmp_path / 'bad').exists()


@pytest.mark.asyncio
async def test_http_mcp_uses_paired_bearer_and_no_ambient_proxy(mcp, monkeypatch):
    """Real streamable HTTP, including endpoint-paired credentials."""
    import socket
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    server, state = mcp
    seen = []
    class Authorization(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            seen.append(request.headers.get('authorization'))
            if seen[-1] != 'Bearer paired-key':
                from starlette.responses import Response
                return Response(status_code=403)
            return await call_next(request)
    app = server.http_app()
    app.add_middleware(Authorization)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        port = sock.getsockname()[1]
        uv = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='on'))
        task = asyncio.create_task(uv.serve(sockets=[sock]))
        host = None
        try:
            for _ in range(100):
                if uv.started:
                    break
                await asyncio.sleep(.01)
            assert uv.started
            url = f'http://127.0.0.1:{port}/mcp'
            cfg = config({'docs': {'transport': 'http', 'url': url, 'credential': 'api'}},
                         {'api': RuntimeCredential(url, 'paired-key')})
            monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
            monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
            monkeypatch.setenv('NO_PROXY', '')
            host = ScopedMCP(await contract(server), cfg)
            await host.start()
            assert (await host.call('increment', {'amount': 2}))['structuredContent'] == {'count': 2}
            assert seen and set(seen) == {'Bearer paired-key'}
        finally:
            if host:
                await host.close()
            uv.should_exit = True
            await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize('credentialed', [False, True], ids=['plain-env', 'vault-slot-env'])
async def test_packaged_app_runs_stdio_without_agent_and_drains(tmp_path, mcp, credentialed):
    script = tmp_path / 'server.py'
    script.write_text('''from fastmcp import FastMCP
import os
from pathlib import Path
Path("mcp-pid").write_text(str(os.getpid()))
server = FastMCP("Subprocess")
count = 0
@server.tool
async def increment(amount: int) -> dict:
    global count
    assert "FLEET_KEY" not in os.environ
    assert "OPENAI_API_KEY" not in os.environ
    if os.environ.get("EXPECT_MCP_KEY") == "yes":
        assert os.environ.get("MCP_API_KEY") == "synthetic-mcp-key"
    else:
        assert "MCP_API_KEY" not in os.environ
    count += amount
    return {"count": count}
server.run(transport="stdio", show_banner=False)
''')
    # Get the exact same schema as the real subprocess fixture's annotation.
    exports = await contract(mcp[0])
    package = build_package(tmp_path / 'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64',
                            exports=exports, credential_slots=['mcp-api'] if credentialed else [])
    from pantheon.apps.schema import parse_manifest
    from pantheon.apps.lifecycle import build_artifact
    assert parse_manifest(json.loads((package / 'app.json').read_text())).id == 'mcp-gateway'
    assert build_artifact(package)[0]
    cfg = config({'docs': {'transport': 'stdio', 'command': [sys.executable, str(script)],
                          'cwd': str(tmp_path), 'env': {}}})
    resolved = dict(protocol=1, values=cfg.values, credentials={}, **{
        key: getattr(cfg, key) for key in ('owner', 'node_id', 'instance_id', 'revision', 'generation', 'component')})
    if credentialed:
        cfg.values['mcp']['servers']['docs'].update(env={'EXPECT_MCP_KEY': 'yes'},
            env_credentials={'MCP_API_KEY': {'credential': 'mcp-api', 'endpoint': 'https://api.test/v1'}})
        resolved['credentials']['mcp-api'] = {'endpoint': 'https://api.test/v1', 'key': 'synthetic-mcp-key'}
        assert all(b'synthetic-mcp-key' not in p.read_bytes() for p in package.rglob('*') if p.is_file())
    config_path = tmp_path / 'prepared.json'
    config_path.write_text(json.dumps(resolved))
    data = tmp_path / 'data'
    data.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))}
    env.update(PANTHEON_APP_CONFIG=str(config_path), PANTHEON_FLEET_ID=cfg.owner,
        PANTHEON_NODE_ID=cfg.node_id, PANTHEON_INSTANCE_ID=cfg.instance_id,
        PANTHEON_APP_REVISION=cfg.revision, PANTHEON_INSTANCE_GENERATION='1',
        PANTHEON_COMPONENT_NAME='backend', PANTHEON_PORT_HTTP='0', PANTHEON_APP_RPC_TOKEN='rpc-token',
        FLEET_KEY='must-not-inherit', OPENAI_API_KEY='must-not-inherit')
    boot = '''import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.agent', 'pantheon.chatroom', 'pantheon.factory', 'pantheon.settings', 'pantheon.toolset')):
   raise AssertionError('MCP App imported Agent or ambient settings')
sys.meta_path.insert(0, Guard())
sys.path.insert(0, sys.argv[1])
sys.argv = ['host.py', *sys.argv[2:]]
runpy.run_module('host', run_name='__main__')
'''
    with (tmp_path / 'host.log').open('w+') as log:
        proc = subprocess.Popen([sys.executable, '-c', boot, str(package / '.fleet-runtime'),
            'start', '--package', str(package), '--data', str(data)], env=env, cwd=tmp_path, stdout=log, stderr=log)
        try:
            record = data / 'backend-endpoint.json'
            for _ in range(300):
                if proc.poll() is not None:
                    log.seek(0)
                    pytest.fail('MCP package exited: ' + log.read())
                if record.exists():
                    break
                await asyncio.sleep(.05)
            assert record.exists()
            address = 'http://127.0.0.1:' + str(json.loads(record.read_text())['port'])
            async with httpx.AsyncClient(base_url=address, trust_env=False, timeout=10) as client:
                assert (await client.get('/health')).json()['methods'] == ['increment']
                body = {'method': 'increment', 'args': {'amount': 2}}
                assert (await client.post('/rpc', json=body)).status_code == 403
                client.headers['X-Fleet-RPC-Token'] = 'rpc-token'
                for expected in (2, 4):
                    response = await client.post('/rpc', json=body)
                    assert response.status_code == 200, response.text
                    assert response.json()['result']['structuredContent'] == {'count': expected}
                assert (await client.post('/rpc', json={'method': 'get_uri', 'args': {}})).status_code == 400
                assert (await client.post('/_fleet/drain')).status_code == 200
                import psutil
                assert not psutil.pid_exists(int((tmp_path / 'mcp-pid').read_text()))
                assert (await client.post('/rpc', json=body)).status_code != 200
        finally:
            if proc.poll() is None:
                proc.terminate()
            await asyncio.to_thread(proc.wait, 10)


@pytest.mark.asyncio
async def test_agent_consumes_the_mcp_export_as_an_ordinary_dependency(mcp):
    """Real Agent + dependency schema + real MCP. RPC link is an in-process fixture."""
    from pantheon.apps.dependency_client import DependencyClient
    from pantheon.dependency_provider import DependencyToolProvider
    from pantheon.factory import create_agent
    from pantheon.factory.bindings import AgentToolBindings
    server, _ = mcp
    exports = validate_exports(await contract(server))
    host = ScopedMCP(exports, config(), client_factory=lambda _: Client(server))
    await host.start()
    loop = asyncio.get_running_loop()
    class Link(DependencyClient):
        def invoke(self, name, args=None, *, timeout_seconds=60):
            result = asyncio.run_coroutine_threadsafe(host.call(name, args), loop).result(timeout_seconds)
            return {'success': True, 'result': result}
    functions = [{'name': name, 'description': spec['description'], 'parameters': spec['parameters']}
                 for name, spec in exports.items()]
    provider = DependencyToolProvider('docs', Link(RuntimeCredential('https://localhost/rpc', 'a' * 64)), functions)
    try:
        agent = await create_agent(name='Scoped MCP', icon='test', instructions='Use bound tools', model='openai/gpt-4o-mini',
            mcp_servers=['docs'], toolsets=[], tool_bindings=AgentToolBindings({}, {'docs': provider}))
        menu = await agent.get_tools_for_llm()
        assert any(t['function']['name'] == 'docs__increment' for t in menu)
        assert not any('forbidden' in t['function']['name'] for t in menu)
        result = await agent.call_tool('docs__increment', {'amount': 9}, {'workspace': 'not-forwarded'})
        assert result['structuredContent'] == {'count': 9}
    finally:
        await provider.shutdown()
        await host.close()


@pytest.mark.asyncio
async def test_cancelled_start_closes_connection_on_its_own_task(mcp):
    from contextlib import asynccontextmanager
    entered, exited = asyncio.Event(), asyncio.Event()
    @asynccontextmanager
    async def hanging(_):
        task = asyncio.current_task()
        try:
            entered.set()
            await asyncio.Event().wait()
            yield
        finally:
            assert asyncio.current_task() is task
            exited.set()
    host = ScopedMCP(await contract(mcp[0]), config(), client_factory=hanging)
    start = asyncio.create_task(host.start())
    await asyncio.wait_for(entered.wait(), 2)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    assert exited.is_set() and host._session.done() and not host._accepting


@pytest.mark.asyncio
async def test_unknown_mutation_outcome_is_not_retried_or_leaked(mcp):
    server, state = mcp
    class LostReply(Client):
        async def call_tool(self, *args, **kwargs):
            await super().call_tool(*args, **kwargs)
            raise OSError('transport failure with private-api-key')
    host = ScopedMCP(await contract(server), config(), client_factory=lambda _: LostReply(server))
    await host.start()
    try:
        with pytest.raises(RuntimeError, match='outcome may be unknown') as error:
            await host.call('increment', {'amount': 1})
        assert 'private-api-key' not in str(error.value)
        assert state.count == 1
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_content_blocks_and_metadata_survive_rpc_representation():
    from mcp.types import TextContent, ImageContent
    from fastmcp.tools.tool import ToolResult
    server = FastMCP('Content')
    @server.tool
    def content():
        return ToolResult(content=[TextContent(type='text', text='explanation'),
            ImageContent(type='image', data='aGVsbG8=', mimeType='image/png')],
            structured_content={'result': 1}, meta={'review': 'kept'})
    host = ScopedMCP(await contract(server, ('content',)), config(), client_factory=lambda _: Client(server))
    await host.start()
    try:
        result = await host.call('content', {})
        assert [b['type'] for b in result['content']] == ['text', 'image']
        assert result['_meta'] == {'review': 'kept'}
        assert result['structuredContent'] == {'result': 1} and result['isError'] is False
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['darwin-arm64', 'darwin-amd64', 'linux-arm64', 'linux-amd64', 'windows-arm64', 'windows-amd64'])
async def test_package_declarations_match_ordinary_app_contract(mcp, tmp_path, platform):
    from pantheon.apps.lifecycle import build_artifact
    from pantheon.dependency_provider import DependencyToolProvider
    package = build_package(tmp_path / platform, platform, exports=await contract(mcp[0]), credential_slots=['api'])
    execution = json.loads((package / 'fleet.json').read_text())
    assert execution['requires']['os'] == [platform.split('-')[0]]
    cfg = execution['components'][0]['configuration']
    assert cfg == {'values': {'mcp': {'required': True}}, 'credentials': {'api': {'required': True}}}
    functions = json.loads((package / 'tool-functions.json').read_text())
    assert list(DependencyToolProvider._validate_functions(functions)) == ['increment']
    assert build_artifact(package, platform)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('slots', [['api'] * 2, ['API'], ['a'*81], ['api.' ], [f'key-{i}' for i in range(17)]])
async def test_package_rejects_slots_outside_fleet_configuration_contract(mcp, tmp_path, slots):
    with pytest.raises(ValueError, match='credential slots'):
        build_package(tmp_path/'invalid', 'linux-amd64', exports=await contract(mcp[0]), credential_slots=slots)
    assert not (tmp_path/'invalid').exists()
