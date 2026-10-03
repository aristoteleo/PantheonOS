"""Desktop readiness/telemetry remains available without Agent activity."""
import asyncio
import time
from types import SimpleNamespace

from pantheon.platform.health import PlatformHealth
from pantheon.platform.service import PlatformService


def test_ping_uses_cached_nodes_without_waiting_for_registry(tmp_path, monkeypatch):
    from pantheon.apps import resolver as resolver_module
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def nodes(**kwargs):
            calls.append(kwargs)
            entered.set()
            await release.wait()
            return [{'name': 'workspace', 'kind': 'machine', 'last_seen': 'now',
                     'capability': {'caps': ['proc', 'fs:workspace'], 'cpu_cores': 4, 'ram_gb': 8},
                     'state': {'load': {'cpu': .12345, 'mem': .25}}}]
        monkeypatch.setattr(resolver_module, 'get_shared_resolver',
                            lambda: SimpleNamespace(_list_nodes=nodes))
        host = PlatformService(workspace_path=tmp_path)
        try:
            first = host._get_platform_status()
            assert first['activity_scope'] == 'platform'
            assert 'has_active_tasks' not in first
            assert 'fleet_nodes' not in first
            await asyncio.wait_for(entered.wait(), 1)
            # Multiple pings must not queue duplicate probes while one is blocked.
            for _ in range(10):
                assert 'fleet_nodes' not in host._get_platform_status()
            assert len(calls) == 1
            release.set()
            await host._fleet_nodes_task
            status = host._get_platform_status()
            assert status['fleet_nodes'][0]['caps'] == ['proc', 'fs:workspace']
            assert status['fleet_nodes'][0]['load_cpu'] == .123
        finally:
            await host.cleanup()
    asyncio.run(scenario())


def test_health_cleanup_cancels_pending_registry_probe(tmp_path, monkeypatch):
    from pantheon.apps import resolver as resolver_module
    async def scenario():
        entered = asyncio.Event()
        async def nodes(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(resolver_module, 'get_shared_resolver',
                            lambda: SimpleNamespace(_list_nodes=nodes))
        host = PlatformService(workspace_path=tmp_path)
        host._get_platform_status()
        await asyncio.wait_for(entered.wait(), 1)
        task = host._fleet_nodes_task
        await host.cleanup()
        assert task.cancelled()
        assert not host._fleet_nodes_refreshing
    asyncio.run(scenario())


def test_workspace_walk_ignores_package_trees_and_symlinks(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/file').write_bytes(b'abc')
    for name in ('.local', '.cache', '.git'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'large').write_bytes(b'x' * 100)
    (tmp_path / 'link').symlink_to(tmp_path / 'data', target_is_directory=True)
    assert PlatformHealth._walk_workspace_bytes(str(tmp_path)) == 3


def test_transitional_agent_does_not_duplicate_platform_disk_scan(monkeypatch):
    import pantheon.platform.health as health
    import threading
    monkeypatch.setenv('PANTHEON_STATE_SYNC_OWNER', 'external')
    monkeypatch.setattr(health, '_psutil', SimpleNamespace())
    monkeypatch.setattr(health, '_psutil_process', SimpleNamespace())
    monkeypatch.setattr('os.path.isdir', lambda path: True)
    def forbidden(*a, **kw):
        raise AssertionError('child should not scan platform filesystem')
    monkeypatch.setattr(threading, 'Thread', forbidden)
    host = PlatformService()
    host._started_monotonic = time.monotonic() - 1000
    host._get_host_metrics()
    assert not getattr(host, '_disk_walk_running', False)


def test_legacy_agent_retains_its_own_activity_metrics():
    from pantheon.chatroom.room import ChatRoom
    room = ChatRoom.__new__(ChatRoom)
    room.threads = {'active': object()}
    room.chat_teams = {}
    room._transfer_handles_cached = lambda: 2
    room._get_host_metrics = lambda: {'fleet_nodes': [{'name': 'workspace'}]}
    result = room._get_activity_status()
    assert result['has_active_tasks'] is True
    assert result['active_threads'] == 1
    assert result['transfer_handles'] == 2
    assert result['fleet_nodes'] == [{'name': 'workspace'}]
