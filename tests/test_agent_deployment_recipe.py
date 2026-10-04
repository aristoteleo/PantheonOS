"""Agent preset through the generic coordinator and real package declarations.

Node operations/issuance are deterministic fixtures; actual Fleet installation
and packaged GUI/model conversations have separate acceptance gates.
"""
from copy import deepcopy
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.dependency_binding_service import DependencyBindingService
from pantheon.apps.lifecycle import build_artifact
from pantheon.chatroom.deployment import compose_deployment
from pantheon.apps.agent_deployment import compose_selected_deployment
from pantheon.models.dependency_service import ModelServiceControl
from pantheon.platform.dependency_package import build_package as build_allocator
from pantheon.platform.model_dependency_package import build_package as build_models
from test_agent_launch import prepared
from test_app_deployment import Nodes, Authority, finish_deployment
from test_agent_release import release
from test_model_dependency import deployment


def inputs(tmp_path):
    config = prepared(tmp_path, 'http://127.0.0.1:1234')['values']['agent']
    config['models'] = {'providers': {'openai': 'provider'}}
    def ref(name):
        return {'ref': 'node-secret://' + name, 'endpoint': 'https://owner.test'}
    return dict(owner='owner', operation_id='deployment-one',
        targets={name: {'node_id': 'worker' if name == 'agent' else 'platform',
            'revision': digest*64, 'scope': name, 'generation': 0}
            for name, digest in [('agent', 'b'), ('allocator', 'a'), ('model-access', 'c')]},
        agent=config, tools={}, models={'deployments': {}, 'routes': {}, 'allow_wake': False},
        credentials={'agent': {'provider': ref('byok')},
            'allocator': {'hub': ref('hub'), 'controller': ref('controller')},
            'model-access': {'hub': ref('hub')}})


def test_preset_is_explicit_immutable_composition(tmp_path):
    spec = inputs(tmp_path)
    saved = deepcopy(spec)
    recipe = compose_deployment(**spec)
    assert spec == saved
    from pantheon.apps.deployment import deployment_recipe
    assert deployment_recipe(**recipe)[1] == ['allocator', 'model-access', 'agent']
    app = recipe['apps']['agent']
    assert set(app['components']['backend']['credentials']) == {'provider'}
    assert app['components']['backend']['values']['agent']['models']['model_services'] == 'model_services'
    assert app['bindings']['model_services']['methods']['model_services_control']['bound'] == {'policy_id': 'agent'}
    assert recipe['apps']['allocator']['components']['backend']['values']['dependency_binding']['policies']['agent']['consumer'] == {'$app': 'agent'}


@pytest.mark.parametrize('change', ['inline-secret', 'owner-in-agent', 'extra-alias', 'replace-model', 'replace-allocator', 'missing-target', 'invalid-credentials'])
def test_preset_rejects_confused_configuration(tmp_path, change):
    spec = inputs(tmp_path)
    if change == 'inline-secret': spec['credentials']['agent']['provider'] = {'endpoint': 'https://model.test', 'key': 'do-not-print'}
    elif change == 'owner-in-agent': spec['credentials']['agent']['allocator'] = spec['credentials']['allocator']['hub']
    elif change == 'extra-alias': spec['tools']['unapproved'] = {}
    elif change == 'replace-model': spec['agent']['models']['model_services'] = 'other'
    elif change == 'replace-allocator': spec['extra_bindings'] = {'allocator': {}}
    elif change == 'missing-target': del spec['targets']['model-access']
    else: spec['credentials']['allocator'] = None
    with pytest.raises(AssemblyError) as error:
        compose_deployment(**spec)
    assert 'do-not-print' not in str(error.value)


@pytest.mark.asyncio
async def test_empty_policies_start_without_ambient_calls_and_reject_allocation():
    consumer = {'node_id': 'node', 'instance_id': 'agent', 'revision': 'a'*64, 'generation': 2}
    owner = SimpleNamespace(bind=AsyncMock(), deployments=AsyncMock(side_effect=AssertionError('ambient Hub call')))
    service = DependencyBindingService(owner, policies={'agent': {'consumer': consumer, 'bindings': {}}})
    for aliases in ([], ['shell']):
        with pytest.raises(AssemblyError):
            await service.bind_dependencies(policy_id='agent', owner_ref='agent-one', operation_id='allocation-one', aliases=aliases)
    owner.bind.assert_not_awaited()
    model = ModelServiceControl(owner, policies={'agent': {'consumer': consumer, 'deployments': {}, 'routes': {}, 'allow_wake': False}})
    for operation in ('deployments', 'routes'):
        response = await model.model_services_control(policy_id='agent', operation=operation, arguments={})
        assert response['status'] == 200 and response['result'] == {operation: []}
    assert (await model.model_services_control(policy_id='agent', operation='connect', arguments={'binding': {}}))['status'] == 403
    owner.deployments.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('populated', [False, True], ids=['byok-only', 'models-and-shell'])
async def test_preset_delivers_actual_agent_package_with_future_consumer_policies(release, tmp_path, populated):
    nodes = Nodes()
    packages = {'agent': release[0], 'allocator': build_allocator(tmp_path/'allocator', 'linux-amd64'),
                'model-access': build_models(tmp_path/'models', 'linux-amd64')}
    spec = inputs(tmp_path)
    if populated:
        row = deployment()
        row['models'][0]['context'] = 8192
        spec['models']['deployments'] = {row['deployment_id']: row['binding']}
        spec['tools']['shell'] = {'app_id': 'shell', 'provider': row['binding'],
            'methods': {'run_command': {'arguments': ['command'], 'bound': {}}},
            'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}
        spec['agent']['dependencies']['profiles']['toolsets']['shell'] = {'alias': 'shell', 'functions': [{
            'name': 'run_command', 'description': 'Execute in this Agent shell',
            'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}, 'required': ['command']}}]}
    for name, path in packages.items():
        _, digest = build_artifact(path)
        spec['targets'][name]['revision'] = digest
        nodes.manifests[digest] = {'protocol': 1, 'revision': digest,
            'manifest': json.loads((path/'app.json').read_text()),
            'definition': json.loads((path/'fleet.json').read_text())}
    declaration = nodes.manifests[spec['targets']['agent']['revision']]['manifest']['dependencies']
    assert declaration['shell'] == {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'}
    if populated:
        owner = SimpleNamespace(deployments=AsyncMock(return_value=[row]))
        from pantheon.models.client import model_ref
        selected = await compose_selected_deployment(owner,
            spec={key: value for key, value in spec.items() if key != 'models'},
            fleet_tiers={'normal': model_ref(row['deployment_id'], row['models'][0]['id'])})
        recipe = selected['recipe']
        owner.deployments.assert_awaited_once()
    else:
        recipe = compose_deployment(**spec)
    result = await finish_deployment(tmp_path/'deployment', nodes, Authority(nodes), recipe['apps'])
    assert result['state'] == 'ready'
    prepared = result['prepared']
    def config(name):
        return nodes.configurations[(spec['targets'][name]['node_id'], prepared[name]['instance_id'], 1)]['backend']
    agent = config('agent')
    assert set(agent['dependencies']) == {'allocator', 'model_services'}
    assert set(agent['credentials']) == {'provider'}
    expected = {**prepared['agent'], 'generation': 2}
    tool_policy = config('allocator')['values']['dependency_binding']['policies']['agent']
    model_policy = config('model-access')['values']['model_services']['policies']['agent']
    assert tool_policy['consumer'] == model_policy['consumer'] == expected
    # Read with the actual hosts' policy implementations, not just JSON checks.
    DependencyBindingService(None, policies={'agent': tool_policy})
    ModelServiceControl(None, policies={'agent': model_policy})
    assert tool_policy['bindings'] == spec['tools']
    assert model_policy['deployments'] == spec['models']['deployments']
    assert len(nodes.calls) == 9


def test_preset_can_prepare_shared_ordinary_providers_without_replacing_core(tmp_path):
    spec = inputs(tmp_path)
    target = dict(node_id='worker', revision='d'*64, scope='shared-files', generation=0,
                  bindings={}, components={'backend':{'values':{'files':{'workspace':'/project'}}}})
    spec['provider_apps'] = {'files':target}
    spec['tools'] = {'files':{'app_id':'file-manager',
        'provider':{'$app':'files','component':'backend','port':'http'},
        'methods':{'read_file':{'arguments':[],'bound':{'file_path':'shared.txt'}}}}}
    spec['agent']['dependencies']['profiles']['toolsets'] = {'file_manager':{'alias':'files','functions':[
        {'name':'read_file','parameters':{'type':'object','properties':{}}}]}}
    recipe = compose_deployment(**spec)
    assert recipe['apps']['files'] == target
    from pantheon.apps.deployment import deployment_recipe
    _, order = deployment_recipe(**recipe)
    assert order.index('files') < order.index('agent')
    spec['provider_apps']['files']['components']['backend']['values']['files']['workspace'] = '/later'
    assert recipe['apps']['files']['components']['backend']['values']['files']['workspace'] == '/project'
    spec['provider_apps'] = {'agent':target}
    with pytest.raises(AssemblyError, match='cannot replace'):
        compose_deployment(**spec)


@pytest.mark.parametrize('module', ['pantheon.chatroom.deployment', 'pantheon.apps.agent_deployment'])
def test_original_and_platform_composer_cli_remain_compatible(tmp_path, module):
    source, output = tmp_path/'input.json', tmp_path/'preset.json'
    spec = inputs(tmp_path)
    source.write_text(json.dumps(spec))
    command = [sys.executable, '-m', module, '--input', str(source), '--output', str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == compose_deployment(**spec)
    if os.name == 'posix':
        assert output.stat().st_mode & 0o777 == 0o600
    original = output.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=30).returncode != 0
    assert output.read_bytes() == original
