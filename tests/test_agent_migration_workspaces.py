"""Explicit retained environments, owned task state and provider admission."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_workspaces import RetainedWorkspaceConversion
from test_agent_application import TEMPLATE
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree


@pytest.fixture
def retained(legacy, tmp_path):
    config = Path(legacy['project_config'])
    (config/'settings.json').write_text('{}')
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory'])/name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = TEMPLATE
        path.write_text(json.dumps(value))
    env = config/'brain/chat-a/environment'
    env.mkdir(parents=True)
    script = env/'run.sh'
    script.write_text('#!/bin/sh\nprintf RETAINED_ENVIRONMENT')
    script.chmod(0o700)
    (env/'current').symlink_to('run.sh')
    (env/'empty').mkdir()
    (env.parent/'task_state.json').write_text(json.dumps({'state': {'task_dirs': {'job': str(env)}}}))
    owned = env.parent/'owned'
    owned.mkdir()
    (owned/'task_state.json').write_text('{}')
    workspaces = config/'workspaces'
    workspaces.mkdir()
    (workspaces/'artifact.txt').write_text('retained artifact')
    roots = [str(env), str(workspaces)]
    providers = {name: {'alias': alias, 'provider': {'node_id': 'source-node',
        'instance_id': name + '-instance', 'revision': char * 64, 'generation': 2,
        'component': 'backend', 'port': 'http'}}
        for name, alias, char in [('file_manager', 'files', 'a'), ('shell', 'shell', 'b')]}
    with fence_legacy(legacy, operation='retain-workspace', target=tmp_path/'app', namespace='retained') as fence:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path/'backup')
        yield legacy, fence, backup, roots, providers


def conversion(values, **changes):
    _, fence, backup, roots, providers = values
    options = dict(digest=backup['sha256'], fence=fence, owner='owner', source_node_id='source-node',
                   roots=roots, providers=providers)
    return RetainedWorkspaceConversion(backup['directory'], **(options | changes))


def imported(values, convert):
    _, fence, backup, _, _ = values
    return import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                         retained_workspaces=convert)


def launch(values, **changes):
    spec, fence, _, _, providers = values
    deps = {'owner': 'owner', 'node_id': 'agent-on-another-node',
            'profiles': {'toolsets': deepcopy(providers)}}
    deps.update(changes)
    return AgentAppData(fence.identity['target'], namespace='retained',
        projects=AppProjects(spec['projects']), dependency_configuration=deps)


def test_environment_stays_on_source_and_task_state_moves_to_agent(retained):
    spec, fence, _, roots, _ = retained
    before = user_tree(Path(spec['project_config']))
    with pytest.raises(ValueError, match='unresolved'):
        imported(retained, None)
    assert not Path(fence.identity['target']).exists()
    convert = conversion(retained)
    receipt = imported(retained, convert)
    root = Path(fence.identity['target'])
    assert receipt['workspace_bindings']['roots'] == sorted(roots)
    assert (root/'configuration/.pantheon/brain/chat-a/task_state.json').is_file()
    assert not (root/'configuration/.pantheon/brain/chat-a/environment').exists()
    assert not list(root.rglob('artifact.txt'))
    assert user_tree(Path(spec['project_config'])) == before
    app = launch(retained); app.close()
    # The reviewed returned documents must not mutate the converter's pins.
    convert.describe()['providers']['shell']['provider']['node_id'] = 'wrong'
    assert convert.describe()['providers']['shell']['provider']['node_id'] == 'source-node'
    (Path(roots[1])/'artifact.txt').write_text('new user work after cutover')
    (Path(roots[0])/'new.log').write_text('new task output')
    assert imported(retained, convert) == receipt
    assert (Path(roots[1])/'artifact.txt').read_text() == 'new user work after cutover'


@pytest.mark.parametrize('change', ['node', 'instance', 'revision', 'alias', 'missing', 'owner', 'record'])
def test_retained_provider_mismatch_blocks_admission(retained, change):
    _, fence, _, _, providers = retained
    imported(retained, conversion(retained))
    profiles = {'toolsets': deepcopy(providers)}
    entry = profiles['toolsets']['shell']
    if change in ('node', 'instance', 'revision'):
        key = {'node': 'node_id', 'instance': 'instance_id', 'revision': 'revision'}[change]
        entry['provider'][key] = 'c' * 64 if change == 'revision' else 'wrong'
    elif change == 'alias': entry['alias'] = 'wrong'
    elif change == 'missing': del profiles['toolsets']['shell']
    elif change == 'record': (Path(fence.identity['target'])/'migration-workspaces.json').write_text('{}')
    with pytest.raises(ValueError, match='retained workspace providers'):
        launch(retained, profiles=profiles, owner='wrong' if change == 'owner' else 'owner')


def test_clean_provider_generation_change_preserves_retained_node(retained):
    providers = deepcopy(retained[4])
    imported(retained, conversion(retained))
    for entry in providers.values(): entry['provider']['generation'] += 2
    app = launch(retained, profiles={'toolsets': providers}); app.close()


@pytest.mark.parametrize('change', ['brain', 'task', 'nested-state', 'config', 'uncaptured', 'overlap', 'source-node'])
def test_retention_cannot_hide_owned_state_or_adopt_uncaptured_paths(retained, change):
    spec, _, _, roots, _ = retained
    config = Path(spec['project_config'])
    selected = {'brain': [str(config/'brain')], 'task': [str(config/'brain/chat-a')],
        'nested-state': [str(config/'brain/chat-a/owned')],
        'config': [str(config)], 'uncaptured': [str(config/'workspaces/not-captured')],
        'overlap': roots + [roots[0]], 'source-node': roots}[change]
    with pytest.raises(ValueError):
        conversion(retained, roots=selected,
                   source_node_id='another-node' if change == 'source-node' else 'source-node')


def test_retained_sources_must_match_archive_until_commit(retained):
    roots = retained[3]
    convert = conversion(retained)
    (Path(roots[1])/'artifact.txt').write_text('changed before import')
    with pytest.raises(ValueError): imported(retained, convert)
    assert not Path(retained[1].identity['target']).exists()


def test_interrupted_retention_requires_same_provider_plan_before_resume(retained, monkeypatch):
    from pantheon.chatroom import migration_import as module
    convert = conversion(retained)
    with monkeypatch.context() as patch:
        def interrupted(*args):
            raise OSError('interrupted copy')
        patch.setattr(module, '_copy', interrupted)
        with pytest.raises(OSError, match='interrupted'):
            imported(retained, convert)
    with pytest.raises(ValueError, match='not committed'):
        launch(retained)
    providers = deepcopy(retained[4])
    providers['shell']['provider']['instance_id'] = 'replacement-shell'
    changed = conversion(retained, providers=providers)
    with pytest.raises(ValueError, match='different migration'):
        imported(retained, changed)
    imported(retained, convert)
    app = launch(retained); app.close()
