import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.lifecycle import build_artifact, FleetLifecycle, CHUNK_SIZE


def package(tmp_path):
    (tmp_path / 'app.json').write_text(json.dumps({'id': 'example', 'version': '1.0.0'}))
    (tmp_path / 'fleet.json').write_text(json.dumps({'protocol': 1, 'app_id': 'example', 'version': '1.0.0'}))
    (tmp_path / 'server.py').write_text('print("ready")\n')
    return tmp_path


def test_artifact_is_reproducible_and_never_ships_private_environment(tmp_path):
    package(tmp_path)
    for name in ['.env', '.env.local', '.env.production']:
        (tmp_path / name).write_text('PRIVATE=never-upload')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git/config').write_text('private remote')
    first, sha = build_artifact(tmp_path)
    (tmp_path / 'server.py').touch()
    assert build_artifact(tmp_path) == (first, sha)
    with tarfile.open(fileobj=io.BytesIO(first)) as archive:
        assert set(archive.getnames()) == {'app.json', 'fleet.json', 'server.py'}
    assert b'never-upload' not in first


def test_artifact_rejects_identity_mismatch_and_symlinks(tmp_path):
    package(tmp_path)
    (tmp_path / 'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symbolic links'):
        build_artifact(tmp_path)
    (tmp_path / 'link').unlink()
    (tmp_path / 'app.json').write_text(json.dumps({'id': 'other', 'version': '1.0.0'}))
    with pytest.raises(ValueError, match='identity'):
        build_artifact(tmp_path)


@pytest.mark.asyncio
async def test_stage_sends_bounded_chunks_and_stops_on_failure(tmp_path, monkeypatch):
    package(tmp_path)
    (tmp_path / 'large.py').write_text('x' * (CHUNK_SIZE * 3))
    client = SimpleNamespace(lifecycle=AsyncMock(side_effect=[{'offset': CHUNK_SIZE}, {'error': 'node disconnected'}]))
    service = FleetLifecycle(None)
    monkeypatch.setattr(service, '_client', AsyncMock(return_value=client))
    with pytest.raises(RuntimeError, match='node disconnected'):
        await service.stage('target-node', tmp_path)
    assert client.lifecycle.await_count == 2
    assert client.lifecycle.await_args_list[0].args == ('target-node', 'stage')
    assert client.lifecycle.await_args_list[1].kwargs['offset'] == CHUNK_SIZE


@pytest.mark.asyncio
async def test_operation_identity_and_generation_are_preserved(monkeypatch):
    service = FleetLifecycle(None)
    request = AsyncMock(return_value={'operation': {'state': 'queued'}})
    monkeypatch.setattr(service, '_request', request)
    assert await service.submit('chosen-node', 'stop', 'a' * 64, generation=9, scope='window-1', operation_id='retry-same-op') == {'state': 'queued'}
    assert request.await_args.args == ('chosen-node', 'submit')
    assert request.await_args.kwargs['request'] == {'protocol': 1, 'operation_id': 'retry-same-op', 'action': 'stop', 'digest': 'a' * 64, 'scope': 'window-1', 'generation': 9}
