from copy import deepcopy

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment import deployment_recipe, _resolve
from pantheon.models.dependency_composition import bind_model_dependency
from pantheon.models.dependency_service import ModelServiceControl


def apps():
    return {name: {'node_id': 'node', 'revision': digit*64, 'scope': name, 'generation': 0,
                   'bindings': {}, 'components': {'backend': {'values': (
                       {'model_services': {'protocol': 1, 'policies': {}}} if name == 'shared-models' else {}),
                       'credentials': ({'hub': {'ref': 'node-secret://hub', 'endpoint': 'https://hub.test'}}
                                       if name == 'shared-models' else {})}}}
            for name, digit in [('files', 'a'), ('notebook', 'b'), ('shared-models', 'c')]}


def policy():
    return {'deployments': {'local': {'node_id': 'gpu', 'instance_id': 'connector', 'revision': 'd'*64,
                                    'generation': 2, 'component': 'backend', 'port': 'http'}},
            'routes': {'approved': 2}, 'allow_wake': False}


def bind(value, **changes):
    return bind_model_dependency(value, **({'consumer': 'files', 'provider': 'shared-models',
                                          'slot': 'models', 'policy_id': 'files', 'policy': policy()} | changes))


def test_independent_shared_control_pins_each_consumer_and_never_copies_owner_credentials():
    original = apps(); saved = deepcopy(original)
    result = bind(original)
    result = bind(result, consumer='notebook', policy_id='notebook')
    assert original == saved
    assert deployment_recipe('owner', 'start', result)[1] == ['shared-models', 'files', 'notebook']
    for name in ('files', 'notebook'):
        consumer = result[name]
        assert not consumer['components']['backend']['credentials']
        grant = consumer['bindings']['models']
        assert grant['methods']['model_services_control'] == {
            'arguments': ['operation', 'arguments'], 'bound': {'policy_id': name}}
        assert grant['provider']['$app'] == 'shared-models'
    prepared = {name: {'node_id': 'node', 'instance_id': name, 'revision': digit*64, 'generation': 1}
                for name, digit in [('files', 'a'), ('notebook', 'b'), ('shared-models', 'c')]}
    resolved = _resolve(result['shared-models']['components']['backend']['values']['model_services'], prepared)
    ModelServiceControl(None, policies=resolved['policies'])
    assert resolved['policies']['files']['consumer']['generation'] == 2
    assert resolved['policies']['notebook']['consumer']['instance_id'] == 'notebook'


@pytest.mark.parametrize('change', ['credential', 'binding', 'policy', 'same-app', 'cycle', 'bad-model', 'bad-route', 'inline-secret'])
def test_conflicts_and_invalid_policies_are_rejected_without_mutation(change):
    value = apps(); arguments = {}
    if change == 'credential': value['files']['components']['backend']['credentials']['models'] = {'key': 'private'}
    elif change == 'binding': value['files']['bindings']['models'] = {}
    elif change == 'policy': value['shared-models']['components']['backend']['values']['model_services']['policies']['files'] = {}
    elif change == 'same-app': arguments['provider'] = 'files'
    elif change == 'cycle': value['shared-models']['bindings']['loop'] = {'provider': {'$app': 'files'}}
    elif change == 'bad-model': arguments['policy'] = {**policy(), 'deployments': {'local': {'$model': 'bad alias'}}}
    elif change == 'bad-route': arguments['policy'] = {**policy(), 'routes': {'approved': True}}
    else: arguments['policy'] = {**policy(), 'api_key': 'private'}
    before = deepcopy(value)
    with pytest.raises(AssemblyError) as error:
        bind(value, **arguments)
    assert 'private' not in str(error.value) and value == before


def test_bootstrap_keeps_exact_model_reference_without_discovery():
    chosen = {**policy(), 'deployments': {'local': {'$model': 'connector'}}}
    result = bind(apps(), policy=chosen)
    config = result['shared-models']['components']['backend']['values']['model_services']
    assert config['policies']['files']['deployments'] == chosen['deployments']


def selected_spec(tmp_path):
    from test_agent_deployment_recipe import inputs
    value = inputs(tmp_path)
    value.pop('models')
    value['provider_apps'] = apps()
    value['model_consumers'] = {'files': {'provider': 'shared-models', 'slot': 'models',
        'policy_id': 'files', 'references': ['fleet-model://gpu/example%3A8b'], 'allow_wake': False}}
    return value


@pytest.mark.asyncio
async def test_owner_preset_selects_separate_models_and_preserves_them_when_agent_changes(tmp_path):
    import httpx
    from pantheon.models.client import ModelServices
    from pantheon.apps.agent_deployment import compose_selected_deployment, update_selected_deployment
    from test_model_dependency_plan import directory
    rows, routes = directory()
    calls = []
    def respond(request):
        assert request.method == 'GET'
        calls.append(request.url.path)
        return httpx.Response(200, json={'deployments': rows} if request.url.path.endswith('model-services') else {'routes': routes})
    client = ModelServices('https://hub.test', 'owner-fixture', httpx.MockTransport(respond))
    spec = selected_spec(tmp_path); before = deepcopy(spec)
    try:
        result = await compose_selected_deployment(client, spec=spec, fleet_tiers={'normal': 'fleet-model://mac/example%3A8b'})
        assert spec == before
        assert set(result['model_selection']['policy']['deployments']) == {'mac'}
        review = result['dependency_model_selections']['files']
        assert set(review['policy']['deployments']) == {'gpu'}
        assert review['authorization'][0]['scope'] == 'connector'
        recipe = result['recipe']
        controls = recipe['apps']['shared-models']['components']['backend']['values']['model_services']['policies']
        assert controls['files']['consumer'] == {'$app': 'files'}
        updated = await update_selected_deployment(client, recipe=recipe, operation_id='changed-models',
                                                   fleet_tiers={'normal': 'fleet-route://balanced'})
        for name in ('files', 'notebook', 'shared-models'):
            assert updated['recipe']['apps'][name] == recipe['apps'][name]
        assert calls
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['agent-provider', 'agent-consumer', 'occupied-slot', 'occupied-policy', 'agent-generation-policy', 'unknown-field'])
async def test_owner_selection_rejects_lifetime_coupling_and_conflicts_before_directory(tmp_path, change):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pantheon.apps.agent_deployment import compose_selected_deployment
    spec = selected_spec(tmp_path)
    if change == 'agent-provider': spec['model_consumers']['files']['provider'] = 'model-access'
    elif change == 'agent-consumer': spec['model_consumers']['agent'] = spec['model_consumers'].pop('files')
    elif change == 'occupied-slot': spec['provider_apps']['files']['bindings']['models'] = {}
    elif change == 'occupied-policy': spec['provider_apps']['shared-models']['components']['backend']['values']['model_services']['policies']['files'] = {}
    elif change == 'agent-generation-policy': spec['provider_apps']['shared-models']['components']['backend']['values']['model_services']['policies']['other'] = {'consumer': {'$app': 'agent'}}
    else: spec['model_consumers']['files']['key'] = 'private'
    client = SimpleNamespace(deployments=AsyncMock(side_effect=AssertionError('unnecessary directory read')))
    with pytest.raises(AssemblyError) as error:
        await compose_selected_deployment(client, spec=spec, fleet_tiers={'normal': 'fleet-model://mac/example%3A8b'})
    assert 'private' not in str(error.value)
    client.deployments.assert_not_awaited()
