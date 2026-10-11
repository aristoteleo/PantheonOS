"""Desktop readiness/telemetry remains available without Agent activity."""
import time
from types import SimpleNamespace

from pantheon.chatroom.services.health import PlatformHealth
from services_host import ServicesHost as PlatformService


def test_workspace_walk_ignores_package_trees_and_symlinks(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/file').write_bytes(b'abc')
    for name in ('.local', '.cache', '.git'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'large').write_bytes(b'x' * 100)
    (tmp_path / 'link').symlink_to(tmp_path / 'data', target_is_directory=True)
    assert PlatformHealth._walk_workspace_bytes(str(tmp_path)) == 3


def test_transitional_agent_does_not_duplicate_platform_disk_scan(monkeypatch):
    import pantheon.chatroom.services.health as health
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
