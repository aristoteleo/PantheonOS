"""Controller receipts with real local tools; deployment grants/stop are fixtures."""
import asyncio
import sqlite3

import pytest

from pantheon.apps.agent_execution_runner import ToolReceiptJournal
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.sandbox.agent_execution import SandboxAgentExecution
from test_agent_execution_runner import BoundClient
from test_evolution_sandbox_tools import compose, context
from test_evolution_remote_execution import evolution_model


class Backend:
    """Local test placement, not a container termination implementation."""
    def __init__(self, tools):
        self.tools, self.calls = tools, []
        self.stops = 0
        self.stop_receipt = True
        self.lose_finish = False

    async def invoke(self, name, args):
        self.calls.append(name)
        function = getattr(self.tools, name)
        value = function(**args)
        if asyncio.iscoroutine(value):
            value = await value
        if name == 'finish' and self.lose_finish:
            raise OSError('Lost result after final evaluation')
        return value

    async def terminate(self):
        self.stops += 1
        await self.tools.close()
        return {'backend_id': 'sandbox-test-1', 'stopped': self.stop_receipt}


def execution(tmp_path, client, backend):
    return SandboxAgentExecution(client, tmp_path / 'receipts', binding_id='sandbox-agent',
        execution_id='mutation-1', backend_id='sandbox-test-1',
        invoke=backend.invoke, terminate=backend.terminate)


RUN = {'instructions': 'Improve the code', 'model': 'openai/fixture', 'evaluate_initial': True}


@pytest.mark.asyncio
async def test_initialization_is_recorded_before_effect_and_not_replayed(tmp_path):
    import json
    seen = []
    async def engine(*_):
        raise AssertionError('Inference must not start after lost initialization')
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = {'parent_files': {'main.py': 'x=1'}, 'objective': 'test'}
    async def invoke(name, args):
        assert name == 'initialize'
        with sqlite3.connect(tmp_path / 'receipts/tool-receipts.sqlite3') as db:
            request, phase = db.execute('SELECT request,phase FROM sandbox_execution').fetchone()
        assert phase == 'initializing' and json.loads(request)['configuration'] == args == config
        seen.append(args)
        raise OSError('Lost initialization reply after materialization')
    async def terminate():
        return {'backend_id': 'sandbox-test-1', 'stopped': True}
    def create():
        return SandboxAgentExecution(BoundClient(service), tmp_path / 'receipts', binding_id='sandbox-agent',
            execution_id='mutation-1', backend_id='sandbox-test-1', invoke=invoke, terminate=terminate)
    first = create()
    with pytest.raises(EvolutionCleanupError):
        await first.run(**RUN, configuration=config)
    with pytest.raises(EvolutionCleanupError):
        await first.close()
    second = create()
    try:
        with pytest.raises(EvolutionCleanupError):
            await second.run(**RUN, configuration=config)
        assert len(seen) == 1
    finally:
        with pytest.raises(EvolutionCleanupError):
            await second.close()
        await service.close()


@pytest.mark.asyncio
async def test_external_agent_tools_evaluation_and_completed_reopen(tmp_path):
    calls = []
    async def engine(spec, invoke):
        calls.append(spec)
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        assert '42' in str(await invoke('python', 'run_python_code', {'code': 'print(6*7)'}))
        assert (await invoke('evolution', 'run_evaluator', {}))['metrics']['score'] == .8
        await invoke('evolution', 'submit', {'summary': 'verified improvement'})
        return {'content': 'Submitted'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    owned = execution(tmp_path, BoundClient(service), backend)
    try:
        result = await owned.run(**RUN)
        assert result['initial']['metrics']['score'] == .1
        assert result['mutation']['child_files'] == {'main.py': 'x=8'}
        assert result['mutation']['metrics']['score'] == .8
        assert result['response']['content'] == 'Submitted'
        assert backend.stops == 1
        assert (await service.poll('consumer', 'mutation-1'))['state'] == 'released'
        await owned.close()
        previous = list(backend.calls)
        reopened = execution(tmp_path, BoundClient(service), backend)
        try:
            assert await reopened.run(**RUN) == result
            assert backend.calls == previous and backend.stops == 1 and len(calls) == 1
        finally:
            await reopened.close()
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_lost_final_reply_never_repeats_evaluation_or_inference(tmp_path):
    called = []
    async def engine(spec, invoke):
        called.append('inference')
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    backend.lose_finish = True
    owned = execution(tmp_path, BoundClient(service), backend)
    try:
        with pytest.raises(EvolutionCleanupError):
            await owned.run(**RUN)
        assert backend.tools.finished['submitted']
        with pytest.raises(EvolutionCleanupError):
            await owned.close()
        previous = list(backend.calls)
        reopened = execution(tmp_path, BoundClient(service), backend)
        with pytest.raises(EvolutionCleanupError):
            await reopened.run(**RUN)
        assert backend.calls == previous and called == ['inference']
        with pytest.raises(EvolutionCleanupError):
            await reopened.close()
        with sqlite3.connect(owned.owner.path) as db:
            assert db.execute('SELECT phase FROM sandbox_execution').fetchone() == ('finalizing',)
    finally:
        await backend.tools.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt', [False, 1])
async def test_failed_termination_keeps_exclusive_owner(tmp_path, receipt):
    async def engine(spec, invoke):
        return {'content': 'No change'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    backend.stop_receipt = receipt
    owned = execution(tmp_path, BoundClient(service), backend)
    try:
        with pytest.raises(EvolutionCleanupError):
            await owned.run(**RUN)
        with pytest.raises((RuntimeError, EvolutionCleanupError)):
            await owned.close()
        with pytest.raises(BlockingIOError):
            ToolReceiptJournal(tmp_path / 'receipts', 'sandbox-agent')
        assert backend.stops == 1
    finally:
        # Fixture has already reaped all real tools; only its acknowledgement
        # was faulted. Simulate owner process exit after asserting the fence.
        await backend.tools.close()
        await owned.session.close()
        owned.owner.close()
        await service.close()


@pytest.mark.asyncio
async def test_observer_loss_does_not_restart_or_cancel_reasoning(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def engine(spec, invoke):
        calls.append('run')
        entered.set()
        await release.wait()
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    owned = execution(tmp_path, BoundClient(service), backend)
    try:
        observer = asyncio.create_task(owned.run(**RUN))
        await asyncio.wait_for(entered.wait(), 5)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
        assert not backend.stops
        with pytest.raises(ValueError, match='another request'):
            await owned.run(**{**RUN, 'instructions': 'different'})
        release.set()
        result = await owned.run(**RUN)
        assert result['mutation']['submitted'] and calls == ['run']
    finally:
        release.set()
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_explicit_stop_joins_inference_and_does_not_finalize(tmp_path):
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def engine(spec, invoke):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    owned = execution(tmp_path, BoundClient(service), backend)
    observer = asyncio.create_task(owned.run(**RUN))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await owned.close()
        with pytest.raises(asyncio.CancelledError):
            await observer
        assert stopped.is_set() and backend.stops == 1
        assert 'finish' not in backend.calls
        reopened = execution(tmp_path, BoundClient(service), backend)
        with pytest.raises(EvolutionCleanupError):
            await reopened.run(**RUN)
        with pytest.raises(EvolutionCleanupError):
            await reopened.close()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_independent_agent_and_tool_apps_complete_a_mutation(tmp_path, evolution_model, monkeypatch):
    import json
    import urllib.request
    from test_agent_native_process import native_process, request
    from test_evolution_sandbox_tools import stdio_tools
    from pantheon.apps.agent_execution_client import AgentExecutionClient
    from pantheon.apps.dependency_client import DependencyClient
    from pantheon.apps.runtime_config import RuntimeCredential
    def forbid(*args, **kwargs):
        raise AssertionError('Controller constructed an embedded Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    root = tmp_path / 'native'
    root.mkdir()
    with native_process(root, evolution_model.url) as (child, base):
        async with asyncio.timeout(15):
            while True:
                try:
                    if (await request(base, '/health'))['ready']:
                        break
                except OSError:
                    assert child.poll() is None, (root / 'process.log').read_text()[-15000:]
                await asyncio.sleep(.05)
        class LocalGrant(DependencyClient):
            def invoke(self, method, args, **kwargs):
                req = urllib.request.Request(base + '/rpc', json.dumps({'method': method,
                    'args': {'consumer_id': 'evolution-consumer', **args}}).encode(),
                    {'Content-Type': 'application/json', 'X-Fleet-RPC-Token': 'native-test-token'})
                with urllib.request.urlopen(req, timeout=20) as response:
                    return json.load(response)
        sdk = AgentExecutionClient(LocalGrant(RuntimeCredential('https://bound.example/rpc', 'a' * 64)))
        try:
            async with stdio_tools(tmp_path / 'tool-app') as backend:
                owned = SandboxAgentExecution(sdk, tmp_path / 'receipts', binding_id='sandbox-agent',
                    execution_id='mutation-1', backend_id=backend.identity,
                    invoke=backend.invoke, terminate=backend.terminate)
                try:
                    result = await asyncio.wait_for(owned.run(instructions='Improve the code',
                        model='openai/gpt-4o-mini', evaluate_initial=True), 45)
                    assert result['initial']['metrics']['score'] == .1
                    assert result['mutation']['child_files'] == {'main.py': 'x = 8'}
                    assert result['mutation']['metrics']['score'] == .8
                    assert result['mutation']['summary'] == 'Verified eight using the caller evaluator'
                    assert len(evolution_model.calls) == 5
                    assert '42' in str(evolution_model.calls[2]['messages'])
                    assert backend.calls.count('evaluate_initial') == backend.calls.count('finish') == 1
                    assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
                finally:
                    await owned.close()
        finally:
            await sdk.close()


@pytest.mark.asyncio
async def test_unknown_tool_effect_blocks_salvage_and_replay(tmp_path):
    async def engine(spec, invoke):
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    ctx = context(tmp_path / 'sandbox')
    backend = Backend(await compose(ctx))
    original = backend.invoke
    async def lost_tool(method, args):
        value = await original(method, args)
        if method == 'invoke_tool':
            raise OSError('Lost reply after mutation')
        return value
    backend.invoke = lost_tool
    owned = execution(tmp_path, BoundClient(service), backend)
    try:
        with pytest.raises(EvolutionCleanupError):
            await owned.run(**RUN)
        assert (ctx.workspace / 'main.py').read_text() == 'x=8'
        assert 'finish' not in backend.calls
        with pytest.raises(EvolutionCleanupError):
            await owned.close()
        assert backend.calls.count('invoke_tool') == 1
    finally:
        await backend.tools.close()
        await service.close()


@pytest.mark.asyncio
async def test_stop_signals_inference_even_when_backend_stop_is_unconfirmed(tmp_path):
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def engine(spec, invoke):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    backend.stop_receipt = False
    owned = execution(tmp_path, BoundClient(service), backend)
    observer = asyncio.create_task(owned.run(**RUN))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(RuntimeError, match='not confirmed'):
            await owned.close()
        await asyncio.wait_for(stopped.wait(), 5)
        with pytest.raises(asyncio.CancelledError):
            await observer
        with pytest.raises(BlockingIOError):
            ToolReceiptJournal(tmp_path / 'receipts', 'sandbox-agent')
    finally:
        await backend.tools.close()
        await owned.session.close()
        owned.owner.close()
        await service.close()


@pytest.mark.asyncio
async def test_stop_retains_owner_until_finalization_transport_joins(tmp_path):
    entered, release, terminated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def engine(spec, invoke):
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    backend = Backend(await compose(context(tmp_path / 'sandbox')))
    original_invoke, original_terminate = backend.invoke, backend.terminate
    async def held(method, args):
        if method == 'finish':
            entered.set()
            await release.wait()
        return await original_invoke(method, args)
    async def stop():
        result = await original_terminate()
        terminated.set()
        return result
    backend.invoke, backend.terminate = held, stop
    owned = execution(tmp_path, BoundClient(service), backend)
    observer = asyncio.create_task(owned.run(**RUN))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        closing = asyncio.create_task(owned.close())
        await asyncio.wait_for(terminated.wait(), 5)
        assert not closing.done()
        with pytest.raises(BlockingIOError):
            ToolReceiptJournal(tmp_path / 'receipts', 'sandbox-agent')
        release.set()
        await asyncio.wait_for(closing, 5)
        with pytest.raises(asyncio.CancelledError):
            await observer
        assert backend.tools.finished is None
    finally:
        release.set()
        await owned.close()
        await service.close()
