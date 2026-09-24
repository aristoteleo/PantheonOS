import copy
import hashlib
import json
import os
import threading
import time

import pytest

from test_model_engines import load
from test_model_speech_cache import Download

diffusion, artifacts = load('diffusion_models'), load('artifacts')


@pytest.fixture(params=['sdxl-turbo-71153311', 'wan2-1-t2v-1-3b-0fad780a'])
def cache(tmp_path, monkeypatch, request):
    selected = copy.deepcopy(diffusion.model(request.param))
    data = {}
    for file in selected['files']:
        value = (file['name'] + ': fixture').encode()
        file.update(size=len(value), sha256=hashlib.sha256(value).hexdigest())
        data[file['url']] = value
    monkeypatch.setattr(diffusion, 'catalog', lambda: [selected])
    transport = Download(data)
    blobs = artifacts.ArtifactCache(tmp_path / 'blobs', opener=transport)
    return diffusion.DiffusionModelCache(tmp_path, artifacts, blob_cache=blobs), selected, transport


def test_catalog_pins_accepted_model_without_pickle_or_repository_code():
    selected = diffusion.model('sdxl-turbo-71153311')
    assert selected['revision'] == '71153311d3dbb46851df1931d3ca6e939de83304'
    assert selected['model'] == 'stabilityai/sdxl-turbo'
    assert len(selected['files']) == 20
    assert sum(f['size'] for f in selected['files']) == 13878882605
    for file in selected['files']:
        assert artifacts.validate_source(file) == file
        assert not file['name'].endswith(('.py', '.bin', '.pt', '.pkl'))
    assert artifacts.validate_source(diffusion.source(selected)) == diffusion.source(selected)


def test_video_catalog_pins_complete_wan_weights_and_tokenizer():
    selected = diffusion.model('wan2-1-t2v-1-3b-0fad780a')
    assert selected['operation'] == 'video'
    assert selected['model'] == 'Wan-AI/Wan2.1-T2V-1.3B-Diffusers'
    assert selected['revision'] == '0fad780a534b6463e45facd96134c9f345acfa5b'
    assert selected['license'] == 'apache-2.0'
    assert len(selected['files']) == 20
    assert sum(f['size'] for f in selected['files']) == 28928905975
    names = {f['name'] for f in selected['files']}
    assert 'tokenizer/spiece.model' in names
    assert 'text_encoder/model.safetensors.index.json' in names
    assert 'transformer/diffusion_pytorch_model.safetensors.index.json' in names
    assert len([n for n in names if n.endswith('.safetensors')]) == 8
    for file in selected['files']:
        assert artifacts.validate_source(file) == file
    assert artifacts.validate_source(diffusion.source(selected)) == diffusion.source(selected)


@pytest.mark.parametrize('name', ['weights.model', 'tokenizer/other.model', 'TokenIzer/spiece.model'])
def test_video_does_not_allow_arbitrary_model_files(cache, name):
    manager, selected, transport = cache
    file = selected['files'][-1]
    file['name'] = name
    file['url'] = f"https://huggingface.co/{selected['model']}/resolve/{selected['revision']}/{name}"
    with pytest.raises(ValueError, match='filename'):
        manager.fetch(diffusion.source(selected), threading.Event(), lambda *args: None)
    assert transport.calls == []


def test_atomic_offline_snapshot_reused_without_network_after_manager_restart(cache):
    manager, selected, transport = cache
    states = []
    descriptor = diffusion.source(selected)
    target = manager.fetch(descriptor, threading.Event(), lambda *state: states.append(state))
    assert states[-1] == ('ready', descriptor['size'])
    assert sum(state == 'ready' for state, _ in states) == 1
    assert len(transport.calls) == len(selected['files'])
    record = diffusion.prepared(manager.root, selected['id'])
    assert record['source'] == descriptor
    snapshot = target / 'hub' / ('models--' + selected['model'].replace('/', '--')) / 'snapshots' / selected['revision']
    for file in selected['files']:
        assert (snapshot / file['name']).read_bytes() == transport.files[file['url']]
    other = diffusion.DiffusionModelCache(manager.root, artifacts)
    assert other.fetch(descriptor, threading.Event(), lambda *args: None) == target


def test_failed_preparation_resumes_and_keeps_already_verified_files(cache, tmp_path):
    manager, selected, transport = cache
    failed = selected['files'][4]['url']
    transport.fail = failed
    jobs = artifacts.DownloadJobs(tmp_path / 'jobs', manager)
    def settled():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with jobs.mutex:
                if not jobs.workers:
                    return jobs.list()[0]
            time.sleep(.01)
        pytest.fail('preparation did not settle')
    try:
        jobs.submit(selected['id'], diffusion.source(selected))
        assert settled()['state'] == 'failed'
        assert diffusion.prepared(manager.root, selected['id']) is None
        transport.fail = None
        jobs.submit(selected['id'], resume=True)
        assert settled()['state'] == 'ready'
        assert transport.calls.count(selected['files'][0]['url']) == 1
        assert transport.calls.count(failed) == 2
    finally:
        jobs.close()


def test_cancel_keeps_blob_cache_but_never_publishes_partial_model(cache):
    manager, selected, transport = cache
    cancel = threading.Event()
    def progress(state, done):
        if state == 'installing':
            cancel.set()
    with pytest.raises(artifacts.Cancelled):
        manager.fetch(diffusion.source(selected), cancel, progress)
    assert diffusion.prepared(manager.root, selected['id']) is None
    calls = len(transport.calls)
    manager.fetch(diffusion.source(selected), threading.Event(), lambda *args: None)
    assert len(transport.calls) == calls


@pytest.mark.parametrize('name', ['../escape.json', '/tmp/escape.json', 'a/../../escape.json',
    r'a\escape.json', 'a//file.json', 'a/CON.json', 'a./file.json', 'a/code.py',
    'a/model.bin', 'a/weights.pkl', 'a/file.json?query=1'])
def test_untrusted_nested_paths_and_code_rejected_before_download(cache, name):
    manager, selected, transport = cache
    selected['files'][-1]['name'] = name
    with pytest.raises(ValueError):
        manager.fetch(diffusion.source(selected), threading.Event(), lambda *args: None)
    assert transport.calls == []


@pytest.mark.parametrize('names', [('a/b.json', 'A/c.json'), ('A/b.json', 'a/c.json'),
                                  ('a.json', 'a.json/b.json'), ('a.json/b.json', 'a.json')])
def test_path_case_and_file_directory_collisions_rejected(cache, names):
    manager, selected, transport = cache
    for file, name in zip(selected['files'][-2:], names):
        file['name'] = name
        file['url'] = f"https://huggingface.co/{selected['model']}/resolve/{selected['revision']}/{name}"
    with pytest.raises(ValueError, match='conflict'):
        diffusion.model(selected['id'])
    assert transport.calls == []


@pytest.mark.parametrize('field,value', [('url', 'https://example.invalid/other'), ('sha256', 'f' * 64),
                                       ('revision', 'main'), ('size', 1)])
def test_callers_cannot_override_manifest(cache, field, value):
    manager, selected, transport = cache
    with pytest.raises(ValueError, match='pinned manifest'):
        manager.fetch({**diffusion.source(selected), field: value}, threading.Event(), lambda *args: None)
    assert transport.calls == []


@pytest.mark.parametrize('change', ['weights', 'manifest', 'directory-link'])
def test_warm_preparation_rejects_changed_files_and_directory_links(cache, change):
    manager, selected, transport = cache
    target = manager.fetch(diffusion.source(selected), threading.Event(), lambda *args: None)
    calls = len(transport.calls)
    snapshot = target / 'hub' / ('models--' + selected['model'].replace('/', '--')) / 'snapshots' / selected['revision']
    weight = next(f['name'] for f in selected['files'] if f['name'].endswith('.safetensors'))
    if change == 'weights':
        path = snapshot / weight
        before = path.stat()
        path.chmod(0o600)
        path.write_bytes(b'x' * before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == 'manifest':
        path = target / 'snapshot.json'
        value = json.loads(path.read_text())
        value['files'] = []
        path.write_text(json.dumps(value))
    else:
        folder = snapshot / weight.split('/')[0]
        folder.rename(target / 'moved')
        folder.symlink_to(target / 'moved', target_is_directory=True)
    with pytest.raises(ValueError):
        manager.fetch(diffusion.source(selected), threading.Event(), lambda *args: None)
    assert len(transport.calls) == calls


def test_owner_rpc_prepares_persistent_jobs_without_leaking_local_paths(cache, tmp_path, monkeypatch):
    import httpx
    from test_model_services import connector_module, serve
    manager, selected, transport = cache
    monkeypatch.setenv('PANTHEON_APP_RPC_TOKEN', 'test-diffusion-owner')
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(manager.root))
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-diffusion-test')
    connector = connector_module.Connector(tmp_path / 'connector')
    connector._modules['diffusion_models'] = diffusion
    connector.downloads().cache.opener = transport
    try:
        with serve(connector_module.handler(connector)) as endpoint, httpx.Client() as client:
            body = dict(method='diffusion_models', args=dict(action='prepare', model_id=selected['id']))
            assert client.post(endpoint + '/rpc', json=body).status_code == 403
            assert transport.calls == []
            headers = {'X-Fleet-RPC-Token': connector.rpc_token}
            response = client.post(endpoint + '/rpc', json=body, headers=headers)
            assert response.status_code == 200
            assert response.json() == {'job_id': selected['id']}
            deadline = time.monotonic() + 5
            while connector._diffusion_models.workers and time.monotonic() < deadline:
                time.sleep(.01)
            status = client.post(endpoint + '/rpc', headers=headers, json=dict(method='diffusion_models',
                args=dict(action='status', model_id=selected['id'])))
            assert status.json()['ready'] is True
            assert str(tmp_path) not in status.text
            assert connector.diffusion_models('jobs')['jobs'][0]['state'] == 'ready'
            assert connector.speech_models('jobs')['jobs'] == []
            connector.diffusion_models('forget', selected['id'])
            assert connector.diffusion_models('jobs')['jobs'] == []
            assert connector.diffusion_models('status', selected['id'])['ready'] is True
    finally:
        for jobs in (connector._diffusion_models, connector._speech_models, connector._downloads):
            if jobs:
                jobs.close()


def test_corrupt_weight_never_becomes_engine_visible(cache):
    manager, selected, transport = cache
    weight = next(f for f in selected['files'] if f['name'].endswith('.safetensors'))
    transport.files[weight['url']] = b'x' * weight['size']
    with pytest.raises(ValueError, match='checksum mismatch'):
        manager.fetch(diffusion.source(selected), threading.Event(), lambda *args: None)
    assert diffusion.prepared(manager.root, selected['id']) is None


def test_preparation_manager_resolves_owned_connector_and_validates_actions():
    import asyncio
    from pantheon.models.manager import ModelServiceManager
    row = dict(engine='sglang', state='draft', binding={'instance_id': 'owned-connector'})
    class Client:
        async def deployment(self, ident):
            assert ident == 'image-test'
            return row
    manager = ModelServiceManager(client=Client())
    calls = []
    async def rpc(binding, method, args):
        calls.append((binding, method, args))
        return {'models': []}
    manager.rpc = rpc
    assert asyncio.run(manager.diffusion_models('image-test')) == {'models': []}
    assert calls == [(row['binding'], 'diffusion_models', dict(action='catalog', model_id='', resume=False))]
    for state in ('stopped', 'stopping', 'recovering', 'unknown'):
        row['state'] = state
        with pytest.raises(ValueError, match='Start an SGLang'):
            asyncio.run(manager.diffusion_models('image-test', 'prepare', 'sdxl-turbo-71153311'))
    row['state'] = 'ready'
    row['engine'] = 'ollama'
    with pytest.raises(ValueError, match='SGLang'):
        asyncio.run(manager.diffusion_models('image-test'))
    with pytest.raises(ValueError, match='Unsupported'):
        asyncio.run(manager.diffusion_models('image-test', 'execute'))
    assert len(calls) == 1
