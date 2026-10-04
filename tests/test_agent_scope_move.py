"""Scope moves preserve explicit App ownership, legacy paths and existing data."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from pantheon.chatroom.app_models import AppSettings
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.factory.scope_move import move_template_scope
from test_agent_model_scope import endpoint as model_endpoint


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def settings(tmp_path):
    return AppSettings(tmp_path / 'app', defaults={}, environment={})


@pytest.mark.parametrize('kind', ['agents', 'teams', 'skills'])
def test_moves_both_directions_with_owned_roots_without_home(settings, monkeypatch, kind):
    source = getattr(settings, kind + '_dir') / ('bundle/SKILL.md' if kind == 'skills' else 'nested/example.md')
    write(source, 'owned definition')
    if kind == 'skills':
        write(source.parent / 'script.py', 'print(1)')
    def no_home(*args):
        raise AssertionError('Scope move looked up OS-user home')
    monkeypatch.setattr(Path, 'home', no_home)
    result = move_template_scope(settings, kind, str(source), 'global')
    assert result['success']
    destination = getattr(settings, 'global_' + kind + '_dir') / source.relative_to(getattr(settings, kind + '_dir'))
    assert destination.read_text() == 'owned definition'
    assert not source.exists()
    if kind == 'skills':
        assert (destination.parent / 'script.py').read_text() == 'print(1)'
    result = move_template_scope(settings, kind, str(destination), 'project')
    assert result['success'] and source.read_text() == 'owned definition'
    assert not destination.exists()


@pytest.mark.parametrize('prefix', ['', 'agents/', '.pantheon/agents/', '.pantheon/'])
def test_legacy_relative_paths_remain_usable(settings, prefix):
    source = write(settings.agents_dir / 'nested/test.md', 'source')
    assert move_template_scope(settings, 'agents', prefix + 'nested/test.md', 'global')['success']
    assert not source.exists()
    assert (settings.global_agents_dir / 'nested/test.md').read_text() == 'source'


def test_same_scope_is_noop_and_conflict_preserves_both(settings):
    source = write(settings.agents_dir / 'test.md', 'source')
    target = write(settings.global_agents_dir / 'test.md', 'target')
    assert move_template_scope(settings, 'agents', str(source), 'project')['success']
    assert move_template_scope(settings, 'agents', str(source), 'global')['conflict']
    assert source.read_text() == 'source' and target.read_text() == 'target'
    assert move_template_scope(settings, 'agents', str(source), 'global', True)['success']
    assert not source.exists() and target.read_text() == 'source'
    assert not list(settings.work_dir.rglob('.scope-move-*'))
    assert not list(settings.user_home.rglob('.scope-move-*'))


@pytest.mark.parametrize('bad', ['outside', 'prefix', 'traversal', 'root', 'link', 'nested-link', 'target-link'])
def test_rejects_escape_or_root_moves(settings, tmp_path, bad):
    source = write(settings.skills_dir / 'bundle/SKILL.md', 'definition')
    outside = write(tmp_path / 'outside/secret', 'private')
    value = str(source.parent)
    if bad == 'outside':
        value = str(outside)
    elif bad == 'prefix':
        value = str(write(settings.skills_dir.with_name('skills-other') / 'SKILL.md', 'outside'))
    elif bad == 'traversal':
        value = '.pantheon/skills/../settings.json'
    elif bad == 'root':
        value = str(settings.skills_dir)
    elif bad == 'link':
        link = settings.skills_dir / 'linked'
        link.symlink_to(outside.parent, target_is_directory=True)
        value = str(link)
    elif bad == 'nested-link':
        (source.parent / 'secret').symlink_to(outside)
    elif bad == 'target-link':
        settings.global_skills_dir.mkdir(parents=True)
        (settings.global_skills_dir / 'bundle').symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        move_template_scope(settings, 'skills', value, 'global', True)
    assert source.read_text() == 'definition'
    assert outside.read_text() == 'private'


@pytest.mark.parametrize('stage', ['copy', 'publish'])
def test_failed_move_restores_source_and_previous_target(settings, monkeypatch, stage):
    source = write(settings.skills_dir / 'bundle/SKILL.md', 'new')
    target = write(settings.global_skills_dir / 'bundle/SKILL.md', 'old')
    import pantheon.factory.scope_move as module
    replace = module.os.replace
    def fail_publish(src, dst):
        if Path(src).name == 'payload':
            raise OSError('publish failed')
        return replace(src, dst)
    def fail_copy(*args, **kwargs):
        raise OSError('copy failed')
    monkeypatch.setattr(module.os, 'replace', fail_publish)
    if stage == 'copy':
        monkeypatch.setattr(module.shutil, 'copytree', fail_copy)
    with pytest.raises(OSError, match=stage):
        move_template_scope(settings, 'skills', str(source), 'global', True)
    assert source.read_text() == 'new' and target.read_text() == 'old'
    assert not list(settings.work_dir.rglob('.scope-move-*'))
    assert not list(settings.user_home.rglob('.scope-move-*'))


@pytest.mark.asyncio
async def test_runtime_rpc_uses_owned_settings_and_inspects_only_validated_team(settings, tmp_path):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._settings = lambda: settings
    reads = []
    def read_team(path):
        reads.append(path)
        return SimpleNamespace(agents=[SimpleNamespace(id='member', source_path='agents/local.md')])
    runtime.template_manager = SimpleNamespace(file_manager=SimpleNamespace(_read_team_from_path=read_team))
    source = write(settings.teams_dir / 'team.md', 'definition')
    result = await runtime.change_template_scope('teams', str(source), 'global')
    assert result['success'] and len(result['warnings']) == 1
    assert reads == [source]
    outside = write(tmp_path / 'outside.md', 'private')
    result = await runtime.change_template_scope('teams', str(outside), 'global')
    assert not result['success']
    assert reads == [source]


@pytest.mark.asyncio
async def test_actual_native_rpc_moves_skill_inside_private_app_data(tmp_path, model_endpoint):
    from test_agent_native_process import native_process, request
    from test_agent_settings_process import ready
    with native_process(tmp_path, model_endpoint.url) as (child, base):
        await ready(child, base, tmp_path)
        source = write(tmp_path / 'data/agent/configuration/.pantheon/skills/owned-test/SKILL.md', '# Owned skill')
        destination = tmp_path / 'data/agent/user/skills/owned-test/SKILL.md'
        response = await request(base, '/rpc', {'method': 'change_template_scope', 'args': {
            'kind': 'skills', 'source_path': str(source), 'target_scope': 'global'}})
        assert response['success'] and response['result']['success'], response
        assert destination.read_text() == '# Owned skill'
        assert not source.exists()
        assert not (tmp_path / 'home/.pantheon/skills/owned-test').exists()
