"""Local recovery reviews a real owner journal against explicit live state.

Node state and model catalog are fixtures here; the full local Agent gate runs
this same planner across actual Controller/NATS/Runner shutdown and startup.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.agent_deployment import compose_deployment, plan_local_agent_restart
from pantheon.apps.dependency_assembly import AssemblyError, DependencyStarter
from pantheon.apps.deployment import AppDeployment, deployment_recipe
from test_local_agent_config import local_spec
from test_model_dependency import deployment as model_deployment


def rig(tmp_path):
    spec = local_spec(tmp_path)
    row = model_deployment()
    row['models'][0]['context'] = 8192
    spec['agent']['models']['fleet_tiers'] = {'normal': 'fleet-model://mac/example%3A8b'}
    spec['models']['deployments'] = {'mac': deepcopy(row['binding'])}
    spec['provider_apps'] = {'shell': {'node_id': 'local', 'revision': 'd'*64,
        'scope': 'shared-shell', 'generation': 0, 'components': {}, 'bindings': {}}}
    recipe = compose_deployment(**spec)
    prepared = {name: {'node_id': app['node_id'], 'revision': app['revision'],
        'instance_id': 'instance-' + name, 'generation': 1} for name, app in recipe['apps'].items()}
    record = {'protocol': 1, 'recipe': recipe, 'order': deployment_recipe(**recipe)[1],
              'prepared': prepared, 'state': 'ready', 'phase': 'ready', 'app': ''}
    state = {'owner': 'owner', 'node_id': 'local', 'dependency_config_protocol': 1,
        'operations': {}, 'installations': {}, 'instances': {
            prepared[name]['instance_id']: {'digest': app['revision'], 'scope': app['scope'],
                'generation': 3, 'state': 'stopped', 'resources': []} for name, app in recipe['apps'].items()}}
    wire = AsyncMock()
    wire.status.return_value = state
    coordinator = AppDeployment(DependencyStarter(wire, tmp_path/'starts'), tmp_path/'deployments')
    coordinator.root.mkdir(mode=0o700)
    def save(): coordinator._write(coordinator._path(recipe['operation_id']), record)
    save()
    row['binding']['generation'] = 5
    directory = SimpleNamespace(deployments=AsyncMock(return_value=[row]))
    args = dict(owner='owner', source_operation_id=recipe['operation_id'], operation_id='restart-all',
        local_transport={**spec['local_transport'], 'origin': 'https://127.0.0.1:18901'},
        owner_credential={'ref': 'node-secret://new-owner', 'endpoint': 'https://127.0.0.1:18901'})
    return SimpleNamespace(spec=spec, recipe=recipe, record=record, state=state, wire=wire,
        coordinator=coordinator, directory=directory, row=row, args=args, save=save)


@pytest.mark.asyncio
async def test_local_recovery_rebases_authority_and_models_without_mutating_or_starting(tmp_path):
    r = rig(tmp_path)
    original, args = deepcopy(r.recipe), deepcopy(r.args)
    result = await plan_local_agent_restart(r.directory, r.coordinator, **r.args)
    apps = result['recipe']['apps']
    assert set(apps) == set(original['apps']) and all(a['generation'] == 3 for a in apps.values())
    agent = apps['agent']['components']['backend']
    assert agent['values']['agent']['rpc_origin'] == args['local_transport']['origin']
    assert agent['credentials'] == original['apps']['agent']['components']['backend']['credentials']
    assert apps['shell'] == {**original['apps']['shell'], 'generation': 3}
    for name in ('allocator', 'model-access'):
        assert all(c == args['owner_credential'] for c in apps[name]['components']['backend']['credentials'].values())
    control = apps['model-access']['components']['backend']['values']['model_services']
    assert control['policies']['agent']['deployments']['mac'] == r.row['binding']
    assert control['directory_root'] == args['local_transport']['directory_root']
    assert r.recipe == original and r.args == args
    assert not r.coordinator._path(args['operation_id']).exists()
    r.wire.configure.assert_not_awaited()
    r.wire.submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'running', 'artifact', 'generation', 'trust', 'directory',
                                  'credential', 'node', 'replacement-model', 'older-model', 'missing-tiers',
                                  'provider-authority', 'custom-core'])
async def test_local_recovery_refuses_changed_identity_or_unreviewed_graph(tmp_path, change):
    r = rig(tmp_path)
    app = r.recipe['apps']['agent']
    if change == 'owner': r.args['owner'] = 'different'
    elif change == 'running': r.state['instances']['instance-agent'].update(state='ready', generation=2)
    elif change == 'artifact': r.state['instances']['instance-agent']['digest'] = 'f'*64
    elif change == 'generation': r.state['instances']['instance-agent']['generation'] = 4
    elif change == 'trust': r.args['local_transport']['trust_roots_pem'] += '\n'
    elif change == 'directory': r.args['local_transport']['directory_root'] += '-other'
    elif change == 'credential': r.args['owner_credential']['endpoint'] = 'https://other.test'
    elif change == 'node': r.state['node_id'] = 'different'
    elif change == 'replacement-model': r.row['binding']['revision'] = 'f'*64
    elif change == 'older-model': r.row['binding']['generation'] = 1
    elif change == 'missing-tiers': del app['components']['backend']['values']['agent']['models']['fleet_tiers']
    elif change == 'provider-authority':
        r.recipe['apps']['shell']['components'] = {'backend': {'values': {'origin': r.spec['local_transport']['origin']}}}
    else: r.recipe['apps']['allocator']['components']['backend']['values']['extra'] = True
    r.save()
    with pytest.raises(AssemblyError):
        await plan_local_agent_restart(r.directory, r.coordinator, **r.args)
    r.wire.submit.assert_not_awaited()
    r.wire.configure.assert_not_awaited()
    assert not r.coordinator._path('restart-all').exists()


@pytest.mark.asyncio
async def test_local_recovery_requires_resuming_existing_attempt(tmp_path):
    r = rig(tmp_path)
    r.coordinator._write(r.coordinator._path('restart-all'), {'existing': 'intent'})
    with pytest.raises(AssemblyError, match='Resume the existing'):
        await plan_local_agent_restart(r.directory, r.coordinator, **r.args)
    r.directory.deployments.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_recovery_does_not_adopt_changed_alias_policy(tmp_path):
    r = rig(tmp_path)
    apps = r.recipe['apps']
    apps['agent']['components']['backend']['values']['agent']['models']['fleet_tiers'] = {'normal': 'fleet-route://local'}
    apps['model-access']['components']['backend']['values']['model_services']['policies']['agent']['routes'] = {'local': 1}
    r.directory.routes = AsyncMock(return_value=[{'route_id': 'local', 'name': 'Changed route', 'revision': 2,
        'requires': {'operation': 'text'}, 'candidates': [{'deployment_id': 'mac', 'model_id': 'example:8b'}]}])
    r.save()
    with pytest.raises(AssemblyError, match='Model selection changed'):
        await plan_local_agent_restart(r.directory, r.coordinator, **r.args)
    r.wire.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_recovery_does_not_reuse_a_tool_generation_outside_its_graph(tmp_path):
    r = rig(tmp_path)
    r.spec['agent']['dependencies']['profiles']['toolsets']['shell'] = {'alias': 'shell'}
    r.spec['tools']['shell'] = {'app_id': 'shell', 'provider': {**r.row['binding'], 'node_id': 'local'},
        'methods': {'run_command': {'arguments': ['command'], 'bound': {}}},
        'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}
    r.recipe.clear()
    r.recipe.update(compose_deployment(**r.spec))
    r.save()
    with pytest.raises(AssemblyError, match='Include local tool providers'):
        await plan_local_agent_restart(r.directory, r.coordinator, **r.args)
    r.directory.deployments.assert_not_awaited()
    r.wire.submit.assert_not_awaited()
