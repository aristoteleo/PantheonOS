"""Agent stop semantics through the ordinary App host, without a paid LLM.

The process test uses the legacy ChatRoom or separated AgentRuntime constructor,
Thread, memory, ToolSet, CLI and TCP RPC transport. Only the team's model work is
a deterministic fixture. The core variant prohibits platform controllers and
legacy bootstrap imports; it is not final Agent packaging/credential isolation.
"""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from pantheon.apps.host_lifecycle import AppShutdownError
from pantheon.background import BackgroundTaskManager
from pantheon.chatroom.lifecycle import AgentLifetime, admitted_chat
from pantheon.chatroom.stream import NATSStreamAdapter
from pantheon.remote.backend.tcp import TCPBackend
from pantheon.utils.misc import generate_service_id

ROOT = Path(__file__).resolve().parents[1]


class Lifetime(AgentLifetime):
    def __init__(self):
        self._background_tasks = set()
        self._plugins = []
        self.chat_teams = {}

    async def _stop_oauth(self):
        pass

    _stop_playground = _stop_oauth
    _stop_model_directory = _stop_oauth
    _stop_health_refresh = _stop_oauth

    @admitted_chat
    async def chat(self, entered, release):
        entered.set()
        await release.wait()
        return "finished"


@pytest.mark.asyncio
async def test_internal_chat_drains_and_late_calls_are_rejected():
    app = Lifetime()
    entered, release = asyncio.Event(), asyncio.Event()
    call = asyncio.create_task(app.chat(entered, release))
    await entered.wait()
    await app.begin_shutdown()
    late = asyncio.Event()
    assert (await app.chat(late, release))["success"] is False
    assert not late.is_set()
    cleanup = asyncio.create_task(app.cleanup())
    await asyncio.sleep(0)
    assert not cleanup.done() and not call.done()
    release.set()
    assert await call == "finished"
    await asyncio.wait_for(cleanup, 1)
    assert not app._agent_calls


@pytest.mark.asyncio
async def test_adopted_tool_mutation_finishes_before_plugins_and_stream_close():
    app = Lifetime()
    manager = BackgroundTaskManager()
    entered, release = asyncio.Event(), asyncio.Event()
    events = []

    async def mutation():
        entered.set()
        await release.wait()
        events.append("write")

    async def close():
        events.append("close")

    tool = manager.adopt("write", "call", {}, asyncio.create_task(mutation()))
    agent = SimpleNamespace(_bg_manager=manager)
    app.chat_teams = {"a": SimpleNamespace(agents={"a": agent, "alias": agent})}
    app._plugins = [SimpleNamespace(on_shutdown=close)]
    app._nats_adapter = SimpleNamespace(close=close)
    await entered.wait()
    cleanup = asyncio.create_task(app.cleanup())
    await asyncio.sleep(.02)
    assert not cleanup.done() and not events
    release.set()
    await asyncio.wait_for(cleanup, 1)
    assert tool.status == "completed"
    assert events == ["write", "close", "close"]


@pytest.mark.asyncio
async def test_observer_cleanup_waited_and_failures_do_not_skip_other_resources():
    app = Lifetime()
    entered, exiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    async def observer():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exiting.set()
            await release.wait()
            events.append("observer")

    async def broken():
        events.append("broken")
        raise RuntimeError("plugin close failed")

    async def close():
        events.append("closed")

    app._track_background(asyncio.create_task(observer()))
    plugin = SimpleNamespace(on_shutdown=broken)
    app._plugins = [plugin, plugin]
    app._nats_adapter = SimpleNamespace(close=close)
    await entered.wait()
    cleanup = asyncio.create_task(app.cleanup())
    await exiting.wait()
    assert not cleanup.done()
    release.set()
    with pytest.raises(AppShutdownError) as failure:
        await cleanup
    assert len(failure.value.errors) == 1
    assert events == ["observer", "broken", "closed"]
    with pytest.raises(AppShutdownError):
        await app.cleanup()
    assert events == ["observer", "broken", "closed"]


@pytest.mark.asyncio
async def test_stream_close_is_final_and_does_not_reconnect():
    adapter = NATSStreamAdapter()
    closed = []

    async def close():
        closed.append(True)

    adapter._backend = SimpleNamespace(close=close)
    await adapter.close()
    await adapter.close()
    with pytest.raises(RuntimeError, match="closed"):
        await adapter._get_backend()
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_chat_keeps_ownership_of_final_save_during_repeated_cancellation(tmp_path, fail):
    from pantheon.chatroom.room import ChatRoom
    from pantheon.internal.memory import MemoryManager
    from pantheon.team.base import Team

    room = ChatRoom.__new__(ChatRoom)
    room.check_before_chat = None
    room._enable_auto_chat_name = False
    room._background_tasks = set()
    room._plugins = []
    room.threads = {}
    room.chat_teams = {}
    room._nats_adapter = None
    room.memory_manager = MemoryManager(tmp_path, use_jsonl=True)
    memory = room.memory_manager.new_memory("save fixture")
    entered, release = threading.Event(), threading.Event()
    finished = threading.Event()
    threads = []

    class TestTeam(Team):
        async def run(self, messages, **kwargs):
            threads.append(room.threads[memory.id])
            return SimpleNamespace(content="done")

    async def project(_):
        return None

    room._default_team = TestTeam([])
    room._project_dir_for_chat = project
    original = room.memory_manager.save_one

    def save(chat_id):
        entered.set()
        try:
            assert release.wait(10), "test did not release final save"
            if fail:
                raise OSError("fixture disk write failed")
            original(chat_id)
        finally:
            finished.set()

    room.memory_manager.save_one = save
    call = asyncio.create_task(room.chat(memory.id, [{"role": "user", "content": "hi"}]))
    cleanup = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        call.cancel()
        await asyncio.sleep(.01)
        call.cancel()
        cleanup = asyncio.create_task(room.cleanup())
        await asyncio.sleep(.01)
        assert not call.done() and not cleanup.done()
        assert not threads[0]._done.is_set()
        release.set()
        with pytest.raises(OSError if fail else asyncio.CancelledError):
            await call
        assert finished.is_set() and threads[0]._done.is_set()
        if fail:
            with pytest.raises(AppShutdownError, match="did not finish cleanly"):
                await cleanup
        else:
            await cleanup
            restored = MemoryManager(tmp_path, use_jsonl=True).get_memory(memory.id)
            assert restored.extra_data["running"] is False
    finally:
        release.set()
        await asyncio.gather(call, *([cleanup] if cleanup else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_gateway_stop_joins_real_channel_thread_without_blocking_loop(tmp_path):
    from pantheon.claw.config import ClawConfigStore
    from pantheon.claw.manager import GatewayChannelManager
    from pantheon.claw.registry import ClawRouteRegistry

    store = ClawConfigStore(tmp_path / "claw.json")
    store.save({"slack": {"app_token": "fixture", "bot_token": "fixture"}})
    loop = asyncio.get_running_loop()
    entered, exiting, release = asyncio.Event(), asyncio.Event(), threading.Event()

    async def runner(*, bridge, config, stop_event):
        loop.call_soon_threadsafe(entered.set)
        while not stop_event.is_set():
            await asyncio.sleep(.01)
        loop.call_soon_threadsafe(exiting.set)
        assert await asyncio.to_thread(release.wait, 10)

    manager = GatewayChannelManager(chatroom=object(), loop=loop, config_store=store,
        registry=ClawRouteRegistry(tmp_path / "routes.json"))
    manager._load_runner = lambda _: (runner, None)
    assert manager.start_channel("slack")["ok"]
    await asyncio.wait_for(entered.wait(), 1)
    closer = asyncio.create_task(manager.close())
    try:
        await asyncio.wait_for(exiting.wait(), 1)
        assert not closer.done()
        assert manager.start_channel("slack") == {"ok": False, "error": "Agent gateway is stopping"}
        release.set()
        await asyncio.wait_for(closer, 2)
        assert not manager._threads and not manager._handlers
    finally:
        release.set()
        await closer


FIXTURE = '''
import asyncio
import importlib.abc
import os
import sys
from pathlib import Path
from types import SimpleNamespace

CORE = os.environ.get('AGENT_FIXTURE_COMPOSITION') == 'core'
if CORE:
    class NoPlatformControllers(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, *args):
            blocked = ('pantheon.chatroom.room', 'pantheon.chatroom.start',
                'pantheon.platform.service', 'pantheon.platform.projects',
                'pantheon.platform.apps_api', 'pantheon.platform.fleet_api',
                'pantheon.platform.models_api', 'pantheon.platform.projects_api',
                'pantheon.platform.store_api', 'pantheon.platform.oauth_api',
                'pantheon.platform.model_directory', 'pantheon.platform.health',
                'pantheon.apps.builtin.llm_playground', 'pantheon.repl')
            if any(fullname == p or fullname.startswith(p + '.') for p in blocked):
                raise AssertionError('Agent imported a platform controller: ' + fullname)
    sys.meta_path.insert(0, NoPlatformControllers())
    from pantheon.chatroom import AgentRuntime as AgentBase
else:
    from pantheon.chatroom import ChatRoom as AgentBase
from pantheon.background import BackgroundTaskManager
from pantheon.team.base import Team

class TestTeam(Team):
    def __init__(self, root):
        self.root = root
        super().__init__([])
        self.manager = BackgroundTaskManager()
        self.agents = {'fixture': SimpleNamespace(name='fixture', _bg_manager=self.manager)}

    async def run(self, messages, memory, **kwargs):
        with (self.root/'run-entered').open('a') as f:
            f.write('run\\n')
        while not (self.root/'release-run').exists():
            await asyncio.sleep(.01)
        memory.add_messages([{'role': 'assistant', 'content': 'finished once'}])
        async def mutation():
            (self.root/'tool-entered').touch()
            while not (self.root/'release-tool').exists():
                await asyncio.sleep(.01)
            with (self.root/'writes').open('a') as f:
                f.write('written\\n')
        self.manager.start('fixture_write', 'call-1', {}, mutation())
        return SimpleNamespace(content='finished once')

class HostedAgent(AgentBase):
    def __init__(self, name, workdir, **kwargs):
        self.root = Path(workdir)
        if CORE:
            from pantheon.chatroom.environment import AgentEnvironment
            from pantheon.factory.template_manager import TemplateManager
            from pantheon.settings import Settings

            class BoundProjects:
                # Only a read-only workspace view is available to the core.
                active_project = default_project = SimpleNamespace(path=workdir, name='fixture')
                def list_projects(self):
                    return [{'name': 'fixture', 'path': workdir}]
                def get_project(self, path):
                    return self.default_project if path == workdir else None

            async def unavailable(*args):
                raise AssertionError('Default-team fixture must not resolve dependencies')

            settings = Settings(Path(workdir), isolated_env=True,
                                user_home=self.root/'agent-user-config')
            kwargs['environment'] = AgentEnvironment(projects=BoundProjects(),
                templates=TemplateManager(settings=settings), settings=lambda: settings,
                ensure_services=unavailable, create_agents=unavailable,
                validate_model=lambda model: (False, 'fixture has no model binding'))
        else:
            kwargs['workspace_path'] = workdir
        super().__init__(name=name, memory_dir=str(self.root/'memory'),
                         default_team=TestTeam(self.root), **kwargs)

    async def run_setup(self):
        # Exclude external model catalogue fetch / optional plugins from the
        # deterministic lifecycle fixture; production constructor and chat run.
        memory = self.memory_manager.new_memory('lifecycle fixture')
        (self.root/'chat-id').write_text(memory.id)
        root = self.root
        class Plugin:
            async def on_shutdown(self):
                with (root/'closed').open('a') as f:
                    f.write('plugin\\n')
        self._plugins = [Plugin()]

    async def begin_shutdown(self):
        await super().begin_shutdown()
        (self.root/'stopping').touch()
'''


@contextmanager
def agent_process(root, composition):
    catalog = root / "catalog" / "agent-fixture"
    catalog.mkdir(parents=True)
    if composition == 'legacy':
        # Only the old combined host requires the Playground package.
        (catalog.parent / "llm_playground").symlink_to(ROOT / "apps" / "llm_playground", target_is_directory=True)
    (catalog / "app.json").write_text(json.dumps({
        "id": "agent-fixture", "name": "Agent fixture", "version": "1.0.0",
        "runtime": "process", "entry": {"backend": "agent_fixture:HostedAgent"},
        "placement": {"requires": ["fs:workspace"]},
    }))
    (root / "agent_fixture.py").write_text(FIXTURE)
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "LANG") if k in os.environ}
    env.update(HOME=str(root), PYTHONPATH=os.pathsep.join((str(root), str(ROOT))),
               AGENT_FIXTURE_COMPOSITION=composition,
               PANTHEON_APPS_ROOT=str(catalog.parent), PANTHEON_REMOTE_BACKEND="tcp",
               PANTHEON_TCP_REGISTRY=str(root / "registry"))
    with (root / "host.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "pantheon.apphost",
            "--app-id", "agent-fixture", "--workdir", str(root), "--id-hash", "agent-fixture"],
            cwd=root, env=env, stdout=log, stderr=log)
        try:
            yield process
        finally:
            (root / "release-run").touch()
            (root / "release-tool").touch()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def until(check, process, root):
    deadline = asyncio.get_running_loop().time() + 30
    while not check():
        assert process.poll() is None, (root / "host.log").read_text()
        assert asyncio.get_running_loop().time() < deadline, (root / "host.log").read_text()
        await asyncio.sleep(.02)


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("composition", ['legacy', 'core'])
async def test_real_app_host_drains_chat_save_and_background_tool(tmp_path, queued, composition):
    with agent_process(tmp_path, composition) as process:
        record = tmp_path / "registry" / (generate_service_id("agent-fixture") + ".json")
        await until(record.exists, process, tmp_path)
        client = await TCPBackend(str(record.parent)).connect(generate_service_id("agent-fixture"))
        pending = None
        try:
            if composition == 'core':
                for method in ('fleet_app_lifecycle', 'llm_playground_catalog', 'list_projects'):
                    with pytest.raises(Exception, match='not found'):
                        await client.invoke(method, {})
            chat_id = (tmp_path / "chat-id").read_text()
            # The context UI must not pull in the terminal/legacy ChatRoom.
            stats = await client.invoke('get_token_stats', {'chat_id': chat_id})
            assert stats['success'] is True
            pending = asyncio.create_task(client.invoke("chat", {"chat_id": chat_id,
                "message": [{"role": "user", "content": "fixture"}]}))
            await until(lambda: (tmp_path / "run-entered").exists(), process, tmp_path)
            if queued:
                assert await client.invoke("chat", {"chat_id": chat_id,
                    "message": [{"role": "user", "content": "accepted steer"}]}) == {
                        "success": True, "queued": True}
            process.terminate()
            await until(lambda: (tmp_path / "stopping").exists(), process, tmp_path)
            assert not (tmp_path / "closed").exists() and not record.exists()
            with pytest.raises(Exception, match="stopping"):
                await client.invoke("chat", {"chat_id": chat_id, "message": []})
            (tmp_path / "release-run").touch()
            assert (await asyncio.wait_for(pending, 10))["success"] is True
            await until(lambda: (tmp_path / "tool-entered").exists(), process, tmp_path)
            await asyncio.sleep(.05)
            assert process.poll() is None and not (tmp_path / "closed").exists()
            (tmp_path / "release-tool").touch()
            assert await asyncio.to_thread(process.wait, 15) == 0, (tmp_path / "host.log").read_text()
            count = 2 if queued else 1
            # Already accepted steer turns finish; tool completion cannot start
            # any further automatic turn after the App begins stopping.
            assert (tmp_path / "run-entered").read_text() == "run\n" * count
            assert (tmp_path / "writes").read_text() == "written\n" * count
            assert (tmp_path / "closed").read_text() == "plugin\n"
            assert not record.exists()
            from pantheon.internal.memory import MemoryManager
            memory = MemoryManager(tmp_path / ".pantheon" / "memory", use_jsonl=True).get_memory(chat_id)
            assert memory.extra_data["running"] is False
            assert any(m.get("content") == "finished once" for m in memory.get_messages())
        finally:
            (tmp_path / "release-run").touch()
            (tmp_path / "release-tool").touch()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await client.close()
