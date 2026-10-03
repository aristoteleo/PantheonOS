"""Real plugin runtimes keep data, skill layers and work inside their owner."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from pantheon.chatroom.lifecycle import AgentLifetime
from pantheon.internal.learning_system.plugin import _create_learning_plugin
from pantheon.internal.memory_system.plugin import _create_memory_plugin
from pantheon.internal.memory_system.types import MemoryEntry, MemoryType
from pantheon.settings import Settings
from pantheon.team.plugin_registry import create_plugins


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def skill(label):
    return f'---\nname: shared\ndescription: {label}\n---\n\n{label}\n'


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Scoped plugin used ambient settings')

    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.delenv('PANTHEON_EXCLUDED_SKILLS', raising=False)
    result = []
    for label in ('stable', 'candidate'):
        root = tmp_path / label
        settings = Settings(root / 'data', isolated_env=True, user_home=root / 'user')
        settings.package_templates = root / 'release' / 'templates'
        # Concrete model IDs avoid exercising the still-global tier resolver.
        write(settings.package_templates / 'settings.json', json.dumps({
            'memory_system': {'enabled': True, 'selection_model': 'openai/gpt-4o-mini'},
            'learning_system': {'enabled': True, 'model': 'openai/gpt-4o-mini'},
        }))
        write(settings.package_templates / 'mcp.json', '{}')
        write(settings.work_dir / '.env', 'PANTHEON_EXCLUDED_SKILLS=' + label + '\n')
        result.append(settings)
    return result


def pair(settings):
    plugins = create_plugins(settings)
    assert len(plugins) == 2
    return {type(p).__name__: p for p in plugins}


def test_registry_creates_owned_runtime_state_even_for_repeated_config(scopes):
    one, two = (pair(s) for s in scopes)
    for name in one:
        assert one[name].runtime is not two[name].runtime
        assert one[name].runtime.pantheon_dir == scopes[0].pantheon_dir
        assert two[name].runtime.pantheon_dir == scopes[1].pantheon_dir
    memory1, memory2 = (p['MemorySystemPlugin'].runtime for p in (one, two))
    memory1.set_active_model('provider-a/model')
    memory2.set_active_model('provider-b/model')
    assert memory1.resolve_model('auto') == 'provider-a/model'
    assert memory2.resolve_model('auto') == 'provider-b/model'
    memory1._shown_memories['same-session'] = {'a.md'}
    assert not memory2._shown_memories
    memory1.increment_session()
    assert memory2.dream_gate._session_counter == 0
    # Sharing the settings object does not implicitly share mutable runtime state.
    again = pair(scopes[0])
    for name in one:
        assert again[name].runtime is not one[name].runtime


@pytest.mark.asyncio
async def test_memory_writes_guidance_and_retrieval_use_same_owner_root(scopes):
    plugins = [_create_memory_plugin({}, s) for s in scopes]
    for plugin, settings, label in zip(plugins, scopes, ('stable', 'candidate')):
        path = plugin.runtime.write_memory(MemoryEntry(title=label, summary=label,
            type=MemoryType.WORKFLOW, content=label + ' contents'))
        assert path.is_relative_to(settings.pantheon_dir)
        assert label in plugin.runtime.load_bootstrap_memory()
        agent = SimpleNamespace(name='same-agent', instructions='Base')
        team = SimpleNamespace(team_agents=[agent])
        await plugin.on_team_created(team)
        assert str(settings.pantheon_dir) in agent.instructions
        other = scopes[1] if settings is scopes[0] else scopes[0]
        assert str(other.pantheon_dir) not in agent.instructions

        async def retrieve(**kwargs):
            return [SimpleNamespace(path=path, content=label,
                entry=SimpleNamespace(title=label, summary=label), age_text='today')]

        plugin.runtime.retrieve_relevant = retrieve
        output = await plugin.on_run_start(team, 'query', {})
        assert f'Memory base: {settings.pantheon_dir}' in output
        assert str(path.relative_to(settings.pantheon_dir)) in output
    assert 'candidate' not in plugins[0].runtime.load_bootstrap_memory()
    assert 'stable' not in plugins[1].runtime.load_bootstrap_memory()


@pytest.mark.asyncio
async def test_learning_reads_own_user_factory_and_private_env(scopes, monkeypatch):
    for settings, label in zip(scopes, ('stable', 'candidate')):
        write(settings.global_skills_dir / 'shared' / 'SKILL.md', skill(label + '-user'))
        write(settings.factory_skills_dir / 'shared' / 'SKILL.md', skill(label + '-factory'))
        write(settings.factory_skills_dir / label / 'SKILL.md', skill(label))
    plugins = [_create_learning_plugin({}, s) for s in scopes]
    monkeypatch.setenv('PANTHEON_EXCLUDED_SKILLS', 'shared')
    for plugin, settings, label in zip(plugins, scopes, ('stable', 'candidate')):
        store = plugin.runtime.store
        assert store.excluded_skills == {label}
        assert store.load_skill(label) is None
        assert label + '-user' in store.load_skill('shared').content
        settings.global_skills_dir.joinpath('shared', 'SKILL.md').unlink()
        assert label + '-factory' in store.load_skill('shared').content
        write(settings.skills_dir / 'shared' / 'SKILL.md', skill(label + '-project'))
        assert label + '-project' in store.load_skill('shared').content
        agent = SimpleNamespace(name='a', instructions='Base')
        await plugin.on_team_created(SimpleNamespace(team_agents=[agent]))
        assert str(settings.pantheon_dir) in agent.instructions
        other = scopes[1] if settings is scopes[0] else scopes[0]
        assert str(other.pantheon_dir) not in agent.instructions


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['memory', 'learning'])
async def test_app_shutdown_waits_for_own_plugin_work_without_stopping_sibling(scopes, kind):
    make = _create_memory_plugin if kind == 'memory' else _create_learning_plugin
    plugins = [make({}, s) for s in scopes]
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    finished = []
    for index, plugin in enumerate(plugins):
        async def extract(*args, index=index, **kwargs):
            started[index].set()
            await release[index].wait()
            write(scopes[index].pantheon_dir / 'completed.txt', kind)
            finished.append(index)

        if kind == 'memory':
            plugin.runtime.maybe_extract_memories = extract
            # Other memory hooks are still invoked, with no model/network needed.
            async def no_work(*args, **kwargs):
                return None
            plugin.runtime.maybe_update_session_note = no_work
            plugin.runtime.maybe_run_dream = no_work
        else:
            plugin.runtime.maybe_extract_skills = extract
        await plugin.on_run_end(SimpleNamespace(plugins=[]), {
            'chat_id': 'same-session', 'messages': [{'role': 'user', 'content': 'hello'}]})
    await asyncio.wait_for(asyncio.gather(*(s.wait() for s in started)), 1)
    owner = AgentLifetime()
    owner._plugins = [plugins[0]]
    closing = asyncio.create_task(owner.cleanup())
    try:
        await asyncio.sleep(0)
        assert not closing.done()
        release[0].set()
        await asyncio.wait_for(closing, 1)
        assert finished == [0]
        assert plugins[1]._background_tasks
        # Late hooks on the stopped plugin cannot schedule additional work.
        await plugins[0].on_run_end(SimpleNamespace(plugins=[]), {
            'messages': [{'role': 'user', 'content': 'late'}]})
        assert not plugins[0]._background_tasks
        assert not plugins[1]._stopping
    finally:
        for gate in release:
            gate.set()
        await asyncio.gather(closing, *(p.on_shutdown() for p in plugins))
    assert finished == [0, 1]


@pytest.mark.asyncio
async def test_cancelled_shutdown_observer_does_not_cancel_accepted_write(scopes):
    plugin = _create_memory_plugin({}, scopes[0])
    started, release = asyncio.Event(), asyncio.Event()
    path = scopes[0].pantheon_dir / 'completed.txt'

    async def write_later():
        started.set()
        await release.wait()
        write(path, 'saved')

    plugin._fire(write_later())
    await started.wait()
    closing = asyncio.create_task(plugin.on_shutdown())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert plugin._background_tasks
    late = write_later()
    plugin._fire(late)
    assert late.cr_frame is None
    release.set()
    await asyncio.wait_for(plugin.on_shutdown(), 1)
    await plugin.on_shutdown()
    assert path.read_text() == 'saved'
    assert not plugin._background_tasks
