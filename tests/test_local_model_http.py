"""Real local Fleet -> original Connector HTTP/SSE, with explicit private TLS.

No mock Controller, node, grant issuance or connector. The consumer is a minimal
live App and the directory is a frozen publication supplied by this test; engine
replies are deterministic. This is not automatic Agent/CLI composition.
"""
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import platform
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import httpx
import nats
import pytest

from pantheon.apps.client import AppClient
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact, CHUNK_SIZE
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.client import ModelServices, model_ref
from pantheon.models.connector_package import build_package
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.model_dependency_control import ModelDependencyControl
from test_local_fleet import binaries
from test_model_services import serve


@pytest.fixture
def model_endpoint():
    calls, release, disconnected = [], threading.Event(), threading.Event()
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/v1/models':
                value = {'data': [{'id': 'example:8b'}]}
            elif self.path == '/api/ps':
                value = {'models': []}
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, dict(self.headers), body))
            if self.path == '/api/show':
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"capabilities":["completion","tools"],"model_info":{"general.architecture":"llama","llama.context_length":8192}}')
                return
            assert self.path == '/v1/chat/completions'
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"scoped reply"}}]}\n\n')
            self.wfile.flush()
            if body['messages'][0]['content'] == 'hold stream':
                try:
                    while not release.wait(.02):
                        self.wfile.write(b': heartbeat\n\n'); self.wfile.flush()
                except OSError:
                    disconnected.set()
                return
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    with serve(Engine) as url:
        try:
            yield SimpleNamespace(url=url, requests=calls, disconnected=disconnected)
        finally:
            release.set()


def consumer_package(path):
    path.mkdir()
    (path / 'server.py').write_text('''
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'ready')
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
''')
    manifest = {'apiVersion': 2, 'id': 'model-consumer', 'version': '1.0.0', 'runtime': 'process',
                'execution': {'protocol': 1, 'manifest': 'fleet.json'}}
    definition = {'protocol': 1, 'app_id': 'model-consumer', 'version': '1.0.0',
        'components': [{'name': 'backend', 'runtime': 'process',
            'argv': ['python3', '${PACKAGE}/server.py'], 'ports': {'http': 0},
            'readiness': {'argv': ['python3', '-c',
                "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'],timeout=1).read()"],
                'timeout_seconds': 10}}]}
    for name, value in (('app.json', manifest), ('fleet.json', definition)):
        (path / name).write_text(json.dumps(value))


@pytest.mark.asyncio
async def test_local_connector_model_client_stream_and_consumer_lifetime(tmp_path, binaries, model_endpoint, monkeypatch):
    platform_id = ('darwin' if sys.platform == 'darwin' else 'linux') + '-' + {
        'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    connector = build_package(tmp_path / 'connector', platform_id)
    consumer = tmp_path / 'consumer'
    consumer_package(consumer)
    async with LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path) as runtime:
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        client = AppClient(nc, info.fleet_id)
        class Wire(FleetLifecycle):
            async def _request(self, node, method, **kwargs):
                value = await client.lifecycle(node, method, **kwargs)
                assert 'error' not in value, value
                return value
        wire = Wire(None)
        async def action(digest, name, generation=0, **kwargs):
            receipt = await wire.submit(info.node_id, name, digest, generation=generation, **kwargs)
            for _ in range(400):
                state = await wire.status(info.node_id)
                op = state['operations'][receipt['request']['operation_id']]
                if op['state'] == 'succeeded':
                    return next((i for i in state['instances'].values() if i['digest'] == digest), None)
                assert op['state'] in ('queued', 'running'), op
                await asyncio.sleep(.05)
            pytest.fail('App operation did not complete')
        async def stage(path):
            data, digest = build_artifact(path)
            for offset in range(0, len(data), CHUNK_SIZE):
                await wire._request(info.node_id, 'stage', digest=digest, offset=offset,
                    data=base64.b64encode(data[offset:offset+CHUNK_SIZE]).decode())
            return digest
        async def invoke(instance, method):
            value = await client.invoke(info.node_id, 'model-service', {
                'instance_id': instance['instance_id'], 'revision': instance['digest'],
                'generation': instance['generation']}, method, {}, 10)
            assert 'error' not in value, value
            assert 'error' not in value['response'], value
            return value['response']
        issuer = models = None
        try:
            digest = await stage(connector)
            await action(digest, 'install')
            prepared = await action(digest, 'prepare_start', operation_id='prepare-model')
            await wire.configure(info.node_id, instance_id=prepared['instance_id'], revision=digest,
                generation=prepared['generation'], preparation_id='prepare-model', components={
                    'backend': {'values': {'connector': {'engine': 'ollama', 'endpoint': model_endpoint.url}}}})
            provider = await action(digest, 'start', prepared['generation'], start_preparation_id='prepare-model')
            cdigest = await stage(consumer)
            consuming = await action(cdigest, 'start')
            identity = {'node_id': info.node_id, 'instance_id': consuming['instance_id'],
                        'revision': cdigest, 'generation': consuming['generation']}
            status = await invoke(provider, 'status')
            discovered = await invoke(provider, 'discover')
            assert discovered['models'][0]['id'] == 'example:8b'
            row = {'deployment_id': 'local', 'name': 'Local Connector', 'engine': 'ollama',
                'node_id': info.node_id, 'node_name': 'Local', 'state': 'ready', 'revision': 1,
                'config_revision': status['config_revision'], 'binding': {'node_id': info.node_id,
                    'instance_id': provider['instance_id'], 'revision': digest, 'generation': provider['generation'],
                    'component': 'backend', 'port': 'http'},
                'models': [{'id': 'example:8b', 'operations': ['text'], 'tools': True, 'context': 8192}]}
            credential = RuntimeCredential(info.controller, (runtime.root / 'owner.key').read_text().strip())
            issuer = ModelDependencyControl(owner=info.fleet_id, credential=credential,
                tls_context=info.tls_context(), http_origin=info.controller)
            grant = await issuer.issue_connection(consumer=identity, deployment=row)
            class PublishedModel(ModelServices):
                # Only directory publication/control composition is a fixture.
                # Calls use the unchanged production ModelServices HTTP/SSE path.
                async def deployments(self): return [row]
                async def connect(self, selected):
                    assert selected == row
                    return grant
            # A supplied CA must work without ambient trust/proxy settings. This
            # also catches accidentally dropping the context in the relay pool.
            monkeypatch.setenv('SSL_CERT_FILE', '/missing/model-test-ca.pem')
            monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
            monkeypatch.setenv('NO_PROXY', '')
            models = PublishedModel('dependency://local', direct_executable='',
                tls_context=info.tls_context())
            chunks = []
            async def chunk(value): chunks.append(value)
            result = await models.complete(model_ref('local', 'example:8b'),
                [{'role': 'user', 'content': 'one scoped model call'}], process_chunk=chunk)
            assert result['content'] == 'scoped reply', result
            assert chunks, 'SSE output was not delivered to the model callback'
            inference = [c for c in model_endpoint.requests if c[0] == '/v1/chat/completions']
            assert len(inference) == 1 and inference[0][2]['messages'][0]['content'] == 'one scoped model call'
            assert credential.key not in json.dumps(model_endpoint.requests)
            async with httpx.AsyncClient(verify=info.tls_context(), trust_env=False) as http:
                headers = {'Authorization': 'Bearer ' + grant['access_token'], 'X-Model-Config': row['config_revision'], 'X-Model-Request': 'a'*32}
                for path, extra, expected in (
                    ('/rpc', {}, 403), ('/v1/models', {}, 403),
                    ('/v1/chat/completions', {'Origin': 'https://atrium.test'}, 403),
                    ('/v1/chat/completions', {'X-Model-Config': 'f'*64}, 409),
                ):
                    response = await http.post(grant['origin'] + path, headers=headers | extra,
                        json={'model': 'example:8b', 'messages': [{'role': 'user', 'content': 'forbidden'}], 'stream': True})
                    assert response.status_code == expected, (path, response.status_code)
                # An untrusted TLS client cannot use even a valid scoped token.
                async with httpx.AsyncClient(trust_env=False) as untrusted:
                    with pytest.raises(httpx.ConnectError):
                        await untrusted.get(grant['origin'] + '/route-state', headers=headers)
                # First output must arrive before the engine ends its stream.
                # Retiring the real consumer must then abort that active request.
                first_chunk = asyncio.Event()
                async def observe(value): first_chunk.set()
                streaming = asyncio.create_task(models.complete(model_ref('local', 'example:8b'),
                    [{'role': 'user', 'content': 'hold stream'}], process_chunk=observe))
                try:
                    await asyncio.wait_for(first_chunk.wait(), 5)
                    assert not streaming.done()
                    await action(cdigest, 'stop', consuming['generation'])
                    with pytest.raises((RuntimeError, httpx.HTTPError)):
                        await asyncio.wait_for(streaming, 8)
                    async with asyncio.timeout(5):
                        while (await invoke(provider, 'status'))['active_calls']:
                            await asyncio.sleep(.05)
                    assert await asyncio.to_thread(model_endpoint.disconnected.wait, 3)
                finally:
                    if not streaming.done(): streaming.cancel()
                    await asyncio.gather(streaming, return_exceptions=True)
                denied = await http.get(grant['origin'] + '/route-state', headers=headers)
                assert denied.status_code == 409
                assert (await invoke(provider, 'status'))['accepting'] is True
                gid = hashlib.sha256(grant['access_token'].encode()).hexdigest()
                revoked = await http.delete(info.controller + '/api/fleet/apps/dependency-http-grants/' + gid,
                    headers={'Authorization': 'Bearer ' + credential.key})
                assert revoked.status_code == 204
                denied = await http.get(grant['origin'] + '/route-state', headers=headers)
                assert denied.status_code >= 400
            assert len([c for c in model_endpoint.requests if c[0] == '/v1/chat/completions']) == 2
        finally:
            if models is not None: await models.aclose()
            if issuer is not None: await issuer.aclose()
            try:
                state = await wire.status(info.node_id)
                for instance in state['instances'].values():
                    if instance['state'] in ('ready', 'prepared', 'failed'):
                        await action(instance['digest'], 'stop', instance['generation'])
            finally:
                await nc.close()
