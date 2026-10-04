"""Scoped control, ordinary prepared Agent, and real Model Service connector.

TLS/auth issuance and Hub directory are fixtures; this does not claim deployed
Fleet consumer-grant enforcement. Engine output is deterministic, not a paid LLM.
"""
import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.client import ModelServices, ControlError, model_ref
from pantheon.models.dependency import DependencyModelServices, control_operation
from pantheon.models.dependency_service import ModelServiceControl
from test_agent_dependency_bindings import endpoint as tls_material
from test_agent_launch import prepared
from test_agent_native_process import native_process, request
from test_agent_application import TEMPLATE
from test_model_services import connector_module, deployment, serve


@pytest.fixture
def model_endpoint():
    calls = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/v1/models'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"example:8b"}]}')
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, dict(self.headers), body))
            self.send_response(200)
            if self.path == '/api/show':
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"capabilities":["completion","tools"],"model_info":{"general.architecture":"llama","llama.context_length":8192}}')
                return
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"scoped reply"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    with serve(Engine) as url:
        yield SimpleNamespace(url=url, requests=calls)


def policy(row):
    return {'consumer': {'node_id': 'node', 'instance_id': 'agent-app', 'revision': 'a'*64, 'generation': 1},
            'deployments': {row['deployment_id']: deepcopy(row['binding'])},
            'routes': {}, 'allow_wake': False}


@pytest.mark.asyncio
async def test_control_policy_blocks_management_cross_node_and_changed_generations():
    row = deployment()
    other = deepcopy(row)
    other.update(deployment_id='other', binding={**row['binding'], 'instance_id': 'other'})
    calls = []
    async def directory(): return deepcopy([row, other])
    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('Unexpected owner request')
    owner = SimpleNamespace(deployments=directory, hub_request=forbidden)
    service = ModelServiceControl(owner, policies={'agent': policy(row)})
    async def invoke(operation, arguments):
        return await service.model_services_control(policy_id='agent', operation=operation, arguments=arguments)
    assert (await invoke('deployments', {}))['result']['deployments'] == [row]
    assert (await invoke('connect', {'binding': other['binding']}))['status'] == 403
    # Missing consumer-aware issuer must never fall back to owner's workload key.
    assert (await invoke('connect', {'binding': row['binding']}))['status'] == 503
    assert (await invoke('configure', {}))['status'] == 403
    assert (await invoke('engine_idle', {'deployment_id': 'mac', 'action': 'wake', 'revision': 1}))['status'] == 403
    row['binding']['generation'] += 1
    assert (await invoke('deployments', {}))['result']['deployments'] == []
    assert (await invoke('connect', {'binding': row['binding']}))['status'] == 403
    assert calls == []


@pytest.mark.parametrize('method,path,data', [
    ('PUT', '/api/model-services/mac', {}),
    ('DELETE', '/api/model-services/mac', {'revision': 1}),
    ('POST', '/api/model-services/mac/engine-idle', {'action': 'disable', 'revision': 1}),
    ('POST', '/api/fleet/apps/deploy', {}),
    ('GET', 'https://owner.invalid/api/model-services', None),
])
def test_consumer_cannot_use_model_binding_for_management(method, path, data):
    with pytest.raises(ControlError) as error:
        control_operation(method, path, data)
    assert error.value.status == 403


@pytest.fixture
def model_dependency(tmp_path, tls_material, model_endpoint):
    connector = connector_module.Connector(tmp_path/'connector')
    connector.configure({'engine': 'ollama', 'endpoint': model_endpoint.url})
    row = deployment()
    row['config_revision'] = connector.revision
    row['models'][0]['context'] = 8192
    route = {'route_id': 'local', 'name': 'Local model', 'revision': 1,
        'candidates': [{'deployment_id': 'mac', 'model_id': 'example:8b'}],
        'requires': {'operation': 'text', 'tools': True, 'context': 8192},
        'transport': 'relay_allowed', 'fallback': 'none', 'selection': 'ordered'}
    state = SimpleNamespace(revoked=False, controls=[], grants=[], data_calls=[])
    def hub(request):
        assert request.headers['authorization'] == 'Bearer owner-only'
        if request.url.path == '/api/model-services':
            return httpx.Response(200, json={'deployments': [row]})
        if request.url.path == '/api/model-services/routes':
            return httpx.Response(200, json={'routes': [route]})
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json={'route': route, 'candidates': [
                {'deployment': row, 'model': row['models'][0], 'compute': 'node', 'billing': 'local'}],
                'excluded': [], 'transport': 'fleet_relay', 'resolved': True})
        raise AssertionError('Broad workload grant endpoint must not be called')
    owner = ModelServices('https://owner.invalid', 'owner-only', httpx.MockTransport(hub))
    async def issue(**args):
        state.grants.append(args)
        return {'origin': state.origin, 'access_token': 'consumer-data', 'expires': int(time.time()) + 60}
    configured = policy(row)
    configured['routes'] = {'local': 1}
    service = ModelServiceControl(owner, policies={'agent': configured}, issue_connection=issue)
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever)
    worker.start()

    class Handler(connector_module.handler(connector)):
        def do_POST(self):
            if self.path != '/rpc':
                state.data_calls.append(self.path)
                if state.revoked or self.headers.get('Authorization') != 'Bearer consumer-data':
                    return self.reply(403, {'error': 'revoked'})
                return super().do_POST()
            if state.revoked or self.headers.get('Authorization') != 'Bearer ' + 'd'*64:
                return self.reply(403, {'error': 'revoked'})
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['method'] == 'model_services_control'
            assert set(body['args']) == {'operation', 'arguments'}
            state.controls.append(body['args'])
            value = asyncio.run_coroutine_threadsafe(
                service.model_services_control(policy_id='agent', **body['args']), loop).result(10)
            return self.reply(200, {'success': True, 'result': value})
        def do_GET(self):
            if state.revoked or self.headers.get('Authorization') != 'Bearer consumer-data':
                return self.reply(403, {'error': 'revoked'})
            return super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(tmp_path/'cert.pem', tmp_path/'key.pem')
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    state.origin = f'https://127.0.0.1:{server.server_port}'
    state.credential = {'endpoint': state.origin + '/rpc', 'key': 'd'*64}
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        asyncio.run_coroutine_threadsafe(owner.aclose(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        worker.join(5)
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('ref', [model_ref('mac', 'example:8b'), 'fleet-route://local'])
async def test_prepared_native_agent_uses_dependency_and_real_connector(
        tmp_path, model_dependency, model_endpoint, monkeypatch, ref):
    value = prepared(tmp_path, model_endpoint.url)
    value['credentials']['models'] = model_dependency.credential
    value['values']['agent']['models'] = {'model_services': 'models'}
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path/'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': ref}]}
    with native_process(tmp_path, model_endpoint.url, configuration=value) as (child, base):
        for _ in range(300):
            try:
                await request(base, '/health')
                break
            except OSError:
                assert child.poll() is None, (tmp_path/'process.log').read_text()[-8000:]
                await asyncio.sleep(.05)
        else:
            pytest.fail('Native Agent did not become ready')
        async def rpc(method, **args):
            response = await request(base, '/rpc', {'method': method, 'args': args})
            assert response['success'], response
            return response['result']
        listing = await rpc('list_available_models')
        assert listing['fleet_catalog_ready']
        assert {m['value'] for m in listing['fleet_models']} == {model_ref('mac', 'example:8b'), 'fleet-route://local'}
        assert all(not m['disabled'] for m in listing['fleet_models'])
        created = await rpc('create_chat', chat_name='Bound model', project_name='Shared', template_obj=template)
        assert created['success'], created
        result = await rpc('chat', chat_id=created['chat_id'], message=[{'role': 'user', 'content': 'Reply once'}])
        assert result['success'], result
        history = await rpc('open_agent_history', chat_id=created['chat_id'])
        data = await rpc('read_agent_history', chat_id=created['chat_id'], snapshot_id=history['snapshot_id'], part=0)
        assert 'scoped reply' in data['json_fragment']
        model_dependency.revoked = True
        listing = await rpc('list_available_models')
        assert listing['fleet_models'] == [] and not listing['fleet_catalog_ready']
        assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert child.returncode == 0
    inference = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
    assert len(inference) == 1
    assert inference[0][2]['model'] == 'example:8b'
    assert model_dependency.data_calls == ['/v1/chat/completions']
    assert model_dependency.grants[0]['consumer'] == policy(deployment())['consumer']
    assert all('Reply once' not in json.dumps(c) for c in model_dependency.controls)


@pytest.mark.asyncio
async def test_cancel_control_drains_worker_and_shutdown_closes_client(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def invoke(*args, **kwargs):
        entered.set()
        release.wait(5)
        return {'success': True, 'result': {'protocol': 1, 'operation': 'deployments',
                                          'status': 200, 'result': {'deployments': []}}}
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    monkeypatch.setenv('FLEET_KEY', 'ambient-secret')
    client = DependencyModelServices(DependencyClient(RuntimeCredential('https://dependency.test/rpc', 'a'*64)))
    task = asyncio.create_task(client.deployments())
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    closing = asyncio.create_task(client.aclose())
    await asyncio.sleep(.02)
    assert not closing.done() and not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await closing
    assert not client._pending and client.relay_http.closed and client.cancel_http.closed
    with pytest.raises(RuntimeError, match='stopping'):
        await client.deployments()
    with pytest.raises(RuntimeError, match='cannot authorize'):
        client.headers()


@pytest.mark.asyncio
async def test_two_policies_get_separate_catalogs_and_pinned_grant_consumers():
    a, b = deployment(), deployment()
    b.update(deployment_id='second', binding={**b['binding'], 'instance_id': 'second'})
    pa, pb = policy(a), policy(b)
    pb['consumer']['instance_id'] = 'agent-b'
    async def rows(): return deepcopy([a, b])
    issued = []
    async def issue(**kwargs):
        issued.append(kwargs)
        return {'test-grant': kwargs['consumer']['instance_id']}
    service = ModelServiceControl(SimpleNamespace(deployments=rows),
                                  policies={'a': pa, 'b': pb}, issue_connection=issue)
    for name, allowed, denied in [('a', a, b), ('b', b, a)]:
        async def call(operation, arguments):
            return await service.model_services_control(policy_id=name, operation=operation, arguments=arguments)
        assert (await call('deployments', {}))['result']['deployments'] == [allowed]
        assert (await call('connect', {'binding': denied['binding']}))['status'] == 403
        assert (await call('connect', {'binding': allowed['binding']}))['status'] == 200
    assert [i['consumer']['instance_id'] for i in issued] == ['agent-app', 'agent-b']
    assert [i['deployment']['deployment_id'] for i in issued] == ['mac', 'second']
