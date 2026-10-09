"""The release set's deployment profile replaces Python composition at setup."""
import json

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.release_profile import BUDGET, HUB, OWNER, OWNER_HUB, compile_profile


def spec():
    return {'apps': {
        'agent': {'package': 'agent', 'scope': 'agent', 'components': {'backend': {'values': {
            'controller': {'$local': 'controller'}, 'workspace': {'$local': 'workspace'},
            'management': {'hub': OWNER_HUB}, 'events': {'auth': 'creds-base64'}}}},
            'bindings': {'allocator': {'app_id': 'dependency-binding', 'component': 'backend', 'methods': {'m': {'arguments': [], 'bound': {}}},
                                       'provider': {'$app': 'allocator', 'component': 'backend', 'port': 'http'}}}},
        'allocator': {'package': 'allocator', 'scope': 'allocator', 'bindings': {}, 'components': {'backend': {
            'values': {'policy': {'consumer': {'$app': 'agent'}}, 'seed': OWNER, 'prefix': {'$local': 'fleet_event_prefix'}},
            'credentials': {'hub': {'$local': 'owner_credential'}, 'bus': {'$local': 'fleet_credential'}}}}},
        'model-access': {'package': 'model-access', 'scope': 'model-access', 'bindings': {}, 'components': {'backend': {
            'values': {'deployments': {'platform': {'$model': 'connector'}}}}}},
    }, 'model_apps': {'connector': {'deployment_id': 'platform', 'name': 'Platform models',
        'models': [{'id': 'claude', 'context_limit': 200000}],
        'app': {'package': 'connector', 'scope': 'model-platform', 'bindings': {}, 'components': {'backend': {'values': {
            'connector': {'engine': 'api', 'endpoint': HUB + '/litellm/v1', 'secret_ref': BUDGET}}}}}}}}


def test_profile_has_only_controller_and_hub_placeholders():
    profile = compile_profile(spec(), tiers={'high': ['claude']}, catalog=[{'id': 'glm', 'suggested': {'tools': True}}])
    apps = profile['spec']['apps']
    agent = apps['agent']['config']['backend']['values']
    assert 'controller' not in agent and agent['workspace'] == '/workspace/default_workspace'
    assert agent['management']['hub'] == {'$secret': 'owner-hub'}
    assert agent['events'] == {'auth': 'fleet-key', 'url': {'$input': 'bus_url'}}
    assert apps['agent']['bindings']['allocator'] == {'$app': 'allocator', 'component': 'backend',
        'app_id': 'dependency-binding', 'methods': {'m': {'arguments': [], 'bound': {}}}}
    allocator = apps['allocator']['config']['backend']
    assert allocator['values'] == {'policy': {'consumer': {'$app': 'agent'}}, 'seed': {'$fleet': 'id'},
                                   'prefix': {'$fleet': 'event_prefix'}}
    assert allocator['credentials'] == {'hub': {'$secret': 'owner-hub'}, 'bus': {'$secret': 'owner-controller'}}
    assert apps['model-access']['config']['backend']['values']['deployments'] == {'platform': 'current'}
    connector = apps['connector']['config']['backend']
    assert connector['values']['connector'] == {'engine': 'api', 'endpoint': {'$input': 'hub', 'suffix': '/litellm/v1'},
                                                'secret_ref': {'$secret_ref': 'budget'}}
    directory = connector['values']['directory']
    assert [m['id'] for m in directory['models']] == ['claude', 'glm'] and directory['routes'] == {'tier-high': ['claude']}
    assert connector['credentials'] == {'directory': {'$secret': 'directory'}}
    assert profile['spec']['secrets'] == sorted(profile['secrets']) == ['budget', 'directory', 'owner-controller', 'owner-hub']
    text = json.dumps(profile)
    assert all(v not in text for v in (HUB, OWNER, BUDGET, '$local', '$model'))


def test_unknown_local_value_is_refused():
    broken = spec()
    broken['apps']['agent']['components']['backend']['values']['x'] = {'$local': 'mystery'}
    with pytest.raises(AssemblyError, match='mystery'):
        compile_profile(broken, tiers={})
