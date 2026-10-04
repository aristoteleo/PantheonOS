"""Template library migration uses the same Model Service binding as saved chats."""
import json
from pathlib import Path

import pytest

from pantheon.chatroom.migration_templates import template_edits, apply_edits
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.factory.template_io import UnifiedMarkdownParser
from pantheon.factory.template_manager import TemplateManager
from pantheon.settings import Settings
from test_agent_migration import legacy
from test_agent_migration_import import prepared
from test_agent_migration_models import entries, plan, restore


AGENT = '''---
id: researcher
name: Researcher
model: 'vendor/research+think:high' # keep the comment
toolsets: [shell, file_manager]
tags: [science]
---
中文 instructions: keep model: vendor/research+think:high here.
---
Keep this instruction section, whitespace, and **Markdown**.
'''
TEAM = '''---
type: team
id: research-team
name: Research team
agents: [researcher, reviewer]
reviewer:
  id: actual-reviewer
  name: Reviewer
  model: vendor/review
  toolsets: [think]
---
Review without changing the instructions.
'''


def library(spec):
    config = Path(spec['project_config'])
    agent = config / 'agents/researcher.md'
    team = config / 'teams/research-team.md'
    agent.write_bytes(AGENT.replace('\n', '\r\n').encode())
    team.write_text(TEAM)
    return [dict(path=str(agent), config_id='researcher', source='vendor/research+think:high',
                 target='fleet-route://research+think:high'),
            dict(path=str(team), config_id='actual-reviewer', source='vendor/review',
                 target='fleet-route://review')]


@pytest.fixture
def templates_prepared(legacy, tmp_path):
    mappings = library(legacy)
    # Populate the library before inventory/fencing and immutable backup.
    for value in prepared.__wrapped__(legacy, tmp_path):
        yield (*value, mappings)


def test_import_preserves_bytes_and_resolves_library_team(templates_prepared):
    spec, fence, backup, root, mappings = templates_prepared
    originals = {row['path']: Path(row['path']).read_bytes() for row in mappings}
    selection = plan(backup, fence, entries(), templates=mappings)
    restore(backup, fence, selection)
    for row in mappings:
        destination = root / 'configuration/.pantheon' / Path(row['path']).relative_to(spec['project_config'])
        token = "'" + row['source'] + "'" if row['config_id'] == 'researcher' else row['source']
        expected = originals[row['path']].replace(token.encode(), json.dumps(row['target']).encode(), 1)
        assert destination.read_bytes() == expected
        assert Path(row['path']).read_bytes() == originals[row['path']]
    parsed = UnifiedMarkdownParser().parse_file(root / 'configuration/.pantheon/agents/researcher.md')
    assert parsed.model == 'fleet-route://research+think:high'
    assert parsed.toolsets == ['shell', 'file_manager']
    assert 'model: vendor/research+think:high' in parsed.instructions
    settings = Settings(root / 'configuration', user_home=root / 'user', isolated_env=True, environment={})
    manager = TemplateManager(settings=settings, seed_settings=False)
    team = manager.get_template('research-team')
    assert [a.model for a in team.agents] == ['fleet-route://research+think:high', 'fleet-route://review']
    payloads, tools, _ = manager.prepare_team(team)
    assert payloads['researcher']['model'] == parsed.model
    assert payloads['researcher']['instructions'] == parsed.instructions
    assert {'shell', 'file_manager'} <= tools
    assert selection.audit()['templates'] == sorted(mappings, key=lambda row: (row['path'], row['config_id']))


@pytest.mark.parametrize('problem', ['missing', 'stale', 'wrong-member', 'outside-library',
                                    'duplicate', 'reasoning', 'list', 'relative-path'])
def test_bad_template_mapping_fails_before_destination_creation(templates_prepared, problem):
    _, fence, backup, root, mappings = templates_prepared
    if problem == 'missing': mappings.pop()
    elif problem == 'stale': mappings[0]['source'] = 'vendor/stale+think:high'
    elif problem == 'wrong-member': mappings[0]['config_id'] = 'different-member'
    elif problem == 'outside-library': mappings[0]['path'] += '.unrelated'
    elif problem == 'duplicate': mappings.append(dict(mappings[0]))
    elif problem == 'reasoning': mappings[0]['target'] = 'fleet-route://research'
    elif problem == 'list': mappings[0]['source'] = [mappings[0]['source']]
    else: mappings[0]['path'] = 'agents/researcher.md'
    with pytest.raises(ValueError):
        restore(backup, fence, plan(backup, fence, entries(), templates=mappings))
    assert not root.exists()


@pytest.mark.parametrize('model', ['normal', 'high+think:low', 'normal,vision', 'fleet-route://preserved', '""'])
def test_pinned_tiers_and_existing_fleet_references_preserved(prepared, model):
    _, fence, backup, _ = prepared
    selection = plan(backup, fence, entries())
    raw = f'---\nid: example\nmodel: {model}\n---\nPrompt\n'.encode()
    edits, used = template_edits(raw, path='/example.md', selection=selection)
    assert apply_edits(raw, edits) == raw and not used


@pytest.mark.parametrize('header', [
    'id: example\nmodel: one\nmodel: two',
    'id: example\nmodel: [one, two]',
    'id: example\nbase: &base {model: normal}\n<<: *base',
    'id: example\nmodel: *missing',
    'id: example\nmodel: !execute normal',
    'id: example\nmodel: [unfinished',
    'id: example\nmodel: normal\nnested: {duplicate: one, duplicate: two}',
    'id: team\ntype: team\nagents: [x, x]',
    'id: team\ntype: team\nagents: [x, y]\nx: {id: repeated, model: normal}\ny: {id: repeated, model: high}',
])
def test_ambiguous_templates_are_not_silently_rewritten(prepared, header):
    _, fence, backup, _ = prepared
    selection = plan(backup, fence, entries())
    with pytest.raises(ValueError):
        template_edits(('---\n' + header + '\n---\nPrompt').encode(), path='/example.md', selection=selection)


def test_prompt_body_is_never_parsed_as_metadata(prepared):
    _, fence, backup, _ = prepared
    selection = plan(backup, fence, entries())
    raw = b'---\nid: example\nmodel: normal\n---\nExecute !unsafe &anchor\n---\nmodel: vendor/other\n'
    assert template_edits(raw, path='/example.md', selection=selection) == ([], set())
    assert template_edits(b'Plain notes\nmodel: vendor/other', path='/example.md', selection=selection) == ([], set())
    assert template_edits(b'---\nid: inherited\n---\nInstructions', path='/example.md', selection=selection) == ([], set())


@pytest.mark.parametrize('style', ['|-', '>-'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_block_scalar_model_keeps_following_fields_separate(prepared, style, newline):
    _, fence, backup, root = prepared
    selection = plan(backup, fence, entries(), templates=[dict(
        path='/example.md', config_id='example', source='vendor/model', target='fleet-route://chosen')])
    original = f'---\nid: example\nmodel: {style}\n  vendor/model\ntoolsets: [shell]\n---\nPrompt\n'
    raw = original.replace('\n', newline).encode()
    edits, used = template_edits(raw, path='/example.md', selection=selection)
    converted = apply_edits(raw, edits)
    assert converted == original.replace(f'{style}\n  vendor/model', '"fleet-route://chosen"').replace('\n', newline).encode()
    output = root.parent / 'converted.md'
    output.write_bytes(converted)
    parsed = UnifiedMarkdownParser().parse_file(output)
    assert parsed.model == 'fleet-route://chosen' and parsed.toolsets == ['shell']
    selection.require_templates(used)


def test_pending_resume_pins_template_mapping(templates_prepared, monkeypatch):
    from pantheon.chatroom import migration_import
    _, fence, backup, root, mappings = templates_prepared
    selection = plan(backup, fence, entries(), templates=mappings)
    real_copy = migration_import._copy
    def crash(*args): raise OSError('interrupted')
    monkeypatch.setattr(migration_import, '_copy', crash)
    with pytest.raises(OSError): restore(backup, fence, selection)
    changed = [dict(row) for row in mappings]
    changed[1]['target'] = 'fleet-route://different'
    with pytest.raises(ValueError, match='different migration'):
        restore(backup, fence, plan(backup, fence, entries(), templates=changed))
    monkeypatch.setattr(migration_import, '_copy', real_copy)
    restore(backup, fence, selection)
    assert json.loads((root / 'migration.json').read_text())['phase'] == 'committed'


def test_empty_history_migrates_project_and_global_template_libraries(legacy, tmp_path):
    for path in Path(legacy['home_memory']).iterdir():
        path.unlink()
    (Path(legacy['project_config']) / 'settings.json').write_text('{}')
    mappings = library(legacy)
    global_agent = Path(legacy['global_config']) / 'agents/global-reader.md'
    global_agent.parent.mkdir()
    global_agent.write_text('---\nid: global-reader\nname: Reader\nmodel: vendor/global\n---\nGlobal instructions.\n')
    mappings.append(dict(path=str(global_agent), config_id='global-reader', source='vendor/global',
                         target='fleet-route://global'))
    root = tmp_path / 'app'
    fence = fence_legacy(legacy, operation='move', target=root, namespace='migrated-agent')
    try:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
        selection = plan(backup, fence, [], templates=mappings)
        receipt = restore(backup, fence, selection)
        assert receipt['conversations'] == 0 and receipt['members'] == []
        parsed = UnifiedMarkdownParser().parse_file(root / 'user/agents/global-reader.md')
        assert parsed.model == 'fleet-route://global'
        assert parsed.instructions == 'Global instructions.'
    finally:
        fence.close()
