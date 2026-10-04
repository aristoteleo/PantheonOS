"""Packaged owner facade and consumer-bound issuance; Hub/node are fixtures."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
import subprocess
import sys
import threading
import time

import httpx
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.models.errors import ControlError
from pantheon.platform.model_dependency_control import ModelDependencyControl, inference_rules
from pantheon.platform.model_dependency_host import ModelDependencyHost
from pantheon.platform.model_dependency_package import build_package
from test_agent_dependency_bindings import endpoint as tls_material
from test_model_dependency import policy
from test_model_services import deployment


def receipt(body):
    provider = body['provider']
    prefix = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
    return {'origin': f'https://{prefix}.apps.test', 'access_token': 'a' * 64,
            'grant_id': 'b' * 64, 'expires': int(time.time()) + 300,
            'consumer': {'fleet_id': 'owner', **body['consumer']},
            'provider': {'fleet_id': 'owner', **provider}, 'credential': 'must-not-forward'}


def direct_receipt(body):
    grant = receipt(body)
    return {k:v for k,v in grant.items() if k not in ('origin', 'provider')} | {
        'binding': grant['provider'], 'peer_id': 'y'*32, 'transport': 'fleet_direct',
        'addresses': ['/ip4/127.0.0.1/udp/1234/quic-v1/p2p/' + 'y'*32]}


def config(endpoint='https://hub.test'):
    return RuntimeConfiguration(
        values={'model_services': {'protocol': 1, 'policies': {'agent': policy(deployment())}}},
        credentials={'hub': RuntimeCredential(endpoint, 'owner-only')}, owner='owner',
        node_id='node', instance_id='model-control', revision='d'*64, generation=1, component='backend')


@pytest.mark.asyncio
async def test_prepared_owner_issues_generic_grant_and_preserves_model_config_checks(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://must-not-use.invalid')
    monkeypatch.setenv('FLEET_KEY', 'ambient-secret')
    calls = []
    row = deployment()
    def hub(request):
        assert request.headers['Authorization'] == 'Bearer owner-only'
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == '/api/model-services':
            return httpx.Response(200, json={'deployments': [row]})
        assert request.url.path in {'/api/fleet/apps/dependency-http-grants', '/api/fleet/apps/dependency-direct-grants'}
        body = json.loads(request.content)
        assert body['consumer'] == policy(row)['consumer']
        assert body['provider'] == row['binding']
        assert body['app_id'] == 'model-service' and body['rules'] == inference_rules()
        # Don't override the request's X-Model-Config with a newer directory
        # revision: the connector must reject calls made with stale metadata.
        assert 'headers' not in body
        if request.url.path.endswith('/dependency-direct-grants'):
            assert body['peer_id'] == 'z'*32
            return httpx.Response(200, json=direct_receipt(body))
        return httpx.Response(200, json=receipt(body))
    host = ModelDependencyHost(configuration=config(), transport=httpx.MockTransport(hub))
    try:
        async def invoke(operation, arguments):
            return await host.model_services_control(policy_id='agent', operation=operation, arguments=arguments)
        result = await invoke('connect', {'binding': row['binding']})
        assert result['status'] == 200
        assert set(result['result']) == {'origin', 'access_token', 'expires'}
        assert 'owner-only' not in json.dumps(result) and 'must-not-forward' not in json.dumps(result)
        direct = await invoke('direct_connect', {'binding': row['binding'], 'peer_id': 'z'*32})
        assert direct['status'] == 200
        assert set(direct['result']) == {'binding', 'peer_id', 'addresses', 'access_token', 'expires', 'transport'}
        assert 'must-not-forward' not in json.dumps(direct)
        assert len([r for r in calls if r[1].endswith('grants')]) == 2
        changed = {**row['binding'], 'generation': row['binding']['generation'] + 1}
        assert (await invoke('connect', {'binding': changed}))['status'] == 403
    finally:
        await host.close()
    with pytest.raises(AssemblyError):
        await host.model_services_control(policy_id='agent', operation='deployments', arguments={})


@pytest.mark.parametrize('change', [
    {'consumer': {}}, {'provider': {}}, {'expires': 1}, {'expires': True},
    {'access_token': 'bad'}, {'grant_id': 'a'*64}, {'origin': 'https://evil.test'},
])
@pytest.mark.asyncio
async def test_owner_does_not_forward_mismatched_grants(change):
    def hub(request):
        return httpx.Response(200, json=receipt(json.loads(request.content)) | change)
    client = ModelDependencyControl(owner='owner', credential=config().credentials['hub'], transport=httpx.MockTransport(hub))
    try:
        with pytest.raises(ControlError) as error:
            await client.issue_connection(consumer=policy(deployment())['consumer'], deployment=deployment())
        assert error.value.status == 502
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_grant_retry_identity_survives_host_restart_but_not_consumer_change(monkeypatch):
    monkeypatch.setattr('pantheon.platform.model_dependency_control.time.time', lambda: 30000)
    operations = []
    def hub(request):
        body = json.loads(request.content)
        operations.append(body['operation_id'])
        return httpx.Response(200, json=receipt(body))
    consumer = policy(deployment())['consumer']
    for selected in (consumer, consumer, {**consumer, 'generation': consumer['generation'] + 1}):
        client = ModelDependencyControl(owner='owner', credential=config().credentials['hub'], transport=httpx.MockTransport(hub))
        try:
            await client.issue_connection(consumer=selected, deployment=deployment())
        finally:
            await client.aclose()
    assert operations[0] == operations[1] and operations[0] != operations[2]


@pytest.mark.parametrize('status', [307, 401, 503])
@pytest.mark.asyncio
async def test_owner_never_redirects_or_replays_failed_authorization(status):
    calls = []
    def hub(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'https://foreign.test'}, text='private-owner-diagnostics')
    client = ModelDependencyControl(owner='owner', credential=config().credentials['hub'], transport=httpx.MockTransport(hub))
    try:
        with pytest.raises(ControlError) as error:
            await client.issue_connection(consumer=policy(deployment())['consumer'], deployment=deployment())
        assert len(calls) == 1
        assert 'private-owner' not in str(error.value)
        assert error.value.status == (502 if status == 307 else status)
    finally:
        await client.aclose()


@pytest.mark.parametrize('change', [{'binding': {}}, {'consumer': {}}, {'transport': 'relay'}, {'peer_id': 'bad'},
    {'expires': True}, {'addresses': []}, {'addresses': ['/p2p/wrong']},
    {'addresses': ['/ip4/127.0.0.1/udp/1234/p2p-circuit/p2p/' + 'y'*32]}])
@pytest.mark.asyncio
async def test_direct_issuer_rejects_foreign_scope_or_relay(change):
    def hub(request):
        assert request.url.path == '/api/fleet/apps/dependency-direct-grants'
        return httpx.Response(200, json=direct_receipt(json.loads(request.content)) | change)
    client = ModelDependencyControl(owner='owner', credential=config().credentials['hub'], transport=httpx.MockTransport(hub))
    try:
        with pytest.raises(ControlError) as error:
            await client.issue_connection(consumer=policy(deployment())['consumer'], deployment=deployment(), peer_id='z'*32)
        assert error.value.status == 502
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_owner_host_drains_admitted_control_before_closing():
    entered, release = asyncio.Event(), asyncio.Event()
    async def hub(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={'deployments': [deployment()]})
    host = ModelDependencyHost(configuration=config(), transport=httpx.MockTransport(hub))
    call = asyncio.create_task(host.model_services_control(policy_id='agent', operation='deployments', arguments={}))
    await entered.wait()
    close = asyncio.create_task(host.close())
    await asyncio.sleep(.01)
    assert not close.done()
    with pytest.raises(AssemblyError):
        await host.model_services_control(policy_id='agent', operation='deployments', arguments={})
    release.set()
    assert (await call)['status'] == 200
    await close


@pytest.mark.asyncio
async def test_generic_deployment_provisions_model_access_for_future_agent_generation(tmp_path):
    from test_app_deployment import Nodes, Authority, apps, finish_deployment
    from pantheon.apps.lifecycle import build_artifact
    nodes, recipe = Nodes(), apps()
    package = build_package(tmp_path/'model-package', 'linux-amd64')
    _, digest = build_artifact(package)
    nodes.manifests[digest] = {'protocol': 1, 'revision': digest,
        'manifest': json.loads((package/'app.json').read_text()),
        'definition': json.loads((package/'fleet.json').read_text())}
    agent = nodes.manifests['b'*64]
    agent['manifest']['dependencies']['model-services-control'] = {
        'range': '^0.1.0', 'uses': ['model-inference@1']}
    agent['definition']['components'][0]['configuration']['credentials']['model_services'] = {'required': True}
    recipe['agent']['components']['backend']['values']['agent']['models'] = {'model_services': 'model_services'}
    recipe['agent']['bindings']['model_services'] = {
        'app_id': 'model-services-control', 'component': 'backend',
        'provider': {'$app': 'model-access', 'component': 'backend', 'port': 'http'},
        'methods': {'model_services_control': {'arguments': ['operation', 'arguments'], 'bound': {'policy_id': 'agent'}}}}
    configured = policy(deployment())
    configured['consumer'] = {'$app': 'agent'}
    recipe['model-access'] = {
        'node_id': 'platform', 'revision': digest, 'scope': 'model-access', 'generation': 0, 'bindings': {},
        'components': {'backend': {'values': {'model_services': {'protocol': 1, 'policies': {'agent': configured}}},
            'credentials': {'hub': {'ref': 'private-vault-hub', 'endpoint': 'https://hub.test'}}}}}
    authority = Authority(nodes)
    issued = []
    issue = authority.issue
    async def observe(body):
        issued.append(deepcopy(body))
        return await issue(body)
    authority.issue = observe
    result = await finish_deployment(tmp_path/'journal', nodes, authority, recipe)
    prepared = result['prepared']
    owner_cfg = nodes.configurations[('platform', prepared['model-access']['instance_id'], 1)]['backend']
    agent_cfg = nodes.configurations[('worker', prepared['agent']['instance_id'], 1)]['backend']
    assert owner_cfg['values']['model_services']['policies']['agent']['consumer'] == {**prepared['agent'], 'generation': 2}
    assert agent_cfg['values']['agent']['models'] == {'model_services': 'model_services'}
    assert 'hub' not in agent_cfg.get('credentials', {})
    grant = next(item for item in issued if item['app_id'] == 'model-services-control')
    assert grant['consumer'] == {**prepared['agent'], 'generation': 2}
    assert grant['methods'] == {'model_services_control': {'arguments': ['operation', 'arguments'], 'bound': {'policy_id': 'agent'}}}
    assert agent_cfg['dependencies']['model_services']['provider'] == {'fleet_id': 'owner', **grant['provider']}
    assert len(nodes.calls) == 9  # one install/prepare/start for each ordinary App


@pytest.mark.parametrize('values,credentials', [
    ({}, {}), ({'model_services': {'protocol': True, 'policies': {}}}, {}),
    ({'model_services': {'protocol': 1, 'policies': {}}}, config().credentials),
    (config().values, {'hub': RuntimeCredential('http://hub.test', 'private-secret')}),
    (config().values, config().credentials | {'controller': RuntimeCredential('https://control.test', 'secret')}),
])
def test_invalid_owner_config_never_uses_ambient_fallback(values, credentials):
    with pytest.raises((AssemblyError, ValueError)) as error:
        ModelDependencyHost(configuration=replace(config(), values=values, credentials=credentials))
    assert 'private-secret' not in str(error.value)


@pytest.mark.asyncio
async def test_packaged_model_owner_real_tls_and_ordinary_host(tmp_path, tls_material):
    calls = []
    class Hub(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): self.dispatch()
        def do_POST(self): self.dispatch()
        def dispatch(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or 'null')
            calls.append((self.path, self.headers.get('Authorization'), body))
            if self.headers.get('Authorization') != 'Bearer owner-only':
                status, value = 403, {}
            elif self.path == '/hub/api/model-services':
                status, value = 200, {'deployments': [deployment()]}
            elif self.path == '/hub/api/fleet/apps/dependency-http-grants':
                status, value = 200, receipt(body)
            else:
                status, value = 404, {'private': 'not exposed'}
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Hub)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(tmp_path/'cert.pem', tmp_path/'key.pem')
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proc = None
    try:
        platform = 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64'
        package = build_package(tmp_path/'package', platform)
        from pantheon.apps.schema import parse_manifest
        from pantheon.apps.lifecycle import build_artifact
        assert parse_manifest(json.loads((package/'app.json').read_text())).id == 'model-services-control'
        payload, digest = build_artifact(package)
        assert payload and len(digest) == 64
        assert (package/'requirements.txt').read_text() == 'httpx==0.28.1\n'
        cfg = config(f'https://127.0.0.1:{server.server_port}/hub')
        cfg.values['model_services']['trust_roots_pem'] = (tmp_path/'cert.pem').read_text()
        snapshot = dict(protocol=1, values=cfg.values,
            credentials={k: {'endpoint': v.endpoint, 'key': v.key} for k,v in cfg.credentials.items()},
            **{k: getattr(cfg,k) for k in ('owner','node_id','instance_id','revision','generation','component')})
        prepared = tmp_path/'prepared.json'
        prepared.write_text(json.dumps(snapshot))
        env = {k:v for k,v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))}
        env.update(PANTHEON_APP_CONFIG=str(prepared), PANTHEON_FLEET_ID=cfg.owner,
            PANTHEON_NODE_ID=cfg.node_id, PANTHEON_INSTANCE_ID=cfg.instance_id,
            PANTHEON_APP_REVISION=cfg.revision, PANTHEON_INSTANCE_GENERATION=str(cfg.generation),
            PANTHEON_COMPONENT_NAME='backend', PANTHEON_PORT_HTTP='0', PANTHEON_APP_RPC_TOKEN='node-rpc-secret',
            FLEET_KEY='ambient-must-not-use', HTTPS_PROXY='http://must-not-use.invalid')
        boot = '''import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.agent', 'pantheon.chatroom', 'pantheon.factory', 'pantheon.team', 'pantheon.models.client', 'nats', 'litellm', 'loguru')):
   raise AssertionError('Heavy or ambient import in model owner: ' + name)
sys.meta_path.insert(0, Guard())
sys.path.insert(0, sys.argv[1])
sys.argv = ['host.py', *sys.argv[2:]]
runpy.run_module('host', run_name='__main__')
'''
        data = tmp_path/'data'
        data.mkdir()
        with (tmp_path/'process.log').open('w+') as log:
            proc = subprocess.Popen([sys.executable, '-c', boot, str(package/'.fleet-runtime'),
                'start', '--package', str(package), '--data', str(data)], cwd=tmp_path, env=env, stdout=log, stderr=log)
            endpoint = data/'backend-endpoint.json'
            for _ in range(200):
                if proc.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read())
                if endpoint.exists(): break
                await asyncio.sleep(.05)
            assert endpoint.exists()
            address = 'http://127.0.0.1:' + str(json.loads(endpoint.read_text())['port'])
            async with httpx.AsyncClient(base_url=address, trust_env=False, timeout=20) as client:
                assert (await client.get('/health')).json()['methods'] == ['model_services_control']
                body = {'method': 'model_services_control', 'args': {'policy_id':'agent',
                    'operation':'connect', 'arguments':{'binding': deployment()['binding']}}}
                assert (await client.post('/rpc', json=body)).status_code == 403
                client.headers['X-Fleet-RPC-Token'] = 'node-rpc-secret'
                response = await client.post('/rpc', json=body)
                assert response.status_code == 200, response.text
                result = response.json()['result']
                assert result['status'] == 200, result
                assert set(result['result']) == {'origin', 'access_token', 'expires'}
                assert 'owner-only' not in response.text and 'must-not-forward' not in response.text
                body['args']['operation'], body['args']['arguments'] = 'configure', {}
                assert (await client.post('/rpc', json=body)).json()['result']['status'] == 403
                assert (await client.post('/_fleet/drain')).json()['safe_to_stop']
                assert (await client.post('/rpc', json=body)).status_code == 400
            assert len(calls) == 2
    finally:
        if proc:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 10)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait, 5)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
