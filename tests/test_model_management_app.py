"""Installed management App with real Fleet/Connector and a local Hub API fixture."""
import asyncio
import base64
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.local_agent import native_platform
from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.apps.tool_profiles import compile_tool_profile
from pantheon.models.management_app import create_service, PreparedModelManagement
from pantheon.models.management_package import build_package
from pantheon.models.management_state import ManagementState
from pantheon.models.manager import ModelServiceManager
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries, assert_stopped
from test_model_services import serve


def test_package_has_original_tools_catalogs_and_no_agent_runtime(tmp_path):
    root = build_package(tmp_path/'release', native_platform())
    manifest = json.loads((root/'app.json').read_text())
    profile, policy, dependency = compile_tool_profile(manifest, alias='models', uses=['model-management@1'])
    assert len(profile['functions']) == 9
    assert 'use_fleet_model' not in policy['methods']
    vendor = root/'backend/_vendor'
    assert not (vendor/'pantheon/agent.py').exists() and not (vendor/'pantheon/settings.py').exists()
    code = '''import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name in ('pantheon.agent', 'pantheon.team', 'pantheon.settings', 'pantheon.chatroom'):
   raise AssertionError('Unexpected embedded runtime: '+name)
sys.meta_path.insert(0, Boundary())
from pantheon.models.management_app import create_service
from pantheon.models.management_tools import ModelManagementToolSet
from pantheon.models.managed import connector_root, module, package
from pantheon.models.model_deploy import ollama_catalog, sglang_architectures
assert connector_root().is_relative_to(sys.argv[1])
assert module('llm_models').catalog() and ollama_catalog() and sglang_architectures()
from pantheon.models.group_package import GroupPackageStore
assert module('server').validate_config({'engine':'api','endpoint':'http://127.0.0.1:1'})
assert 'use_fleet_model' not in ModelManagementToolSet(object()).functions
'''
    import os
    result = subprocess.run([sys.executable, '-I', '-c', code, str(vendor)], cwd=tmp_path,
        env={**os.environ, 'PANTHEON_APPS_ROOT': str(tmp_path/'does-not-exist')}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_prepared_shutdown_drains_before_closing_connections(tmp_path):
    state = tmp_path/'private'; state.mkdir(mode=0o700)
    controller = SimpleNamespace(request=AsyncMock(), close=AsyncMock())
    owned = ManagementState(state, controller)
    client = SimpleNamespace(aclose=AsyncMock())
    resolver = SimpleNamespace(close=AsyncMock())
    service = PreparedModelManagement(ModelServiceManager(client, resolver, management=owned), controller)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def work():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            await release.wait()
            finished.set()
    owned.engine_tasks('deploy')['example'] = asyncio.create_task(work())
    await entered.wait()
    closing = asyncio.create_task(service.cleanup())
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done() and not client.aclose.called and not controller.close.called
    release.set()
    with pytest.raises(asyncio.CancelledError): await closing
    await service.cleanup()
    assert finished.is_set()
    client.aclose.assert_awaited_once(); resolver.close.assert_awaited_once(); controller.close.assert_awaited_once()
    with pytest.raises(RuntimeError, match='closing'): await service.model_services_overview()


@pytest.fixture
def hub_and_engine():
    rows, requests = {}, []
    class Hub(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, value, status=200):
            self.send_response(status); self.end_headers(); self.wfile.write(json.dumps(value).encode())
        def authorized(self):
            if self.headers.get('Authorization') == 'Bearer owner-hub-key': return True
            self.reply({'detail': 'unauthorized'}, 401); return False
        def do_GET(self):
            if self.path == '/v1/models': return self.reply({'data': [{'id': 'example', 'context_length': 8192}]})
            if not self.authorized(): return
            requests.append(('GET', self.path))
            if self.path == '/api/model-services': return self.reply({'deployments': list(rows.values())})
            if self.path == '/api/model-services/routes': return self.reply({'routes': []})
            if self.path == '/api/model-services/modal-gpu': return self.reply({'services': []})
            self.reply({'detail': 'unsupported'}, 404)
        def do_PUT(self):
            if not self.authorized(): return
            row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            previous = rows.get(row['deployment_id'])
            if previous and previous['revision'] != row['revision']: return self.reply({'detail': 'conflict'}, 409)
            row['revision'] += 1; rows[row['deployment_id']] = row
            requests.append(('PUT', self.path, row['state']))
            self.reply(row)
    with serve(Hub) as endpoint:
        yield endpoint, rows, requests


@pytest.mark.asyncio
async def test_installed_management_controls_original_connector_and_reopens(tmp_path, binaries, hub_and_engine):
    import nats
    from pantheon.apps.client import AppClient
    from pantheon.apps.credentials import RemoteAppCredentialVault
    from pantheon.apps.lifecycle import FleetLifecycle, ConfigurationBusy
    from pantheon.apps.resolver import AppInstanceResolver
    endpoint, rows, requests = hub_and_engine
    package = build_package(tmp_path/'management', native_platform())
    workspace = tmp_path/'workspace'; workspace.mkdir()
    async with LocalFleet(tmp_path/'profile', binaries, workspace=workspace) as runtime:
        info, children = runtime.coordinates, list(runtime._children)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(workspace), connection=nc)
        wire, client = FleetLifecycle(resolver), AppClient(nc, info.fleet_id)
        owned = []
        async def action(digest, name, scope, generation=0, **kwargs):
            receipt = await wire.submit(info.node_id, name, digest, scope=scope, generation=generation, **kwargs)
            async with asyncio.timeout(180):
                while True:
                    status = await wire.status(info.node_id)
                    op = status['operations'][receipt['request']['operation_id']]
                    if op['state'] == 'succeeded':
                        return next((i for i in status['instances'].values() if i['digest'] == digest and i['scope'] == scope), None)
                    assert op['state'] in ('queued', 'running'), op
                    await asyncio.sleep(.1)
        async def invoke(app, current, method, **args):
            result = await client.invoke(info.node_id, app, {'instance_id': current['instance_id'],
                'revision': current['digest'], 'generation': current['generation']}, method, args, 60)
            assert not result.get('error'), result
            result = result['response']
            assert not result.get('error'), result
            if app == 'model-services-management':
                assert result.get('success'), result
                return result['result']
            return result
        try:
            # Install the exact original Connector carried inside the new package.
            connector = package/'backend/_vendor/pantheon/models/_connector'
            cd = await wire.stage(info.node_id, connector); owned.append((cd, 'model-example'))
            await action(cd, 'install', 'model-example')
            ci = await action(cd, 'start', 'model-example')
            configured = await invoke('model-service', ci, 'configure', config={'engine': 'api', 'endpoint': endpoint})
            assert configured['config_revision']
            rows['example'] = dict(deployment_id='example', name='Existing API', engine='api', mode='attached',
                state='ready', revision=1, node_id=info.node_id, node_name='Local', models=[],
                config_revision=configured['config_revision'], binding=dict(node_id=info.node_id,
                instance_id=ci['instance_id'], revision=cd, generation=ci['generation'], component='backend', port='http'))
            vault = RemoteAppCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id)
            refs = {}
            for alias, origin, key in (('hub', endpoint, 'owner-hub-key'),
                ('fleet', info.nats, base64.b64encode(info.credentials.read_bytes()).decode()),
                ('controller', info.controller, (runtime.root/'owner.key').read_text().strip())):
                ref = 'node-secret://model-management-'+alias
                await vault.ensure_async(ref, origin, key)
                refs[alias] = {'ref': ref, 'endpoint': origin}
            digest = await wire.stage(info.node_id, package); owned.append((digest, 'management'))
            current = await action(digest, 'install', 'management')
            previous = None
            for cycle in (1, 2):
                operation = 'prepare-model-manager-'+str(cycle)
                prepared = await action(digest, 'prepare_start', 'management', current['generation'] if current else 0,
                                        operation_id=operation)
                async with asyncio.timeout(10):
                    while True:
                        try:
                            await wire.configure(info.node_id, instance_id=prepared['instance_id'], revision=digest,
                                generation=prepared['generation'], preparation_id=operation, components={'backend': {
                                    'values': {'model_management': {'bus': {'auth': 'creds-base64'},
                                        'controller_ca_pem': info.ca_certificate.read_text()}}, 'credentials': refs}})
                            break
                        except ConfigurationBusy: await asyncio.sleep(.05)
                current = await action(digest, 'start', 'management', prepared['generation'], start_preparation_id=operation)
                assert current['state'] == 'ready' and previous in (None, current['instance_id'])
                previous = current['instance_id']
                overview = await invoke('model-services-management', current, 'model_services_overview')
                assert overview['catalog'] and overview['deployments'][0]['deployment_id'] == 'example'
                options = await invoke('model-services-management', current, 'model_options', node_id=info.node_id)
                assert options  # Original options derive capabilities from the actual Fleet node.
                denied = await invoke('model-services-management', current, 'modal_gpu_start', service_id='not-approved')
                assert denied['started'] is False
                for running in (False, True):
                    result = await invoke('model-services-management', current, 'model_service_set_running',
                                          deployment_id='example', running=running)
                    assert result['state'] == ('ready' if running else 'stopped'), result
                    state = await wire.status(info.node_id)
                    instance = state['instances'][rows['example']['binding']['instance_id']]
                    assert instance['state'] == ('ready' if running else 'stopped')
                current = await action(digest, 'stop', 'management', current['generation'])
                assert current['state'] == 'stopped'
                assert not list((runtime.root/'node').rglob('.app-bus-*.creds'))
                # Closing management must leave the existing model Connector running.
                snapshot = await wire.status(info.node_id)
                assert snapshot['instances'][rows['example']['binding']['instance_id']]['state'] == 'ready'
        finally:
            try:
                for digest, scope in reversed(owned):
                    status = await wire.status(info.node_id)
                    instance = next((i for i in status['instances'].values() if i['digest'] == digest and i['scope'] == scope), None)
                    if instance and instance['state'] != 'stopped': await action(digest, 'stop', scope, instance['generation'])
            finally:
                await resolver.close()
    assert_stopped(children, info)
    assert any(call[:2] == ('PUT', '/api/model-services/example') for call in requests)


@pytest.mark.asyncio
async def test_failed_prepared_connect_closes_clients_without_ambient_fallback(tmp_path, monkeypatch):
    from pantheon.models import management_app as app
    workspace = tmp_path/'workspace'; workspace.mkdir()
    state = tmp_path/'state'; state.mkdir(mode=0o700)
    for name in ('FLEET_KEY', 'PANTHEON_HUB_URL', 'FLEET_NATS_URL', 'FLEET_CONTROLLER_URL'):
        monkeypatch.setenv(name, 'must-not-borrow')
    clients, controllers = [], []
    original_hub, original_controller = app._hub, app.Controller
    def hub(*args):
        result = original_hub(*args); clients.append(result); return result
    def controller(*args):
        result = original_controller(*args); controllers.append(result); return result
    async def fail(*args, **kwargs): raise RuntimeError('explicit bus unavailable')
    monkeypatch.setattr(app, '_hub', hub)
    monkeypatch.setattr(app, 'Controller', controller)
    monkeypatch.setattr(app.OwnedBus, 'connect', fail)
    configuration = RuntimeConfiguration({'model_management': {'bus': {'auth': 'token'}}}, {
        'hub': RuntimeCredential('http://127.0.0.1:1', 'hub-owner'),
        'controller': RuntimeCredential('http://127.0.0.1:2', 'controller-owner'),
        'fleet': RuntimeCredential('nats://127.0.0.1:3', 'bus-owner')},
        'manager', 'a'*64, 1, 'backend', 'owner', 'node')
    with pytest.raises(RuntimeError, match='explicit bus'):
        await create_service(configuration, workspace, state)
    assert len(clients) == len(controllers) == 1
    assert clients[0].hub == 'http://127.0.0.1:1' and clients[0].token == 'hub-owner'
    assert all(pool.closed for pool in (clients[0].control_http, clients[0].relay_http, clients[0].cancel_http))
    assert controllers[0]._http.is_closed
    assert not list(state.iterdir())


@pytest.mark.parametrize('endpoint,key,ca', [
    ('http://remote.invalid', 'key', None), ('https://user@example.com', 'key', None),
    ('https://example.com/path', 'key', None), ('https://example.com', '', None),
    ('https://example.com', 'key', 'not a certificate'), ('http://127.0.0.1', 'key', 'invalid'),
])
def test_management_hub_rejects_ambiguous_identity(endpoint, key, ca):
    from pantheon.models.management_app import _hub
    with pytest.raises(ValueError, match='explicit Hub'):
        _hub(RuntimeCredential(endpoint, key), ca)
