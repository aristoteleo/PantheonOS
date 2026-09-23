import copy
import hashlib
import io
import json
import os
import threading
import time

import pytest

from test_model_engines import load

speech, artifacts = load('speech_models'), load('artifacts')


class Response(io.BytesIO):
    status = 200

    def __init__(self, value):
        super().__init__(value)
        self.headers = {'Content-Length': str(len(value))}


class Download:
    def __init__(self, files):
        self.files, self.calls = files, []
        self.fail = None

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        if self.fail == request.full_url:
            raise OSError('temporary transfer failure')
        return Response(self.files[request.full_url])


@pytest.fixture
def cache(tmp_path, monkeypatch):
    selected = copy.deepcopy(speech.model('kokoro-82m-v1'))
    data = {}
    for file in selected['files']:
        value = (file['name'] + ': fixture').encode()
        file.update(size=len(value), sha256=hashlib.sha256(value).hexdigest())
        data[file['url']] = value
    monkeypatch.setattr(speech, 'catalog', lambda: [selected])
    transport = Download(data)
    blobs = artifacts.ArtifactCache(tmp_path / 'blobs', opener=transport)
    return speech.SpeechModelCache(tmp_path, artifacts, blob_cache=blobs), selected, transport


def test_pinned_catalog_has_exact_sources_and_bounded_cpu_budgets():
    for item in speech.catalog():
        selected = speech.model(item['id'])
        assert selected['operation'] in {'speech', 'transcription'}
        assert selected['minimum_memory_bytes'] >= 2 << 30
        for file in selected['files']:
            assert artifacts.validate_source(file) == file
        assert artifacts.validate_source(speech.source(selected)) == speech.source(selected)


def test_prepare_publishes_offline_cache_once_and_survives_new_manager(cache):
    manager, selected, transport = cache
    states = []
    descriptor = speech.source(selected)
    target = manager.fetch(descriptor, threading.Event(), lambda *state: states.append(state))
    assert [state for state, _ in states].count('ready') == 1
    assert states[-1] == ('ready', descriptor['size'])
    assert len(transport.calls) == len(selected['files'])
    record = speech.prepared(manager.root, selected['id'])
    assert record['source'] == descriptor
    repo = target / 'hub' / ('models--' + selected['model'].replace('/', '--'))
    assert (repo / 'refs/main').read_text() == selected['revision']
    for file in selected['files']:
        assert (repo / 'snapshots' / selected['revision'] / file['name']).read_bytes() == transport.files[file['url']]
    # A new manager has no network fixture; reuse must need no HTTP or model SDK.
    other = speech.SpeechModelCache(manager.root, artifacts)
    assert other.fetch(descriptor, threading.Event(), lambda *state: None) == target


def test_failed_download_retains_verified_files_and_resumes_same_job(cache, tmp_path):
    manager, selected, transport = cache
    transport.fail = selected['files'][1]['url']
    jobs = artifacts.DownloadJobs(tmp_path / 'jobs', manager)
    def settled():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with jobs.mutex:
                if not jobs.workers:
                    return jobs.list()[0]
            time.sleep(.01)
        pytest.fail('preparation worker did not settle')
    try:
        jobs.submit('speech-test', speech.source(selected))
        assert settled()['state'] == 'failed'
        assert speech.prepared(manager.root, selected['id']) is None
        transport.fail = None
        jobs.submit('speech-test', resume=True)
        row = settled()
        assert row['state'] == 'ready'
        assert row['bytes_done'] == sum(file['size'] for file in selected['files'])
        assert transport.calls.count(selected['files'][0]['url']) == 1
    finally:
        jobs.close()


def test_cancel_does_not_publish_partial_snapshot(cache):
    manager, selected, transport = cache
    cancel = threading.Event()
    def progress(state, done):
        if state == 'installing':
            cancel.set()
    with pytest.raises(artifacts.Cancelled):
        manager.fetch(speech.source(selected), cancel, progress)
    assert speech.prepared(manager.root, selected['id']) is None
    calls = len(transport.calls)
    manager.fetch(speech.source(selected), threading.Event(), lambda *state: None)
    assert len(transport.calls) == calls


@pytest.mark.parametrize('field,value', [('url', 'https://example.invalid/other'), ('sha256', 'a' * 64),
                                       ('revision', 'main'), ('size', 1)])
def test_job_cannot_override_pins(cache, field, value):
    manager, selected, transport = cache
    with pytest.raises(ValueError, match='pinned manifest'):
        manager.fetch({**speech.source(selected), field: value}, threading.Event(), lambda *state: None)
    assert transport.calls == []


@pytest.mark.parametrize('change', ['weights', 'manifest', 'revision', 'directory-link'])
def test_prepared_cache_rejects_mutation_without_silent_redownload(cache, change):
    manager, selected, transport = cache
    target = manager.fetch(speech.source(selected), threading.Event(), lambda *state: None)
    calls = len(transport.calls)
    repo = target / 'hub' / ('models--' + selected['model'].replace('/', '--'))
    snapshot = repo / 'snapshots' / selected['revision']
    if change == 'weights':
        path = snapshot / 'model.onnx'
        before = path.stat()
        path.chmod(0o600)
        path.write_bytes(b'x' * before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == 'manifest':
        path = target / 'snapshot.json'
        value = json.loads(path.read_text())
        value['files'] = []
        path.write_text(json.dumps(value))
    elif change == 'revision':
        path = repo / 'refs/main'
        path.chmod(0o600)
        path.write_text('b' * 40)
    else:
        moved = target / 'moved'
        snapshot.rename(moved)
        snapshot.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError):
        manager.fetch(speech.source(selected), threading.Event(), lambda *state: None)
    assert len(transport.calls) == calls


def test_preparation_rpc_requires_owner_and_reports_durable_jobs(cache, tmp_path, monkeypatch):
    import httpx
    from test_model_services import connector_module, serve
    manager, selected, transport = cache
    monkeypatch.setenv('PANTHEON_APP_RPC_TOKEN', 'test-speech-owner')
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(manager.root))
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-speech-test')
    connector = connector_module.Connector(tmp_path / 'connector')
    connector._modules['speech_models'] = speech
    # Keep the real durable job store, substitute only its HTTP transport.
    connector.downloads().cache.opener = transport
    try:
        with serve(connector_module.handler(connector)) as endpoint, httpx.Client() as client:
            body = dict(method='speech_models', args=dict(action='prepare', model_id=selected['id']))
            assert client.post(endpoint + '/rpc', json=body).status_code == 403
            assert transport.calls == []
            headers = {'X-Fleet-RPC-Token': connector.rpc_token}
            response = client.post(endpoint + '/rpc', json=body, headers=headers)
            assert response.status_code == 200
            assert response.json() == {'job_id': selected['id']}
            deadline = time.monotonic() + 5
            while connector._speech_models.workers and time.monotonic() < deadline:
                time.sleep(.01)
            status = client.post(endpoint + '/rpc', headers=headers, json=dict(method='speech_models',
                args=dict(action='status', model_id=selected['id'])))
            assert status.json()['ready'] is True
            assert 'hub' not in status.text and str(tmp_path) not in status.text
            connector.speech_models('forget', selected['id'])
            assert connector.speech_models('jobs')['jobs'] == []
            assert connector.speech_models('status', selected['id'])['ready'] is True
    finally:
        if connector._speech_models:
            connector._speech_models.close()
        connector.downloads().close()


def test_speech_manager_uses_owned_binding_and_rejects_stopped_services():
    import asyncio
    from pantheon.models.manager import ModelServiceManager
    row = dict(engine='speaches', state='draft', binding={'instance_id': 'owned-connector'})
    class Client:
        async def deployment(self, ident):
            assert ident == 'speech-test'
            return row
    manager = ModelServiceManager(client=Client())
    calls = []
    async def rpc(binding, method, args):
        calls.append((binding, method, args))
        return {'models': []}
    manager.rpc = rpc
    assert asyncio.run(manager.speech_models('speech-test')) == {'models': []}
    assert calls[0][0] == row['binding'] and calls[0][1] == 'speech_models'
    for state in ('stopped', 'stopping', 'recovering'):
        row['state'] = state
        with pytest.raises(ValueError, match='Start a Speaches'):
            asyncio.run(manager.speech_models('speech-test', 'prepare', 'kokoro-82m-v1'))
    assert len(calls) == 1
