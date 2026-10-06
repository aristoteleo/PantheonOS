from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest

from pantheon.apps.agent_setup import prepare_setup
from pantheon.apps.agent_deployment import compose_selected_deployment
from pantheon.apps.dependency_assembly import AssemblyError
from test_agent_deployment_recipe import inputs
from test_model_dependency import deployment


def setup_inputs(tmp_path):
    original = inputs(tmp_path)
    profile = dict(agent=original['agent'], tools=original['tools'],
        agent_credentials=original['credentials']['agent'], extra_bindings={}, provider_apps={})
    controls = dict(protocol=1, owner=original['owner'], source='platform-key',
        nodes={'platform': original['credentials']['allocator']})
    return dict(targets=original['targets'], profile=profile,
        control_credentials=controls, operation_id='first-setup')


def test_setup_preserves_full_profile_and_matches_target_node_credentials(tmp_path):
    kwargs = setup_inputs(tmp_path)
    profile = kwargs['profile']
    profile['agent']['settings']['custom_plugin'] = {'enabled': True, 'limit': 7}
    profile['agent']['auxiliary'] = {'tools': {'extension': 'grant'}}
    kwargs['targets']['model-access']['node_id'] = 'model-control-node'
    controls = kwargs['control_credentials']
    controls['nodes']['model-control-node'] = {
        'hub': {'ref': 'node-secret://other-hub', 'endpoint': 'https://owner.test'},
        'controller': {'ref': 'node-secret://other-controller', 'endpoint': 'https://controller.test'}}
    before = deepcopy(kwargs)
    spec = prepare_setup(**kwargs)
    assert kwargs == before
    assert spec['agent'] == profile['agent']
    assert spec['tools'] == profile['tools']
    assert spec['credentials']['agent'] == profile['agent_credentials']
    assert spec['credentials']['allocator'] == controls['nodes']['platform']
    assert spec['credentials']['model-access'] == {'hub': controls['nodes']['model-control-node']['hub']}
    assert 'models' not in spec and 'apps' not in spec  # Requires model review before it is deployable.


def test_setup_keeps_shared_providers_and_instance_tool_profiles(tmp_path):
    kwargs = setup_inputs(tmp_path)
    profile = kwargs['profile']
    profile['provider_apps'] = {'files': dict(node_id='worker', revision='d'*64,
        scope='shared-files', generation=0, bindings={},
        components={'backend': {'values': {'files': {'workspace': '/project'}}}})}
    profile['tools'] = {'files': {'app_id': 'file-manager',
        'provider': {'$app': 'files', 'component': 'backend', 'port': 'http'},
        'methods': {'read_file': {'arguments': [], 'bound': {'file_path': 'shared.txt'}}}}}
    profile['agent']['dependencies']['profiles']['toolsets'] = {'file_manager': {
        'alias': 'files', 'functions': [{'name': 'read_file', 'parameters': {'type': 'object', 'properties': {}}}]}}
    before = deepcopy(kwargs)
    spec = prepare_setup(**kwargs)
    assert kwargs == before
    assert spec['provider_apps'] == profile['provider_apps']
    assert spec['tools'] == profile['tools']
    assert spec['agent']['dependencies'] == profile['agent']['dependencies']


@pytest.mark.parametrize('change', ['missing-node', 'unknown-profile', 'inline-key', 'unknown-target', 'wrong-source'])
def test_invalid_setup_is_not_partially_composed(tmp_path, change):
    kwargs = setup_inputs(tmp_path)
    if change == 'missing-node': kwargs['control_credentials']['nodes'] = {}
    elif change == 'unknown-profile': kwargs['profile']['discard-me'] = True
    elif change == 'inline-key': kwargs['control_credentials']['nodes']['platform']['hub']['key'] = 'never-print'
    elif change == 'unknown-target': kwargs['targets']['unmapped'] = kwargs['targets']['agent']
    else: kwargs['control_credentials']['source'] = 'browser-login'
    with pytest.raises(AssemblyError) as error:
        prepare_setup(**kwargs)
    assert 'never-print' not in str(error.value)


@pytest.mark.asyncio
async def test_setup_composes_using_original_model_directory_without_starting_apps(tmp_path):
    row = deployment()
    row['models'][0]['context'] = 8192
    class Directory:
        async def deployments(self): return [row]
        async def routes(self): return []
    spec = prepare_setup(**setup_inputs(tmp_path))
    from pantheon.models.client import model_ref
    ref = model_ref(row['deployment_id'], row['models'][0]['id'])
    result = await compose_selected_deployment(Directory(), spec=spec, fleet_tiers={'normal': ref})
    agent = result['recipe']['apps']['agent']['components']['backend']
    assert agent['values']['agent']['models']['fleet_tiers'] == {'normal': ref}
    assert agent['values']['agent']['settings'] == spec['agent']['settings']
    assert result['model_selection']['authorization'][0]['deployment_id'] == row['deployment_id']


@pytest.mark.parametrize('profile_bytes', [0, 70_000])
def test_cli_emits_private_setup_and_refuses_to_overwrite(tmp_path, profile_bytes):
    kwargs = setup_inputs(tmp_path)
    kwargs['profile']['agent']['settings']['custom_plugin'] = {'instructions': 'x' * profile_bytes}
    args = [sys.executable, '-m', 'pantheon.apps.agent_setup', '--operation-id', kwargs.pop('operation_id')]
    for name, value in kwargs.items():
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        args += ['--' + name.replace('_', '-'), str(path)]
    output = tmp_path / 'setup.json'
    args += ['--output', str(output)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert os.stat(output).st_mode & 0o077 == 0
    assert json.loads(output.read_text())['operation_id'] == 'first-setup'
    assert json.loads(output.read_text())['agent'] == kwargs['profile']['agent']
    before = output.read_bytes()
    assert subprocess.run(args, capture_output=True, timeout=20).returncode == 1
    assert output.read_bytes() == before
