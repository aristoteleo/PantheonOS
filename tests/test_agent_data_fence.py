"""Actual cooperative process locks and durable cutover fencing, no live data."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.chatroom.data_fence import (
    DataFencedError, LegacyDataLease, MigrationFence, LOCK_NAME, MARKER_NAME,
)
from pantheon.chatroom.routed_memory import ProjectRoutedMemoryManager


def fence(roots, target, **kwargs):
    return MigrationFence(roots, operation=kwargs.get('operation', 'move-1'),
                          target=target, namespace=kwargs.get('namespace', 'agent-1'))


@contextmanager
def child(code, *args):
    proc = subprocess.Popen([sys.executable, '-u', '-c', code, *map(str, args)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'ready'
        yield proc
    finally:
        proc.kill()
        proc.communicate(timeout=15)


@pytest.mark.skipif(os.name == 'nt', reason='Windows locking requires Windows runtime acceptance')
def test_migration_waits_for_all_legacy_processes_then_blocks_restart(tmp_path):
    root = tmp_path / 'source'
    code = '''
import sys
from pantheon.chatroom.data_fence import LegacyDataLease
lease = LegacyDataLease()
lease.acquire(sys.argv[1])
print('ready', flush=True)
sys.stdin.read()
'''
    with child(code, root):
        local = LegacyDataLease()
        local.acquire(root)  # Legacy peers remain compatible with each other.
        try:
            with pytest.raises(DataFencedError): fence([root], tmp_path / 'target')
            assert not (root / MARKER_NAME).exists()
        finally:
            local.close()
        with pytest.raises(DataFencedError): fence([root], tmp_path / 'target')
    # Kernel releases the dead process's shared lock.
    with fence([root], tmp_path / 'target'):
        with pytest.raises(DataFencedError): LegacyDataLease().acquire(root)
    # Closing the migrator does not make legacy data writable again.
    with pytest.raises(DataFencedError): LegacyDataLease().acquire(root)
    with pytest.raises(DataFencedError): fence([root], tmp_path / 'other-target')
    with pytest.raises(DataFencedError): fence([root], tmp_path / 'target', operation='other')
    with pytest.raises(DataFencedError): fence([root], tmp_path / 'target', namespace='other')
    with fence([root], tmp_path / 'target') as migration:
        migration.release_sources()
    local = LegacyDataLease()
    local.acquire(root)
    local.close()


def test_all_roots_are_locked_before_any_is_marked(tmp_path):
    roots = [tmp_path / 'a', tmp_path / 'b']
    writer = LegacyDataLease()
    writer.acquire(roots[1])
    try:
        with pytest.raises(DataFencedError): fence(roots, tmp_path / 'target')
        assert not any((root / MARKER_NAME).exists() for root in roots)
        # Failed multi-root acquire must not leak the earlier exclusive lock.
        other = LegacyDataLease()
        other.acquire(roots[0]); other.close()
    finally:
        writer.close()
    with fence(roots, tmp_path / 'target'):
        pass
    with pytest.raises(DataFencedError): fence([roots[0]], tmp_path / 'target')


@pytest.mark.skipif(os.name == 'nt', reason='Windows locking requires Windows runtime acceptance')
def test_migrator_crash_keeps_marker_and_same_operation_can_resume(tmp_path):
    root, target = tmp_path / 'source', tmp_path / 'target'
    code = '''
import sys
from pantheon.chatroom.data_fence import MigrationFence
fence = MigrationFence([sys.argv[1]], operation='move-1', target=sys.argv[2], namespace='agent-1')
print('ready', flush=True)
sys.stdin.read()
'''
    with child(code, root, target):
        before = (root / MARKER_NAME).read_bytes()
        with pytest.raises(DataFencedError): fence([root], target)
    with pytest.raises(DataFencedError): LegacyDataLease().acquire(root)
    with fence([root], target):
        assert (root / MARKER_NAME).read_bytes() == before
        assert not (root / MARKER_NAME).stat().st_mode & 0o077


@pytest.mark.parametrize('kind', ['symlink-lock', 'hardlink-lock', 'symlink-marker', 'bad-marker'])
def test_unsafe_or_corrupt_controls_fail_closed(tmp_path, kind):
    root = tmp_path / 'source'; root.mkdir()
    other = tmp_path / 'untouched'; other.write_text('unchanged')
    if kind == 'symlink-lock': (root / LOCK_NAME).symlink_to(other)
    if kind == 'hardlink-lock': os.link(other, root / LOCK_NAME)
    if kind == 'symlink-marker': (root / MARKER_NAME).symlink_to(other)
    if kind == 'bad-marker': (root / MARKER_NAME).write_text('{partial')
    for action in (lambda: LegacyDataLease().acquire(root), lambda: fence([root], tmp_path / 'target')):
        with pytest.raises((ValueError, OSError, DataFencedError)): action()
    assert other.read_text() == 'unchanged'


def test_lazy_project_access_is_fenced_and_failed_selection_keeps_old_route(tmp_path):
    writer = LegacyDataLease()
    home, second = tmp_path / 'home', tmp_path / 'second'
    manager = ProjectRoutedMemoryManager(home, acquire_store=writer.acquire)
    with fence([second], tmp_path / 'target'):
        with pytest.raises(DataFencedError): manager.set_active_dir(second)
        assert manager.active_dir == str(home)
        with pytest.raises(DataFencedError): manager.new_memory_in(second, 'must not write')
        assert set(p.name for p in second.iterdir()) == {LOCK_NAME, MARKER_NAME}
    memory = manager.new_memory('old project still works')
    memory.set_metadata('name', 'saved')
    assert (home / (memory.id + '.meta.json')).exists()
    writer.close()
    with pytest.raises(DataFencedError): manager.new_memory('late call after stop')


@pytest.fixture
def room(tmp_path, monkeypatch):
    from pantheon.chatroom.room import ChatRoom
    from pantheon.chatroom.app_data import AppProjects
    from pantheon.settings import Settings
    config = tmp_path / 'project'; config.mkdir()
    settings = Settings(config, user_home=tmp_path / 'user', isolated_env=True, environment={})
    projects = AppProjects([{'id': 'p', 'name': 'P', 'path': str(config)}], active_id='p', default_id='p')
    monkeypatch.setattr('pantheon.chatroom.room.get_settings', lambda: settings)
    monkeypatch.setattr('pantheon.chatroom.room.get_template_manager', lambda: SimpleNamespace())
    monkeypatch.setattr('pantheon.chatroom.room.ProjectManager', lambda **k: projects)
    # No external service/plugin startup is needed to exercise the real runtime
    # constructor, routing thread, memory flush and cleanup barrier.
    monkeypatch.setattr(ChatRoom, '_init_plugins', lambda self: setattr(self, '_plugins', []))
    instance = ChatRoom(memory_dir=str(config / '.pantheon/memory'))
    instance._memory_routing_thread.join()
    instance._stop_auxiliary_services = AsyncMock()
    yield instance, settings, projects
    instance._legacy_data_lease.close()


@pytest.mark.asyncio
async def test_real_legacy_constructor_holds_roots_through_flush(room, tmp_path):
    instance, settings, _ = room
    roots = [settings.user_home, settings.pantheon_dir, instance.memory_dir]
    entered, release = asyncio.Event(), asyncio.Event()
    original = instance.memory_manager.flush
    async def slow_flush():
        entered.set()
        await release.wait()
        await original()
    instance.memory_manager.flush = slow_flush
    task = asyncio.create_task(instance.cleanup())
    await entered.wait()
    try:
        for root in roots:
            with pytest.raises(DataFencedError): fence([root], tmp_path / 'target')
    finally:
        release.set()
        await task
    with fence(roots, tmp_path / 'target'):
        with pytest.raises(DataFencedError):
            type(instance)(memory_dir=str(instance.memory_dir))
    await instance.cleanup()  # Idempotent drain.


@pytest.mark.asyncio
async def test_failed_drain_does_not_authorize_migration(room, tmp_path):
    from pantheon.apps.host_lifecycle import AppShutdownError
    instance, settings, _ = room
    instance.memory_manager.flush = AsyncMock(side_effect=OSError('simulated save failure'))
    with pytest.raises(AppShutdownError): await instance.cleanup()
    with pytest.raises(DataFencedError): fence([settings.user_home], tmp_path / 'target')


@pytest.mark.asyncio
async def test_missing_project_selection_does_not_create_directory(room, tmp_path):
    instance, _, _ = room
    missing = tmp_path / 'missing'
    assert not (await instance.set_active_project(str(missing)))['success']
    assert not (await instance.switch_project(str(missing)))['success']
    assert not missing.exists()


def test_interrupted_marker_creation_is_resumable_without_releasing_sources(tmp_path, monkeypatch):
    import pantheon.chatroom.data_fence as module
    original = module._write_marker
    roots = [tmp_path / 'a', tmp_path / 'b']
    def fail_second(root, value):
        if root == roots[1]: raise OSError('simulated disk error')
        original(root, value)
    monkeypatch.setattr(module, '_write_marker', fail_second)
    with pytest.raises(OSError): fence(roots, tmp_path / 'target')
    with pytest.raises(DataFencedError): LegacyDataLease().acquire(roots[0])
    monkeypatch.setattr(module, '_write_marker', original)
    with fence(roots, tmp_path / 'target') as migration:
        assert all(json.loads((root / MARKER_NAME).read_text()) == migration.identity for root in roots)
        migration.release_sources()


def test_concurrent_first_store_leases_do_not_leak_shared_locks(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root = tmp_path / 'source'
    writer = LegacyDataLease()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer.acquire, [root] * 40))
    writer.close()
    with fence([root], tmp_path / 'target'):
        pass


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-death acceptance')
@pytest.mark.parametrize('phase', ['partial-write', 'staged', 'published'])
def test_process_death_during_marker_publication_can_resume(tmp_path, phase):
    root, target = tmp_path/'source', tmp_path/'target'
    code = '''
import os, sys
sys.path.insert(0, sys.argv[1])
import pantheon.chatroom.data_fence as module
if sys.argv[4] == 'partial-write':
    def interrupted(value, stream, **kwargs):
        stream.write('{"protocol":')
        stream.flush()
        os.fsync(stream.fileno())
        os._exit(73)
    module.json.dump = interrupted
elif sys.argv[4] == 'staged':
    def interrupted(*args):
        os._exit(73)
    module.os.replace = interrupted
else:
    original = module._sync_directory
    def interrupted(root):
        original(root)
        os._exit(73)
    module._sync_directory = interrupted
module.MigrationFence([sys.argv[2]], operation='move-1', target=sys.argv[3], namespace='agent-1')
raise AssertionError('fault did not execute')
'''
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parents[1]),
                             str(root), str(target), phase], capture_output=True, text=True, timeout=20)
    assert result.returncode == 73, result.stderr
    if phase == 'published':
        with pytest.raises(DataFencedError): LegacyDataLease().acquire(root)
    else:
        # No ownership was published; no backup/import has begun. Updated
        # legacy writers can still access this previously unfenced root.
        writer = LegacyDataLease(); writer.acquire(root); writer.close()
    with fence([root], target) as migration:
        assert json.loads((root/MARKER_NAME).read_text()) == migration.identity
        with pytest.raises(DataFencedError): LegacyDataLease().acquire(root)
        migration.release_sources()
    writer = LegacyDataLease(); writer.acquire(root); writer.close()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory', 'fifo', 'public'])
def test_pending_marker_is_not_allowed_to_replace_foreign_files(tmp_path, kind):
    from pantheon.utils.local_data_ownership import MARKER_PARTIAL
    root = tmp_path/'source'; root.mkdir()
    outside = tmp_path/'untouched'; outside.write_text('unchanged')
    pending = root/MARKER_PARTIAL
    if kind == 'symlink': pending.symlink_to(outside)
    elif kind == 'hardlink': os.link(outside, pending)
    elif kind == 'directory': pending.mkdir()
    elif kind == 'fifo':
        if os.name == 'nt': pytest.skip('POSIX FIFO')
        os.mkfifo(pending)
    else:
        if os.name == 'nt': pytest.skip('POSIX file privacy')
        pending.write_text('partial'); pending.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        fence([root], tmp_path/'target')
    assert outside.read_text() == 'unchanged'
    assert not (root/MARKER_NAME).exists()
    assert pending.exists()


def test_failed_staging_does_not_replace_published_ownership(tmp_path):
    from pantheon.chatroom.data_fence import _write_marker
    from pantheon.utils.local_data_ownership import MARKER_PARTIAL
    root = tmp_path/'source'
    with fence([root], tmp_path/'target') as migration:
        before = (root/MARKER_NAME).read_bytes()
        with pytest.raises(DataFencedError):
            _write_marker(root, {**migration.identity, 'operation': 'other'})
        assert (root/MARKER_NAME).read_bytes() == before
        assert not (root/MARKER_PARTIAL).exists()
