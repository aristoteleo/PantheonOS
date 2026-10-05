import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from pantheon.evolution import EvolutionTeam, HybridEvaluator, Program, CodebaseSnapshot
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.remote_reasoning import RemoteEvolutionReasoner
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from test_agent_execution_runner import BoundClient
from test_evolution_remote_execution import configuration, binding, EVALUATOR
from test_evolution_worker_resources import local_settings


def helper(tmp_path, client):
    return RemoteEvolutionReasoner(SimpleNamespace(client=client, binding_id='feedback'),
        tmp_path / 'helper', instructions='review', model='normal', timeout=60)


def records(root):
    result = []
    for path in root.rglob('helper-calls.sqlite3'):
        with sqlite3.connect(path) as db:
            result += [(phase, json.loads(body)) for phase, body in db.execute('SELECT phase,result FROM calls')]
    return result


@pytest.mark.asyncio
async def test_parallel_evolution_feedback_preserves_scores_without_embedded_agent(tmp_path, local_settings, monkeypatch):
    feedback = []
    async def engine(spec, invoke):
        if not spec['tools']:
            assert spec['model'] == 'normal'
            assert 'expert code reviewer' in spec['instructions']
            assert 'Current Evaluation Metrics' in spec['prompt']
            feedback.append(spec['prompt'])
            return {'content': json.dumps({'score': 85, 'summary': 'Measured improvement',
                'issues': ['Review bounds'], 'suggestions': ['Keep evaluation']})}
        await invoke('shell', 'run_command', {'command': "printf 'x = 2' > main.py"})
        result = await invoke('evolution', 'run_evaluator', {})
        assert result['metrics']['llm_score'] == .85
        await invoke('evolution', 'submit', {'summary': 'Improved and reviewed'})
        return {'content': 'done'}
    def forbid(*args, **kwargs): pytest.fail('Evolution constructed an embedded Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = configuration(tmp_path, num_workers=2)
    config.max_iterations = 2
    config.llm_weight, config.function_weight = .3, .7
    team = EvolutionTeam(config=config, remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    try:
        result = await asyncio.wait_for(team.evolve('x = 1', EVALUATOR, 'increase x'), 30)
        assert all(r.error is None for r in result.iteration_results)
        children = [p for p in team.database.programs.values() if p.parent_id]
        assert len(children) == 2 and len(feedback) == 5
        assert all(p.metrics['llm_score'] == .85 and p.llm_feedback == 'Measured improvement' for p in children)
        assert all(p.artifacts['issues'] == ['Review bounds'] for p in children)
        saved = records(tmp_path / 'receipts')
        assert len(saved) == 5 and all(phase == 'settled' for phase, _ in saved)
        assert all(body['state'] == 'completed' for _, body in saved)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_helper_cancel_and_close_join_inference_before_settling(tmp_path):
    entered, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def engine(spec, invoke):
        entered.set()
        try: await asyncio.Event().wait()
        finally:
            stopping.set()
            await release.wait()
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = helper(tmp_path, BoundClient(service))
    await owned.setup()
    observer = asyncio.create_task(owned.run('review'))
    await asyncio.wait_for(entered.wait(), 5)
    observer.cancel()
    await asyncio.wait_for(stopping.wait(), 5)
    observer.cancel()
    closing = asyncio.create_task(owned.close())
    await asyncio.sleep(.03)
    assert not observer.done() and not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await observer
    await closing
    assert records(tmp_path) == [('settled', {'state': 'cancelled'})]
    await service.close()


@pytest.mark.asyncio
async def test_helper_receipt_failure_does_not_become_a_neutral_evaluation(tmp_path, local_settings, monkeypatch):
    def fail(*args): raise OSError('receipt volume unavailable')
    monkeypatch.setattr(RemoteEvolutionReasoner, '_save', fail)
    calls, evaluations = [], []
    evaluate = HybridEvaluator._run_function_evaluation
    async def tracked(self, workspace):
        evaluations.append(workspace)
        return await evaluate(self, workspace)
    monkeypatch.setattr(HybridEvaluator, '_run_function_evaluation', tracked)
    async def engine(spec, invoke):
        calls.append(spec)
        return {'content': '{"score": 90}'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = configuration(tmp_path)
    config.llm_weight = .3
    def team(): return EvolutionTeam(config=config, remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    first = team()
    try:
        with pytest.raises(EvolutionCleanupError): await first.evolve('x = 1', EVALUATOR, 'increase x')
        assert not first.database.programs
        with pytest.raises(EvolutionCleanupError): await team().evolve('x = 1', EVALUATOR, 'increase x')
        assert len(calls) == 1, 'An unresolved review must not be replaced by another paid call'
        assert len(evaluations) == 1, 'Recovery must stop before repeating the evaluator subprocess'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_confirmed_model_failure_keeps_feedback_fallback_and_releases_receipts(tmp_path):
    async def engine(spec, invoke): raise ValueError('model rejected request')
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = helper(tmp_path, BoundClient(service))
    await owned.setup()
    try:
        evaluator = HybridEvaluator('', feedback_agent=owned, function_weight=0, llm_weight=1,
                                    workspace_base=str(tmp_path / 'evaluation'))
        result = await evaluator.evaluate(Program(id='one', snapshot=CodebaseSnapshot(files={'main.py': 'x=1'})))
        assert result.success and result.metrics['llm_score'] == .5
        assert 'LLM feedback error' in result.llm_feedback
        assert records(tmp_path)[0][0] == 'settled'
        assert records(tmp_path)[0][1]['state'] == 'failed'
        assert not owned.session._runs
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_confirmed_deadline_preserves_timeout_feedback_after_join(tmp_path):
    stopped = asyncio.Event()
    async def engine(spec, invoke):
        try: await asyncio.Event().wait()
        finally: stopped.set()
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = helper(tmp_path, BoundClient(service))
    owned.timeout = 1
    await owned.setup()
    evaluator = HybridEvaluator('', feedback_agent=owned, function_weight=0, llm_weight=1,
                                workspace_base=str(tmp_path / 'evaluation'))
    try:
        result = await evaluator.evaluate(Program(id='one', snapshot=CodebaseSnapshot(files={'main.py': 'x=1'})))
        assert stopped.is_set() and result.metrics['llm_score'] == .5
        assert result.llm_feedback == 'LLM feedback timed out'
        assert records(tmp_path) == [('settled', {'state': 'failed', 'error': 'execution_timeout'})]
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_cancel_during_helper_archive_waits_for_receipt_and_release(tmp_path, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    async def engine(spec, invoke): return {'content': '{"score": 80}'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = helper(tmp_path, BoundClient(service))
    await owned.setup()
    original = owned._save
    def save(identity, phase, result):
        if phase == 'recorded':
            entered.set()
            assert release.wait(10)
        return original(identity, phase, result)
    monkeypatch.setattr(owned, '_save', save)
    observer = asyncio.create_task(owned.run('review'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set(): await asyncio.sleep(.01)
        observer.cancel()
        await asyncio.sleep(.02)
        observer.cancel()
        closing = asyncio.create_task(owned.close())
        await asyncio.sleep(.02)
        assert not observer.done() and not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await observer
        await closing
        assert records(tmp_path) == [('settled', {'state': 'completed', 'response': {'content': '{"score": 80}'}})]
        assert not owned.session._runs
    finally:
        release.set()
        await owned.close()
        await service.close()
