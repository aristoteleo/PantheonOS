"""Binary transcription ownership and HTTP protocol, with a local engine fixture."""
import hashlib
import io
import json
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from pantheon.models.jobs import InferenceSession
from test_model_services import connector_module, serve
from test_model_inference_jobs import close, wait_done
from test_model_speech_jobs import audio


def upload(store, data, *, kind='audio', mime='audio/wav'):
    a = store.create(request_key='input', kind=kind, mime=mime, size=len(data),
                     sha256=hashlib.sha256(data).hexdigest(), purpose='input')
    for offset in range(0, len(data), 1024 * 1024):
        store.append(a['id'], offset, data[offset:offset + 1024 * 1024])
    return store.seal(a['id'])


def request(artifact, **overrides):
    return {'job_id': 'transcribe', 'model': 'whisper', 'operation': 'transcription',
            'input': {'audio': artifact}, 'parameters': {'language': 'en'}, **overrides}


def engine(calls, entered=None, release=None, result=None):
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/v1/models'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'whisper'}]}).encode())
        def do_POST(self):
            assert self.path == '/v1/audio/transcriptions'
            assert not self.headers.get('Transfer-Encoding')
            body = self.rfile.read(int(self.headers['Content-Length']))
            parts = BytesParser(policy=default).parsebytes(
                ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + body)
            fields = {p.get_param('name', header='Content-Disposition'): p.get_payload(decode=True)
                      for p in parts.iter_parts()}
            calls.append(fields)
            if entered: entered.set()
            if release: release.wait(5)
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(json.dumps(result if result is not None else
                    {'text': 'Hello from the Fleet node.', 'private_path': '/do-not-keep'}).encode())
            except OSError:
                pass
    return Engine


@pytest.mark.asyncio
async def test_sdk_transcription_leases_binary_input_and_retains_only_text(tmp_path):
    calls, entered, release = [], threading.Event(), threading.Event()
    c = connector_module.Connector(tmp_path)
    data = audio()
    try:
        with serve(engine(calls, entered, release)) as endpoint:
            c.configure({'engine': 'speaches', 'endpoint': endpoint})
            a = upload(c.media_store(), data)
            with serve(connector_module.handler(c)) as url:
                async with httpx.AsyncClient() as http:
                    session = InferenceSession(http, {'deployment_id': 'voice', 'config_revision': c.revision},
                        {'origin': url, 'access_token': ''}, 'direct', model='whisper', operation='transcription')
                    ref = 'fleet-artifact://voice/' + a['id']
                    await session.submit({'audio': ref}, request_id='transcribe', parameters={'language': 'en'})
                    assert entered.wait(2)
                    with pytest.raises(ValueError, match='retained'):
                        c.media_store().remove(a['id'])
                    release.set()
                    result = wait_done(c, 'transcribe')
                    assert result['state'] == 'succeeded', result
                    assert result['result'] == {'text': 'Hello from the Fleet node.', 'usage': {}}
                    assert calls == [{'model': b'whisper', 'language': b'en', 'response_format': b'json', 'file': data}]
                    assert (await session.submit({'audio': ref}, request_id='transcribe', parameters={'language': 'en'}))['state'] == 'succeeded'
                    assert len(calls) == 1
                    assert 'private_path' not in json.dumps(result)
        close(c)
        c = connector_module.Connector(tmp_path)
        assert c.inference_jobs().status('transcribe')['result'] == result['result']
        c.inference_jobs().remove('transcribe')
        # Input is user-owned; only its transient job lease is released.
        assert c.media_store().get(a['id'])['state'] == 'ready'
        c.media_store().remove(a['id'])
    finally:
        release.set()
        close(c)


@pytest.mark.parametrize('mode', ['cancel', 'bad-result', 'too-large-result'])
def test_cancel_or_invalid_result_never_replays_and_releases_input(tmp_path, mode):
    c = connector_module.Connector(tmp_path)
    calls, entered, release = [], threading.Event(), threading.Event()
    result = {'text': []} if mode == 'bad-result' else {'text': 'x' * 270000} if mode == 'too-large-result' else None
    try:
        with serve(engine(calls, entered, release if mode == 'cancel' else None, result)) as endpoint:
            c.configure({'engine': 'speaches', 'endpoint': endpoint})
            a = upload(c.media_store(), audio())
            body = request(a['id'])
            c.inference_jobs().submit(body, c.revision)
            if mode == 'cancel':
                assert entered.wait(2)
                c.inference_jobs().cancel('transcribe')
            record = wait_done(c, 'transcribe')
            assert record['state'] == ('cancelled' if mode == 'cancel' else 'unknown')
            assert record['result'] is None
            assert c.inference_jobs().submit(body, c.revision) == record
            assert len(calls) == 1
            c.media_store().remove(a['id'])
    finally:
        release.set()
        close(c)


@pytest.mark.asyncio
async def test_sdk_rejects_foreign_artifact_before_any_http():
    from unittest.mock import AsyncMock
    session = object.__new__(InferenceSession)
    session.model, session.operation, session.deployment = 'whisper', 'transcription', 'voice'
    session.wire = AsyncMock()
    for ref in ['fleet-artifact://other/' + 'a' * 32, 'https://remote/audio.wav', '/tmp/audio.wav']:
        with pytest.raises(ValueError):
            await session.submit({'audio': ref}, request_id='no-call')
    session.wire.request.assert_not_called()


@pytest.mark.parametrize('field', ['input', 'parameters', 'engine', 'mime', 'writing'])
def test_invalid_inputs_fail_before_upstream_or_durable_job(tmp_path, field):
    c = connector_module.Connector(tmp_path)
    try:
        c.configure({'engine': 'ollama' if field == 'engine' else 'speaches', 'endpoint': 'http://127.0.0.1:1/v1'})
        a = upload(c.media_store(), audio())
        body = request(a['id'])
        if field == 'input': body['input'] = {'audio': '/tmp/not-allowed'}
        if field == 'parameters': body['parameters'] = {'endpoint': 'https://other'}
        if field in {'mime', 'writing'}:
            with c.media_store().transaction():
                c.media_store().db.execute('UPDATE media SET mime=?,state=? WHERE id=?',
                    ('image/png' if field == 'mime' else 'audio/wav', 'writing' if field == 'writing' else 'ready', a['id']))
        with pytest.raises(ValueError):
            c.inference_jobs().submit(body, c.revision)
        assert c.inference_jobs().list() == [] and not c.calls
        assert not c.media_store().db.execute('SELECT 1 FROM leases').fetchone()
    finally:
        close(c)
