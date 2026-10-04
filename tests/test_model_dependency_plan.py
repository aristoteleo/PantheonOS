"""Owner catalog selection must use existing routing and exact App identities."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.agent_deployment import compose_selected_deployment
from pantheon.models.client import ModelServices, model_ref
from pantheon.models.dependency_plan import plan_dependency
from pantheon.models.dependency_service import ModelServiceControl
from pantheon.platform.models_api import ModelServicesAPI
from test_agent_deployment_recipe import inputs
from test_model_services import deployment


def directory():
    mac = deployment()
    mac['models'][0]['context'] = 8192
    # Selecting one model grants its connector, not an invented per-model ACL.
    mac['models'].append({'id': 'other', 'operations': ['embedding']})
    gpu = deepcopy(mac)
    gpu.update(deployment_id='gpu', node_id='gpu-node', name='GPU')
    gpu['binding'].update(node_id='gpu-node', instance_id='gpu-instance')
    gpu['models'] = [dict(gpu['models'][0], context=32768)]
    unrelated = deepcopy(gpu)
    unrelated['deployment_id'] = 'unselected'
    route = dict(route_id='balanced', name='Balanced', revision=5,
        requires={'operation': 'text'}, transport='relay_allowed', selection='ready_first',
        fallback='preflight', allowed_nodes=['mac-node', 'gpu-node'],
        candidates=[{'deployment_id': name, 'model_id': 'example:8b'} for name in ('mac', 'gpu')])
    return [mac, gpu, unrelated], [route]


@pytest.fixture
def owner_directory():
    rows, routes = directory()
    calls = []
    def respond(request):
        calls.append((request.method, request.url.path))
        assert request.headers['Authorization'] == 'Bearer owner-fixture'
        assert request.method == 'GET', 'Selection must not wake/install/issue grants/infer'
        if request.url.path == '/api/model-services':
            return httpx.Response(200, json={'deployments': rows})
        assert request.url.path == '/api/model-services/routes'
        return httpx.Response(200, json={'routes': routes})
    return rows, routes, calls, respond


@pytest.mark.asyncio
async def test_selected_refs_pin_original_bindings_and_whole_route_without_mutations(owner_directory):
    rows, routes, calls, respond = owner_directory
    original = deepcopy([rows, routes])
    client = ModelServices('https://hub.test', 'owner-fixture', httpx.MockTransport(respond))
    try:
        plan = await plan_dependency(client, references=[model_ref('mac', 'example:8b'), 'fleet-route://balanced'])
    finally:
        await client.aclose()
    assert [rows, routes] == original
    assert calls == [('GET', '/api/model-services'), ('GET', '/api/model-services/routes')]
    assert plan['policy'] == {'deployments': {r['deployment_id']: r['binding'] for r in rows[:2]},
                              'routes': {'balanced': 5}, 'allow_wake': False}
    assert plan['selected'][1]['context'] == 8192
    assert plan['selected'][1]['capabilities']['tools'] is True
    assert plan['authorization'][0]['scope'] == 'connector'
    assert model_ref('mac', 'other') in plan['authorization'][0]['published_models']
    assert 'owner-fixture' not in json.dumps(plan)
    assert 'unselected' not in json.dumps(plan)

    # The generated policy is enforced by the existing control provider.
    owner = SimpleNamespace(deployments=AsyncMock(side_effect=lambda: deepcopy(rows)),
                            routes=AsyncMock(side_effect=lambda: deepcopy(routes)))
    control = ModelServiceControl(owner, policies={'agent': {
        'consumer': {'node_id': 'worker', 'instance_id': 'agent', 'revision': 'b'*64, 'generation': 1},
        **plan['policy']}})
    async def call(operation):
        return await control.model_services_control(policy_id='agent', operation=operation, arguments={})
    assert len((await call('deployments'))['result']['deployments']) == 2
    routes[0]['revision'] += 1
    assert (await call('routes'))['result']['routes'] == []
    rows[0]['binding']['generation'] += 1
    assert [r['deployment_id'] for r in (await call('deployments'))['result']['deployments']] == ['gpu']


@pytest.mark.asyncio
async def test_platform_composes_reviewable_regular_recipe_from_quality_tiers(tmp_path, owner_directory):
    rows, _, calls, respond = owner_directory
    spec = inputs(tmp_path)
    del spec['models']
    original = deepcopy(spec)
    client = ModelServices('https://hub.test', 'owner-fixture', httpx.MockTransport(respond))
    api = ModelServicesAPI()
    api._model_services = SimpleNamespace(client=client)
    tiers = {'normal': model_ref('mac', 'example:8b'), 'low': model_ref('mac', 'example:8b'),
             'high': 'fleet-route://balanced'}
    try:
        result = await api.model_services_agent_preset(spec, tiers, allow_wake=True)
    finally:
        await client.aclose()
    assert spec == original
    assert result['success'] is True
    recipe = result['recipe']
    from pantheon.apps.deployment import deployment_recipe
    assert deployment_recipe(**recipe)[1] == ['allocator', 'model-access', 'agent']
    config = recipe['apps']['agent']['components']['backend']
    assert config['values']['agent']['models'] == {
        'providers': {'openai': 'provider'}, 'model_services': 'model_services', 'fleet_tiers': tiers}
    policy = recipe['apps']['model-access']['components']['backend']['values']['model_services']['policies']['agent']
    assert policy == {'consumer': {'$app': 'agent'}, **result['model_selection']['policy']}
    assert policy['deployments']['gpu'] == rows[1]['binding']
    assert len(result['model_selection']['selected']) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing', 'stopped', 'binding-node', 'generation', 'route-missing',
    'route-revision', 'route-candidate', 'duplicate-deployment', 'duplicate-model', 'duplicate-route'])
async def test_selection_rejects_unusable_or_ambiguous_directory(change):
    rows, routes = directory()
    ref = model_ref('mac', 'example:8b')
    if change == 'missing': rows.pop(0)
    elif change == 'stopped': rows[0]['state'] = 'stopped'
    elif change == 'binding-node': rows[0]['binding']['node_id'] = 'elsewhere'
    elif change == 'generation': rows[0]['binding']['generation'] = True
    elif change == 'duplicate-deployment': rows.append(deepcopy(rows[0]))
    elif change == 'duplicate-model': rows[0]['models'].append(deepcopy(rows[0]['models'][0]))
    else:
        ref = 'fleet-route://balanced'
        if change == 'route-missing': routes.clear()
        elif change == 'route-revision': routes[0]['revision'] = True
        elif change == 'route-candidate': routes[0]['candidates'][0]['model_id'] = 'gone'
        else: routes.append(deepcopy(routes[0]))
    client = SimpleNamespace(deployments=AsyncMock(return_value=rows), routes=AsyncMock(return_value=routes))
    with pytest.raises(AssemblyError, match='unavailable or.*invalid binding'):
        await plan_dependency(client, references=[ref])


@pytest.mark.asyncio
@pytest.mark.parametrize('refs', [[], ['openai/x'], ['fleet-model://mac/example:8b'],
    ['fleet-route://balanced?token=secret'], ['fleet-route://balanced']*2, [None]])
async def test_invalid_selection_makes_no_directory_calls(refs):
    client = SimpleNamespace(deployments=AsyncMock())
    with pytest.raises(AssemblyError):
        await plan_dependency(client, references=refs)
    client.deployments.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['no-tools', 'unknown-context', 'image-only', 'context-bool',
    'missing-normal', 'bad-tier', 'manual-policy', 'conflicting-tiers'])
async def test_agent_selection_cannot_bypass_agent_catalog_requirements(tmp_path, change):
    rows, _ = directory()
    model = rows[0]['models'][0]
    spec = inputs(tmp_path)
    del spec['models']
    tiers = {'normal': model_ref('mac', 'example:8b')}
    if change == 'no-tools': model['tools'] = None
    elif change == 'unknown-context': model.pop('context')
    elif change == 'image-only': model['operations'] = ['image']
    elif change == 'context-bool': model['context'] = True
    elif change == 'missing-normal': tiers = {'high': tiers['normal']}
    elif change == 'bad-tier': tiers['vision'] = tiers['normal']
    elif change == 'manual-policy': spec['models'] = {}
    else: spec['agent']['models']['fleet_tiers'] = {'normal': 'fleet-route://different'}
    client = SimpleNamespace(deployments=AsyncMock(return_value=rows))
    with pytest.raises(AssemblyError):
        await compose_selected_deployment(client, spec=spec, fleet_tiers=tiers)
    if change in ('missing-normal', 'bad-tier', 'manual-policy', 'conflicting-tiers'):
        client.deployments.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_and_directory_errors_do_not_leak_or_retry():
    client = SimpleNamespace(deployments=AsyncMock(side_effect=RuntimeError('private upstream response')))
    with pytest.raises(AssemblyError) as error:
        await plan_dependency(client, references=[model_ref('mac', 'example:8b')])
    assert 'private upstream response' not in str(error.value)
    assert client.deployments.await_count == 1
    client.deployments.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await plan_dependency(client, references=[model_ref('mac', 'example:8b')])


def test_platform_preset_rpc_runs_with_agent_imports_blocked(tmp_path):
    spec = inputs(tmp_path)
    del spec['models']
    rows, _ = directory()
    code = '''
import asyncio, importlib.abc, json, sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        for prefix in ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team', 'pantheon.factory'):
            if fullname == prefix or fullname.startswith(prefix + '.'):
                raise AssertionError('Platform imported Agent: ' + fullname)
sys.meta_path.insert(0, NoAgent())
from pantheon.platform.service import PlatformService
async def check():
    spec, rows = json.loads(sys.stdin.read())
    platform = PlatformService()
    platform._model_services = SimpleNamespace(resolver=None,
        client=SimpleNamespace(deployments=AsyncMock(return_value=rows), aclose=AsyncMock()))
    assert 'model_services_agent_preset' in platform.functions
    result = await platform.model_services_agent_preset(spec, {'normal': 'fleet-model://mac/example%3A8b'})
    assert result['success']
    assert result['recipe']['apps']['agent']['bindings']['model_services']['app_id'] == 'model-services-control'
    await platform.cleanup()
asyncio.run(check())
'''
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PANTHEON_', 'FLEET_', 'NATS_'))}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, '-c', code], input=json.dumps([spec, rows]),
                            text=True, capture_output=True, env=env, cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
