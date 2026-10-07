import json
import os
from pathlib import Path
import tarfile

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.local_agent import _entries, compose_profile
from pantheon.platform import first_run

ARCHIVE = os.environ.get('AGENT_RELEASE_ARCHIVE')
OWNER = 'f_0123456789abcdef'
HUB, CONTROLLER = 'https://hub.example', 'https://fleet.example'
CREDS = {'hub': {'ref': 'node-secret://platform-owner-hub', 'endpoint': HUB},
         'controller': {'ref': 'node-secret://platform-owner-controller', 'endpoint': CONTROLLER}}


def test_render_remote_replaces_local_markers():
    context = dict(owner_credentials=CREDS, workspace='/workspace/default_workspace', bus_url='wss://fleet.example/nats',
                   fleet_credential=CREDS['controller'], fleet_event_prefix='fleet.f.apps.desktop')
    value = {'values': {'agent': {'rpc_origin': {'$local': 'controller'}, 'trust_roots_pem': {'$local': 'trust_roots_pem'},
                                  'workspace': {'$local': 'workspace'}},
                        'bus': {'auth': 'creds-base64'}},
             'credentials': {'hub': {'$local': 'owner_credential'}, 'controller': {'$local': 'owner_credential'},
                             'fleet': {'$local': 'fleet_credential'}}}
    out = first_run.render_remote(value, context)
    assert out['values']['agent'] == {'workspace': '/workspace/default_workspace'}
    assert out['values']['bus'] == {'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}
    assert out['credentials'] == {'hub': CREDS['hub'], 'controller': CREDS['controller'], 'fleet': CREDS['controller']}
    with pytest.raises(AssemblyError):
        first_run.render_remote({'x': {'$local': 'owner_credential'}}, context, key='x')


@pytest.mark.skipif(not ARCHIVE, reason='set AGENT_RELEASE_ARCHIVE to the linux release-set archive')
def test_general_team_composes_a_valid_remote_recipe(tmp_path):
    with tarfile.open(ARCHIVE) as tar:
        tar.extractall(tmp_path / 'release', filter='data')
    entries = _entries(tmp_path / 'release', 'linux-amd64')
    setup = first_run.general_team_setup(owner=OWNER, hub=HUB, models={'openrouter/openai/gpt-5.5': 200000,
        'openrouter/openai/gpt-5.4-mini': 200000}, tiers=first_run.DEFAULT_TIERS, management_hub=CREDS['hub'])
    spec = compose_profile(entries, setup)
    recipe = first_run.remote_recipe(spec, owner=OWNER, node_id='n_workspace', owner_credentials=CREDS,
                                     controller=CONTROLLER, operation_id='agent-setup-1')
    text = json.dumps(recipe)
    assert '$local' not in text and 'creds-base64' not in text and 'local-template' not in text
    assert recipe['kind'] == 'model-services' and set(recipe['model_apps']) == {'connector'}
    assert len(recipe['apps']) == 12 and {a['node_id'] for a in recipe['apps'].values()} == {'n_workspace'}
    connector = recipe['model_apps']['connector']['app']['components']['backend']['values']['connector']
    assert connector == {'engine': 'api', 'endpoint': HUB + '/litellm/v1', 'secret_ref': 'node-secret://platform-budget'}
    desktop = recipe['apps']['desktop']['components']['backend']
    assert desktop['values']['desktop']['fleet'] == {'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}
    assert desktop['credentials']['fleet'] == CREDS['controller']
    assert desktop['values']['desktop']['event_prefix'] == f'fleet.{OWNER}.apps.desktop'
