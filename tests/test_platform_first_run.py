import asyncio
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


NODES = [{'node_id': 'n_brain', 'kind': 'pod', 'caps': ['proc', 'fs:local']},
         {'node_id': 'n_workspace', 'kind': 'sandbox', 'caps': ['proc', 'fs:workspace', 'display', 'net', 'fs:local']}]


def test_placement_follows_requirements_and_prefers_the_brain():
    place = first_run.place
    assert place({'id': 'agent', 'placement': {'requires': ['dom']}}, NODES, local='n_brain') == 'n_brain'
    assert place({'id': 'allocator'}, NODES, local='n_brain') == 'n_brain'
    assert place({'id': 'shell', 'placement': {'requires': ['proc', 'fs:workspace'], 'prefer': ['sandbox']}},
                 NODES, local='n_brain') == 'n_workspace'
    assert place({'id': 'web', 'placement': {'requires': ['net']}}, NODES, local='n_brain') == 'n_workspace'
    # Without a Runner beside the platform, a free App still lands deterministically.
    assert place({'id': 'allocator'}, NODES[1:], local=None) == 'n_workspace'
    with pytest.raises(AssemblyError, match='No online node'):
        place({'id': 'gpu', 'placement': {'requires': ['gpu']}}, NODES, local='n_brain')


@pytest.mark.skipif(not ARCHIVE, reason='set AGENT_RELEASE_ARCHIVE to the linux release-set archive')
def test_general_team_composes_a_valid_remote_recipe(tmp_path):
    with tarfile.open(ARCHIVE) as tar:
        tar.extractall(tmp_path / 'release', filter='data')
    entries = _entries(tmp_path / 'release', 'linux-amd64')
    setup = first_run.general_team_setup(owner=OWNER, hub=HUB, models={
        m: 200000 for m in first_run.tier_models(first_run.DEFAULT_TIERS)}, tiers=first_run.DEFAULT_TIERS,
        management_hub=CREDS['hub'], budget_ref='node-secret://platform-budget-1')
    spec = compose_profile(entries, setup)
    targets = {**spec['apps'], **{n: m['app'] for n, m in spec['model_apps'].items()}}
    manifest = lambda app: json.loads((Path(entries[app['package']][1]) / 'app.json').read_text())
    nodes = {name: first_run.place(manifest(app), NODES, local='n_brain') for name, app in targets.items()}
    recipe = first_run.remote_recipe(spec, owner=OWNER, nodes=nodes, credentials={'n_brain': CREDS, 'n_workspace': CREDS},
                                     controller=CONTROLLER, operation_id='agent-setup-1')
    text = json.dumps(recipe)
    assert '$local' not in text and 'creds-base64' not in text and 'local-template' not in text
    assert recipe['kind'] == 'model-services' and set(recipe['model_apps']) == {'connector'}
    # Brain/body separation: workspace Apps on the sandbox, the rest beside the platform.
    on = lambda node: {name for name, app in recipe['apps'].items() if app['node_id'] == node}
    assert on('n_workspace') == {'desktop', 'evolution', 'files', 'notebook', 'shell', 'web'}
    assert on('n_brain') == {'agent', 'allocator', 'files-models', 'fleet', 'model-access', 'model-management'}
    assert recipe['model_apps']['connector']['app']['node_id'] == 'n_brain'
    # Tiers are owner-editable Model Services routes the Agent follows with failover.
    agent_models = recipe['apps']['agent']['components']['backend']['values']['agent']['models']
    assert agent_models['fleet_tiers'] == {t: f'fleet-route://tier-{t}' for t in ('high', 'normal', 'low')}
    assert {m['id'] for m in recipe['model_apps']['connector']['models']} == set(first_run.tier_models(first_run.DEFAULT_TIERS))
    assert 'tier-normal' in json.dumps(recipe['apps']['model-access'])
    connector = recipe['model_apps']['connector']['app']['components']['backend']['values']['connector']
    assert connector == {'engine': 'api', 'endpoint': HUB + '/litellm/v1', 'secret_ref': 'node-secret://platform-budget-1'}
    desktop = recipe['apps']['desktop']['components']['backend']
    assert desktop['values']['desktop']['fleet'] == {'auth': 'fleet-key', 'url': 'wss://fleet.example/nats'}
    assert desktop['credentials']['fleet'] == CREDS['controller']
    assert desktop['values']['desktop']['event_prefix'] == f'fleet.{OWNER}.apps.desktop'


def test_each_setup_run_uses_its_own_vault_references():
    # Owner-key references differ per run; the budget reference stays stable so the
    # model connector's retained configuration matches on a retry.
    assert first_run._refs('agent-setup-1791350000') == ('node-secret://platform-budget', 'platform-owner-1791350000')
    assert first_run._refs('agent-setup-1791350000')[1] != first_run._refs('agent-setup-1791350001')[1]


def test_existing_stopped_instances_keep_their_generation():
    spec = {'packages': {'connector': {'revision': 'c' * 64}, 'agent': {'revision': 'a' * 64}},
            'apps': {'agent': {'package': 'agent', 'scope': 'agent'}},
            'model_apps': {'connector': {'app': {'package': 'connector', 'scope': 'model-platform'}}}}
    state = {'instances': {
        'x': {'digest': 'c' * 64, 'scope': 'model-platform', 'state': 'stopped', 'generation': 3},
        'y': {'digest': 'b' * 64, 'scope': 'agent', 'state': 'stopped', 'generation': 2}}}  # older Agent release
    nodes = {'agent': 'n_ws', 'connector': 'n_ws'}
    assert first_run.existing_generations({'n_ws': state}, spec, nodes) == {'connector': 3}
    state['instances']['x']['state'] = 'ready'
    with pytest.raises(AssemblyError, match='already running'):
        first_run.existing_generations({'n_ws': state}, spec, nodes)


class _Directory:
    def __init__(self, rows):
        self.rows, self.calls = {r['deployment_id']: dict(r) for r in rows}, []

    async def deployments(self):
        return list(self.rows.values())

    async def save(self, row):
        self.calls.append(('save', row['state']))
        row = {**row, 'revision': row['revision'] + 1}
        self.rows[row['deployment_id']] = row
        return row

    async def remove(self, deployment_id, revision):
        assert self.rows[deployment_id]['revision'] == revision
        self.calls.append(('remove', deployment_id))
        del self.rows[deployment_id]


def test_stale_connector_registration_is_retired_only_when_its_instance_stopped():
    row = {'deployment_id': 'platform', 'node_id': 'n_workspace', 'state': 'ready', 'revision': 4,
           'binding': {'instance_id': 'i_old'}}
    stopped = {'instances': {'i_old': {'state': 'stopped'}}}
    directory = _Directory([row])
    # The connector moves to the brain node; its stopped row on the workspace is retired.
    asyncio.run(first_run.retire_stale_registrations(directory, {'n_workspace': stopped}, ['platform'], 'n_brain'))
    assert directory.calls == [('save', 'stopped'), ('remove', 'platform')] and not directory.rows

    live = {'instances': {'i_old': {'state': 'ready'}}}
    with pytest.raises(AssemblyError, match='still running'):
        asyncio.run(first_run.retire_stale_registrations(_Directory([row]), {'n_workspace': live}, ['platform'], 'n_workspace'))
    with pytest.raises(AssemblyError, match='registered elsewhere'):
        asyncio.run(first_run.retire_stale_registrations(_Directory([row]), {'n_other': stopped}, ['platform'], 'n_other'))
