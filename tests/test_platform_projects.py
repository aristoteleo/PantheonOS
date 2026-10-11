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

from pantheon.chatroom.projects import ProjectManager


def test_registry_alias_preserves_legacy_imports():
    from pantheon.chatroom import projects as old
    from pantheon.chatroom import projects as new
    assert old is new


def test_startup_preserves_custom_home_name(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    project = tmp_path / 'workspace'
    project.mkdir()
    first = ProjectManager(str(project))
    first.register(str(project), name='Research')
    restarted = ProjectManager(str(project), activate_on_start=False)
    assert restarted.default_project.name == 'Research'


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
    from pantheon.chatroom.data_fence import LegacyDataLease
    room._legacy_data_lease = LegacyDataLease()
    try:
        result = asyncio.run(room.set_active_project(str(project)))
        assert result['success']
        room.memory_manager.set_active_dir.assert_called_once_with(project_memory_dir(str(project)))
        room.memory_manager.set_search_dirs.assert_called_once_with([project_memory_dir(str(project))])
        result = asyncio.run(room.set_active_project(str(project / 'missing')))
        assert not result['success']
        assert room.memory_manager.set_active_dir.call_count == 1
    finally:
        room._legacy_data_lease.close()


def test_long_lived_registry_instances_observe_other_writers(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    first, second = ProjectManager(), ProjectManager()
    first.register(str(tmp_path / 'a'))
    second.register(str(tmp_path / 'b'))
    assert len(first.list_projects()) == len(second.list_projects()) == 2
    first.remove(str(tmp_path / 'a'))
    second.register(str(tmp_path / 'c'))
    assert {p['name'] for p in first.list_projects()} == {'b', 'c'}


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
from pantheon.chatroom.projects import ProjectManager
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


def test_legacy_snapshot_persists_ids_and_preserves_external_paths(tmp_path, monkeypatch):
    from pantheon.chatroom.app_data import AppProjects
    monkeypatch.setenv('HOME', str(tmp_path))
    root = tmp_path / '.pantheon'
    root.mkdir()
    path = root / 'projects.json'
    project = tmp_path / 'external-project'
    project.mkdir()
    (project / 'asset.txt').write_text('unchanged')
    path.write_text(json.dumps({'active': str(project), 'projects': [
        {'path': str(project), 'name': 'Research', 'created_at': 'then', 'last_accessed': 'now'}]}))
    manager = ProjectManager()
    before = path.read_bytes()
    assert 'id' not in manager.list_projects()[0]
    assert path.read_bytes() == before  # Ordinary legacy reads do not migrate.
    snapshot = manager.snapshot()
    project_id = snapshot['projects'][0]['id']
    assert json.loads(path.read_text())['projects'][0]['id'] == project_id
    assert snapshot == ProjectManager().snapshot()
    view = AppProjects(snapshot['projects'], active_id=snapshot['active_project'])
    assert view.active_project.id == project_id and view.active_project.path == str(project)
    manager.register(str(project), 'Renamed')
    assert ProjectManager().snapshot()['projects'][0]['id'] == project_id
    assert (project / 'asset.txt').read_text() == 'unchanged'
    assert not (project / '.pantheon').exists()
    # Relocating a registry entry with its ID retains identity across mounts.
    data = json.loads(path.read_text())
    data['projects'][0]['path'] = str(tmp_path / 'relocated')
    data['active'] = data['projects'][0]['path']
    path.write_text(json.dumps(data))
    assert ProjectManager().snapshot()['active_project'] == project_id


@pytest.mark.parametrize('kind', ['duplicate-id', 'invalid-id', 'duplicate-name'])
def test_invalid_identity_snapshot_never_overwrites_registry(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('HOME', str(tmp_path))
    manager = ProjectManager()
    manager.register(str(tmp_path / 'one'), 'One')
    manager.register(str(tmp_path / 'two'), 'Two')
    path = manager._registry_path
    value = json.loads(path.read_text())
    if kind == 'duplicate-id': value['projects'][1]['id'] = value['projects'][0]['id']
    elif kind == 'invalid-id': value['projects'][0]['id'] = ''
    else: value['projects'][1]['name'] = 'One'
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError): manager.snapshot()
    assert path.read_bytes() == before


def test_competing_legacy_snapshot_exports_publish_one_project_identity(tmp_path):
    registry = tmp_path / '.pantheon/projects.json'
    registry.parent.mkdir()
    registry.write_text(json.dumps({'projects': [{'path': str(tmp_path / 'project'), 'name': 'Research'}]}))
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(root))
    code = '''
import json
from pantheon.chatroom.projects import ProjectManager
print(json.dumps(ProjectManager().snapshot()))
'''
    workers = [subprocess.Popen([sys.executable, '-c', code], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    try:
        outputs = []
        for process in workers:
            out, err = process.communicate(timeout=10)
            assert process.returncode == 0, err
            outputs.append(json.loads(out))
        assert all(value == outputs[0] for value in outputs)
        assert json.loads(registry.read_text())['projects'][0]['id'] == outputs[0]['projects'][0]['id']
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill(); process.communicate(timeout=5)
