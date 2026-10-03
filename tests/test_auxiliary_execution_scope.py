"""Plugin calls use owned models and real scoped dependency transports.

HTTP/SSE and TLS endpoints are deterministic fixtures, not live Fleet acceptance.
"""
import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_agent_model_scope import scopes, endpoint as model_endpoint
from test_agent_dependency_bindings import endpoint as files_endpoint, provider
from pantheon.internal.auxiliary_execution import AuxiliaryExecution
from pantheon.internal.memory_system.plugin import _create_memory_plugin
from pantheon.internal.learning_system.plugin import _create_learning_plugin
from pantheon.factory.bindings import AgentToolBindings


@pytest.mark.asyncio
async def test_memory_selection_flush_and_note_use_owned_wire(scopes, model_endpoint):
    scope = scopes({'OPENAI_API_KEY': 'memory-fixture', 'OPENAI_API_BASE': model_endpoint.url + '/memory/v1'},
                   resolve_models=lambda spec: ['openai/gpt-4o-mini'])
    execution = AuxiliaryExecution(model_scope=scope)
    plugin = _create_memory_plugin({}, scope.settings, execution=execution)
    runtime = plugin.runtime
    await runtime.retriever._llm_select('query', 'manifest', 1, 1)
    assert await runtime.flusher._run_llm('remember this') == 'scoped reply'
    await runtime.session_note._extract('session', [{'role': 'user', 'content': 'hello'}])
    assert 'scoped reply' in runtime.session_note.read('session')
    assert len(model_endpoint.requests) == 3
    assert all(path == '/memory/v1/responses' and headers['Authorization'] == 'Bearer memory-fixture'
               for path, headers, body in model_endpoint.requests)
    await plugin.on_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['memory', 'learning'])
async def test_one_plugin_concurrent_background_tasks_capture_each_agent(scopes, model_endpoint, kind):
    first = scopes({'OPENAI_API_KEY': 'first', 'OPENAI_API_BASE': model_endpoint.url + '/first/v1'})
    second = scopes({'OPENAI_API_KEY': 'second', 'OPENAI_API_BASE': model_endpoint.url + '/second/v1'})
    make = _create_memory_plugin if kind == 'memory' else _create_learning_plugin
    plugin = make({}, first.settings)
    execution = plugin.runtime.execution
    gate = asyncio.Event()
    seen = []
    async def extract(session, messages, **kwargs):
        await gate.wait()
        model = execution.resolve_model('auto')
        seen.append((session, model, execution.snapshot().scope, messages[0]['content']))
        await execution.complete_text(model, messages, {})
        return []
    if kind == 'memory':
        plugin.runtime.maybe_extract_memories = extract
        plugin.runtime.maybe_update_session_note = AsyncMock()
        plugin.runtime.maybe_run_dream = AsyncMock()
    else:
        plugin.runtime.maybe_extract_skills = extract
    for scope, label, model in ((first, 'first', 'openai/gpt-4o-mini'),
                                 (second, 'second', 'openai/gpt-4.1-mini')):
        agent = SimpleNamespace(models=[model], model_scope=scope)
        team = SimpleNamespace(get_active_agent=lambda memory, a=agent: a, plugins=[])
        messages = [{'role': 'user', 'content': label}]
        memory = SimpleNamespace(id=label, _messages=messages, file_path=None)
        await plugin.on_run_end(team, {'chat_id': label, 'memory': memory, 'messages': messages})
        messages[0]['content'] = 'changed after dispatch'
    assert execution.snapshot().scope is None
    gate.set()
    await plugin.on_shutdown()
    assert seen == [('first', 'openai/gpt-4o-mini', first, 'first'),
                    ('second', 'openai/gpt-4.1-mini', second, 'second')]
    assert sorted(headers['Authorization'] for path, headers, body in model_endpoint.requests) == [
        'Bearer first', 'Bearer second']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['memory', 'note'])
async def test_pending_drain_retains_latest_call_snapshot(scopes, kind):
    scope = scopes()
    plugin = _create_memory_plugin({}, scope.settings)
    runtime = plugin.runtime
    execution = runtime.execution
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def extract(session, messages, *args):
        seen.append((execution.resolve_model('auto'), len(messages)))
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return []
    target = runtime.memory_extractor if kind == 'memory' else runtime.session_note
    target._extract = extract
    async def invoke(model, count):
        with execution.for_agent(SimpleNamespace(models=[model])):
            messages = [{'role': 'user', 'content': str(i)} for i in range(count)]
            if kind == 'memory':
                await target.maybe_extract('session', messages)
            else:
                await target.maybe_update('session', messages, 15000 * count)
    running = asyncio.create_task(invoke('provider/first', 1))
    await entered.wait()
    await invoke('provider/second', 2)
    release.set()
    await running
    assert seen == [('provider/first', 1), ('provider/second', 2)]
    assert execution.snapshot().model is None


@pytest.mark.asyncio
async def test_scoped_background_work_cannot_construct_local_files(scopes, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Constructed local FileManager')
    monkeypatch.setattr('pantheon.apps.builtin.file.file_manager.FileManagerToolSet', forbidden)
    execution = AuxiliaryExecution(model_scope=scopes())
    with pytest.raises(RuntimeError, match='explicit Files'):
        await execution.run_agent('test', name='test', instructions='test',
                                  model='openai/gpt-4o-mini', workspace_path='/not-a-grant')


@pytest.mark.asyncio
async def test_background_agent_tool_loop_uses_scoped_files_over_tls(scopes, files_endpoint, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Constructed local FileManager')
    monkeypatch.setattr('pantheon.apps.builtin.file.file_manager.FileManagerToolSet', forbidden)
    ref = 'fleet-model://service/model'
    clients = []
    async def run(token, label):
        binding = provider(files_endpoint, 'file_manager', token)
        clients.append(binding)
        async def complete(model, messages, tools, *args):
            if any(m['role'] == 'tool' for m in messages):
                return {'role': 'assistant', 'content': label + ' finished'}
            assert any(t['function']['name'] == 'file_manager__execute' for t in tools)
            return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': label, 'type': 'function',
                    'function': {'name': 'file_manager__execute',
                                 'arguments': '{"command":"' + label + '","_background":false}'}}]}
        published = {'context': 131072, 'tools': True}
        scope = scopes(fleet_client=SimpleNamespace(metadata={ref: published},
                       describe=AsyncMock(return_value=({}, published)), complete=complete))
        execution = AuxiliaryExecution(model_scope=scope,
                                        tool_bindings=AgentToolBindings({'file_manager': binding}))
        result = await execution.run_agent('test', name=label, instructions='test', model=ref,
                                          workspace_path=scope.settings.work_dir)
        assert result.content == label + ' finished'
        # A finished background Agent borrowed its client's lifetime.
        assert (await binding.call_tool('execute', {'command': 'still available'}))['session']
    try:
        await asyncio.gather(run('a' * 64, 'first'), run('b' * 64, 'second'))
        assert sorted((token, body['args']['command']) for path, token, body in files_endpoint.calls) == [
            ('a' * 64, 'first'), ('a' * 64, 'still available'),
            ('b' * 64, 'second'), ('b' * 64, 'still available')]
    finally:
        await asyncio.gather(*(c.shutdown() for c in clients))


@pytest.mark.asyncio
async def test_background_operation_drains_adopted_work_when_cancelled(scopes, monkeypatch):
    from pantheon.agent import Agent
    from pantheon.internal.background_agent import run_background_agent
    started, release, running = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = []
    agent = Agent('worker', 'test', model='openai/gpt-4o-mini', model_scope=scopes())
    async def write():
        started.set()
        await release.wait()
        writes.append('completed')
    async def run(*args, **kwargs):
        agent._bg_manager.start('file_write', 'one', {}, write())
        running.set()
        await asyncio.Event().wait()
    agent.run = run
    monkeypatch.setattr('pantheon.internal.background_agent.create_background_agent', AsyncMock(return_value=agent))
    task = asyncio.create_task(run_background_agent('test', name='worker', instructions='test',
                                                    model='openai/gpt-4o-mini', workspace_path='/fixture'))
    await running.wait()
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert not writes
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert writes == ['completed']
    assert all(t.asyncio_task.done() for t in agent._bg_manager.list_tasks())


@pytest.mark.asyncio
async def test_runtime_uses_explicit_plugin_composition(scopes):
    from test_agent_plugin_scope import domain
    from pantheon.team.plugin_registry import create_owned_plugins
    scope = scopes(resolve_models=lambda spec: ['openai/gpt-4o-mini'])
    settings = SimpleNamespace(get_section=lambda name: {'enabled': name in ('memory_system', 'learning_system')})
    execution = AuxiliaryExecution(model_scope=scope)
    factories = {
        'memory_system': lambda config, _: _create_memory_plugin(config, scope.settings, execution=execution),
        'learning_system': lambda config, _: _create_learning_plugin(config, scope.settings, execution=execution),
    }
    runtime = domain(settings)
    runtime._environment.create_plugins = partial(create_owned_plugins, settings, factories=factories)
    try:
        plugins = await runtime._ensure_plugins()
        assert len(plugins) == 2
        assert all(p.runtime.execution is execution for p in plugins)
        assert await runtime._ensure_plugins() is plugins
    finally:
        await runtime.cleanup()


@pytest.mark.asyncio
async def test_explicit_plugin_map_rejects_missing_enabled_factory_and_rolls_back(monkeypatch):
    from test_agent_plugin_scope import registry, OwnedPlugin
    from pantheon.team.plugin_registry import create_owned_plugins, PluginInitializationError
    closed = []
    def forbidden(*args):
        raise AssertionError('Used ambient plugin factory')
    registry(monkeypatch, [('owned', forbidden), ('missing', forbidden)])
    async def close():
        closed.append('owned')
    settings = SimpleNamespace(get_section=lambda name: {'enabled': True})
    with pytest.raises(PluginInitializationError) as error:
        await create_owned_plugins(settings, factories={'owned': lambda *args: OwnedPlugin(close)})
    assert error.value.plugin_name == 'missing'
    assert closed == ['owned']
