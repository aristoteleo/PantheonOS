"""Imported default teams and saved instructions cannot read the old library."""
import json
from pathlib import Path

import pytest

from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_templates import apply_edits, prompt_reference_edits
from pantheon.factory.template_io import PromptResolver
from pantheon.factory.template_manager import TemplateManager
from pantheon.settings import Settings
from test_agent_migration import legacy
from test_agent_migration_environment import source_files, backed_up


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace('\n', '\r\n').encode())


def prompt_library(spec, tmp_path):
    _, settings_path, _ = source_files(spec, tmp_path, 'https://unused.example')
    project, user = Path(spec['project_config']), Path(spec['global_config'])
    agent = project / 'agents/researcher.md'
    prompt = project / 'prompts/local.md'
    shared = user / 'prompts/shared.md'
    leaf = user / 'prompts/nested/leaf.md'
    write(shared, '---\nparams:\n  label: {type: string, default: original}\n---\n共享 {label}: {{./nested/leaf.md}}\n')
    write(leaf, 'Leaf content: keep ordinary paths /old/example.md and model: original.\n')
    write(prompt, '{{' + str(shared) + '(label="kept")}}\n${{/escaped/example.md}}\n')
    body = 'Default instructions {{local}}\nDirect {{' + str(prompt) + '}}\nLiteral {{TITLE}}.\n'
    write(agent, '---\nid: researcher\nname: Researcher\nmodel: normal\ntoolsets: []\n---\n' + body)
    write(project / 'teams/default.md', '---\nid: default\nname: Default\ntype: team\nagents: [researcher]\n---\n')
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(spec['home_memory']) / name
        saved = json.loads(path.read_text())
        member = saved['extra_data']['team_template']['agents'][0]
        member.update(source_path=str(agent), instructions=body)
        path.write_text(json.dumps(saved))
    return agent, prompt, shared, leaf


def manager(root, user):
    return TemplateManager(settings=Settings(root, user_home=user, isolated_env=True, environment={}), seed_settings=False)


def test_default_and_saved_teams_expand_nested_prompts_from_imported_files_only(legacy, tmp_path, monkeypatch):
    originals = prompt_library(legacy, tmp_path)
    old = manager(Path(legacy['project_config']).parent, Path(legacy['global_config']))
    expected = old.prepare_team(old.get_template('default'))[0]['researcher']['instructions']
    assert expected.count('共享 kept: Leaf content:') == 2
    assert '${{/escaped/example.md}}' in expected and '{{TITLE}}' in expected
    original_bytes = {path: path.read_bytes() for path in originals}
    saved_expected = {}
    for name in ('chat-a.meta.json', 'chat-b.json'):
        saved = json.loads((Path(legacy['home_memory']) / name).read_text())
        team = old.dict_to_team_config(saved['extra_data']['team_template'])
        saved_expected[name] = old.prepare_team(team)[0][team.agents[0].id]['instructions']
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        assert import_backup(backup['directory'], digest=backup['sha256'], fence=fence) == receipt
        assert all(path.read_bytes() == data for path, data in original_bytes.items())
        read_text = Path.read_text
        def no_legacy_read(path, *args, **kwargs):
            assert not any(path.is_relative_to(Path(legacy[key])) for key in ('project_config', 'global_config')), \
                'Template expansion read the pre-migration configuration'
            return read_text(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', no_legacy_read)
        migrated = manager(root / 'configuration', root / 'user')
        assert migrated.prepare_team(migrated.get_template('default'))[0]['researcher']['instructions'] == expected
        for name in ('chat-a.meta.json', 'chat-b.json'):
            saved = json.loads(next((root / 'conversations').rglob(name)).read_text())
            team = migrated.dict_to_team_config(saved['extra_data']['team_template'])
            assert migrated.prepare_team(team)[0][team.agents[0].id]['instructions'] == saved_expected[name]
        converted = (root / 'configuration/.pantheon/prompts/local.md').read_bytes()
        assert b'../../../user/prompts/shared.md' in converted
        assert b'(label="kept")' in converted and b'\r\n' in converted
        assert (root / 'user/prompts/nested/leaf.md').read_bytes() == original_bytes[originals[-1]]


@pytest.mark.parametrize('where', ['agent', 'prompt', 'saved'])
def test_unbacked_prompt_reference_prevents_partial_import(legacy, tmp_path, where):
    agent, prompt, _, _ = prompt_library(legacy, tmp_path)
    unknown = '{{/external/not-backed-up.md}}'
    if where == 'saved':
        path = Path(legacy['home_memory']) / 'chat-b.json'
        saved = json.loads(path.read_text())
        saved['extra_data']['team_template']['agents'][0]['instructions'] = unknown
        path.write_text(json.dumps(saved))
    else:
        path = agent if where == 'agent' else prompt
        path.write_text(path.read_text() + unknown)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        with pytest.raises(ValueError, match='outside the backed-up library'):
            import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        assert not root.exists()


def test_interrupted_prompt_import_resumes_exact_transformed_bytes(legacy, tmp_path, monkeypatch):
    from pantheon.chatroom import migration_import
    prompt_library(legacy, tmp_path)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        copy = migration_import._copy
        count = 0
        def interrupted(*args):
            nonlocal count
            copy(*args)
            count += 1
            if count == 6: raise OSError('interrupted')
        monkeypatch.setattr(migration_import, '_copy', interrupted)
        with pytest.raises(OSError): import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        assert json.loads((root / 'migration.json').read_text())['phase'] == 'importing'
        monkeypatch.setattr(migration_import, '_copy', copy)
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        for row in receipt['files']:
            from hashlib import sha256
            assert sha256((root / row['target']).read_bytes()).hexdigest() == row['sha256']


@pytest.mark.parametrize('header', [
    b'---\nexample: "{{/old/example.md}}"\n---\n',
    b'+++\nexample = "{{/old/example.md}}"\n+++\n',
    b'{\n"example": "{{/old/example.md}}"\n}\n',
])
def test_prompt_relocation_changes_only_path_tokens_and_keeps_metadata_and_parameters(header):
    raw = header + b'{{/old/example.md(name="/literal/path")}}\r\n${{/not/a/reference.md}} {{namespaced/id}}'
    mapping = {'/old/agent.md': '/new/agent.md', '/old/example.md': '/new/prompt.md'}
    edits = prompt_reference_edits(raw, path='/old/agent.md', relocations=mapping)
    assert apply_edits(raw, edits) == raw.replace(b'{{/old/example.md(name=', b'{{./prompt.md(name=')
    # Relative references with unchanged meaning retain their original spelling.
    assert prompt_reference_edits(b'{{./example.md}}', path='/old/agent.md',
        relocations={**mapping, '/old/example.md': '/new/example.md'}) == []


def test_saved_relative_reference_needs_source_and_output_must_match_resolver_syntax():
    with pytest.raises(ValueError, match='original template source'):
        prompt_reference_edits(b'{{../prompt.md}}', path=None, relocations={}, body_only=True)
    with pytest.raises(ValueError, match='cannot be represented'):
        prompt_reference_edits(b'{{/old/prompt.md}}', path=None,
            relocations={'/old/prompt.md': '/new path/prompt.md'}, body_only=True)


@pytest.mark.parametrize('scope', ['project', 'global', 'factory'])
def test_named_prompt_nested_paths_and_default_path_parameters_use_actual_source(tmp_path, scope):
    roots = {name: tmp_path / name for name in ('project', 'global', 'factory')}
    actual = roots[scope] / 'folder'
    write(actual / 'outer.md', '---\nparams:\n  root: {type: path, default: ./resources}\n---\n{root}: {{./inner.md}}\n')
    write(actual / 'inner.md', 'Correct scope.\n')
    write(roots['factory'] / 'inner.md', 'Wrong factory-root scope.\n')
    resolver = PromptResolver(prompts_dir=roots['factory'], user_prompts_dir=roots['project'], global_prompts_dir=roots['global'])
    expected = str(actual / 'resources') + ': Correct scope.'
    for _ in range(2):  # The cached call must keep its origin too.
        assert resolver.resolve('{{folder/outer}}', base_path=tmp_path) == expected
    # Passed paths still resolve against the caller, preserving the public API.
    assert resolver.resolve('{{folder/outer(root="./caller")}}', base_path=tmp_path) == str(tmp_path / 'caller') + ': Correct scope.'
    assert len(resolver._load_prompt('folder/outer')) == 2
    resolver.clear_cache()
    assert resolver.resolve('{{folder/outer}}', base_path=tmp_path) == expected
