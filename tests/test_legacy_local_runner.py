"""A legacy chatroom sandbox runs its Apps on its own runner.

Outside Agent App mode the entrypoint starts a runner of the sandbox's own
on a loopback broker and sets PANTHEON_APPS_RUNNER=local. The resolver then
commands that runner only: it never joins the Fleet controller as the user,
even though the hub still sets FLEET_CONTROLLER_URL and FLEET_KEY.
"""

import asyncio
import json
import re
import subprocess
from pathlib import Path

import pytest

from pantheonos.appkit import resolver as R
from pantheonos.appkit.resolver import AppInstanceResolver, NotJoinedError

REPO = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO / "docker" / "docker-entrypoint-dual-mode.sh"


@pytest.fixture
def sandbox_env(tmp_path, monkeypatch):
    """A hub sandbox's environment: a seed, the Fleet wiring, the Hub bus."""
    for name in ("PANTHEON_FLEET_ID", "PANTHEON_FLEET_NODE_ID", "PANTHEON_USER_SEED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ID_HASH", "u1")
    monkeypatch.setenv("FLEET_CONTROLLER_URL", "https://fleet.example")
    monkeypatch.setenv("FLEET_KEY", "fleet-key")
    monkeypatch.setenv("NATS_SERVERS", "nats://hub-bus:4222")
    monkeypatch.setenv("PANTHEON_FLEET_STATE_DIR", str(tmp_path))
    joins = []

    class NoController:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            joins.append(url)
            raise ConnectionError("controller unreachable in this test")

    monkeypatch.setattr("httpx.AsyncClient", NoController)
    return tmp_path, joins


@pytest.fixture
def captured_connect(monkeypatch):
    calls = []

    class FakeConn:
        is_connected = True

        def jetstream(self):
            raise RuntimeError("no registry in this test")

        async def close(self):
            pass

    async def connect(**kwargs):
        calls.append(kwargs)
        return FakeConn()

    import nats

    monkeypatch.setattr(nats, "connect", connect)
    return calls


def _runtime(state_dir: Path, nats_url="nats://127.0.0.1:14250"):
    (state_dir / "runtime.json").write_text(
        json.dumps({"node_id": "n_local", "fleet_id": "sandbox", "nats_url": nats_url}))


async def test_local_runner_never_joins_the_controller(sandbox_env, monkeypatch):
    state_dir, joins = sandbox_env
    monkeypatch.setenv("PANTHEON_APPS_RUNNER", "local")
    monkeypatch.setattr(R, "LOCAL_RUNNER_WAIT_S", 0.3)
    r = AppInstanceResolver.from_env(workdir=str(state_dir))
    with pytest.raises(NotJoinedError, match="App runner has not started"):
        await r._ensure_coords()
    with pytest.raises(NotJoinedError):
        await r.ensure_instance("shell")
    assert joins == []
    assert r._disabled_at is None  # a late runner is not a fault


async def test_local_runner_is_waited_for(sandbox_env, monkeypatch):
    state_dir, joins = sandbox_env
    monkeypatch.setenv("PANTHEON_APPS_RUNNER", "local")
    r = AppInstanceResolver.from_env(workdir=str(state_dir))

    async def runner_starts():
        await asyncio.sleep(0.3)
        _runtime(state_dir)

    starting = asyncio.create_task(runner_starts())
    await r._ensure_coords()
    await starting
    assert (r._fleet, r._node) == ("sandbox", "n_local")
    assert joins == []


async def test_local_runner_is_commanded_on_its_loopback_broker(sandbox_env, monkeypatch, captured_connect):
    state_dir, joins = sandbox_env
    monkeypatch.setenv("PANTHEON_APPS_RUNNER", "local")
    _runtime(state_dir)
    r = AppInstanceResolver.from_env(workdir=str(state_dir))
    await r._ensure_coords()
    await r._ensure_client()
    assert captured_connect[-1]["servers"] == ["nats://127.0.0.1:14250"]
    assert "user_credentials" not in captured_connect[-1]
    assert joins == []


async def test_the_client_is_bound_to_the_runners_fleet_even_when_asked_first(sandbox_env, monkeypatch, captured_connect):
    """_list_nodes asks for the client before the coordinates; the client
    must still carry the runner's fleet id, not an empty one."""
    state_dir, joins = sandbox_env
    monkeypatch.setenv("PANTHEON_APPS_RUNNER", "local")
    _runtime(state_dir)
    r = AppInstanceResolver.from_env(workdir=str(state_dir))
    client = await r._ensure_client()
    assert client._fleet == "sandbox"
    assert (r._fleet, r._node) == ("sandbox", "n_local")
    assert joins == []


async def test_an_app_process_stays_on_the_sandbox_runner(sandbox_env, monkeypatch, captured_connect):
    """App processes get explicit coordinates and no state dir from their
    spec; they must still command the loopback broker, not the Hub bus."""
    state_dir, joins = sandbox_env
    monkeypatch.setenv("PANTHEON_APPS_RUNNER", "local")
    monkeypatch.setenv("PANTHEON_FLEET_ID", "sandbox")
    monkeypatch.setenv("PANTHEON_FLEET_NODE_ID", "n_local")
    _runtime(state_dir)
    r = AppInstanceResolver.from_env(workdir=str(state_dir))
    assert r._state_dir is None
    await r._ensure_client()
    assert captured_connect[-1]["servers"] == ["nats://127.0.0.1:14250"]
    assert joins == []


async def test_without_the_marker_the_controller_join_is_unchanged(sandbox_env, monkeypatch, captured_connect):
    """Agent App mode (no PANTHEON_APPS_RUNNER) keeps joining as the user."""
    state_dir, joins = sandbox_env
    monkeypatch.delenv("PANTHEON_APPS_RUNNER", raising=False)
    r = AppInstanceResolver.from_env(workdir=str(state_dir))
    with pytest.raises(ConnectionError):
        await r._ensure_coords()  # no runtime.json: adopts a node via the controller
    assert joins == ["https://fleet.example/join"]


async def _spec_for(app_service, state_dir):
    starts = []

    class Client:
        async def ping(self, target, timeout=3.0):
            return True

        async def start(self, target, spec):
            starts.append((target, spec))
            return {"ok": True}

    class Conn:
        is_connected = True

        def jetstream(self):
            raise RuntimeError("no registry")

    r = AppInstanceResolver("sandbox", "n_local", "u1", str(state_dir))
    r._nc, r._client = Conn(), Client()
    await r.ensure_instance(app_service)
    return starts[-1]


async def test_the_fleet_app_carries_the_users_key_only_on_a_local_runner(sandbox_env, monkeypatch):
    state_dir, _ = sandbox_env
    monkeypatch.setenv("PANTHEON_APPS_RUNNER", "local")
    target, spec = await _spec_for("fleet", state_dir)
    assert target == "n_local"
    assert spec["env"]["FLEET_KEY"] == "fleet-key"
    assert spec["env"]["FLEET_CONTROLLER_URL"] == "https://fleet.example"
    _, spec = await _spec_for("shell", state_dir)
    assert "FLEET_KEY" not in spec["env"]

    monkeypatch.delenv("PANTHEON_APPS_RUNNER")
    _, spec = await _spec_for("fleet", state_dir)
    assert "FLEET_KEY" not in spec["env"]


# --- the entrypoint's decision ------------------------------------------------

def _mode_block() -> str:
    text = ENTRYPOINT.read_text()
    match = re.search(r'\n(\s*case "\$\{PANTHEON_AGENT_APP_MODE:-\}" in\n.*?\n\s*esac)\n', text, re.S)
    assert match, "the Agent App mode decision moved"
    return match.group(1)


@pytest.mark.parametrize("env,expected", [
    ({}, "0"),                                             # old prod hub, new hub with topology off
    ({"FLEET_CONTROLLER_URL": "https://fleet.example"}, "0"),
    ({"PANTHEON_AGENT_APP_MODE": "1"}, "1"),               # new hub, Agent App node
    ({"PANTHEON_AGENT_APP_MODE": "1", "PANTHEON_NODE_APPS": "chatroom"}, "1"),
    ({"PANTHEON_NODE_APPS": "shell,file_manager,desktop"}, "1"),  # topology hub before the variable
    ({"PANTHEON_APPS_DEPLOYED": "1"}, "1"),
    ({"PANTHEON_AGENT_APP_MODE": "0", "PANTHEON_NODE_APPS": "chatroom"}, "0"),
])
def test_entrypoint_defaults_to_the_legacy_chatroom(env, expected):
    script = _mode_block() + '\nprintf %s "$AGENT_APP_MODE"\n'
    out = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", **env},
                         capture_output=True, text=True, check=True)
    assert out.stdout == expected


def test_entrypoint_legacy_branch_starts_a_loopback_runner():
    text = ENTRYPOINT.read_text()
    legacy = text[text.index('if [ "$AGENT_APP_MODE" != "1" ]'):text.index("elif [ -n \"${FLEET_CONTROLLER_URL:-}\" ]")]
    assert "export PANTHEON_APPS_RUNNER=local" in legacy
    assert "pantheon node broker -a 127.0.0.1" in legacy
    up = re.search(r"pantheon node up --nats .*?--no-capture-setup", legacy, re.S).group(0)
    assert "--controller" not in up
    # The pre-Agent-App join, kept as it was: a plain node, no Files.
    join = re.search(r"pantheon node up --controller .*?--no-capture-setup", legacy, re.S).group(0)
    assert '--name "sandbox-${ID_HASH}"' in join and "--no-files" in join
    assert "fs:workspace" not in join
