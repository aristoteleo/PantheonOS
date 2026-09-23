"""Generated audio lifecycle with a streaming HTTP fixture, not a speech model."""
import hashlib
import io
import json
import threading
import time
import wave

import httpx
import pytest
from http.server import BaseHTTPRequestHandler

from pantheon.models.jobs import InferenceSession
from test_model_services import connector_module, serve
from test_model_inference_jobs import wait_done, close


def audio():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\1' * 80000)
    return output.getvalue()


def request(job='speech-1', **fields):
    return {'job_id': job, 'model': 'local-kokoro', 'operation': 'speech',
            'input': {'text': 'Private test input'}, 'parameters': {'voice': 'af_heart'}, **fields}


def engine(calls, data, *, entered=None, release=None, content_type='audio/wav', declared=None):
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            assert self.path == '/v1/audio/speech'
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            if declared is not None:
                self.send_header('Content-Length', str(declared))
            self.end_headers()
            try:
                self.wfile.write(data[:1000])
                self.wfile.flush()
                if entered:
                    entered.set()
                if release:
                    release.wait(5)
                self.wfile.write(data[1000:])
            except OSError:
                pass
    return Engine


@pytest.mark.asyncio
async def test_speech_binary_result_reconnect_restart_and_cleanup(tmp_path):
    c = connector_module.Connector(tmp_path)
    data, calls = audio(), []
    try:
        with serve(engine(calls, data)) as upstream:
            c.configure({'engine': 'speaches', 'endpoint': upstream})
            with serve(connector_module.handler(c)) as url:
                async with httpx.AsyncClient() as client:
                    session = InferenceSession(client, {'deployment_id': 'node-a', 'config_revision': c.revision},
                        {'origin': url, 'access_token': ''}, 'direct', model='local-kokoro', operation='speech')
                    await session.submit({'text': 'Private test input'}, request_id='speech-1', parameters={'voice': 'af_heart'})
                    result = wait_done(c, 'speech-1')
                    assert result['state'] == 'succeeded'
                    result = await session.status('speech-1')
                    artifact = result['result']['artifacts'][0]
                    assert artifact['size'] == len(data) and artifact['sha256'] == hashlib.sha256(data).hexdigest()
                    assert artifact['ref'].startswith('fleet-artifact://node-a/')
                    assert 'Private test input' not in json.dumps(result)
                    assert len(json.dumps(result)) < 2048
                    destination = io.BytesIO()
                    await session.wire.download(artifact['ref'], destination)
                    assert destination.getvalue() == data
                    assert (await session.list())['jobs'][0].get('result') is None
                    for suffix in ('', '/complete'):
                        response = await client.request('PUT' if not suffix else 'POST',
                            url + '/media/artifacts/' + artifact['id'] + suffix,
                            headers={'X-Model-Config': c.revision, 'Upload-Offset': '0'}, content=b'x' if not suffix else b'')
                        assert response.status_code == 400
                    with pytest.raises(ValueError, match='retained'):
                        c.media_store().remove(artifact['id'])
                    assert (await session.submit({'text': 'Private test input'}, request_id='speech-1',
                        parameters={'voice': 'af_heart'}))['result'] == result['result']
                    assert len(calls) == 1
        close(c)
        c = connector_module.Connector(tmp_path)
        assert c.inference_jobs().status('speech-1')['state'] == 'succeeded'
        assert c.media_store().read(artifact['id'])[1] == data
        with pytest.raises(ValueError, match='retained'):
            c.media_store().remove(artifact['id'])
        c.inference_jobs().remove('speech-1')
        assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
        assert not list(c.media_store().root.glob('*.blob'))
    finally:
        close(c)


def test_budget_admission_happens_before_model_call(tmp_path):
    c = connector_module.Connector(tmp_path)
    calls = []
    try:
        with serve(engine(calls, audio())) as upstream:
            c.configure({'engine': 'speaches', 'endpoint': upstream})
            store = c.media_store()
            store.quota = 100
            with pytest.raises(ValueError, match='budget'):
                c.inference_jobs().submit(request(), c.revision)
            assert c.inference_jobs().list() == [] and not calls and not c.calls
            assert not store.db.execute('SELECT 1 FROM media').fetchone()
            assert c.slots.acquire(blocking=False)
            c.slots.release()
    finally:
        close(c)


@pytest.mark.parametrize('mode', ['cancel', 'deadline', 'oversize', 'mime', 'signature', 'truncated'])
def test_incomplete_speech_is_not_published_or_replayed(tmp_path, monkeypatch, mode):
    c = connector_module.Connector(tmp_path)
    calls, entered, release = [], threading.Event(), threading.Event()
    data = audio() if mode != 'signature' else b'not audio' * 1000
    delayed = mode in {'cancel', 'deadline'}
    try:
        with serve(engine(calls, data, entered=entered, release=release if delayed else None,
                content_type='application/json' if mode == 'mime' else 'audio/wav',
                declared=len(data) + 500 if mode == 'truncated' else None)) as upstream:
            c.configure({'engine': 'speaches', 'endpoint': upstream})
            if mode == 'oversize':
                monkeypatch.setattr(c.module('job_drivers'), 'SPEECH_OUTPUT_LIMIT', 1200)
            jobs = c.inference_jobs()
            if mode == 'deadline':
                monkeypatch.setattr(c.module('inference_jobs'), 'TIMEOUT_SECONDS', .2)
            jobs.submit(request(), c.revision)
            if delayed:
                assert entered.wait(2)
                assert c.drain()['safe_to_stop'] is False
                if mode == 'cancel':
                    jobs.cancel('speech-1')
            result = wait_done(c, 'speech-1')
            assert result['state'] in {'cancelled', 'unknown'} and result['result'] is None
            assert jobs.submit(request(), c.revision) == result and len(calls) == 1
            assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
            assert not list(c.media_store().root.glob('*.blob'))
            release.set()
    finally:
        release.set()
        close(c)


def test_crash_reclaims_partial_output_without_resubmission(tmp_path):
    c = connector_module.Connector(tmp_path)
    c.configure({'engine': 'speaches', 'endpoint': 'http://127.0.0.1:9'})
    jobs = c.inference_jobs()
    with jobs.store.transaction():
        output = jobs.store.reserve_output('inference-crashed', kind='audio', mime='audio/wav', max_size=10000)
        jobs.put('crashed', 'fingerprint', {'job_id': 'crashed', 'state': 'running', 'created_at': time.time(), 'result': None})
    jobs.store.append(output['id'], 0, b'partial', owner='inference-crashed')
    close(c)
    c = connector_module.Connector(tmp_path)
    try:
        assert c.inference_jobs().status('crashed')['state'] == 'unknown'
        assert not c.calls and not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
        assert not list(c.media_store().root.glob('*.blob'))
    finally:
        close(c)


def test_interrupted_history_removal_is_recoverable(tmp_path, monkeypatch):
    c = connector_module.Connector(tmp_path)
    calls = []
    with serve(engine(calls, audio())) as upstream:
        c.configure({'engine': 'speaches', 'endpoint': upstream})
        jobs = c.inference_jobs()
        jobs.submit(request(), c.revision)
        assert wait_done(c, 'speech-1')['state'] == 'succeeded'
        monkeypatch.setattr(jobs.store, 'remove', lambda artifact: (_ for _ in ()).throw(OSError('disk unavailable')))
        with pytest.raises(OSError):
            jobs.remove('speech-1')
        assert jobs.status('speech-1')['removing'] is True
        close(c)
    c = connector_module.Connector(tmp_path)
    try:
        assert not c.inference_jobs().list()
        assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
        assert not list(c.media_store().root.glob('*.blob'))
    finally:
        close(c)


@pytest.mark.parametrize('fields', [
    {'parameters': {'voice': 'af_heart', 'url': 'http://elsewhere'}},
    {'parameters': {'voice': 'af_heart', 'response_format': 'flac'}},
    {'parameters': {'voice': 'af_heart', 'speed': True}},
    {'parameters': {'voice': '../file.wav'}},
    {'input': {'text': 'a' * 32769}},
])
def test_speech_rejects_unsupported_parameters_before_reservation(tmp_path, fields):
    c = connector_module.Connector(tmp_path)
    c.configure({'engine': 'speaches', 'endpoint': 'http://127.0.0.1:9'})
    try:
        with pytest.raises(ValueError):
            c.inference_jobs().submit(request(**fields), c.revision)
        assert not c.calls and not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
    finally:
        close(c)
