"""Migrate actual plugin selectors without changing plugin policy or scope."""
import json
from pathlib import Path
import ssl
from types import SimpleNamespace

import pytest

from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_models import PLUGIN_MODEL_FIELDS
from pantheon.settings import Settings
from pantheon.internal.memory_system.config import get_memory_system_config
from pantheon.internal.learning_system.config import get_learning_system_config
from test_agent_application import PLUGIN_KEYS
from test_agent_migration import legacy
from test_agent_migration_import import RECIPE
from test_agent_migration_models import entries, plan, restore
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_agent_dependency_bindings import provider


@pytest.fixture
def configured(legacy, tmp_path):
    project = Path(legacy['project_config']) / 'settings.json'
    values = {key: {'enabled': False} for key in PLUGIN_KEYS}
    values.update(context_compression={'enable': True, 'compression_model': 'vendor/compress+think:low',
                                       'threshold': 0.65, 'preserve_recent_messages': 7},
                  memory_system={'enabled': True, 'selection_model': 'vendor/select',
                                 'flush_model': None, 'dream_model': 'auto', 'selection_max_memories': 3},
                  learning_system={'enabled': False, 'model': 'low', 'extract_model': 'vendor/extract',
                                   'extract_nudge_interval': 8})
    project.write_text(json.dumps(values))
    global_path = Path(legacy['global_config']) / 'settings.json'
    global_path.write_text(json.dumps({'learning_system': {'model': 'vendor/global'}}))
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory']) / name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = dict(
            id='saved', agents=[{'id': 'original-member', **RECIPE}])
        path.write_text(json.dumps(value))
    mappings = [dict(path=str(project), field=[section, field], source=source, target=target)
                for section, field, source, target in [
                    ('context_compression', 'compression_model', 'vendor/compress+think:low', 'fleet-route://compress+think:low'),
                    ('memory_system', 'selection_model', 'vendor/select', 'fleet-route://select'),
                    ('learning_system', 'extract_model', 'vendor/extract', 'fleet-route://extract')]]
    mappings.append(dict(path=str(global_path), field=['learning_system', 'model'],
                         source='vendor/global', target='fleet-route://global'))
    root = tmp_path / 'app'
    fence = fence_legacy(legacy, operation='move', target=root, namespace='migrated-agent')
    try:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
        yield legacy, fence, backup, root, mappings, values
    finally:
        fence.close()


def test_plugin_models_migrate_across_layers_preserving_runtime_semantics(configured):
    spec, fence, backup, root, mappings, original = configured
    source_bytes = {row['path']: Path(row['path']).read_bytes() for row in mappings}
    selection = plan(backup, fence, entries(), settings=mappings)
    restore(backup, fence, selection)
    saved = json.loads((root / 'configuration/.pantheon/settings.json').read_text())
    expected = json.loads(json.dumps(original))
    for row in mappings:
        if row['path'].startswith(spec['project_config']):
            section, field = row['field']
            expected[section][field] = row['target']
    assert saved == expected
    assert json.loads((root / 'user/settings.json').read_text()) == {'learning_system': {'model': 'fleet-route://global'}}
    settings = Settings(root / 'configuration', user_home=root / 'user', isolated_env=True, environment={})
    memory = get_memory_system_config(settings)
    assert memory['selection_model']._tag == memory['flush_model']._tag == 'fleet-route://select'
    assert memory['dream_model']._tag == 'auto'
    learning = get_learning_system_config(settings)
    assert learning['model']._tag == 'low'
    assert learning['extract_model']._tag == 'fleet-route://extract' and not learning['enabled']
    assert settings.get_compression_config()['compression_model'] == 'fleet-route://compress+think:low'
    assert all(Path(path).read_bytes() == raw for path, raw in source_bytes.items())
    assert json.loads((root / 'migration-model-selections.json').read_text()) == selection.audit()


@pytest.mark.parametrize('problem', ['missing', 'stale', 'duplicate', 'unknown-field', 'unrelated-file',
                                    'unknown-path', 'list', 'effort', 'not-fleet'])
def test_bad_plugin_mapping_fails_before_import(configured, problem):
    _, fence, backup, root, mappings, _ = configured
    if problem == 'missing': mappings.pop()
    elif problem == 'stale': mappings[0]['source'] = 'vendor/stale+think:low'
    elif problem == 'duplicate': mappings.append(dict(mappings[0]))
    elif problem == 'unknown-field': mappings[0]['field'] = ['memory_system', 'instructions']
    elif problem == 'unrelated-file': mappings[0]['path'] += '.unrelated'
    elif problem == 'unknown-path': mappings[0]['field'] = ['memory_system', 'flush_model']
    elif problem == 'list': mappings[0]['source'] = [mappings[0]['source']]
    elif problem == 'effort': mappings[0]['target'] = 'fleet-route://compress+think:high'
    else: mappings[0]['target'] = 'openai/model+think:low'
    with pytest.raises(ValueError):
        restore(backup, fence, plan(backup, fence, entries(), settings=mappings))
    assert not root.exists()


@pytest.mark.parametrize('source', [None, '', 'auto', ' AUTO ', 'low', 'high+think:low', 'fleet-route://chosen'])
def test_plugin_inheritance_and_bound_quality_tiers_survive(configured, source):
    _, fence, backup, _, _, _ = configured
    selection = plan(backup, fence, entries())
    document = {}
    for section, field in PLUGIN_MODEL_FIELDS:
        document.setdefault(section, {})[field] = source
    converted, used = selection.convert_settings('/settings.json', document)
    assert converted == document and not used
    for section in document:
        assert converted[section] is not document[section]


def test_plugin_mapping_is_part_of_resume_identity(configured, monkeypatch):
    from pantheon.chatroom import migration_import
    _, fence, backup, root, mappings, _ = configured
    selection = plan(backup, fence, entries(), settings=mappings)
    copy_file = migration_import._copy
    def crash(*args): raise OSError('interrupted')
    monkeypatch.setattr(migration_import, '_copy', crash)
    with pytest.raises(OSError): restore(backup, fence, selection)
    changed = [dict(row) for row in mappings]
    changed[0]['target'] = 'fleet-route://other+think:low'
    with pytest.raises(ValueError, match='different migration'):
        restore(backup, fence, plan(backup, fence, entries(), settings=changed))
    monkeypatch.setattr(migration_import, '_copy', copy_file)
    restore(backup, fence, selection)
    assert json.loads((root / 'migration.json').read_text())['phase'] == 'committed'


@pytest.mark.asyncio
async def test_migrated_plugin_work_calls_original_connector(configured, tmp_path, model_dependency,
                                                            model_endpoint, tls_material, monkeypatch):
    from pantheon.agent import Agent, _parse_thinking_suffix
    from pantheon.apps.runtime_config import RuntimeCredential
    from pantheon.chatroom.app_models import AppModels
    from pantheon.models.client import model_ref
    from pantheon.factory.bindings import AgentToolBindings
    from pantheon.internal.auxiliary_execution import AuxiliaryExecution
    from pantheon.internal.memory_system.plugin import _create_memory_plugin
    from pantheon.internal.learning_system.plugin import _create_learning_plugin
    from pantheon.internal.learning_system.extractor import SkillExtractor
    from pantheon.internal.compression.plugin import _create_compression_plugin
    from pantheon.internal.memory import Memory

    _, fence, backup, root, mappings, _ = configured
    reference = model_ref('mac', 'example:8b')
    for row in mappings:
        _, effort = _parse_thinking_suffix(row['source'])
        row['target'] = reference + (f'+think:{effort}' if effort else '')
    selection = plan(backup, fence, entries(target=reference), settings=mappings,
                     fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')})
    restore(backup, fence, selection)
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    def forbidden(*a, **kw): raise AssertionError('Plugin used ambient model state')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.setattr('pantheon.agent._resolve_model_tag', forbidden)
    models = AppModels(root, defaults={}, config=selection.describe()['models'], credentials={
        'model_services': RuntimeCredential(**model_dependency.credential)},
        tls_context=ssl.create_default_context(cafile=tmp_path / 'cert.pem'))
    files = provider(tls_material, 'file_manager', 'a' * 64)
    plugins = []
    try:
        await models.refresh()
        execution = AuxiliaryExecution(model_scope=models.scope, tool_bindings=AgentToolBindings({'file_manager': files}))
        memory_plugin = _create_memory_plugin({}, models.settings, execution=execution)
        learning = _create_learning_plugin({}, models.settings, execution=execution)
        plugins.extend([memory_plugin, learning])
        runtime = memory_plugin.runtime
        await runtime.retriever._llm_select('query', 'manifest', 1, 1)
        assert await runtime.flusher._run_llm('remember this') == 'scoped reply'
        await runtime.session_note._extract('session', [{'role': 'user', 'content': 'hello'}])
        assert 'scoped reply' in runtime.session_note.read('session')

        # Invoke the extractor explicitly; the migration must not enable its
        # disabled automatic scheduling just to use the new model transport.
        assert learning.runtime.extractor is None
        extractor = SkillExtractor(learning.runtime.store, model=learning.runtime.config['extract_model'],
                                   execution=execution)
        await extractor._extract([{'role': 'user', 'content': 'A reusable procedure'}])
        assert await execution.complete_text(execution.resolve_model('low+think:high'),
            [{'role': 'user', 'content': 'Reason about this'}], {}) == 'scoped reply'
        compression = _create_compression_plugin(models.settings.get_compression_config(), models.settings)
        active = Agent('active', 'Test', model=reference, model_scope=models.scope)
        team = SimpleNamespace(get_active_agent=lambda _: active, plugins=[compression])
        memory = Memory(name='compression')
        memory._messages = [{'role': 'user' if i % 2 == 0 else 'assistant',
                             'content': 'Earlier conversation. ' * 30} for i in range(20)]
        await compression.on_team_created(team)
        result = await compression._perform_compression(team, memory, force=True)
        assert result['success'], result
        requests = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
        assert len(requests) == 6
        assert all(body['model'] == 'example:8b' for body in requests)
        assert requests[-2]['reasoning_effort'] == 'high'
        assert requests[-1]['reasoning_effort'] == 'low'
        assert model_dependency.data_calls == ['/v1/chat/completions'] * 6
    finally:
        for plugin in plugins:
            await plugin.on_shutdown()
        await files.shutdown()
        await models.aclose()
