"""Binary transfers through the actual connector HTTP handler."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
import sqlite3

import httpx
import pytest

from test_model_services import connector_module, serve


def test_binary_upload_range_read_and_stale_configuration(tmp_path):
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'api', 'endpoint': 'https://provider.invalid/v1'})
    body = b'\x89PNG\x00\x01\x02' * 300
    digest = hashlib.sha256(body).hexdigest()
    declaration = dict(request_key='upload-one', kind='image', mime='image/png', size=len(body), sha256=digest)
    try:
        with serve(connector_module.handler(connector)) as url, httpx.Client(base_url=url) as client:
            assert client.post('/media/artifacts', json=declaration).status_code == 409
            assert connector._media_store is None
            client.headers['X-Model-Config'] = connector.revision
            created = client.post('/media/artifacts', json=declaration)
            assert created.status_code == 201
            artifact = created.json()['id']
            base = '/media/artifacts/' + artifact
            assert client.post('/media/artifacts', json=declaration).json()['id'] == artifact
            assert client.get(base + '/content', headers={'Range': 'bytes=0-99'}).status_code == 400
            uploaded = client.put(base, headers={'Upload-Offset': '0'}, content=body)
            assert uploaded.status_code == 200 and uploaded.json()['received'] == len(body)
            assert client.put(base, headers={'Upload-Offset': '0'}, content=body).json() == uploaded.json()
            completed = client.post(base + '/complete')
            assert completed.status_code == 200 and completed.json()['sha256'] == digest
            read = client.get(base + '/content', headers={'Range': 'bytes=3-102'})
            assert read.status_code == 206 and read.content == body[3:103]
            assert read.headers['Content-Range'] == f'bytes 3-102/{len(body)}'
            assert read.headers['ETag'] == '"' + digest + '"'
            assert read.headers['X-Content-Type-Options'] == 'nosniff'
            assert client.get(base + '/content').status_code == 400
            assert client.get(base + '/content', headers={'Range': 'bytes=0-1048576'}).status_code == 400
            assert client.get(base + '/content', headers={'Range': f'bytes={len(body)}-{len(body)+5}'}).status_code == 416
            # A restarted/reconfigured service cannot use a stale model grant header.
            assert client.get(base, headers={'X-Model-Config': 'stale'}).status_code == 409
            assert client.post('/media/artifacts', json={**declaration, 'purpose': 'output'}).status_code == 400
            assert client.post('/media/artifacts', json={**declaration, 'path': '/tmp/private'}).status_code == 400
            assert client.delete(base).status_code == 200
            assert client.get(base).status_code == 400
            assert connector.media_transfers == 0 and not connector.calls
    finally:
        if connector._media_store:
            connector._media_store.close()


def test_stop_waits_for_transfer_and_rejects_new_uploads(tmp_path, monkeypatch):
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'api', 'endpoint': 'https://provider.invalid/v1'})
    entered, release = threading.Event(), threading.Event()
    store = connector.media_store()
    original = store.append
    def held(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(store, 'append', held)
    try:
        with serve(connector_module.handler(connector)) as url:
            headers = {'X-Model-Config': connector.revision}
            with httpx.Client(base_url=url, headers=headers) as client, ThreadPoolExecutor() as pool:
                declaration = dict(request_key='transfer', kind='audio', mime='audio/wav', size=4)
                artifact = client.post('/media/artifacts', json=declaration).json()['id']
                future = pool.submit(client.put, '/media/artifacts/' + artifact,
                                     headers={'Upload-Offset': '0'}, content=b'abcd')
                try:
                    assert entered.wait(3)
                    assert connector.media_transfers == 1
                    with pytest.raises(ValueError, match='active requests'):
                        connector.configure({'engine': 'api', 'endpoint': 'https://another.invalid/v1'})
                    assert connector.drain()['safe_to_stop'] is False
                    assert client.post('/media/artifacts', json=declaration).status_code == 409
                finally:
                    release.set()
                assert future.result().status_code == 200
                assert connector.drain()['safe_to_stop'] is True
                assert connector.media_transfers == 0 and not connector.accepting
    finally:
        release.set()
        store.close()


def test_storage_errors_are_bounded_and_release_transfer_admission(tmp_path, monkeypatch):
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'api', 'endpoint': 'https://provider.invalid/v1'})
    def unavailable():
        raise sqlite3.OperationalError('/private/node/data/ledger: database is locked')
    monkeypatch.setattr(connector, 'media_store', unavailable)
    with serve(connector_module.handler(connector)) as url, httpx.Client(base_url=url) as client:
        result = client.get('/media/artifacts/' + 'a' * 32, headers={'X-Model-Config': connector.revision})
        assert result.status_code == 503 and '/private' not in result.text
        assert connector.media_transfers == 0
        assert connector.drain()['safe_to_stop']
