"""Saved model selection migration into the existing Model Service dependency."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_models import ModelSelectionConversion
from pantheon.models.client import model_ref
from test_agent_launch import snapshot
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_agent_migration_import import prepared, RECIPE
from test_agent_migration_credentials import vault, stage, conversion, read_key
from test_agent_model_scope import endpoint
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_agent_release import release, release_process, ready
from test_agent_native_process import request


def entries(source=RECIPE['model'], target='fleet-route://preserved'):
    return [{'conversation_id': cid, 'config_id': 'original-member', 'source': source, 'target': target}
            for cid in ('chat-a', 'chat-b')]


def plan(backup, fence, selections, **kwargs):
    kwargs.setdefault('fleet_tiers', {tier: 'fleet-route://preserved' for tier in ('normal', 'high', 'low')})
    return ModelSelectionConversion(backup['directory'], digest=backup['sha256'], fence=fence,
                                    owner='owner', node_id='node', selections=selections, **kwargs)


def restore(backup, fence, selection, **kwargs):
    return import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                         model_selection=selection, **kwargs)


def configuration(selection):
    return {k: v for k, v in selection.describe().items() if k in ('owner', 'node_id', 'models', 'credentials')}


def template_library(spec):
    agent_path = Path(spec['project_config']) / 'agents/researcher.md'
    agent_path.write_text('---\nid: researcher\nname: Researcher\nmodel: openai/fixture\n'
                          'toolsets: []\n---\nPreserved research instructions.\n')
    team_path = Path(spec['project_config']) / 'teams/migrated.md'
    team_path.write_text('---\nid: migrated\nname: Migrated\ntype: team\nagents: [researcher]\n---\n')
    return [dict(path=str(agent_path), config_id='researcher', source='openai/fixture',
                 target=model_ref('mac', 'example:8b'))]


def test_explicit_selection_conversion_preserves_history_and_identities(prepared):
    spec, fence, backup, root = prepared
    original = user_tree(Path(spec['home_memory']))
    selection = plan(backup, fence, entries())
    assert not root.exists()
    receipt = restore(backup, fence, selection)
    assert receipt['model_bindings'] == selection.describe()
    for member in receipt['members']:
        path = next((root / 'conversations').rglob(member['conversation_id'] + '*.json'))
        saved = json.loads(path.read_text())['extra_data']['team_template']['agents'][0]
        assert saved['model'] == 'fleet-route://preserved'
        for field in ('name', 'instructions', 'toolsets', 'mcp_servers'):
            assert saved.get(field) == RECIPE.get(field)
    assert user_tree(Path(spec['home_memory'])) == original
    assert restore(backup, fence, selection) == receipt
    assert json.loads((root / 'migration-model-selections.json').read_text()) == selection.audit()
    app = AgentAppData(root, namespace='migrated-agent', projects=AppProjects(spec['projects']),
                       model_configuration=configuration(selection))
    app.close()


@pytest.mark.parametrize('problem', ['missing', 'extra', 'stale', 'direct-target', 'effort',
                                    'duplicate', 'shape', 'fallback-count'])
def test_invalid_mapping_never_populates_destination(prepared, problem):
    _, fence, backup, root = prepared
    mappings = entries()
    if problem == 'missing': mappings.pop()
    elif problem == 'extra': mappings.append({**mappings[0], 'conversation_id': 'unrelated'})
    elif problem == 'stale': mappings[0]['source'] = 'stale/selection'
    elif problem == 'direct-target': mappings[0]['target'] = 'openai/new-model'
    elif problem == 'effort': mappings[0]['target'] += '+think:high'
    elif problem == 'duplicate': mappings.append(mappings[0])
    elif problem == 'shape': mappings[0]['target'] = [mappings[0]['target']]
    else:
        mappings[0].update(source=['first', 'second'], target=['fleet-route://first'])
    with pytest.raises(ValueError):
        restore(backup, fence, plan(backup, fence, mappings))
    assert not root.exists()


def test_reasoning_and_fallback_mapping_keeps_each_position(prepared):
    _, fence, backup, _ = prepared
    source = ['vendor/first+think:low', 'vendor/second+think:high']
    target = ['fleet-route://first+think:low', model_ref('second', 'second-model') + '+think:high']
    conversion = plan(backup, fence, entries(source, target))
    assert conversion.convert('chat-a', 'original-member', source) == target
    with pytest.raises(ValueError): conversion.convert('chat-a', 'original-member', source[::-1])


@pytest.mark.parametrize('change', ['placement', 'models', 'audit-missing', 'audit-modified', 'audit-symlink', 'audit-fifo'])
def test_startup_requires_committed_selection_binding_and_audit(prepared, change):
    spec, fence, backup, root = prepared
    selection = plan(backup, fence, entries())
    restore(backup, fence, selection)
    config = configuration(selection)
    if change == 'placement': config['node_id'] = 'another-node'
    elif change == 'models': config['models'] = {'providers': {'openai': 'direct'}}
    else:
        audit = root / 'migration-model-selections.json'
        if change == 'audit-missing': audit.unlink()
        elif change == 'audit-modified': audit.write_text('{}')
        elif change == 'audit-fifo': audit.unlink(); os.mkfifo(audit, 0o600)
        else:
            moved = root / 'moved-audit.json'; audit.rename(moved); audit.symlink_to(moved)
    with pytest.raises(ValueError, match='migrated model bindings'):
        AgentAppData(root, namespace='migrated-agent', projects=AppProjects(spec['projects']), model_configuration=config)


def test_interrupted_import_cannot_resume_with_a_different_mapping(prepared, monkeypatch):
    from pantheon.chatroom import migration_import
    _, fence, backup, root = prepared
    selection = plan(backup, fence, entries())
    real_copy = migration_import._copy
    def crash(*args): raise OSError('interrupted')
    monkeypatch.setattr(migration_import, '_copy', crash)
    with pytest.raises(OSError): restore(backup, fence, selection)
    changed = plan(backup, fence, entries(target='fleet-route://different'))
    with pytest.raises(ValueError, match='different migration'): restore(backup, fence, changed)
    monkeypatch.setattr(migration_import, '_copy', real_copy)
    receipt = restore(backup, fence, selection)
    assert receipt['model_bindings'] == selection.describe()
    assert json.loads((root / 'migration.json').read_text())['phase'] == 'committed'


@pytest.mark.parametrize('tiers', [{}, {'normal': 'fleet-route://default'},
    {tier: 'openai/default' for tier in ('normal', 'high', 'low')},
    {tier: 'fleet-route://default+think:high' for tier in ('normal', 'high', 'low')}])
def test_default_agents_need_explicit_complete_quality_tiers(prepared, tiers):
    _, fence, backup, root = prepared
    with pytest.raises(ValueError): plan(backup, fence, entries(), fleet_tiers=tiers)
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint', ['saved', 'new-template', 'switch-template'])
async def test_migrated_conversation_calls_connector_with_key_only_on_provider(
        legacy, tmp_path, endpoint, vault, model_dependency, model_endpoint, monkeypatch, entrypoint):
    templates = template_library(legacy) if entrypoint != 'saved' else []
    config, root, fence, backup, key_bindings = stage(legacy, tmp_path, endpoint, vault)
    try:
        original = user_tree(Path(legacy['home_memory']))
        keys = conversion(backup, fence, key_bindings, vault)
        # Use the actual saved member ID from the staged source, not a default.
        raw = json.loads((Path(legacy['home_memory']) / 'chat-b.json').read_text())
        member_id = raw['extra_data']['team_template']['agents'][0]['id']
        reference = model_ref('mac', 'example:8b')
        mappings = [{'conversation_id': cid, 'config_id': member_id, 'source': 'openai/fixture',
                     'target': reference} for cid in ('chat-a', 'chat-b')]
        selection = plan(backup, fence, mappings, templates=templates,
                         fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')})
        receipt = restore(backup, fence, selection, model_credentials=keys)
        assert 'legacy-synthetic-key' not in json.dumps(receipt)
        assert receipt['model_bindings']['credentials'] == {}
        assert receipt['model_bindings']['provisioning'] == keys.describe()
        assert read_key(vault, key_bindings[0]['ref'], key_bindings[0]['endpoint']) == 'legacy-synthetic-key'
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        connector = model_dependency.connector
        connector.configure({'engine': 'api', 'endpoint': key_bindings[0]['endpoint'], 'secret_ref': key_bindings[0]['ref']})
        model_dependency.deployment.update(engine='api', config_revision=connector.revision)
        config['values']['agent']['models'] = selection.describe()['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        assert 'legacy-synthetic-key' not in json.dumps(config)
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
        app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                         dependency_ca_file=tmp_path / 'cert.pem')
        try:
            await app.run_setup()
            assert app.app_models.resolve('normal') == [reference]
            chat_id = 'chat-b'
            if entrypoint == 'new-template':
                created = await app.create_chat('From migrated library', template_id='migrated')
                assert created['success'], created
                chat_id = created['chat_id']
            elif entrypoint == 'switch-template':
                changed = await app.setup_team_for_chat(chat_id, template_id='migrated')
                assert changed['success'], changed
            result = await app.chat(chat_id=chat_id, message=[{'role': 'user', 'content': 'Continue'}])
            assert result['success'], result
            bound = app.chat_teams[chat_id].team_agents[0]
            if entrypoint == 'saved':
                expected_id = next(m['instance_id'] for m in receipt['members'] if m['conversation_id'] == 'chat-b')
                assert str(bound.id) == expected_id
            else:
                assert bound.instructions == 'Preserved research instructions.'
                assert (root / 'configuration/.pantheon/agents/researcher.md').exists()
        finally:
            await app.cleanup()
        assert user_tree(Path(legacy['home_memory'])) == original
        assert len(endpoint.requests) == 1 and not model_endpoint.requests
        path, headers, body = endpoint.requests[0]
        assert path == '/byok/v1/chat/completions'
        assert headers['Authorization'] == 'Bearer legacy-synthetic-key'
        assert body['model'] == 'example:8b'
        assert model_dependency.data_calls == ['/v1/chat/completions']
    finally:
        fence.close()


@pytest.mark.asyncio
async def test_packaged_migrated_agent_enforces_mapping_then_calls_model_service(
        release, legacy, tmp_path, endpoint, vault, model_dependency, model_endpoint, monkeypatch):
    templates = template_library(legacy)
    config, root, fence, backup, key_bindings = stage(legacy, tmp_path, endpoint, vault,
                                                    target=tmp_path / 'data' / 'agent')
    try:
        keys = conversion(backup, fence, key_bindings, vault)
        raw = json.loads((Path(legacy['home_memory']) / 'chat-b.json').read_text())
        member_id = raw['extra_data']['team_template']['agents'][0]['id']
        reference = model_ref('mac', 'example:8b')
        selection = plan(backup, fence, [
            {'conversation_id': cid, 'config_id': member_id, 'source': 'openai/fixture', 'target': reference}
            for cid in ('chat-a', 'chat-b')], templates=templates,
            fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')})
        restore(backup, fence, selection, model_credentials=keys)
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        connector = model_dependency.connector
        connector.configure({'engine': 'api', 'endpoint': key_bindings[0]['endpoint'], 'secret_ref': key_bindings[0]['ref']})
        model_dependency.deployment.update(engine='api', config_revision=connector.revision)
        config['values']['agent']['models'] = selection.describe()['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
        audit = root / 'migration-model-selections.json'
        approved = audit.read_bytes()
        audit.write_text('{}')
        with release_process(tmp_path, release, config) as (process, base):
            assert await asyncio.to_thread(process.wait, timeout=20) != 0
        assert 'preserve its migrated model bindings' in (tmp_path / 'release.log').read_text()
        assert not endpoint.requests
        audit.write_bytes(approved)
        with release_process(tmp_path, release, config) as (process, base):
            await ready(process, base, tmp_path)
            result = await request(base, '/rpc', {'method': 'chat', 'args': {
                'chat_id': 'chat-b', 'message': [{'role': 'user', 'content': 'Continue'}]}})
            assert result['success'] and result['result']['success'], result
            created = await request(base, '/rpc', {'method': 'create_chat', 'args': {
                'chat_name': 'From migrated library', 'template_id': 'migrated'}})
            assert created['success'] and created['result']['success'], created
            result = await request(base, '/rpc', {'method': 'chat', 'args': {
                'chat_id': created['result']['chat_id'], 'message': [{'role': 'user', 'content': 'Research'}]}})
            assert result['success'] and result['result']['success'], result
            assert (root / 'configuration/.pantheon/agents/researcher.md').exists()
            assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
        assert process.returncode == 0
        assert len(endpoint.requests) == 2 and not model_endpoint.requests
        assert all(row[1]['Authorization'] == 'Bearer legacy-synthetic-key' for row in endpoint.requests)
        assert 'legacy-synthetic-key' not in (tmp_path / 'release.log').read_text()
    finally:
        fence.close()
