"""Real plugin runtimes keep data, skill layers and work inside their owner."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from pantheon.chatroom.lifecycle import AgentLifetime
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.apps.host_lifecycle import AppShutdownError
from pantheon.internal.learning_system.plugin import _create_learning_plugin
from pantheon.internal.memory_system.plugin import _create_memory_plugin
from pantheon.internal.memory_system.types import MemoryEntry, MemoryType
from pantheon.settings import Settings
from pantheon.team.plugin_registry import create_plugins
from pantheon.team.plugin import TeamPlugin
from pantheon.team.plugin_registry import PluginDef, PluginInitializationError, create_owned_plugins


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
async def test_plugin_readiness_does_not_resolve_lazy_models_for_log_messages(scopes, monkeypatch):
    from pantheon.internal.memory_system.config import LazyModel

    def forbidden(*args):
        raise AssertionError('Plugin startup resolved an inference model')

    monkeypatch.setattr(LazyModel, 'resolve', forbidden)
    plugins = await create_owned_plugins(scopes[0])
    assert len(plugins) == 2
    await asyncio.gather(*(p.on_shutdown() for p in plugins))


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


class OwnedPlugin(TeamPlugin):
    def __init__(self, close):
        self.close = close

    async def on_team_created(self, team):
        pass

    async def on_shutdown(self):
        await self.close()


def registry(monkeypatch, factories):
    from pantheon.team import plugin_registry
    monkeypatch.setattr(plugin_registry, '_ensure_plugins_registered', lambda: None)
    monkeypatch.setattr(plugin_registry, '_registry', [PluginDef(
        name=name, config_key='memory_system', enabled_key='enabled', factory=factory)
        for name, factory in factories])


def domain(settings):
    # Exercise actual runtime initialization/lifetime without starting transport,
    # loading conversations or allocating providers unrelated to these checks.
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._environment = SimpleNamespace(settings=lambda: settings, close_agents=None)
    runtime._nats_adapter = None
    runtime.worker = None
    runtime._init_plugins()
    return runtime


@pytest.mark.asyncio
async def test_owned_registry_rollback_is_complete_and_error_is_visible(scopes, monkeypatch):
    events = []

    async def close_one():
        events.append('closed-one')

    async def close_two():
        events.append('closed-two')
        raise RuntimeError('cleanup failed')

    def fail(config, settings):
        raise ValueError('private provider diagnostic')

    registry(monkeypatch, [
        ('one', lambda c, s: OwnedPlugin(close_one)),
        ('two', lambda c, s: OwnedPlugin(close_two)),
        ('required', fail),
    ])
    with pytest.raises(PluginInitializationError) as raised:
        await create_owned_plugins(scopes[0])
    assert events == ['closed-two', 'closed-one']
    assert raised.value.plugin_name == 'required'
    assert len(raised.value.cleanup_errors) == 1
    assert isinstance(raised.value.__cause__, ValueError)
    assert 'private provider diagnostic' not in str(raised.value)


@pytest.mark.asyncio
async def test_domain_empty_composition_cached_and_stop_prevents_late_construction(scopes, monkeypatch):
    calls = []
    registry(monkeypatch, [('empty', lambda c, s: calls.append(s))])
    runtime = domain(scopes[0])
    assert await runtime._ensure_plugins() == []
    assert await runtime._ensure_plugins() == []
    assert calls == [scopes[0]]
    await runtime.cleanup()
    cold = domain(scopes[1])
    await cold.cleanup()
    with pytest.raises(RuntimeError, match='Agent is stopping'):
        await cold._ensure_plugins()
    assert calls == [scopes[0]]


@pytest.mark.asyncio
async def test_domain_cancelled_observer_does_not_recreate_or_stop_sibling_plugins(scopes, monkeypatch):
    created, closed = [], []

    def make(config, settings):
        created.append(settings)
        async def close():
            closed.append(settings)
        return OwnedPlugin(close)

    registry(monkeypatch, [('owned', make)])
    a, b = (domain(s) for s in scopes)
    first = asyncio.create_task(a._ensure_plugins())
    second = asyncio.create_task(a._ensure_plugins())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    plugins = await second
    assert plugins is await a._ensure_plugins()
    assert created == [scopes[0]]
    await b._ensure_plugins()
    assert a._plugins[0] is not b._plugins[0]
    await a.cleanup()
    assert closed == [scopes[0]]
    assert b._plugins
    await b.cleanup()
    assert closed == scopes


@pytest.mark.asyncio
async def test_failed_startup_and_stop_wait_for_rollback_before_providers_close(scopes, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    events = []

    async def rollback():
        entered.set()
        await release.wait()
        events.append('rollback')

    def fail(config, settings):
        events.append('attempt')
        raise ValueError('broken config')

    registry(monkeypatch, [('owned', lambda c, s: OwnedPlugin(rollback)), ('required', fail)])
    runtime = domain(scopes[0])
    async def close_agents():
        events.append('providers')
    runtime._environment.close_agents = close_agents
    startup = asyncio.create_task(runtime.run_setup())
    await asyncio.wait_for(entered.wait(), 1)
    assert not startup.done() and runtime._plugins == []
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup
    stopping = asyncio.create_task(runtime.cleanup())
    try:
        await asyncio.sleep(0)
        assert not stopping.done()
        assert events == ['attempt']
        release.set()
        with pytest.raises(AppShutdownError) as raised:
            await asyncio.wait_for(stopping, 1)
        assert isinstance(raised.value.errors[0], PluginInitializationError)
        # Failed construction is not silently retried, even if another caller
        # asks for plugins after the first startup observer was cancelled.
        with pytest.raises(PluginInitializationError, match='required'):
            await runtime._ensure_plugins()
        assert events == ['attempt', 'rollback', 'providers']
    finally:
        release.set()
        await asyncio.gather(stopping, return_exceptions=True)


@pytest.mark.asyncio
async def test_readiness_fails_when_enabled_plugin_cannot_start(scopes, monkeypatch):
    def fail(config, settings):
        raise ValueError('broken config')
    registry(monkeypatch, [('required', fail)])
    runtime = domain(scopes[0])
    with pytest.raises(PluginInitializationError, match='required'):
        await runtime.run_setup()
    with pytest.raises(AppShutdownError):
        await runtime.cleanup()
