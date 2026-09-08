"""A factory-bound App survives Workspace replacement without changing identity."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from nats.errors import NoRespondersError

from pantheon.agent import Agent
from pantheon.apps import proxy as proxy_module
from pantheon.apps.proxy import ToolsetProxy
from pantheon.apps.registry import by_service_type
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.spec import apphost_spec
from pantheon.providers import ToolSetProvider
from pantheon.toolset import ToolSet, tool

_REAL_SLEEP = asyncio.sleep


class Echo(ToolSet):
    @tool
    async def echo(self, text: str):
        """Echo a value.

        Args:
            text: The value.
        """
        return text


@pytest.fixture
def no_backoff_wait(monkeypatch):
    async def sleep(_):
        await _REAL_SLEEP(0)
    monkeypatch.setattr(asyncio, 'sleep', sleep)


class FleetBoundary:
    """Real resolver/proxy; only fleet commands and remote tool transport fake."""

    def __init__(self, tmp_path, service_type='file_manager', scope='app'):
        self.resolver = AppInstanceResolver('fleet-test', 'agent', uuid4().hex, str(tmp_path))
        self.service_type, self.scope, self.workdir = service_type, scope, str(tmp_path)
        app_id = by_service_type()[service_type].manifest.id
        spec = apphost_spec(app_id, user_seed=self.resolver._seed, scope=scope, workdir=str(tmp_path))
        self.sid = spec['service_id']
        self.resolver._started[(service_type, scope)] = self.sid
        self.resolver._started[(service_type, 'another-project')] = 'unrelated-instance'
        self.starts = []
        self.methods = []
        self.alive = False
        self.fresh_reads = 0
        self.start_entered = asyncio.Event()
        self.start_release = None
        self.start_cancelled = False
        self.start_error = None
        self.stay_missing = False
        self.echo = Echo('echo-recovery')
        self.resolver._nodes_cache = (0, [self.node('retired-workspace')])
        self.resolver._client = self

        async def client():
            return self

        async def list_nodes():
            if self.resolver._nodes_cache is not None:
                return self.resolver._nodes_cache[1]
            self.fresh_reads += 1
            self.resolver._nodes_cache = (0, [self.node('new-workspace')])
            return self.resolver._nodes_cache[1]

        self.resolver._ensure_client = client
        self.resolver._list_nodes = list_nodes
        self.proxy = ToolsetProxy.from_toolset(self.sid)
        self.proxy.service = self

    @staticmethod
    def node(node_id):
        return {'node_id': node_id, 'name': node_id, 'kind': 'sandbox',
                'capability': {'caps': ['proc', 'fs:workspace', 'display', 'net'],
                               'runtimes': {'python': {}}}}

    def bind(self):
        return self.proxy.bind_instance(self.resolver, self.service_type,
                                       scope=self.scope, workdir=self.workdir)

    async def ping(self, node, timeout):
        return True

    async def start(self, node, spec):
        self.starts.append((node, spec))
        self.start_entered.set()
        if self.start_release is not None:
            try:
                await self.start_release.wait()
            except asyncio.CancelledError:
                self.start_cancelled = True
                raise
        if self.start_error:
            raise self.start_error
        self.alive = not self.stay_missing
        return {'ok': True}

    async def invoke(self, method, args):
        self.methods.append((method, args))
        if not self.alive:
            raise NoRespondersError()
        return await getattr(self.echo, method)(**args)


@pytest.mark.asyncio
async def test_factory_agent_recovers_same_app_on_fresh_workspace(tmp_path, monkeypatch, no_backoff_wait):
    from pantheon.factory import _resolve_toolset_proxy
    import pantheon.apps.resolver as resolver_module

    f = FleetBoundary(tmp_path)
    monkeypatch.setattr(resolver_module, 'get_shared_resolver', lambda: f.resolver)
    proxy = await _resolve_toolset_proxy('file_manager')
    assert proxy is f.proxy
    assert proxy.has_instance_binding
    assert not f.starts  # factory got the old resolver cache hit, as in production
    a = Agent('recover-test', 'Test', model='gpt-4o-mini')
    await a.toolset(ToolSetProvider(proxy))
    assert await a.call_tool(f'{proxy.toolset_name}__echo', {'text': 'new workspace'}) == 'new workspace'
    assert len(f.starts) == 1
    node, spec = f.starts[0]
    assert node == 'new-workspace'
    assert spec['service_id'] == f.sid
    assert spec['scope'] == 'app'
    assert spec['dir'] == f.workdir
    assert f.resolver._started[('file_manager', 'another-project')] == 'unrelated-instance'
    assert f.fresh_reads == 1
    assert [name for name, _ in f.methods] == ['list_tools'] * 7 + ['echo']


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_reensure_and_replay_once(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path, scope='owned-project')
    f.bind()
    f.start_release = asyncio.Event()
    first = asyncio.create_task(f.proxy.invoke('echo', {'text': 'first'}))
    second = asyncio.create_task(f.proxy.invoke('echo', {'text': 'second'}))
    await f.start_entered.wait()
    while f.proxy._recovery_waiters != 2:
        await _REAL_SLEEP(0)
    f.start_release.set()
    assert await asyncio.gather(first, second) == ['first', 'second']
    assert len(f.starts) == 1
    assert f.starts[0][1]['scope'] == 'owned-project'
    assert [args['text'] for method, args in f.methods].count('first') == 7
    assert [args['text'] for method, args in f.methods].count('second') == 7
    assert f.proxy._recovery_task is None


@pytest.mark.asyncio
async def test_late_missing_call_reuses_completed_recovery(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    old_generation = f.proxy._recovery_generation
    assert await f.proxy.invoke('echo', {'text': 'first'}) == 'first'
    await f.proxy._recover_instance(old_generation)
    assert len(f.starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [TimeoutError('completion unknown'), RuntimeError('method rejected')])
async def test_non_missing_errors_are_never_replayed(tmp_path, failure, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    async def failed(method, args):
        f.methods.append((method, args))
        raise failure
    f.invoke = failed
    with pytest.raises(type(failure), match=str(failure)):
        await f.proxy.invoke('echo', {'text': 'must not duplicate'})
    assert len(f.methods) == 1
    assert not f.starts


@pytest.mark.asyncio
async def test_business_rejection_is_not_replayed(tmp_path):
    f = FleetBoundary(tmp_path)
    f.bind()
    async def rejected(method, args):
        f.methods.append((method, args))
        return {'success': False, 'error': 'Rejected by application'}
    f.invoke = rejected
    assert (await f.proxy.invoke('echo', {}))['success'] is False
    assert len(f.methods) == 1
    assert not f.starts


@pytest.mark.asyncio
async def test_recovery_gets_only_one_new_retry_round(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    f.stay_missing = True
    with pytest.raises(NoRespondersError):
        await f.proxy.invoke('echo', {'text': 'missing'})
    assert len(f.starts) == 1
    assert len(f.methods) == 12


@pytest.mark.asyncio
async def test_unbound_legacy_proxy_never_guesses_an_app(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    with pytest.raises(NoRespondersError):
        await f.proxy.invoke('echo', {'text': 'unknown binding'})
    assert not f.starts
    assert len(f.methods) == 6


@pytest.mark.asyncio
async def test_failed_reensure_can_be_retried_explicitly(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    f.start_error = RuntimeError('node unavailable')
    with pytest.raises(RuntimeError, match='node unavailable'):
        await f.proxy.invoke('echo', {'text': 'first'})
    assert f.proxy._recovery_task is None
    f.start_error = None
    assert await f.proxy.invoke('echo', {'text': 'retry'}) == 'retry'
    assert len(f.starts) == 2


@pytest.mark.asyncio
async def test_recovery_refuses_changed_service_identity(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    async def wrong_instance(*args, **kwargs):
        return 'different-service'
    f.resolver.ensure_instance = wrong_instance
    with pytest.raises(RuntimeError, match='does not match'):
        await f.proxy.invoke('echo', {'text': 'must not cross instance'})
    assert len(f.methods) == 6
    assert f.proxy.service_id == f.sid


@pytest.mark.asyncio
async def test_cancel_one_recovery_waiter_then_last_cancels_control_plane(tmp_path, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    f.start_release = asyncio.Event()
    first = asyncio.create_task(f.proxy.invoke('echo', {'text': 'first'}))
    second = asyncio.create_task(f.proxy.invoke('echo', {'text': 'second'}))
    await f.start_entered.wait()
    while f.proxy._recovery_waiters != 2:
        await _REAL_SLEEP(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not f.start_cancelled
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert f.start_cancelled
    assert f.proxy._recovery_waiters == 0
    assert f.proxy._recovery_task is None
    assert f.proxy._recovery_generation == 0


@pytest.mark.asyncio
async def test_metadata_read_timeout_does_not_restart_a_slow_responder(tmp_path, monkeypatch):
    f = FleetBoundary(tmp_path)
    f.bind()
    monkeypatch.setattr(proxy_module, 'TOOLSET_SCHEMA_READ_TIMEOUT', 0.01)
    cancelled = asyncio.Event()
    async def slow_read(method, args):
        f.methods.append((method, args))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    f.invoke = slow_read
    with pytest.raises(TimeoutError):
        await f.proxy.list_tools()
    assert cancelled.is_set()
    assert len(f.methods) == 1
    assert not f.starts


@pytest.mark.asyncio
async def test_metadata_recovery_has_separate_budget_and_keeps_parameters(tmp_path, monkeypatch, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    monkeypatch.setattr(proxy_module, 'TOOLSET_SCHEMA_READ_TIMEOUT', 0.01)
    monkeypatch.setattr(proxy_module, 'TOOLSET_RECOVERY_TIMEOUT', 0.3)
    original = f.start
    async def cold_start(node, spec):
        await _REAL_SLEEP(0.03)  # longer than a read budget, within recovery's own
        return await original(node, spec)
    f.start = cold_start
    provider = ToolSetProvider(f.proxy)
    assert [t.name for t in await provider.list_tools()] == ['echo']
    assert await provider.call_tool('echo', {'text': 'preserved'}) == 'preserved'
    assert len(f.starts) == 1


@pytest.mark.asyncio
async def test_recovery_timeout_cancels_control_plane_and_releases_singleflight(tmp_path, monkeypatch, no_backoff_wait):
    f = FleetBoundary(tmp_path)
    f.bind()
    f.start_release = asyncio.Event()
    monkeypatch.setattr(proxy_module, 'TOOLSET_RECOVERY_TIMEOUT', 0.01)
    with pytest.raises(TimeoutError):
        await f.proxy.list_tools()
    assert f.start_cancelled
    assert f.proxy._recovery_task is None
    assert f.proxy._recovery_waiters == 0


def test_binding_conflicts_and_desktop_canonical_scope(tmp_path):
    f = FleetBoundary(tmp_path)
    f.bind()
    with pytest.raises(ValueError, match='different instance binding'):
        f.proxy.bind_instance(f.resolver, 'file_manager', scope='other-project')
    other_user = AppInstanceResolver('fleet-test', 'agent', 'other-user', str(tmp_path))
    with pytest.raises(ValueError, match='different instance binding'):
        f.proxy.bind_instance(other_user, 'file_manager')
    d = FleetBoundary(tmp_path, service_type='desktop')
    d.bind()
    assert d.proxy.bind_instance(d.resolver, 'desktop', scope='some-project', workdir='/ignored') is d.proxy


def test_resolver_invalidate_default_compatibility_and_precise_scope(tmp_path):
    f = FleetBoundary(tmp_path)
    f.resolver._started[('shell', 'app')] = 'shell-instance'
    f.resolver.invalidate('file_manager', scope='app')
    assert ('file_manager', 'app') not in f.resolver._started
    assert f.resolver._started[('file_manager', 'another-project')] == 'unrelated-instance'
    assert f.resolver._started[('shell', 'app')] == 'shell-instance'
    assert f.resolver._nodes_cache is None
    f.resolver.invalidate('file_manager')
    assert f.resolver._started == {('shell', 'app'): 'shell-instance'}


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', [False, True])
async def test_chatroom_pooled_proxy_has_only_one_recovery_owner(tmp_path, monkeypatch, no_backoff_wait, bound):
    import pantheon.apps.resolver as resolver_module
    from pantheon.chatroom.room import ChatRoom

    f = FleetBoundary(tmp_path, service_type='desktop')
    if bound:
        f.bind()
    f.stay_missing = True
    monkeypatch.setattr(resolver_module, 'get_shared_resolver', lambda: f.resolver)
    room = ChatRoom.__new__(ChatRoom)
    async def project_dir(_):
        return f.workdir
    room._project_dir_for_chat = project_dir
    result = await room.proxy_toolset('echo', {'text': 'must not repeat recovery'}, 'desktop')
    assert result['success'] is False
    assert 'no responders' in result['error']
    assert len(f.starts) == 1
    assert len(f.methods) == 12
