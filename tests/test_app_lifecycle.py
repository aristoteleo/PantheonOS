import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.lifecycle import build_artifact, FleetLifecycle, CHUNK_SIZE


@pytest.mark.asyncio
async def test_start_fence_preserves_original_request_and_rejects_partial_identity(monkeypatch):
    service = FleetLifecycle(None)
    rpc = AsyncMock(return_value={'operation': {'state': 'cancelled'}})
    monkeypatch.setattr(service, '_request', rpc)
    request = dict(protocol=1, action='start', operation_id='lost-start', scope='model-group',
                   digest='a' * 64, generation=1, start_preparation_id='prepared-start')
    assert await service.fence_start('node', request) == {'state': 'cancelled'}
    rpc.assert_awaited_once_with('node', 'fence_start', request=request)
    for invalid in ({}, {**request, 'action': 'stop'}, {**request, 'start_preparation_id': ''},
                    {**request, 'generation_override': 2}):
        with pytest.raises(ValueError):
            await service.fence_start('node', invalid)


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
    client = SimpleNamespace(lifecycle=AsyncMock(side_effect=[{'installations': {}}, {'offset': CHUNK_SIZE}, {'error': 'node disconnected'}]))
    service = FleetLifecycle(None)
    monkeypatch.setattr(service, '_client', AsyncMock(return_value=client))
    with pytest.raises(RuntimeError, match='node disconnected'):
        await service.stage('target-node', tmp_path)
    assert client.lifecycle.await_count == 3
    assert client.lifecycle.await_args_list[1].args == ('target-node', 'stage')
    assert client.lifecycle.await_args_list[2].kwargs['offset'] == CHUNK_SIZE


@pytest.mark.asyncio
async def test_operation_identity_and_generation_are_preserved(monkeypatch):
    service = FleetLifecycle(None)
    request = AsyncMock(return_value={'operation': {'state': 'queued'}})
    monkeypatch.setattr(service, '_request', request)
    assert await service.submit('chosen-node', 'stop', 'a' * 64, generation=9, scope='window-1', operation_id='retry-same-op') == {'state': 'queued'}
    assert request.await_args.args == ('chosen-node', 'submit')
    assert request.await_args.kwargs['request'] == {'protocol': 1, 'operation_id': 'retry-same-op', 'action': 'stop', 'digest': 'a' * 64, 'scope': 'window-1', 'generation': 9}


@pytest.mark.asyncio
async def test_prepared_start_requires_durable_caller_identity(monkeypatch):
    service = FleetLifecycle(None)
    request = AsyncMock(return_value={'operation': {'state': 'queued'}})
    monkeypatch.setattr(service, '_request', request)
    with pytest.raises(ValueError, match='stable operation_id'):
        await service.submit('node', 'prepare_start', 'a' * 64)
    with pytest.raises(ValueError, match='exact preparation'):
        await service.submit('node', 'stop', 'a' * 64, start_preparation_id='prepared')
    request.assert_not_awaited()
    await service.submit('node', 'prepare_start', 'a' * 64, operation_id='prepared', scope='group', generation=2)
    assert request.await_args.kwargs['request'] == dict(protocol=1, operation_id='prepared', action='prepare_start', digest='a' * 64, scope='group', generation=2)
    await service.submit('node', 'start', 'a' * 64, operation_id='commit', scope='group', generation=3, start_preparation_id='prepared')
    assert request.await_args.kwargs['request'] == dict(protocol=1, operation_id='commit', action='start', digest='a' * 64, scope='group', generation=3, start_preparation_id='prepared')


def test_platform_artifact_matches_target_without_changing_source(tmp_path):
    package(tmp_path)
    manifest = {'id':'example','version':'1.0.0','execution':{'platform_manifests':{'windows-amd64':'fleet.windows-amd64.json','darwin-arm64':'fleet.darwin-arm64.json'}}}
    (tmp_path/'app.json').write_text(json.dumps(manifest))
    for target in ('windows-amd64','darwin-arm64'):
        os, arch = target.split('-')
        (tmp_path/f'fleet.{target}.json').write_text(json.dumps({'protocol':1,'app_id':'example','version':'1.0.0','requires':{'os':[os],'arch':[arch]}}))
    original=(tmp_path/'fleet.json').read_bytes()
    payload, digest=build_artifact(tmp_path,'windows-amd64')
    assert (tmp_path/'fleet.json').read_bytes()==original
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        assert json.load(archive.extractfile('fleet.json'))['requires']=={'os':['windows'],'arch':['amd64']}
    assert build_artifact(tmp_path,'windows-amd64')==(payload,digest)
    assert build_artifact(tmp_path,'darwin-arm64')[1]!=digest
    with pytest.raises(ValueError,match='no native package'):
        build_artifact(tmp_path,'linux-arm64')
    manifest['execution']['platform_manifests']['windows-amd64']='../outside.json'
    (tmp_path/'app.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='no native package'):
        build_artifact(tmp_path,'windows-amd64')


@pytest.mark.asyncio
async def test_installed_immutable_artifact_is_not_uploaded_again(tmp_path, monkeypatch):
    package(tmp_path)
    _, digest = build_artifact(tmp_path)
    client = SimpleNamespace(lifecycle=AsyncMock(return_value={'installations': {digest: {'state': 'installed'}}}))
    service = FleetLifecycle(None)
    monkeypatch.setattr(service, '_client', AsyncMock(return_value=client))
    assert await service.stage('target-node', tmp_path) == digest
    client.lifecycle.assert_awaited_once_with('target-node', 'status')


@pytest.mark.asyncio
async def test_snapshot_reuses_digest_but_validates_installation_on_each_node(tmp_path, monkeypatch):
    package(tmp_path)
    _, digest = build_artifact(tmp_path)
    client = SimpleNamespace(lifecycle=AsyncMock(return_value={'installations': {digest: {'state': 'installed'}}}))
    resolver = SimpleNamespace()
    builds = []
    def build(*args):
        builds.append(args)
        return build_artifact(*args)
    monkeypatch.setattr('pantheon.apps.lifecycle.build_artifact', build)
    for node in ['node-a', 'node-a', 'node-b']:
        service = FleetLifecycle(resolver)
        monkeypatch.setattr(service, '_client', AsyncMock(return_value=client))
        assert await service.stage(node, tmp_path, immutable_revision='a'*40) == digest
    assert len(builds) == 1
    assert client.lifecycle.await_count == 3  # never reuse node readiness
    client.lifecycle.return_value = {'installations': {}}
    await service.stage('node-b', tmp_path, immutable_revision='a'*40)
    assert len(builds) == 2  # uninstalled/new node still receives the artifact
    assert client.lifecycle.await_args.args == ('node-b', 'stage')
    (tmp_path / 'server.py').write_text('changed')
    changed = await service.stage('node-b', tmp_path)  # mutable sources bypass cache
    assert changed != digest


@pytest.mark.asyncio
async def test_snapshot_cache_separates_revision_platform_and_workspace(tmp_path, monkeypatch):
    package(tmp_path)
    client = SimpleNamespace(lifecycle=AsyncMock(return_value={'installations': {}}))
    resolver = SimpleNamespace(_node='workspace', _workdir='/data')
    service = FleetLifecycle(resolver)
    monkeypatch.setattr(service, '_client', AsyncMock(return_value=client))
    for node, revision, platform in [('workspace','a'*40,'linux-amd64'), ('mac','a'*40,'darwin-arm64'), ('workspace','b'*40,'linux-amd64')]:
        service._platforms[node] = platform
        await service.stage(node, tmp_path, immutable_revision=revision)
    assert len(resolver._staged_app_digests) == 3

@pytest.mark.asyncio
async def test_usage_cannot_switch_node_or_generation(monkeypatch):
    service = FleetLifecycle(None)
    request = AsyncMock(return_value={'ok': True})
    monkeypatch.setattr(service, '_request', request)
    await service.usage('mac', 'lease', instance_id='one', revision='a'*64,
                        generation=3, lease_id='window-a', release=True)
    request.assert_awaited_once_with('mac', 'lease', instance_id='one', revision='a'*64,
        generation=3, lease_id='window-a', release=True, keep_alive=False)
    with pytest.raises(ValueError, match='Unsupported'):
        await service.usage('mac', 'stop', instance_id='one', revision='a'*64, generation=3)
