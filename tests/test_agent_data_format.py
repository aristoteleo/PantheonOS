"""A future format must fail before opening/migrating the instance database."""
import json
import os
import sqlite3

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.data_format import FORMAT_FILE


def open_data(root):
    return AgentAppData(root, namespace='test', projects=AppProjects([]))


def test_new_and_unmarked_existing_data_get_durable_format(tmp_path):
    root = tmp_path / 'data'
    data = open_data(root)
    identity = data.instances.reserve('chat', {'assistant': {'name': 'Assistant', 'instructions': '', 'model': None, 'icon': ''}})[0].instance_id
    data.close()
    marker = root / FORMAT_FILE
    expected = {'id': 'pantheon-agent', 'version': 1, 'namespace': 'test'}
    assert json.loads(marker.read_text()) == expected
    marker.unlink()  # Original extracted releases used this same unmarked layout.
    data = open_data(root)
    assert data.instances.reserve('chat', {'assistant': {'name': 'Assistant', 'instructions': '', 'model': None, 'icon': ''}})[0].instance_id == identity
    data.close()
    assert json.loads(marker.read_text()) == expected


@pytest.mark.parametrize('marker', [
    {'id': 'pantheon-agent', 'version': 2, 'namespace': 'test'},
    {'id': 'pantheon-agent', 'version': True, 'namespace': 'test'},
    {'id': 'pantheon-agent', 'version': 1, 'namespace': 'other'},
    {'id': 'other', 'version': 1, 'namespace': 'test'},
    {'id': 'pantheon-agent', 'version': 1, 'namespace': 'test', 'unknown': 1},
    'not json', 'x' * 4097,
])
def test_invalid_marker_leaves_database_unchanged(tmp_path, marker):
    root = tmp_path / 'data'
    open_data(root).close()
    path = root / FORMAT_FILE
    path.write_text(json.dumps(marker) if isinstance(marker, dict) else marker)
    database = root / 'instances' / 'instances.sqlite3'
    before = database.read_bytes()
    with pytest.raises(ValueError, match='format'):
        open_data(root)
    assert database.read_bytes() == before


def test_failed_database_admission_does_not_mark_legacy_data(tmp_path):
    root = tmp_path / 'data'
    open_data(root).close()
    (root / FORMAT_FILE).unlink()
    with sqlite3.connect(root / 'instances' / 'instances.sqlite3') as db:
        db.execute('PRAGMA user_version=999')
    with pytest.raises(ValueError, match='schema'):
        open_data(root)
    assert not (root / FORMAT_FILE).exists()


def test_marker_write_failure_releases_instance_writer(tmp_path, monkeypatch):
    from pantheon.chatroom import app_data
    root = tmp_path / 'data'
    original = app_data.stamp_format
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(app_data, 'stamp_format', fail)
    with pytest.raises(OSError): open_data(root)
    monkeypatch.setattr(app_data, 'stamp_format', original)
    open_data(root).close()


@pytest.mark.parametrize('kind', ['link', 'dangling', 'fifo'])
def test_nonregular_marker_refused_without_creating_database(tmp_path, kind):
    if os.name != 'posix': pytest.skip('POSIX admission')
    root = tmp_path / 'data'
    root.mkdir(mode=0o700)
    marker = root / FORMAT_FILE
    target = tmp_path / 'outside'
    if kind == 'fifo': os.mkfifo(marker)
    else:
        if kind == 'link': target.write_text('{}')
        marker.symlink_to(target)
    with pytest.raises(ValueError, match='format'): open_data(root)
    assert not (root / 'instances').exists()
