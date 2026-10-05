"""Durable execution ownership and real Agent tool-loop integration."""
import asyncio
import base64
import hashlib
import json
import threading

import pytest

from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal


FUNCTION = {'name': 'edit', 'description': 'Apply an owned edit', 'parameters': {
    'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}}
SPEC = {'prompt': 'Improve the file and verify it', 'instructions': 'Use your tools',
        'model': 'openai/gpt-4o-mini', 'tools': {'files': [FUNCTION]}}


async def observe(service, *, owner='consumer', identity='run', predicate=None):
    async with asyncio.timeout(10):
        while True:
            result = await service.poll(owner, identity)
            if predicate(result) if predicate else result['state'] not in {'running', 'cancelling'}:
                return result
            await asyncio.sleep(.005)


async def result(service, owner='consumer', identity='run'):
    offset, parts = 0, []
    while True:
        page = await service.read_result(owner, identity, offset)
        parts.append(base64.b64decode(page['data']))
        offset = page['next_offset']
        if offset == page['size']:
            break
    raw = b''.join(parts)
    assert hashlib.sha256(raw).hexdigest() == page['sha256']
    return json.loads(raw)


@pytest.mark.asyncio
async def test_one_execution_claim_and_reply_are_idempotent_with_caller_isolation(tmp_path):
    executed = []
    async def engine(spec, invoke):
        executed.append(spec)
        value = await invoke('files', 'edit', {'text': 'changed'})
        return {'content': value}
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        await asyncio.gather(*(service.submit('consumer', 'run', SPEC) for _ in range(4)))
        status = await observe(service, predicate=lambda s: s['call'] is not None)
        call = status['call']['id']
        assert status['call']['state'] == 'queued' and len(executed) == 1
        with pytest.raises(ValueError, match='absent'):
            await service.poll('different-consumer', 'run')
        with pytest.raises(ValueError, match='different request'):
            await service.submit('consumer', 'run', {**SPEC, 'prompt': 'another request'})
        with pytest.raises(ValueError, match='belong'):
            await service.reply('consumer', 'run', call, 'worker', {'ok': True, 'value': 'wrong'})
        assert not (await service.claim('consumer', 'run', call, 'worker'))['recovered']
        assert (await service.claim('consumer', 'run', call, 'worker'))['recovered']
        with pytest.raises(ValueError, match='another worker'):
            await service.claim('consumer', 'run', call, 'other-worker')
        reply = {'ok': True, 'value': 'verified'}
        await service.reply('consumer', 'run', call, 'worker', reply)
        await service.reply('consumer', 'run', call, 'worker', reply)
        with pytest.raises(ValueError, match='different response'):
            await service.reply('consumer', 'run', call, 'worker', {'ok': True, 'value': 'changed again'})
        assert (await observe(service))['state'] == 'completed'
        assert await result(service) == {'content': 'verified'}
        assert (await service.release('consumer', 'run'))['state'] == 'released'
        assert (await service.submit('consumer', 'run', SPEC))['state'] == 'released'
        assert len(executed) == 1
        with pytest.raises(ValueError, match='unavailable'):
            await service.read_result('consumer', 'run')
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('claimed', [False, True])
async def test_cancel_withdraws_unclaimed_tools_and_retains_claimed_effects(tmp_path, claimed):
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'pending'})
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        await service.submit('consumer', 'run', SPEC)
        call = (await observe(service, predicate=lambda s: s['call'] is not None))['call']['id']
        if claimed:
            await service.claim('consumer', 'run', call, 'worker')
        await service.cancel('consumer', 'run')
        status = await observe(service)
        assert status['state'] == 'cancelled'
        assert status['pending_tools'] == int(claimed)
        if claimed:
            with pytest.raises(ValueError, match='Settle'):
                await service.release('consumer', 'run')
            await service.reply('consumer', 'run', call, 'worker', {'ok': False, 'error': 'cancelled and reaped'})
        else:
            assert not (await service.claim('consumer', 'run', call, 'worker'))['claimed']
        assert (await service.release('consumer', 'run'))['state'] == 'released'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_submit_observer_disconnect_cannot_split_journal_from_execution(tmp_path):
    journal = ExecutionJournal(tmp_path)
    entered, release = threading.Event(), threading.Event()
    write = journal.write
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        write(*args)
    journal.write = blocked
    executions = []
    async def engine(spec, invoke):
        executions.append('executed')
        return {'content': 'done'}
    service = AgentExecutions(journal, engine)
    try:
        observer = asyncio.create_task(service.submit('consumer', 'run', SPEC))
        assert await asyncio.to_thread(entered.wait, 5)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError): await observer
        release.set()
        async with asyncio.timeout(5):
            while service._operations: await asyncio.sleep(.005)
        assert (await observe(service))['state'] == 'completed'
        await service.submit('consumer', 'run', SPEC)
        assert executions == ['executed']
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_restart_reports_interrupted_and_never_replays_claimed_tools(tmp_path):
    executions = []
    async def engine(spec, invoke):
        executions.append('started')
        await invoke('files', 'edit', {'text': 'may have been written'})
    first = AgentExecutions(ExecutionJournal(tmp_path), engine)
    await first.submit('consumer', 'run', SPEC)
    call = (await observe(first, predicate=lambda s: s['call'] is not None))['call']['id']
    await first.claim('consumer', 'run', call, 'worker')
    await first.close()
    # Simulate the last durable record of a killed process, before final receipt.
    record = first.journal.read(('consumer', 'run'))
    record['state'] = 'running'
    first.journal.write(('consumer', 'run'), record)
    second = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        status = await second.submit('consumer', 'run', SPEC)
        assert status['state'] == 'interrupted' and status['pending_tools'] == 1
        assert (await second.claim('consumer', 'run', call, 'worker'))['recovered']
        await second.reply('consumer', 'run', call, 'worker', {'ok': True, 'value': 'reconciled'})
        assert (await second.poll('consumer', 'run'))['state'] == 'interrupted'
        assert executions == ['started']
        await second.release('consumer', 'run')
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_paged_result_capacity_release_and_shutdown_admission(tmp_path):
    async def engine(spec, invoke): return {'content': '中文🧪' * 20000}
    service = AgentExecutions(ExecutionJournal(tmp_path), engine, max_retained=1)
    try:
        await service.submit('consumer', 'run', SPEC)
        assert (await observe(service))['state'] == 'completed'
        assert await result(service) == {'content': '中文🧪' * 20000}
        with pytest.raises(RuntimeError, match='capacity'):
            await service.submit('consumer', 'second', SPEC)
        await service.release('consumer', 'run')
        await service.submit('consumer', 'second', SPEC)
        await service.close()
        assert (await service.poll('consumer', 'second'))['state'] in {'completed', 'cancelled'}
        with pytest.raises(RuntimeError, match='stopping'):
            await service.submit('consumer', 'third', SPEC)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_during_engine_cleanup_waits_without_losing_final_receipt(tmp_path):
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def engine(spec, invoke):
        entered.set()
        try: await asyncio.Event().wait()
        finally:
            closing.set()
            await release.wait()
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        await service.submit('consumer', 'run', SPEC)
        await entered.wait()
        await service.cancel('consumer', 'run')
        await closing.wait()
        await service.cancel('consumer', 'run')
        shutdown = asyncio.create_task(service.close())
        await asyncio.sleep(.02)
        assert not shutdown.done()
        assert (await service.poll('consumer', 'run'))['state'] == 'cancelling'
        shutdown.cancel()
        with pytest.raises(asyncio.CancelledError): await shutdown
        release.set()
        await service.close()
        assert (await service.poll('consumer', 'run'))['state'] == 'cancelled'
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_journal_failure_closes_admission_and_fails_shutdown(tmp_path):
    journal = ExecutionJournal(tmp_path)
    async def engine(spec, invoke): pytest.fail('No model call before accepted persistence')
    def fail(*args): raise OSError('disk full')
    journal.write = fail
    service = AgentExecutions(journal, engine)
    with pytest.raises(OSError): await service.submit('consumer', 'run', SPEC)
    with pytest.raises(RuntimeError, match='recovery'): await service.submit('consumer', 'other', SPEC)
    with pytest.raises(RuntimeError, match='recovery'): await service.close()


@pytest.mark.asyncio
async def test_cleanup_failure_is_not_reported_as_clean_app_shutdown(tmp_path):
    from pantheon.chatroom.execution_engine import AgentExecutionCleanupError
    async def engine(spec, invoke): raise AgentExecutionCleanupError('owned resource did not close')
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    await service.submit('consumer', 'run', SPEC)
    assert (await observe(service))['error'] == 'execution_cleanup_failed'
    with pytest.raises(RuntimeError, match='recovery'): await service.close()


@pytest.mark.asyncio
async def test_parallel_calls_can_be_claimed_before_either_has_completed(tmp_path):
    async def engine(spec, invoke):
        return await asyncio.gather(*(invoke('files', 'edit', {'text': str(i)}) for i in range(2)))
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        await service.submit('consumer', 'run', SPEC)
        await observe(service, predicate=lambda s: s['pending_tools'] == 2)
        calls = []
        for _ in range(2):
            call = (await service.poll('consumer', 'run'))['call']
            assert call['state'] == 'queued'
            await service.claim('consumer', 'run', call['id'], 'worker')
            calls.append(call)
        for call in reversed(calls):
            await service.reply('consumer', 'run', call['id'], 'worker', {'ok': True, 'value': call['args']['text']})
        assert (await observe(service))['state'] == 'completed'
        assert await result(service) == ['0', '1']
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_tool_reply_persistence_failure_cannot_resume_inference(tmp_path):
    reached = []
    async def engine(spec, invoke):
        await invoke('files', 'edit', {'text': 'owned effect'})
        reached.append('resumed')
    journal = ExecutionJournal(tmp_path)
    service = AgentExecutions(journal, engine)
    await service.submit('consumer', 'run', SPEC)
    call = (await observe(service, predicate=lambda s: s['call'] is not None))['call']['id']
    await service.claim('consumer', 'run', call, 'worker')
    write = journal.write
    def fail(*args): raise OSError('disk full')
    journal.write = fail
    with pytest.raises(OSError):
        await service.reply('consumer', 'run', call, 'worker', {'ok': True, 'value': 'saved'})
    journal.write = write
    with pytest.raises(RuntimeError, match='recovery'): await service.close()
    assert reached == []
    assert journal.read(('consumer', 'run'))['calls'][call]['state'] == 'claimed'


@pytest.mark.asyncio
async def test_timeout_does_not_discard_a_claimed_tool_outcome(tmp_path):
    async def engine(spec, invoke):
        await invoke('files', 'edit', {'text': 'still running at the caller'})
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        await service.submit('consumer', 'run', {**SPEC, 'timeout_seconds': 1})
        call = (await observe(service, predicate=lambda s: s['call'] is not None))['call']['id']
        await service.claim('consumer', 'run', call, 'worker')
        status = await observe(service)
        assert status['state'] == 'failed' and status['error'] == 'execution_timeout'
        assert status['pending_tools'] == 1
        await service.reply('consumer', 'run', call, 'worker', {'ok': True, 'value': 'caller finished'})
        assert (await service.poll('consumer', 'run'))['pending_tools'] == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_engine_keeps_memory_and_image_authority_private_per_execution(tmp_path, monkeypatch):
    from pantheon.agent import AgentResponse
    from pantheon.chatroom.execution_engine import AgentExecutionEngine
    from pantheon.chatroom.execution_service import _spec
    from pantheon.settings import Settings
    from pantheon.team.pantheon import PantheonTeam
    from pantheon.utils.model_scope import ModelCallScope
    settings = Settings(tmp_path / 'settings', isolated_env=True, environment={}, user_home=tmp_path / 'home')
    scope = ModelCallScope(settings=settings, resolve_models=lambda model: [model])
    agents = []
    async def run(team, prompt, memory, **kwargs):
        agent = team.team_agents[0]
        agents.append(agent)
        assert agent.model_scope is scope and agent.memory is memory
        assert agent._image_resolver.root == settings.pantheon_dir / 'images' / memory.id
        with pytest.raises(ValueError, match='unavailable'):
            await agent._image_resolver(str(settings.pantheon_dir / 'images' / 'other-chat' / 'private.png'))
        return AgentResponse(agent_name=agent.name, content='done', details=None)
    monkeypatch.setattr(PantheonTeam, 'run', run)
    async def refresh(): pass
    engine = AgentExecutionEngine(scope, validate_model=lambda model: (True, ''), refresh_models=refresh)
    async def invoke(*args): pytest.fail('No implicit local tools')
    for _ in range(2):
        assert (await engine(_spec(SPEC), invoke))['content'] == 'done'
    assert agents[0].memory is not agents[1].memory
    assert agents[0]._image_resolver.root != agents[1]._image_resolver.root


@pytest.mark.asyncio
async def test_actual_engine_propagates_plugin_teardown_failure(tmp_path, monkeypatch):
    from pantheon.agent import AgentResponse
    from pantheon.chatroom.execution_engine import AgentExecutionEngine, AgentExecutionCleanupError
    from pantheon.chatroom.execution_service import _spec
    from pantheon.internal.compression.plugin import CompressionPlugin
    from pantheon.settings import Settings
    from pantheon.team.pantheon import PantheonTeam
    from pantheon.utils.model_scope import ModelCallScope
    scope = ModelCallScope(settings=Settings(tmp_path, isolated_env=True, environment={}, user_home=tmp_path / 'home'),
                           resolve_models=lambda model: [model])
    async def run(*args, **kwargs): return AgentResponse(agent_name='test', content='done', details=None)
    async def fail(*args): raise RuntimeError('plugin could not drain')
    async def refresh(): pass
    monkeypatch.setattr(PantheonTeam, 'run', run)
    monkeypatch.setattr(CompressionPlugin, 'on_shutdown', fail)
    engine = AgentExecutionEngine(scope, validate_model=lambda model: (True, ''), refresh_models=refresh)
    with pytest.raises(AgentExecutionCleanupError):
        await engine(_spec(SPEC), None)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'timeout_seconds': True}, {'max_turns': 0},
    {'tools': {'bad__prefix': [FUNCTION]}}, {'extra_capability': 'local_shell'},
    {'tools': {'files': [{**FUNCTION, 'parameters': {'type': 'object', 'properties': {'_call_agent': {}}}}]}}])
async def test_malformed_requests_never_reach_engine(tmp_path, change):
    async def engine(spec, invoke): pytest.fail('Invalid request was executed')
    service = AgentExecutions(ExecutionJournal(tmp_path), engine)
    try:
        with pytest.raises(ValueError): await service.submit('consumer', 'run', {**SPEC, **change})
        assert service.journal.retained() == 0
    finally:
        await service.close()
