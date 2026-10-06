"""Complete product graph compilation, without starting services or models."""
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pantheon.apps.general_agent_preset import expand_general_team, PROVIDERS
from pantheon.apps.local_agent import compose_profile, native_platform, read_bundle
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.platform.model_dependency_package import build_package as access
from test_local_agent_product import product, setup


@pytest.fixture
def complete_entries(product, tmp_path):
    from pantheon.apps.builtin.file.build_managed import build as files
    from pantheon.apps.builtin.desktop.build_managed import build as desktop
    from pantheon.apps.builtin.evolution.build_managed import build as evolution
    from pantheon.apps.builtin.fleet.build_managed import build as fleet
    from pantheon.models.management_package import build_package as management
    _, entries = read_bundle(product)
    builders = {'files': files, 'desktop': desktop, 'evolution': evolution,
                'fleet': fleet, 'model-management': management, 'files-models': access}
    for alias, build in builders.items():
        kwargs = {'model_sampling': True, 'image_generation': True} if alias == 'files' else {}
        root = build(tmp_path/alias, native_platform(), **kwargs)
        manifest = json.loads((root/'app.json').read_text())
        payload, revision = build_artifact(root, native_platform())
        entries[alias] = ({'app_id': manifest['id'], 'version': manifest['version'],
                           'revision': revision, 'bytes': len(payload)}, root)
    # Compiler fixture's shell package has placeholder methods. Use the real
    # declaration here; real immutable bytes are exercised by the native gate.
    shell = Path(__file__).resolve().parents[1]/'apps/shell'
    entry, _ = entries['shell']
    entries['shell'] = (entry, shell)
    return entries


def compact_setup():
    base = setup()
    base['agent'].pop('dependencies')
    base['agent']['models']['fleet_tiers'] = {tier: 'fleet-model://local/example%3A8b'
                                            for tier in ('low', 'normal', 'high')}
    base['agent']['projects'].append({'id': 'second', 'name': 'Second', 'path': '/explicit-second'})
    return {'protocol': 1, 'preset': 'general-team', 'agent': base['agent'],
        'models': base['models'], 'model_apps': base['model_apps'],
        'files': {'sampling': {'model': 'fleet-model://local/example%3A8b', 'max_tokens': 1024,
                               'max_requests_per_call': 2},
                  'image_generation': {'model': 'fleet-model://local/image', 'aliases': {}, 'timeout_seconds': 60}},
        'desktop': {'user_seed': 'owner-choice', 'catalog': [{'path': '/explicit-catalog', 'scope': 'user'}],
                    'store': {'origin': 'https://store.example'}, 'data': {'mode': 'loopback'}},
        'evolution': {'execution': 'node', 'options': {}}}


def test_preset_preserves_choices_and_compiles_complete_scoped_graph(complete_entries):
    raw = compact_setup()
    raw['credentials'] = {'budget': {'ref': 'node-secret://owner-budget', 'endpoint': 'https://models.example'}}
    raw['agent']['models']['platform_budget'] = 'budget'
    before = deepcopy(raw)
    profile = compose_profile(complete_entries, raw)
    assert raw == before
    assert set(profile['apps']) == {*PROVIDERS, 'files-models', 'agent', 'allocator', 'model-access'}
    agent = profile['apps']['agent']['components']['backend']['values']['agent']
    for key in ('settings', 'projects', 'namespace'):
        assert agent[key] == raw['agent'][key]
    assert agent['models'] == {**raw['agent']['models'], 'model_services': 'model_services'}
    assert profile['apps']['agent']['components']['backend']['credentials']['budget'] == raw['credentials']['budget']
    assert agent['dependencies']['defaults'] == {'toolsets': [], 'mcp_servers': [],
                                               'primary_toolsets': ['fleet', 'model_services']}
    assert set(agent['dependencies']['profiles']['toolsets']) == {value[1] for value in PROVIDERS.values()}
    assert set(agent['view_dependencies']) == {'shared', 'second'}
    assert agent['auxiliary']['toolsets']['file_manager'] == {'credential': 'files', 'profile': 'file_manager'}
    policies = profile['apps']['allocator']['components']['backend']['values']['dependency_binding']['policies']
    bindings = policies['agent']['bindings']
    assert bindings['shell']['resource'] == {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}
    assert 'resource' not in bindings['files']
    assert profile['apps']['model-management']['components']['backend']['values']['model_management']['directory_root'] == {'$local': 'directory_root'}
    assert profile['apps']['evolution']['bindings']['agent']['methods']['agent_execution_submit']['bound'] == {'consumer_id': 'evolution-controller'}


def test_files_model_access_is_only_selected_subset_and_explicit_cloud_survives(complete_entries):
    raw = compact_setup()
    raw['models']['deployments']['other'] = {'$model': 'other'}
    raw['models']['routes']['pictures'] = 2
    raw['models']['routes']['not-for-files'] = 7
    raw['files']['image_generation']['model'] = 'fleet-route://pictures'
    raw['management'] = {'hub': {'ref': 'node-secret://own-hub', 'endpoint': 'https://hub.example'},
                         'hub_ca_pem': 'owner-ca'}
    expanded = expand_general_team(complete_entries, raw)
    access = expanded['providers']['files-models']['components']['backend']['values']['model_services']['policies']['files']
    assert set(access['deployments']) == {'local'}
    assert access['routes'] == {'pictures': 2}
    management = expanded['providers']['model-management']['components']['backend']
    assert management['credentials']['hub'] == raw['management']['hub']
    assert management['values']['model_management']['hub_ca_pem'] == 'owner-ca'


@pytest.mark.parametrize('damage', ['missing-provider', 'wrong-provider', 'files-access', 'override',
    'auxiliary', 'image-authority', 'desktop-authority', 'unknown', 'cloud-trust', 'duplicate-project', 'model-tier'])
def test_incomplete_or_conflicting_preset_is_rejected(complete_entries, damage):
    raw = compact_setup()
    if damage == 'missing-provider': del complete_entries['web']
    elif damage == 'wrong-provider': complete_entries['fleet'] = complete_entries['files']
    elif damage == 'files-access': del complete_entries['files-models']
    elif damage == 'override': raw['agent']['dependencies'] = {}
    elif damage == 'auxiliary': raw['agent']['auxiliary'] = {}
    elif damage == 'image-authority': raw['files']['image_generation']['model'] = 'fleet-model://absent/image'
    elif damage == 'desktop-authority': raw['desktop']['credentials'] = {'fleet': 'ambient'}
    elif damage == 'unknown': raw['preset'] = 'quietly-disable-memory'
    elif damage == 'cloud-trust': raw['management'] = {'hub_ca_pem': 'unowned'}
    elif damage == 'model-tier': del raw['agent']['models']['fleet_tiers']['low']
    elif damage == 'duplicate-project': raw['agent']['projects'].append(raw['agent']['projects'][0])
    with pytest.raises(AssemblyError): compose_profile(complete_entries, raw)


def test_compilation_does_not_load_agent_runtime(complete_entries):
    raw = compact_setup()
    script = """
import json, sys
from pathlib import Path
from pantheon.apps.local_agent import compose_profile
value = json.load(sys.stdin)
entries = {k: (v[0], Path(v[1])) for k, v in value['entries'].items()}
compose_profile(entries, value['setup'])
assert not any(n == 'pantheon.agent' or n.startswith('pantheon.chatroom') for n in sys.modules)
print('compiled')
"""
    result = subprocess.run([sys.executable, '-c', script], input=json.dumps({
        'entries': {k: [v[0], str(v[1])] for k, v in complete_entries.items()}, 'setup': raw}),
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'compiled'
