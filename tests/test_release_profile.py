"""The release set's deployment profile replaces Python composition at setup."""
import json

import pytest

from pantheonos.apps.dependency_assembly import AssemblyError
from pantheonos.apps.release_profile import BUDGET, HUB, OWNER, OWNER_HUB, compile_profile


def spec():
    return {'apps': {
        'agent': {'package': 'agent', 'scope': 'agent', 'components': {'backend': {'values': {
            'controller': {'$local': 'controller'}, 'workspace': {'$local': 'workspace'},
            'management': {'hub': OWNER_HUB}, 'events': {'auth': 'creds-base64'}}}},
            'bindings': {'allocator': {'app_id': 'dependency-binding', 'component': 'backend', 'methods': {'m': {'arguments': [], 'bound': {}}},
                                       'provider': {'$app': 'allocator', 'component': 'backend', 'port': 'http'}}}},
        'allocator': {'package': 'allocator', 'scope': 'allocator', 'bindings': {}, 'components': {'backend': {
            'values': {'dependency_binding': {'protocol': 1, 'trust_roots_pem': {'$local': 'trust_roots_pem'},
                'policies': {'agent': {'consumer': {'$app': 'agent'}, 'bindings': {}}}}},
            'credentials': {'hub': {'$local': 'owner_credential'}, 'controller': {'$local': 'owner_credential'}}}}},
        'model-access': {'package': 'model-access', 'scope': 'model-access', 'bindings': {}, 'components': {'backend': {
            'values': {'model_services': {'protocol': 1, 'policies': {'agent': {'consumer': {'$app': 'agent'},
                'deployments': {'platform': {'$model': 'connector'}}, 'routes': {}, 'allow_wake': False}}}},
            'credentials': {'hub': {'$local': 'owner_credential'}}}}},
        'desktop': {'package': 'desktop', 'scope': 'desktop', 'bindings': {}, 'components': {'backend': {
            'values': {'seed': OWNER, 'prefix': {'$local': 'fleet_event_prefix'}},
            'credentials': {'bus': {'$local': 'fleet_credential'}}}}},
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
    # The controller hosts the allocator (docs §15): its late policies only.
    assert apps['allocator'] == {'hosted': 'dependency-binding', 'scope': 'allocator', 'intent': 'running',
        'config': {'backend': {'values': {'dependency_binding': {'protocol': 1,
            'policies': {'agent': {'consumer': {'$app': 'agent', 'late': True}, 'bindings': {}}}}}}}}
    # Model access too: its policies and its Hub credential's secret name.
    assert apps['model-access'] == {'hosted': 'model-services-control', 'scope': 'model-access', 'intent': 'running',
        'config': {'backend': {'values': {'model_services': {'protocol': 1, 'policies': {'agent': {
            'consumer': {'$app': 'agent'}, 'deployments': {'platform': 'current'}, 'routes': {}, 'allow_wake': False}}}},
            'credentials': {'hub': {'$secret': 'owner-hub'}}}}}
    desktop = apps['desktop']['config']['backend']
    assert desktop['values'] == {'seed': {'$fleet': 'id'}, 'prefix': {'$fleet': 'event_prefix'}}
    assert desktop['credentials'] == {'bus': {'$secret': 'owner-controller'}}
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


def test_model_management_is_hosted_with_its_hub_credential_only():
    from pantheonos.apps.release_profile import hosted_apps, LOCAL_HOSTED
    app = {'package': 'model-management', 'scope': 'shared-model-management', 'intent': 'running', 'placement': {},
           'config': {'backend': {'credentials': {'controller': {'$secret': 'owner-controller'},
               'fleet': {'$secret': 'owner-controller'}, 'hub': {'$secret': 'owner-hub'}},
               'values': {'model_management': {'bus': {'auth': 'fleet-key'}}}}}}
    assert hosted_apps({'model-management': app})['model-management'] == {
        'hosted': 'model-services-management', 'scope': 'shared-model-management', 'intent': 'running',
        'config': {'backend': {'credentials': {'hub': {'$secret': 'owner-hub'}}}}}
    # A single machine keeps its local-directory management App.
    assert 'model-management' not in LOCAL_HOSTED
