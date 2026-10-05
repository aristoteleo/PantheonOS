"""Real Connector/SDK/media transport with a controlled Images API upstream."""
import base64
import hashlib
import io
import json
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler

import httpx
import pytest
from PIL import Image

from pantheon.models.jobs import InferenceSession
from test_model_services import connector_module, serve
from test_model_inference_jobs import close, wait_done
from test_model_image_jobs import png


def request(**extra):
    return {'job_id': 'api-image', 'model': 'chosen-image-model', 'operation': 'image',
            'input': {'text': 'Keep the first image layout; use the second image colours'},
            'parameters': {}, **extra}


def upstream(calls, *, data=None, mode='normal', entered=None, release=None):
    data = png() if data is None else data
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.path.endswith('/edits'):
                parts = BytesParser(policy=default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + raw)
                payload = [(p.get_param('name', header='Content-Disposition'), p.get_payload(decode=True))
                           for p in parts.iter_parts()]
            else:
                payload = json.loads(raw)
            calls.append((self.path, payload, self.headers.get('Authorization')))
            if entered: entered.set()
            if release: release.wait(5)
            if mode == 'lost':
                self.close_connection = True
                return
            value = {'data': [{'b64_json': base64.b64encode(data).decode(),
                              'revised_prompt': 'private', 'url': 'https://must-not-fetch.invalid/private'}],
                     'usage': {'input_tokens': 11, 'output_tokens': 23, 'total_tokens': 34,
                               'private': 'secret', 'unbounded': 10**20}}
            if mode == 'url': del value['data'][0]['b64_json']
            if mode == 'base64': value['data'][0]['b64_json'] = '$$'
            if mode == 'many': value['data'].append(value['data'][0])
            raw = json.dumps(value).encode()
            self.send_response(302 if mode == 'redirect' else 200)
            if mode == 'redirect': self.send_header('Location', 'https://must-not-fetch.invalid/private')
            self.send_header('Content-Type', 'text/html' if mode == 'mime' else 'application/json')
            self.send_header('Content-Length', str(len(raw) + (20 if mode == 'truncated' else 0)))
            self.end_headers()
            try: self.wfile.write(raw)
            except OSError: pass
    return Engine


def put(store, data, name='reference', mime='image/png', sealed=True, kind='image'):
    record = store.create(name, kind=kind, mime=mime, purpose='input', size=len(data),
                          sha256=hashlib.sha256(data).hexdigest())
    store.append(record['id'], 0, data)
    return store.seal(record['id']) if sealed else record


@pytest.mark.asyncio
@pytest.mark.parametrize('fmt', ['png', 'jpeg', 'webp'])
async def test_generate_or_edit_through_sdk_preserves_inputs_and_binary_result(tmp_path, fmt):
    data = io.BytesIO(); Image.new('RGB', (64, 64), '#205090').save(data, format=fmt.upper())
    data = data.getvalue()
    calls, c = [], connector_module.Connector(tmp_path / 'connector')
    key = tmp_path / 'key'; key.write_text('node-owned-secret')
    try:
        with serve(upstream(calls, data=data)) as endpoint:
            c.configure({'engine': 'api', 'endpoint': endpoint, 'credential_file': str(key)})
            with serve(connector_module.handler(c)) as origin:
                async with httpx.AsyncClient() as http:
                    session = InferenceSession(http, {'deployment_id': 'images', 'config_revision': c.revision},
                        {'origin': origin, 'access_token': ''}, 'direct', model='chosen-image-model', operation='image')
                    first = await session.wire.upload(io.BytesIO(png()), request_key='reference-1', kind='image', mime='image/png')
                    second = await session.wire.upload(io.BytesIO(data), request_key='reference-2', kind='image', mime='image/' + fmt)
                    refs = [first['ref'], second['ref'], first['ref']]
                    inputs = {'text': request()['input']['text'], 'images': refs} if fmt != 'png' else {'text': 'Draw a square'}
                    params = {'output_format': fmt, 'quality': 'high', 'size': 'auto'}
                    await session.submit(inputs, request_id='api-image', parameters=params)
                    assert wait_done(c, 'api-image')['state'] == 'succeeded'
                    receipt = await session.status('api-image')
                    artifact = receipt['result']['artifacts'][0]
                    assert artifact['mime'] == 'image/' + fmt
                    out = io.BytesIO(); await session.wire.download(artifact['ref'], out)
                    assert out.getvalue() == data
                    assert receipt['result']['usage'] == {'input_tokens': 11, 'output_tokens': 23, 'total_tokens': 34}
                    assert not any(s in json.dumps(receipt) for s in ['private', 'b64_json', 'node-owned-secret', 'must-not-fetch'])
                    assert await session.submit(inputs, request_id='api-image', parameters=params) == receipt
                    assert len(calls) == 1 and calls[0][2] == 'Bearer node-owned-secret'
                    if fmt == 'png':
                        assert calls[0][0] == '/v1/images/generations'
                        assert calls[0][1] == {'model': 'chosen-image-model', 'prompt': 'Draw a square', **params, 'n': 1}
                    else:
                        assert calls[0][0] == '/v1/images/edits'
                        assert [v for k, v in calls[0][1] if k == 'image[]'] == [png(), data, png()]
                        assert dict(calls[0][1])['model'] == b'chosen-image-model'
                    assert inputs.get('images', refs) == refs  # caller-owned references are not mutated
                    await session.remove('api-image')
                    assert c.media_store().read(first['id'])[1] == png()
                    assert c.media_store().read(second['id'])[1] == data
                    assert not list(c.media_store().db.execute('SELECT * FROM leases'))
    finally: close(c)


@pytest.mark.parametrize('mode', ['lost', 'url', 'base64', 'many', 'redirect', 'mime', 'truncated', 'bad_image'])
def test_failed_generation_is_not_replayed_and_reserved_media_is_removed(tmp_path, mode):
    calls, c = [], connector_module.Connector(tmp_path)
    try:
        with serve(upstream(calls, mode=mode, data=png()[:-12] if mode == 'bad_image' else None)) as endpoint:
            c.configure({'engine': 'api', 'endpoint': endpoint})
            jobs = c.inference_jobs(); jobs.submit(request(), c.revision)
            record = wait_done(c, 'api-image')
            assert record['state'] in {'unknown', 'failed'}
            assert jobs.submit(request(), c.revision) == record and len(calls) == 1
            assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
    finally: close(c)


def test_edit_leases_inputs_during_cancel_and_releases_only_generated_output(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls, c = [], connector_module.Connector(tmp_path)
    try:
        with serve(upstream(calls, entered=entered, release=release)) as endpoint:
            c.configure({'engine': 'api', 'endpoint': endpoint})
            store = c.media_store(); a = put(store, png())
            body = request(input={'text': 'Edit this', 'images': [a['id']]})
            c.inference_jobs().submit(body, c.revision)
            assert entered.wait(2)
            with pytest.raises(ValueError): store.remove(a['id'])
            c.inference_jobs().cancel('api-image'); release.set()
            assert wait_done(c, 'api-image')['state'] == 'cancelled'
            assert store.read(a['id'])[1] == png()
            assert len(list(store.db.execute('SELECT * FROM media'))) == 1
            assert not list(store.db.execute('SELECT * FROM leases'))
            assert c.inference_jobs().submit(body, c.revision)['state'] == 'cancelled'
            assert len(calls) == 1
    finally: release.set(); close(c)


@pytest.mark.asyncio
async def test_foreign_references_fail_before_submission(tmp_path):
    c = connector_module.Connector(tmp_path); calls = []
    try:
        with serve(upstream(calls)) as endpoint:
            c.configure({'engine': 'api', 'endpoint': endpoint})
            with serve(connector_module.handler(c)) as origin:
                async with httpx.AsyncClient() as http:
                    session = InferenceSession(http, {'deployment_id': 'images', 'config_revision': c.revision},
                        {'origin': origin, 'access_token': ''}, 'direct', model='chosen', operation='image')
                    for refs in [['fleet-artifact://other/' + 'a' * 32], ['https://example.com/a.png'], [], 'bad']:
                        with pytest.raises(ValueError):
                            await session.submit({'text': 'edit', 'images': refs}, request_id='api-image')
            assert not c.inference_jobs().list() and not calls
    finally: close(c)


def test_invalid_input_parameters_and_quota_fail_before_upstream(tmp_path):
    c = connector_module.Connector(tmp_path); calls = []
    try:
        with serve(upstream(calls)) as endpoint:
            c.configure({'engine': 'api', 'endpoint': endpoint})
            store = c.media_store()
            writing = put(store, png(), sealed=False)
            audio = put(store, b'audio', name='wrong-kind', kind='audio', mime='audio/wav')
            for inputs in [{'text': 'edit', 'images': [writing['id']]}, {'text': 'edit', 'images': [audio['id']]},
                           {'text': 'edit', 'images': ['file:///private']}, {'text': 'edit', 'images': []}]:
                with pytest.raises(ValueError): c.inference_jobs().submit(request(input=inputs), c.revision)
            for params in [{'size': '99999x2'}, {'output_format': 'svg'}, {'quality': []}, {'n': True},
                           {'background': 'transparent', 'output_format': 'jpeg'}, {'response_format': 'url'},
                           {'endpoint': 'https://evil.invalid'}]:
                with pytest.raises(ValueError): c.inference_jobs().submit(request(parameters=params), c.revision)
            store.quota = 100
            with pytest.raises(ValueError, match='budget'): c.inference_jobs().submit(request(), c.revision)
            assert not c.inference_jobs().list() and not calls
    finally: close(c)
