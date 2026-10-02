"""Platform project ownership, legacy routing, and safe shared-registry writes."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pantheon.platform.projects import ProjectManager
from pantheon.platform.service import PlatformService


def test_registry_alias_preserves_legacy_imports():
    from pantheon.chatroom import projects as old
    from pantheon.platform import projects as new
    assert old is new


def test_platform_project_selection_is_independent_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    workspace = tmp_path / 'workspace'
    project = workspace / 'project'
    project.mkdir(parents=True)
    asset = project / 'user-data.txt'
    asset.write_text('keep')
    cwd = Path.cwd()

    async def check():
        platform = PlatformService(workspace_path=str(workspace))
        assert platform._project_manager is None
        await platform.platform_info()
        assert platform._project_manager is None
        assert (await platform.register_project(str(project), 'My project'))['success']
        result = await platform.set_active_project(str(project))
        assert result['project']['name'] == 'My project'
        assert Path.cwd() == cwd
        assert not (project / '.pantheon').exists()
        restarted = PlatformService(workspace_path=str(workspace))
        active = await restarted.get_active_project()
        assert active['active']['path'] == str(project)
        assert active['home']['path'] == str(workspace)
        assert (await restarted.remove_project(str(project)))['success']
        assert asset.read_text() == 'keep'
        assert (await platform.get_active_project())['active']['path'] == str(workspace)
    asyncio.run(check())


def test_legacy_agent_selection_still_updates_its_memory(tmp_path, monkeypatch):
    from pantheon.chatroom.room import ChatRoom
    from pantheon.chatroom.routed_memory import project_memory_dir
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    project = tmp_path / 'project'
    project.mkdir()
    # Avoid starting agents or background tasks; exercise the actual override.
    room = object.__new__(ChatRoom)
    room.project_manager = ProjectManager()
    room.memory_manager = SimpleNamespace(set_active_dir=Mock(), set_search_dirs=Mock())
    result = asyncio.run(room.set_active_project(str(project)))
    assert result['success']
    room.memory_manager.set_active_dir.assert_called_once_with(project_memory_dir(str(project)))
    room.memory_manager.set_search_dirs.assert_called_once_with([project_memory_dir(str(project))])
    result = asyncio.run(room.set_active_project(str(project / 'missing')))
    assert not result['success']
    assert room.memory_manager.set_active_dir.call_count == 1


def test_long_lived_registry_instances_observe_other_writers(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    first, second = ProjectManager(), ProjectManager()
    first.register(str(tmp_path / 'a'))
    second.register(str(tmp_path / 'b'))
    assert len(first.list_projects()) == len(second.list_projects()) == 2
    first.remove(str(tmp_path / 'a'))
    second.register(str(tmp_path / 'c'))
    assert {p['name'] for p in first.list_projects()} == {'b', 'c'}


def test_slow_registry_does_not_block_platform_rpc_loop():
    import threading
    started, release = threading.Event(), threading.Event()
    def slow_list():
        started.set()
        assert release.wait(5), 'registry I/O blocked the RPC event loop'
        return []

    async def check():
        platform = PlatformService()
        platform._project_manager = SimpleNamespace(list_projects=slow_list)
        pending = asyncio.create_task(platform.list_projects())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not pending.done()
            assert (await asyncio.wait_for(platform.platform_info(), .5))['api_version'] == 1
        finally:
            release.set()
            assert await pending == {'projects': []}
    asyncio.run(check())


def test_corrupt_registry_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    manager = ProjectManager()
    manager.register(str(tmp_path / 'a'))
    manager._registry_path.write_text('partial invalid data')
    with pytest.raises(ValueError, match='unreadable project registry'):
        manager.register(str(tmp_path / 'b'))
    assert manager._registry_path.read_text() == 'partial invalid data'


def test_atomic_publish_failure_preserves_previous_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    manager = ProjectManager()
    manager.register(str(tmp_path / 'a'))
    previous = manager._registry_path.read_bytes()
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='disk failure'):
        manager.register(str(tmp_path / 'b'))
    assert manager._registry_path.read_bytes() == previous
    assert not list(manager._registry_path.parent.glob('.projects-*.json'))


def test_concurrent_processes_do_not_lose_registrations(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(root))
    code = '''
from pathlib import Path
import sys, time
from pantheon.platform.projects import ProjectManager
manager = ProjectManager()
Path(sys.argv[1]).touch()
while not Path('go').exists():
    time.sleep(.01)
for i in range(12):
    manager.register(str(Path.cwd() / (sys.argv[1] + str(i))))
'''
    workers = [subprocess.Popen([sys.executable, '-c', code, f'w{i}'],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(4)]
    try:
        import time
        deadline = time.monotonic() + 10
        while not all((tmp_path / f'w{i}').exists() for i in range(4)):
            assert time.monotonic() < deadline
            assert all(p.poll() is None for p in workers)
            time.sleep(.01)
        (tmp_path / 'go').touch()
        for process in workers:
            out, err = process.communicate(timeout=15)
            assert process.returncode == 0, out + err
        data = json.loads((tmp_path / '.pantheon/projects.json').read_text())
        assert len(data['projects']) == 48
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
