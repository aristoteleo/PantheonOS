"""Restore real history/config/member identity into an ordinary Agent App."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.application import AgentApplication
from pantheon.chatroom.data_fence import DataFencedError, LegacyDataLease
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_import import import_backup, abort_pending_import
from pantheon.factory.instance_store import AgentInstanceStore
from pantheon.settings import Settings
from pantheon.utils.model_scope import ModelCallScope
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_agent_data_fence import child
from test_agent_application import PLUGIN_KEYS
from test_agent_instance_factory import RECIPE
from test_provisioned_agent_instances import Provisioner
from test_agent_dependency_bindings import endpoint, forbid_ambient_tools
from test_agent_model_scope import endpoint as model_endpoint


@pytest.fixture
def prepared(legacy, tmp_path):
    settings = Path(legacy['project_config']) / 'settings.json'
    settings.write_text(json.dumps({**{key: {'enabled': False} for key in PLUGIN_KEYS},
        'default_template_auto_update': False, 'models': {'saved_models': {'openai': ['test']}},
        'endpoint': {'workspace_path': legacy['projects'][0]['path']}, 'api_keys': {}}))
    memory = Path(legacy['home_memory'])
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = memory / name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = {
            'id': 'existing-team', 'name': 'Saved team', 'source_path': str(settings.parent / 'teams/saved.md'),
            'agents': [{'id': 'original-member', **RECIPE}]}
        path.write_text(json.dumps(value))
    target = tmp_path / 'app'
    guard = fence_legacy(legacy, operation='move', target=target, namespace='migrated-agent')
    receipt = backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
    yield legacy, guard, receipt, target
    guard.close()


def view(spec):
    return AppProjects(spec['projects'], active_id=spec['active_project'], default_id=spec['default_project'])


def run_import(guard, backup):
    return import_backup(backup['directory'], digest=backup['sha256'], fence=guard)


@pytest.mark.asyncio
async def test_imported_history_opens_in_real_app_with_stable_members_and_tool_calls(
        prepared, endpoint, model_endpoint, forbid_ambient_tools):
    spec, guard, backup, root = prepared
    originals = user_tree(Path(spec['project_config']))
    receipt = run_import(guard, backup)
    assert receipt['conversations'] == 2 and len(receipt['members']) == 2
    ids = {(item['conversation_id'], item['config_id']): item['instance_id'] for item in receipt['members']}
    assert ids['chat-a', 'original-member'] != ids['chat-b', 'original-member']
    assert user_tree(Path(spec['project_config'])) == originals
    assert not (root / 'configuration/.pantheon/settings.json').stat().st_mode & 0o077
    settings_data = json.loads((root / 'configuration/.pantheon/settings.json').read_text())
    assert 'api_keys' not in settings_data and 'endpoint' not in settings_data
    assert settings_data['models'] == {'saved_models': {'openai': ['test']}}
    assert receipt['conversions'][0]['retained_at_source'] == ['endpoint']
    with pytest.raises(DataFencedError): LegacyDataLease().acquire(spec['home_memory'])
    for iteration in range(2):
        settings = Settings(root / 'configuration', user_home=root / 'user', isolated_env=True,
                            environment={'OPENAI_API_KEY': 'app-fixture', 'OPENAI_API_BASE': model_endpoint.url + '/byok/v1'})
        provisioner = Provisioner(endpoint)
        app = AgentApplication('agent', data_dir=root, namespace='migrated-agent', projects=view(spec),
            settings=settings, model_scope=ModelCallScope(settings), provisioner=provisioner,
            ensure_services=AsyncMock(), validate_model=lambda model: (True, ''))
        try:
            await app.run_setup()
            assert {row['id'] for row in (await app.list_chats('Research'))['chats']} == {'chat-a', 'chat-b'}
            memory = app.memory_manager.get_memory('chat-a')
            assert memory.extra_data['project']['path'] == spec['projects'][0]['path']
            assert memory.extra_data['session_storage']['metadata']['customTitle'] == 'Saved conversation'
            assert 'asset://external-image' in json.dumps(memory.get_messages(for_llm=False))
            template = memory.extra_data['team_template']
            assert template['source_path'] == str(root / 'configuration/.pantheon/teams/saved.md')
            assert template['agents'][0]['model'] == RECIPE['model']
            for cid in ('chat-a', 'chat-b'):
                result = await app.get_agents(cid)
                assert result['success'], result
                agent = app.chat_teams[cid].team_agents[0]
                assert str(agent.id) == ids[cid, 'original-member']
                assert (await agent.call_tool('shell__execute', {'command': 'pwd'}))['session'] in ('session-a', 'session-b')
            if iteration == 0:
                response = await app.chat_teams['chat-b'].team_agents[0].run('Reply once')
                assert response.content == 'scoped reply'
            # Retrying import after use must not overwrite newer runtime state.
            assert run_import(guard, backup) == receipt
        finally:
            await app.cleanup()
    assert user_tree(Path(spec['project_config'])) == originals


def test_interrupted_import_blocks_startup_and_resumes_same_identities(prepared, monkeypatch):
    import pantheon.chatroom.migration_import as module
    spec, guard, backup, root = prepared
    original = module._copy
    count = 0
    def interrupted(snapshot, root, item):
        nonlocal count
        count += 1
        if count == 2: raise OSError('simulated copy failure')
        return original(snapshot, root, item)
    monkeypatch.setattr(module, '_copy', interrupted)
    with pytest.raises(OSError): run_import(guard, backup)
    assert json.loads((root / 'migration.json').read_text())['phase'] == 'importing'
    with pytest.raises(ValueError, match='not committed'):
        AgentAppData(root, namespace='migrated-agent', projects=view(spec))
    monkeypatch.setattr(module, '_copy', original)
    receipt = run_import(guard, backup)
    assert run_import(guard, backup) == receipt
    data = AgentAppData(root, namespace='migrated-agent', projects=view(spec)); data.close()


def test_failure_after_identity_registration_reuses_ids_and_pre_cutover_abort_preserves_source(prepared, monkeypatch):
    import pantheon.chatroom.migration_import as module
    spec, guard, backup, root = prepared
    original = module._atomic_json
    def fail_commit(path, value):
        if path.name == 'migration-receipt.json': raise OSError('simulated interrupted publication')
        return original(path, value)
    monkeypatch.setattr(module, '_atomic_json', fail_commit)
    with pytest.raises(OSError): run_import(guard, backup)
    with sqlite3.connect(root / 'instances/instances.sqlite3') as db:
        before = db.execute('SELECT * FROM instances ORDER BY conversation_id').fetchall()
    assert len(before) == 2
    # Abort retains partial destination data for diagnosis; only the old source
    # becomes writable. The target remains blocked from startup indefinitely.
    source_before = user_tree(Path(spec['project_config']))
    abort_pending_import(fence=guard)
    with pytest.raises(ValueError): AgentAppData(root, namespace='migrated-agent', projects=view(spec))
    writer = LegacyDataLease(); writer.acquire(spec['home_memory']); writer.close()
    assert user_tree(Path(spec['project_config'])) == source_before
    with sqlite3.connect(root / 'instances/instances.sqlite3') as db:
        assert db.execute('SELECT * FROM instances ORDER BY conversation_id').fetchall() == before


def test_committed_import_cannot_silently_roll_back_new_writes(prepared):
    _, guard, backup, root = prepared
    run_import(guard, backup)
    with pytest.raises(ValueError, match='uncommitted'): abort_pending_import(fence=guard)
    assert json.loads((root / 'migration.json').read_text())['phase'] == 'committed'


@pytest.mark.parametrize('problem', ['credentials', 'environment', 'unknown-settings', 'missing-team', 'duplicate-member', 'invalid-model'])
def test_unresolved_conversion_never_partially_populates_app(legacy, tmp_path, problem):
    config = Path(legacy['project_config'])
    (config / 'settings.json').write_text('{}')
    for filename in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory']) / filename
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = {
            'id': 'saved-team', 'agents': [{'id': 'member', **RECIPE}]}
        if problem == 'missing-team': value['extra_data'].pop('team_template')
        elif problem == 'duplicate-member': value['extra_data']['team_template']['agents'] *= 2
        elif problem == 'invalid-model': value['extra_data']['team_template']['agents'][0]['model'] = {'invalid': True}
        path.write_text(json.dumps(value))
    if problem == 'credentials': (config / 'settings.json').write_text('{"api_keys":{"KEY":"secret-value"}}')
    if problem == 'environment':
        (config / 'settings.json').write_text('{"env_file":".env"}')
        (config.parent / '.env').write_text('UNMAPPED_KEY=secret-value\n')
    if problem == 'unknown-settings': (config / 'settings.json').write_text('{"custom_execution":{"something":true}}')
    target = tmp_path / 'app'
    with fence_legacy(legacy, operation='move', target=target, namespace='migrated-agent') as guard:
        backup = backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
        with pytest.raises(ValueError) as error: run_import(guard, backup)
        assert 'secret-value' not in str(error.value)
        assert not target.exists()


def test_seed_identity_mapping_is_atomic_and_does_not_replay_resources(tmp_path):
    store = AgentInstanceStore(tmp_path / 'instances', namespace='target')
    mapping = [{'conversation_id': 'a', 'config_id': 'member', 'instance_id': '10000000-0000-4000-8000-000000000001'}]
    try:
        store.seed_legacy_members(mapping)
        store.seed_legacy_members(mapping)
        mixed = [{'conversation_id': 'b', 'config_id': 'member', 'instance_id': '10000000-0000-4000-8000-000000000002'},
                 {**mapping[0], 'instance_id': '10000000-0000-4000-8000-000000000003'}]
        with pytest.raises(ValueError): store.seed_legacy_members(mixed)
        assert store._db.execute('SELECT COUNT(*) FROM instances').fetchone() == (1,)
        assert store._db.execute('SELECT COUNT(*) FROM revisions').fetchone() == (0,)
        reserved = store.reserve('a', {'member': RECIPE})[0]
        assert reserved.instance_id == mapping[0]['instance_id']
    finally:
        store.close()


def test_import_rejects_source_edits_after_backup(prepared):
    spec, guard, backup, root = prepared
    path = Path(spec['home_memory']) / 'chat-b.json'
    value = json.loads(path.read_text())
    value['name'] = 'Later change'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): run_import(guard, backup)
    assert not root.exists()


def test_resume_after_identity_registration_preserves_mapping(prepared, monkeypatch):
    import pantheon.chatroom.migration_import as module
    _, guard, backup, root = prepared
    original = module._atomic_json
    def interrupted(path, value):
        if path.name == 'migration-receipt.json': raise OSError('publication interrupted')
        return original(path, value)
    monkeypatch.setattr(module, '_atomic_json', interrupted)
    with pytest.raises(OSError): run_import(guard, backup)
    with sqlite3.connect(root / 'instances/instances.sqlite3') as db:
        before = db.execute('SELECT instance_id, conversation_id, config_id FROM instances ORDER BY conversation_id').fetchall()
    monkeypatch.setattr(module, '_atomic_json', original)
    result = run_import(guard, backup)
    assert sorted((m['instance_id'], m['conversation_id'], m['config_id']) for m in result['members']) == sorted(before)


@pytest.mark.skipif(os.name == 'nt', reason='Requires real Windows process/locking acceptance')
def test_killed_import_stays_unstartable_and_resumes(prepared, tmp_path):
    spec, guard, backup, root = prepared
    guard.close()
    spec_path = tmp_path / 'spec.json'; spec_path.write_text(json.dumps(spec))
    code = """
import json, sys
from pathlib import Path
from pantheon.chatroom.migration import fence_legacy
import pantheon.chatroom.migration_import as module
spec = json.loads(Path(sys.argv[1]).read_text())
original = module._copy
def suspended(snapshot, root, item):
    original(snapshot, root, item)
    print('ready', flush=True)
    sys.stdin.read()
module._copy = suspended
with fence_legacy(spec, operation='move', target=sys.argv[2], namespace='migrated-agent') as guard:
    module.import_backup(sys.argv[3], digest=sys.argv[4], fence=guard)
"""
    with child(code, spec_path, root, backup['directory'], backup['sha256']):
        with pytest.raises(ValueError): AgentAppData(root, namespace='migrated-agent', projects=view(spec))
    with pytest.raises(ValueError): AgentAppData(root, namespace='migrated-agent', projects=view(spec))
    with fence_legacy(spec, operation='move', target=root, namespace='migrated-agent') as resumed:
        result = run_import(resumed, backup)
        assert result['conversations'] == 2
    data = AgentAppData(root, namespace='migrated-agent', projects=view(spec)); data.close()


def test_existing_app_data_is_not_adopted_as_an_import_target(prepared):
    spec, guard, backup, root = prepared
    data = AgentAppData(root, namespace='existing-app', projects=view(spec))
    try:
        with pytest.raises(ValueError, match='must be empty'): run_import(guard, backup)
        assert not (root / 'migration.json').exists()
    finally:
        data.close()


@pytest.mark.parametrize('contents', ['{broken', '{"protocol":1,"phase":"committed"}',
                                     '{"protocol":true,"phase":"committed","namespace":"x"}'])
def test_malformed_migration_state_never_opens_instance_database(tmp_path, contents):
    root = tmp_path / 'app'; root.mkdir(mode=0o700)
    (root / 'migration.json').write_text(contents)
    projects = AppProjects([])
    with pytest.raises(ValueError): AgentAppData(root, namespace='x', projects=projects)
    assert not (root / 'instances').exists()


@pytest.mark.parametrize('selection', ['omitted', None, ''])
def test_import_preserves_implicit_model_selection(legacy, tmp_path, selection):
    config = Path(legacy['project_config'])
    (config / 'settings.json').write_text('{}')
    originals = {}
    for filename in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory']) / filename
        value = json.loads(path.read_text())
        member = {'id': 'member', **RECIPE}
        if selection == 'omitted': member.pop('model')
        else: member['model'] = selection
        value.setdefault('extra_data', {})['team_template'] = {'id': 'saved-team', 'agents': [member]}
        path.write_text(json.dumps(value))
        originals[filename] = value
    target = tmp_path / 'app'
    with fence_legacy(legacy, operation='move', target=target, namespace='migrated-agent') as guard:
        backup = backup_legacy(legacy, fence=guard, directory=tmp_path / 'backup')
        receipt = run_import(guard, backup)
        assert receipt['conversations'] == 2
        for filename, original in originals.items():
            matches = list((target / 'conversations').rglob(filename))
            assert len(matches) == 1
            assert json.loads(matches[0].read_text()) == original
