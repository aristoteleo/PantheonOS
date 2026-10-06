"""Real local Fleet -> original Connector HTTP/SSE, with explicit private TLS.

No mock Controller, node, directory, grant issuance or connector. Original
registration publishes live discovery to the private local catalog. A packaged
control App supplies scoped catalog/routes/grants to the production dependency
model client. The consumer is a minimal live App; engine replies are deterministic.
This is not yet automatic Agent/CLI composition.
"""
import asyncio
import base64
import hashlib
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler
import json
import socket
import platform
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import httpx
import nats
import pytest

from pantheon.apps.client import AppClient
from pantheon.apps.dependency_assembly import DependencyAuthority, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.lifecycle import ConfigurationBusy, FleetLifecycle, build_artifact, CHUNK_SIZE
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.bootstrap import ModelServiceBootstrap
from pantheon.models.client import model_ref
from pantheon.models.dependency import DependencyModelServices
from pantheon.models.errors import ControlError
from pantheon.models.connector_package import build_package
from pantheon.models.credentials import RemoteModelCredentialVault
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.manager import ModelServiceManager
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.model_dependency_package import build_package as build_control
from test_local_fleet import binaries
from test_model_services import serve


@pytest.fixture
def model_endpoint():
    calls, release, disconnected = [], threading.Event(), threading.Event()
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def authorized(self):
            if state.required_key is None or self.headers.get('Authorization') == 'Bearer ' + state.required_key:
                return True
            state.unauthorized += 1
            self.send_response(401); self.end_headers()
            return False
        def do_GET(self):
            if not self.authorized(): return
            if self.path == '/v1/models':
                value = {'data': [{'id': 'example:8b'}]}
            elif self.path == '/api/ps':
                value = {'models': []}
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_POST(self):
            if not self.authorized(): return
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, dict(self.headers), body))
            if self.path == '/api/show':
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'capabilities': ['completion', 'tools'], 'model_info': {
                    'general.architecture': 'llama', 'llama.context_length': state.context_length}}).encode())
                return
            assert self.path == '/v1/chat/completions'
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            request_tool = state.tool_command and body['messages'][-1]['role'] == 'user'
            if state.tool_prompt_prefix:
                # Full teams append plugin reminders as user messages. Only
                # issue one tool call per actual test prompt, not per reminder.
                anchors = [i for i, m in enumerate(body['messages']) if m['role'] == 'user'
                           and isinstance(m.get('content'), str)
                           and m['content'].startswith(state.tool_prompt_prefix)]
                request_tool = (state.tool_command and anchors
                    and not any(m['role'] == 'tool' for m in body['messages'][anchors[-1]+1:])
                    and any(t['function']['name'] == 'shell__run_command' for t in body.get('tools', [])))
            if request_tool:
                tool = {'index': 0, 'id': 'call_local_' + str(len(calls)), 'type': 'function',
                    'function': {'name': 'shell__run_command',
                                 'arguments': json.dumps({'command': state.tool_command, 'timeout': 5})}}
                self.wfile.write(('data: ' + json.dumps({'choices': [{'index': 0,
                    'delta': {'tool_calls': [tool]}, 'finish_reason': 'tool_calls'}]}) + '\n\ndata: [DONE]\n\n').encode())
                return
            content = 'scoped reply'
            if state.tool_prompt_prefix and any('Your ONLY task is to update the session note' in str(m.get('content', ''))
                                                for m in body['messages']):
                content = '---\ntitle: General Team verification\nsummary: Real Shell call completed.\n---\n## Task State\nVerified PROFILE_TOOL_OK.'
            self.wfile.write(('data: '+json.dumps({'choices': [{'index': 0, 'delta': {'content': content}}]})+'\n\n').encode())
            self.wfile.flush()
            if body['messages'][0]['content'] == 'hold stream':
                try:
                    while not release.wait(.02):
                        self.wfile.write(b': heartbeat\n\n'); self.wfile.flush()
                except OSError:
                    disconnected.set()
                return
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    state = SimpleNamespace(tool_command=None, tool_prompt_prefix=None, context_length=8192,
                            required_key=None, unauthorized=0)
    with serve(Engine) as url:
        try:
            state.url, state.requests, state.disconnected = url, calls, disconnected
            yield state
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
    control_package = build_control(tmp_path / 'model-control', platform_id)
    consumer = tmp_path / 'consumer'
    consumer_package(consumer)
    async with AsyncExitStack() as profiles:
        runtime = await profiles.enter_async_context(LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path))
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        client = AppClient(nc, info.fleet_id)
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
        wire = FleetLifecycle(resolver)
        async def configure(**kwargs):
            # Only this explicit pre-write refusal may be retried. No replay
            # for a lost acknowledgement, start, discovery or inference.
            async with asyncio.timeout(5):
                while True:
                    try:
                        return await wire.configure(info.node_id, **kwargs)
                    except ConfigurationBusy:
                        await asyncio.sleep(.05)
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
            await action(digest, 'install', scope='model-local')
            prepared = await action(digest, 'prepare_start', operation_id='prepare-model', scope='model-local')
            await configure(instance_id=prepared['instance_id'], revision=digest,
                generation=prepared['generation'], preparation_id='prepare-model', components={
                    'backend': {'values': {'connector': {'engine': 'ollama', 'endpoint': model_endpoint.url}}}})
            provider = await action(digest, 'start', prepared['generation'], start_preparation_id='prepare-model', scope='model-local')
            cdigest = await stage(consumer)
            consuming = await action(cdigest, 'start')
            identity = {'node_id': info.node_id, 'instance_id': consuming['instance_id'],
                        'revision': cdigest, 'generation': consuming['generation']}
            directory = LocalModelDirectory(tmp_path / 'directory', owner=info.fleet_id)
            await directory.initialize()
            manager = ModelServiceManager(client=directory, resolver=resolver)
            registration = {'deployment_id': 'local', 'name': 'Local Connector', 'binding': {'node_id': info.node_id,
                    'instance_id': provider['instance_id'], 'revision': digest, 'generation': provider['generation'],
                    'component': 'backend', 'port': 'http'},
                'configuration': {'engine': 'ollama', 'endpoint': model_endpoint.url},
                'models': [{'id': 'example:8b', 'context_limit': 4096}]}
            row = await manager.register_prepared(**registration)
            assert row['models'][0]['tools'] is True and row['models'][0]['context'] == 4096
            # Re-opened owner storage and the original registration recover
            # without rewriting the publication or reconfiguring the engine.
            manager.client = LocalModelDirectory(directory.root, owner=info.fleet_id)
            assert await manager.register_prepared(**registration) == row
            route_path = '/api/model-services/routes/local'
            route = await directory.hub_request('PUT', route_path, {
                'route_id': 'local', 'name': 'Local choice', 'allowed_nodes': [info.node_id],
                'candidates': [{'deployment_id': 'local', 'model_id': 'example:8b'}],
                'requires': {'tools': True}})
            credential = RuntimeCredential(info.controller, (runtime.root / 'owner.key').read_text().strip())
            await RemoteModelCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id).ensure_async(
                'node-secret://local-model-owner', credential.endpoint, credential.key)
            control_digest = await stage(control_package)
            await action(control_digest, 'install')
            control_prepared = await action(control_digest, 'prepare_start', operation_id='prepare-control')
            control_values = {'protocol': 1, 'http_origin': info.controller,
                'trust_roots_pem': info.ca_certificate.read_text(), 'directory_root': str(directory.root),
                'policies': {'consumer': {'consumer': identity, 'deployments': {'local': row['binding']},
                    'routes': {'local': route['revision']}, 'allow_wake': False}}}
            await configure(instance_id=control_prepared['instance_id'], revision=control_digest,
                generation=control_prepared['generation'], preparation_id='prepare-control', components={
                    'backend': {'values': {'model_services': control_values},
                        'credentials': {'hub': {'endpoint': credential.endpoint, 'ref': 'node-secret://local-model-owner'}}}})
            control = await action(control_digest, 'start', control_prepared['generation'], start_preparation_id='prepare-control')
            issuer = DependencyAuthority(credential=credential, tls_context=info.tls_context(), rpc_origin=info.controller)
            receipt = await issuer.issue({'operation_id': 'model-control', 'consumer': identity,
                'provider': {'node_id': info.node_id, 'instance_id': control['instance_id'],
                    'revision': control_digest, 'generation': control['generation'], 'component': 'backend', 'port': 'http'},
                'app_id': 'model-services-control', 'methods': {'model_services_control': {
                    'arguments': ['operation', 'arguments'], 'bound': {'policy_id': 'consumer'}}}, 'ttl_seconds': 300})
            # A supplied CA must work without ambient trust/proxy settings. This
            # also catches accidentally dropping the context in the relay pool.
            monkeypatch.setenv('SSL_CERT_FILE', '/missing/model-test-ca.pem')
            monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
            monkeypatch.setenv('NO_PROXY', '')
            models = DependencyModelServices(DependencyClient(
                RuntimeCredential(receipt['endpoint'], receipt['access_token']), info.tls_context()), direct_executable='')
            assert await models.deployments() == [row]
            assert await models.routes() == [route]
            grant = await models.connect(row)
            chunks = []
            async def chunk(value): chunks.append(value)
            result = await models.complete(model_ref('local', 'example:8b'),
                [{'role': 'user', 'content': 'one scoped model call'}], process_chunk=chunk)
            assert result['content'] == 'scoped reply', result
            assert chunks, 'SSE output was not delivered to the model callback'
            aliased = await models.complete('fleet-route://local',
                [{'role': 'user', 'content': 'one aliased model call'}])
            assert aliased['content'] == 'scoped reply'
            await directory.hub_request('PUT', route_path, route | {'name': 'Changed policy'})
            with pytest.raises(ControlError) as changed:
                await models.complete('fleet-route://local', [{'role': 'user', 'content': 'must not run'}])
            assert changed.value.status == 403
            inference = [c for c in model_endpoint.requests if c[0] == '/v1/chat/completions']
            assert len(inference) == 2 and inference[0][2]['messages'][0]['content'] == 'one scoped model call'
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
            assert len([c for c in model_endpoint.requests if c[0] == '/v1/chat/completions']) == 3
            # Original owner lifecycle also records/executes an explicit stop;
            # a dead consumer alone did not stop this shared Connector above.
            stopped = await manager.set_running('local', False)
            previous_routes = await directory.routes()
            assert stopped['state'] == 'stopped' and stopped['revision'] > row['revision']
            assert await directory.deployment('local') == stopped
            state = await wire.status(info.node_id)
            assert state['instances'][provider['instance_id']]['state'] == 'stopped'
            # Exit the entire local profile, not only the Connector. No live
            # App is left for the Runner to abandon. Its new authority must use
            # a different port while retaining the owner, node and private CA.
            for item in state['instances'].values():
                if item['state'] == 'ready':
                    await action(item['digest'], 'stop', item['generation'], scope=item['scope'])
            await models.aclose()
            models = None
            await resolver.close()
            old_info, old_ca = info, info.ca_certificate.read_bytes()
            await profiles.aclose()
            with socket.socket() as unavailable:
                unavailable.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                unavailable.bind(('127.0.0.1', int(old_info.controller.rsplit(':', 1)[1])))
                runtime = await profiles.enter_async_context(LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path))
            info = runtime.coordinates
            assert info.controller != old_info.controller
            assert (info.fleet_id, info.node_id) == (old_info.fleet_id, old_info.node_id)
            assert info.ca_certificate.read_bytes() == old_ca
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            client = AppClient(nc, info.fleet_id)
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
            wire = FleetLifecycle(resolver)
            directory = LocalModelDirectory(directory.root, owner=info.fleet_id)
            assert await directory.deployment('local') == stopped
            state = await wire.status(info.node_id)
            # The owner startup coordinator now persists the clean model rebind
            # and prepares both ordinary consumers with their new identities.
            # Vault delivery still explicitly belongs to the profile owner.
            prior = provider
            replacement = {**row['binding'], 'generation': stopped['binding']['generation'] + 2}
            credential = RuntimeCredential(info.controller, (runtime.root / 'owner.key').read_text().strip())
            ref = 'node-secret://local-model-owner-' + hashlib.sha256(info.controller.encode()).hexdigest()[:16]
            await RemoteModelCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id).ensure_async(
                ref, credential.endpoint, credential.key)
            control_values.update(http_origin=info.controller)
            control_values['policies']['consumer'].update(consumer={'$app': 'consumer'},
                deployments={'local': {'$model': 'connector'}}, routes={'local': previous_routes[0]['revision']})
            def target(instance, components):
                return dict(node_id=info.node_id, revision=instance['digest'], scope=instance['scope'],
                    generation=state['instances'][instance['instance_id']]['generation'],
                    components=components, bindings={})
            spec = dict(kind='model-services', owner=info.fleet_id, operation_id='profile-restart',
                model_apps={'connector': dict(deployment_id='local', name=row['name'],
                    models=registration['models'], restart_from=stopped,
                    app=target(provider, {'backend': {'values': {'connector': registration['configuration']}}}))},
                apps={'consumer': target(consuming, {}), 'model-control': target(control, {
                    'backend': {'values': {'model_services': control_values},
                        'credentials': {'hub': {'endpoint': credential.endpoint, 'ref': ref}}}})})
            manager = ModelServiceManager(client=LocalModelDirectory(directory.root, owner=info.fleet_id), resolver=resolver)
            deploy = AppDeployment(DependencyStarter(wire, tmp_path / 'starts'), tmp_path / 'deployments')
            bootstrap = ModelServiceBootstrap(deploy, manager, tmp_path / 'model-starts')
            original_write = bootstrap._write
            def interrupted(path, record):
                if record['registered']:
                    raise OSError('injected registration checkpoint interruption')
                original_write(path, record)
            bootstrap._write = interrupted
            interrupted_once = False
            async with asyncio.timeout(30):
                while True:
                    try:
                        result = await bootstrap.advance(**spec)
                    except OSError as error:
                        assert str(error) == 'injected registration checkpoint interruption'
                        assert not interrupted_once
                        interrupted_once = True
                        observed = await wire.status(info.node_id)
                        assert observed['instances'][consuming['instance_id']]['state'] == 'stopped'
                        assert observed['instances'][control['instance_id']]['state'] == 'stopped'
                        bootstrap = ModelServiceBootstrap(deploy, manager, tmp_path / 'model-starts')
                        continue
                    if result['state'] == 'ready': break
                    await asyncio.sleep(.05)
            assert interrupted_once
            assert await bootstrap.advance(owner=info.fleet_id, operation_id='profile-restart') == result
            observed = await wire.status(info.node_id)
            provider = observed['instances'][provider['instance_id']]
            consuming = observed['instances'][consuming['instance_id']]
            control = observed['instances'][control['instance_id']]
            identity = {**identity, 'generation': consuming['generation']}
            rebound = await directory.deployment('local')
            assert rebound['binding'] == replacement
            assert rebound['models'] == row['models'] and rebound['revision'] == stopped['revision'] + 1
            assert await directory.routes() == previous_routes
            assert (await invoke(provider, 'status'))['accepting'] is True
            stale = await client.invoke(info.node_id, 'model-service', {
                'instance_id': prior['instance_id'], 'revision': prior['digest'],
                'generation': prior['generation']}, 'status', {}, 10)
            assert 'error' in stale, stale
            issuer = DependencyAuthority(credential=credential, tls_context=info.tls_context(), rpc_origin=info.controller)
            fresh = await issuer.issue({'operation_id': 'model-control-reopened', 'consumer': identity,
                'provider': {'node_id': info.node_id, 'instance_id': control['instance_id'],
                    'revision': control_digest, 'generation': control['generation'], 'component': 'backend', 'port': 'http'},
                'app_id': 'model-services-control', 'methods': {'model_services_control': {
                    'arguments': ['operation', 'arguments'], 'bound': {'policy_id': 'consumer'}}}, 'ttl_seconds': 300})
            models = DependencyModelServices(DependencyClient(
                RuntimeCredential(fresh['endpoint'], fresh['access_token']), info.tls_context()), direct_executable='')
            assert await models.deployments() == [rebound]
            restored = await models.complete('fleet-route://local',
                [{'role': 'user', 'content': 'inference after full local profile restart'}])
            assert restored['content'] == 'scoped reply'
            inference = [c for c in model_endpoint.requests if c[0] == '/v1/chat/completions']
            assert len(inference) == 4 and inference[-1][2]['messages'][0]['content'] == 'inference after full local profile restart'
            assert credential.key not in json.dumps(model_endpoint.requests)
            async with httpx.AsyncClient(verify=info.tls_context(), trust_env=False) as http:
                stale = await http.post(info.controller + '/rpc',
                    headers={'Authorization': 'Bearer ' + receipt['access_token']},
                    json={'method': 'model_services_control', 'args': {'operation': 'deployments', 'arguments': {}}, 'timeout_s': 5})
                assert stale.status_code in (401, 403, 409), stale.text
        finally:
            if models is not None: await models.aclose()
            try:
                if nc.is_connected:
                    state = await wire.status(info.node_id)
                    for instance in state['instances'].values():
                        if instance['state'] in ('ready', 'prepared', 'failed'):
                            await action(instance['digest'], 'stop', instance['generation'], scope=instance['scope'])
            finally:
                await nc.close()
