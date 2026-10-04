"""Prepared registration uses the original Connector and CAS directory client."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.client import ModelServices, ControlError
from pantheon.models.manager import ModelServiceManager
from test_model_services import connector_module, serve
from test_model_connector_package import prepared, configuration
from http.server import BaseHTTPRequestHandler


@pytest.fixture
def rig(tmp_path, monkeypatch):
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/v1/models'
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'data': [
                {'id': 'chat', 'context_length': 8192, 'supported_parameters': ['tools']},
                {'id': 'bare'}]}).encode())
        def do_POST(self):
            raise AssertionError('Registration must not submit inference')
    rows, writes, calls = [], [], []
    lose_reply = [False]
    def hub(request):
        assert request.headers['Authorization'] == 'Bearer owner-fixture'
        if request.method == 'GET':
            return httpx.Response(200, json={'deployments': deepcopy(rows)})
        assert request.method == 'PUT' and request.url.path == '/api/model-services/prepared'
        row = json.loads(request.content)
        assert row['revision'] == 0
        if rows: return httpx.Response(409, json={'detail': 'changed'})
        row['revision'] = 1
        rows.append(row); writes.append(deepcopy(row))
        if lose_reply[0]: raise httpx.ReadTimeout('lost acknowledgement')
        return httpx.Response(200, json=row)
    client = ModelServices(hub='https://hub.test', token='owner-fixture', transport=httpx.MockTransport(hub))
    binding = dict(node_id='node', instance_id='instance', revision='a'*64, generation=2, component='backend', port='http')
    instance = dict(instance_id='instance', digest='a'*64, generation=2, scope='model-prepared', app_id='model-service', state='ready')
    async def status(self, node):
        calls.append('fleet.status')
        return {'instances': {'instance': deepcopy(instance)}}
    monkeypatch.setattr(FleetLifecycle, 'status', status)
    with serve(Provider) as upstream:
        value = dict(engine='api', endpoint=upstream)
        connector = connector_module.Connector(tmp_path/'connector')
        prepared.initialize(connector, configuration(value))
        with serve(connector_module.handler(connector)) as endpoint:
            manager = ModelServiceManager(client=client, resolver=object())
            async def node(node_id):
                return {'node_id': node_id, 'name': 'Test node'}
            manager.node = node
            async def rpc(b, method, args=None):
                assert b == binding
                assert method in {'preview_configuration', 'status', 'activity', 'discover'}
                calls.append(method)
                with httpx.Client() as http:
                    response = http.post(endpoint+'/rpc', headers={'X-Fleet-RPC-Token':connector.rpc_token},
                                         json={'method':method, 'args':args or {}})
                    response.raise_for_status()
                    return response.json()
            manager.rpc = rpc
            yield SimpleNamespace(manager=manager, client=client, binding=binding, instance=instance, value=value,
                                  connector=connector, rows=rows, writes=writes, calls=calls, lose_reply=lose_reply)
    asyncio.run(client.aclose())


def register(rig, **changes):
    args = dict(deployment_id='prepared', name='Prepared', binding=rig.binding, configuration=rig.value,
                models=[{'id':'chat', 'context_limit':4096}])
    args.update(changes)
    return asyncio.run(rig.manager.register_prepared(**args))


def test_register_publishes_reported_capabilities_and_retry_does_not_write(rig):
    before = rig.connector.path.stat().st_mtime_ns
    row = register(rig)
    assert row['models'] == [dict(id='chat', name='chat', operations=['text'], compute='provider',
                                  tools=True, reasoning=False, context=4096, context_limit=4096)]
    # Hub normalizes optional unset fields to null; a retry still matches.
    rig.rows[0]['models'][0].update(vision=None, structured_output=None)
    assert register(rig) == rig.rows[0]
    assert len(rig.writes) == 1 and rig.connector.path.stat().st_mtime_ns == before
    assert rig.calls.count('fleet.status') == 4


def test_lost_save_acknowledgement_recovers_by_reading_directory(rig):
    rig.lose_reply[0] = True
    with pytest.raises(httpx.ReadTimeout): register(rig)
    assert len(rig.rows) == 1
    assert register(rig)['revision'] == 1
    assert len(rig.writes) == 1


@pytest.mark.parametrize('field,value', [('generation',3), ('scope','wrong'), ('digest','b'*64),
                                         ('app_id','agent'), ('state','stopped'), ('state','starting')])
def test_no_registration_or_lifecycle_changes_for_wrong_instance(rig, field, value):
    rig.instance[field] = value
    with pytest.raises(ValueError): register(rig)
    assert not rig.writes and rig.calls == ['fleet.status']


@pytest.mark.parametrize('change', ['name', 'binding', 'config', 'models', 'mode', 'recovery', 'stop'])
def test_retry_never_overwrites_owner_changes(rig, change):
    register(rig)
    row = rig.rows[0]
    if change == 'name': row['name'] = 'Renamed'
    elif change == 'binding': row['binding']['generation'] = 3
    elif change == 'config': row['config_revision'] = 'b'*64
    elif change == 'models': row['models'] = []
    elif change == 'mode': row['mode'] = 'managed'
    elif change == 'recovery': row['recovery'] = {'phase':'pending'}
    elif change == 'stop': row['state'] = 'stopped'
    before = deepcopy(row)
    with pytest.raises(ValueError, match='differs from the directory'): register(rig)
    assert rig.rows == [before] and len(rig.writes) == 1


@pytest.mark.parametrize('change', ['config', 'drain', 'maintenance', 'lifetime', 'generation-during-discovery'])
def test_connector_changes_before_publication_fail_closed(rig, change):
    original = rig.manager.rpc
    async def rpc(binding, method, args=None):
        result = await original(binding, method, args)
        if method == 'discover':
            if change == 'config': rig.connector.config['endpoint'] += '/different'
            elif change == 'drain': rig.connector.accepting = False
            elif change == 'maintenance': rig.connector.maintenance = True
            elif change == 'lifetime': rig.connector.lifetime_pending = True
            else: rig.instance['generation'] += 1
        return result
    rig.manager.rpc = rpc
    with pytest.raises(ValueError): register(rig)
    assert not rig.writes


@pytest.mark.parametrize('models', [[{'id':'missing'}], [{'id':'chat'},{'id':'chat'}],
                                     [{'id':'bare'}], [{'id':'chat','tools':True}],
                                     [{'id':'chat','context_limit':True}]])
def test_selection_is_explicit_and_never_guesses_context_or_capabilities(rig, models):
    with pytest.raises(ValueError): register(rig, models=models)
    assert not rig.writes


def test_empty_publication_can_be_managed_later(rig):
    assert register(rig, models=[])['models'] == []


@pytest.mark.parametrize('extra', [{'credential_file':'/secret'}, {'api_key':'secret'}, {'managed':{}}])
def test_registration_never_accepts_inline_or_legacy_credentials(rig, extra):
    with pytest.raises(ValueError): register(rig, configuration={**rig.value, **extra})
    assert not rig.calls and not rig.writes


def test_concurrent_registration_conflict_is_not_retried_or_overwritten(rig):
    save = rig.client.save
    async def competing_save(row):
        winner = {**deepcopy(row), 'revision':1, 'name':'Concurrent owner choice'}
        rig.rows.append(winner)
        return await save(row)
    rig.client.save = competing_save
    with pytest.raises(ControlError) as error: register(rig)
    assert error.value.status == 409
    with pytest.raises(ValueError, match='differs from the directory'): register(rig)
    assert not rig.writes and rig.rows[0]['name'] == 'Concurrent owner choice'


def test_expected_configuration_is_checked_without_reconfiguring(rig):
    before = rig.connector.path.read_bytes()
    with pytest.raises(ValueError, match='configuration or admission'):
        register(rig, configuration={**rig.value, 'endpoint':'https://different.test/v1'})
    assert rig.connector.path.read_bytes() == before and not rig.writes
    assert 'discover' not in rig.calls
