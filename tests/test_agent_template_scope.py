"""App-owned settings/templates cannot select another deployment's definitions."""
from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_io import FileBasedTemplateManager, PromptResolver
from pantheon.factory.template_manager import TemplateManager
from pantheon.settings import Settings


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('App templates consulted process-global settings')

    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.setenv('PANTHEON_FACTORY_TEMPLATE_MODE', 'runtime')
    sentinel = PromptResolver(tmp_path / 'legacy-prompts')
    monkeypatch.setattr('pantheon.factory.template_io._prompt_resolver', sentinel)
    managers = []
    for label in ('stable', 'candidate'):
        root = tmp_path / label
        settings = Settings(root / 'data', isolated_env=True, user_home=root / 'user-config')
        settings.package_templates = root / 'release' / 'templates'
        write(settings.package_templates / 'settings.json', '{}')
        write(settings.package_templates / 'mcp.json', '{}')
        manager = TemplateManager(settings=settings)
        assert manager.settings is settings and manager.file_manager.settings is settings
        managers.append(manager)
    return managers, sentinel


def definition():
    return TeamConfig(id='team', name='Team', description='', agents=[AgentConfig(
        id='member', name='Member', model='openai/gpt-4o-mini', instructions='{{scope}}',
        toolsets=['shell'], mcp_servers=['docs'])])


def test_two_deployments_resolve_same_definition_without_global_or_input_mutation(scopes):
    from pantheon.factory import template_io
    managers, sentinel = scopes
    for manager, label in zip(managers, ('stable', 'candidate')):
        write(manager.prompts_dir / 'scope.md', '{{inner}}')
        write(manager.prompts_dir / 'inner.md', label)
    team = definition()

    def prepare(manager):
        for _ in range(4):
            payload, tools, mcps = manager.prepare_team(team)
            assert tools == {'shell'} and mcps == {'docs'}
            assert team.agents[0].instructions == '{{scope}}'
        return payload['member']['instructions']

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(prepare, managers)) == ['stable', 'candidate']
    assert template_io._prompt_resolver is sentinel
    assert team.agents[0].toolsets == ['shell']


def test_prompt_edits_and_deleted_overrides_affect_next_snapshot_only(scopes):
    from pantheon.factory import template_io
    managers, _ = scopes
    for manager, label in zip(managers, ('stable', 'candidate')):
        write(manager.prompts_dir / 'scope.md', label)
        write(manager.settings.global_prompts_dir / 'scope.md', label + '-user')
        write(manager.system_templates_dir / 'prompts' / 'scope.md', label + '-factory')
    team = definition()
    first, _, _ = managers[0].prepare_team(team)
    write(managers[0].prompts_dir / 'scope.md', 'edited')
    assert managers[0].prepare_team(team)[0]['member']['instructions'] == 'edited'
    assert first['member']['instructions'] == 'stable'
    managers[0].prompts_dir.joinpath('scope.md').unlink()
    assert managers[0].prepare_team(team)[0]['member']['instructions'] == 'stable-user'
    managers[0].settings.global_prompts_dir.joinpath('scope.md').unlink()
    assert managers[0].prepare_team(team)[0]['member']['instructions'] == 'stable-factory'
    template_io.init_prompt_resolver(user_prompts_dir=managers[0].prompts_dir)
    assert managers[1].prepare_team(team)[0]['member']['instructions'] == 'candidate'


def test_template_catalog_and_team_references_use_owner_scopes(scopes):
    managers, _ = scopes
    for manager, label in zip(managers, ('stable', 'candidate')):
        for location, suffix in ((manager.settings.global_agents_dir, 'user'),
                                 (manager.system_templates_dir / 'agents', 'factory')):
            agent = AgentConfig(id='scoped-reader', name=label + '-' + suffix,
                                model='openai/gpt-4o-mini', instructions='Read')
            write(location / 'scoped-reader.md', manager.file_manager.parser.generate_agent(agent))
        team = TeamConfig(id='scoped-team', name=label, description='', agents=[
            AgentConfig(id='scoped-reader', name='', model='')])
        write(manager.settings.global_teams_dir / 'scoped-team.md',
              manager.file_manager.parser.generate_team(team))
    for manager, label in zip(managers, ('stable', 'candidate')):
        assert manager.get_template('scoped-team').agents[0].name == label + '-user'
        reader = manager.file_manager.read_agent('scoped-reader')
        assert reader.name == label + '-user'
        summary = manager.list_template_files('teams', view='summary')
        assert summary['success']
        assert summary['files'][0]['agent_refs'][0]['name'] == label + '-user'
        assert {a.name for a in manager.file_manager.list_agents()} == {label + '-user'}
        assert manager.write_template_file('agents/scoped-reader.md', {
            'id': 'scoped-reader', 'name': label + '-project', 'model': 'openai/gpt-4o-mini',
            'instructions': 'Project override'})['success']
        assert manager.file_manager.read_agent('scoped-reader').name == label + '-project'
        # Keep the existing protection against deleting referenced definitions.
        assert not manager.delete_template_file('agents/scoped-reader.md')['success']
        (manager.settings.global_teams_dir / 'scoped-team.md').unlink()
        assert manager.delete_template_file('agents/scoped-reader.md')['success']
        assert manager.file_manager.read_agent('scoped-reader').name == label + '-user'


def test_settings_scopes_do_not_modify_environment_or_other_deployment(scopes, monkeypatch):
    managers, _ = scopes
    key = 'PANTHEON_SCOPE_TEST_KEY'
    monkeypatch.delenv(key, raising=False)
    before = dict(os.environ)
    for manager, label in zip(managers, ('stable', 'candidate')):
        settings = manager.settings
        write(settings.user_home / 'settings.json', json.dumps({'test': {'user': label}}))
        write(settings.pantheon_dir / 'settings.json', json.dumps({'test': {'project': label}}))
        write(settings.user_home / 'mcp.json', json.dumps({'mcpServers': {label: {'command': label}}}))
        write(settings.work_dir / '.env', key + '=' + label)
        settings.reload()
        assert settings.get('test') == {'user': label, 'project': label}
        assert settings.get_api_key(key) == label
        assert set(settings._mcp['mcpServers']) == {label}
    managers[0].settings.persist_project_value('test.project', 'changed')
    managers[0].settings.reload()
    assert managers[0].settings.get('test.project') == 'changed'
    assert managers[1].settings.get('test.project') == 'candidate'
    assert managers[1].settings.get_api_key(key) == 'candidate'
    assert dict(os.environ) == before


@pytest.mark.parametrize('cls', [TemplateManager, FileBasedTemplateManager])
def test_conflicting_owners_rejected_before_bootstrap(tmp_path, cls):
    root = tmp_path / 'should-not-be-created'
    with pytest.raises(ValueError, match='not both'):
        cls(work_dir=root, settings=Settings(tmp_path / 'other'))
    assert not root.exists()


def test_builtin_fallback_and_parameterized_prompt_preserved(scopes):
    manager = scopes[0][0]
    write(manager.system_templates_dir / 'prompts' / 'scope.md',
          '---\nparams:\n  task:\n    type: string\n    default: default-task\n---\nDo {task}. {{nested}}')
    write(manager.system_templates_dir / 'prompts' / 'nested.md', 'Use the bound tools.')
    team = definition()
    team.agents[0].instructions = '{{scope(task="analysis")}}'
    assert manager.prepare_team(team)[0]['member']['instructions'] == 'Do analysis. Use the bound tools.'
    assert team.agents[0].instructions == '{{scope(task="analysis")}}'
