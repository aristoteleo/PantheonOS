import asyncio
import json
from pathlib import Path

import pytest

from pantheon.evolution import EvolutionTeam
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.remote_execution import OwnedMutationTool
from pantheon.apps.builtin.python import PythonInterpreterToolSet
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from test_agent_execution_runner import BoundClient
from test_evolution_remote_execution import configuration, binding, EVALUATOR
from test_evolution_worker_resources import local_settings, exited


def configured(tmp_path, client, settings, kernels, *, python=True, workers=1):
    bound = binding(tmp_path, client, settings)
    async def analyzer_tools(workdir):
        py = PythonInterpreterToolSet('analyzer-python', str(workdir), strict_lifecycle=True)
        kernels.append(py)
        return {'python': OwnedMutationTool(py, cancel_on_stop=True, reset_after_iteration=True)}
    bound.analyzer_tool_factory = analyzer_tools
    config = configuration(tmp_path, single_agent_mutation=False, use_analyzer=True,
                           analyzer_use_python=python, num_workers=workers)
    config.max_iterations = workers
    config.save_prompts = True
    config.analyzer_model = 'high'
    config.analyzer_exploration_initial = 1
    config.analyzer_exploration_final = 1
    return EvolutionTeam(config=config, remote_execution=bound)


@pytest.mark.asyncio
@pytest.mark.parametrize('python,workers', [(False, 1), (True, 2)])
async def test_analysis_mutation_summary_pipeline_preserves_tools_metadata_and_archive(tmp_path, monkeypatch, local_settings, python, workers):
    calls, kernels = [], []
    both = asyncio.Event()
    async def engine(spec, invoke):
        calls.append(spec)
        instructions = spec['instructions']
        if 'expert code analyzer' in instructions:
            assert spec['model'] == 'high'
            assert 'Objective' in spec['prompt']
            assert set(spec['tools']) == ({'python', 'evolution'} if python else {'evolution'})
            assert 'recorded' in await invoke('evolution', 'think', {'thought': 'Study improvement'})
            if python:
                if len([c for c in calls if 'expert code analyzer' in c['instructions']]) == workers: both.set()
                await asyncio.wait_for(both.wait(), 5)
                reply = await invoke('python', 'run_python_code', {'code':
                    "from pathlib import Path\nimport os\nPath('kernel.pid').write_text(str(os.getpid()))\nprint(6 * 7)"})
                assert '42' in str(reply)
            return {'content': 'Replace x = 1 with x = 4. This increases the measured score.',
                    'details': {'messages': [{'role': 'assistant', '_metadata': {'current_cost': .01}}]}}
        if 'technical summarizer' in instructions:
            assert spec['model'] == 'low' and not spec['tools']
            assert 'DIFF (actual code changes)' in spec['prompt']
            return {'content': json.dumps({'direction': 'Increase x with a measured replacement',
                'category': 'implementation', 'is_algorithmic': False, 'match_confidence': 'high'})}
        assert 'code editor' in instructions and spec['model'] == 'openai/gpt-4o-mini'
        assert not spec['tools'] and 'Modification Instructions' in spec['prompt']
        assert 'increases the measured score' in spec['prompt']
        return {'content': '<<<<<<< SEARCH\nx = 1\n=======\nx = 4\n>>>>>>> REPLACE',
                'details': {'messages': [{'role': 'assistant', '_metadata': {'current_cost': .02}}]}}
    def forbid(*args, **kwargs): pytest.fail('Remote pipeline constructed a local Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = configured(tmp_path, BoundClient(service), local_settings, kernels, python=python, workers=workers)
    try:
        result = await asyncio.wait_for(team.evolve('x = 1', EVALUATOR, 'increase x'), 30)
        assert len(calls) == workers * 3
        assert all(r.error is None and abs(r.llm_cost - .03) < 1e-8 for r in result.iteration_results)
        children = [p for p in team.database.programs.values() if p.parent_id]
        assert len(children) == workers
        assert all(p.snapshot.files['main.py'] == 'x = 4' and p.metrics['score'] == 4 for p in children)
        assert all(p.mutation_summary == 'Increase x with a measured replacement' for p in children)
        assert all(p.mutation_category == 'implementation' and not p.is_algorithmic for p in children)
        assert all(p.analysis_used.startswith('Replace x') and p.analysis_prompt_used and p.mutator_prompt_used for p in children)
        assert all(not py.kernels.sessions for py in kernels)
        if python:
            assert len({py.workdir for py in kernels}) == workers
            await exited([int((py.workdir / 'kernel.pid').read_text()) for py in kernels])
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_remote_pipeline_without_analysis_uses_original_full_context(tmp_path, local_settings, monkeypatch):
    calls = []
    async def engine(spec, invoke):
        calls.append(spec)
        assert 'multi-file codebase' in spec['instructions']
        assert not spec['tools']
        return {'content': '<<<<<<< SEARCH\nx = 1\n=======\nx = 3\n>>>>>>> REPLACE'}
    def forbid(*args, **kwargs): pytest.fail('Mutator constructed an embedded Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = configuration(tmp_path, single_agent_mutation=False, use_analyzer=False)
    team = EvolutionTeam(config=config, remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    try:
        result = await team.evolve('x = 1', EVALUATOR, 'increase x')
        assert result.iteration_results[0].error is None and len(calls) == 1
        assert any(p.snapshot.files['main.py'] == 'x = 3' for p in team.database.programs.values())
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_remote_analyzer_cancellation_reaps_python_before_return(tmp_path, local_settings, monkeypatch):
    entered, kernels = asyncio.Event(), []
    async def engine(spec, invoke):
        await invoke('python', 'run_python_code', {'code':
            "from pathlib import Path\nimport os\nPath('kernel.pid').write_text(str(os.getpid()))"})
        entered.set()
        await invoke('python', 'run_python_code', {'code': 'import time; time.sleep(60)'})
        return {'content': 'unexpected'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = configured(tmp_path, BoundClient(service), local_settings, kernels)
    task = asyncio.create_task(team.evolve('x = 1', EVALUATOR, 'increase x'))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 15)
        assert kernels and all(not py.kernels.sessions for py in kernels)
        await exited([int((py.workdir / 'kernel.pid').read_text()) for py in kernels])
        assert not [p for p in team.database.programs.values() if p.parent_id]
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_run_identity_blocks_repeated_initial_evaluation_even_when_helpers_settled(tmp_path, local_settings, monkeypatch):
    from pantheon.evolution import HybridEvaluator
    evaluations = []
    original = HybridEvaluator._run_function_evaluation
    async def evaluate(self, path):
        evaluations.append(path)
        return await original(self, path)
    monkeypatch.setattr(HybridEvaluator, '_run_function_evaluation', evaluate)
    calls = []
    async def engine(spec, invoke):
        calls.append(spec)
        return {'content': '<<<<<<< SEARCH\nx = 1\n=======\nx = 3\n>>>>>>> REPLACE'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = configuration(tmp_path, single_agent_mutation=False, use_analyzer=False)
    def team(): return EvolutionTeam(config=config, remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    try:
        await team().evolve('x = 1', EVALUATOR, 'increase x')
        assert len(calls) == 1 and len(evaluations) == 2
        with pytest.raises(EvolutionCleanupError): await team().evolve('x = 1', EVALUATOR, 'increase x')
        assert len(calls) == 1 and len(evaluations) == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unconfirmed_analyzer_result_stops_instead_of_skipping_iteration(tmp_path, local_settings):
    class LostResult(BoundClient):
        async def read_result(self, identity): raise OSError('connection lost after analysis')
    calls = []
    async def engine(spec, invoke):
        calls.append(spec)
        return {'content': 'Change x to improve the measured score.'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = configured(tmp_path, LostResult(service), local_settings, [], python=False)
    team.config.max_iterations = 4
    try:
        with pytest.raises(EvolutionCleanupError): await team.evolve('x = 1', EVALUATOR, 'increase x')
        assert len(calls) == 1
        assert not [p for p in team.database.programs.values() if p.parent_id]
    finally:
        await service.close()
        # Fault fixture has no unsettled external tool. Simulate process exit
        # only after proving this failed owner cannot continue the search.
        if team._run_lease and team._run_lease.journal:
            team._run_lease.journal.close()


@pytest.mark.asyncio
async def test_two_process_owners_cannot_start_the_same_run(tmp_path, local_settings, monkeypatch):
    import sys
    entered = asyncio.Event()
    calls = []
    async def engine(spec, invoke):
        calls.append(spec)
        entered.set()
        await asyncio.Event().wait()
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = configuration(tmp_path, single_agent_mutation=False, use_analyzer=False)
    bound = binding(tmp_path, BoundClient(service), local_settings)
    team = EvolutionTeam(config=config, remote_execution=bound)
    task = asyncio.create_task(team.evolve('x = 1', EVALUATOR, 'increase x'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # A second Python process attempts to claim the exact durable run mount.
        # It must fail before any model call, initial evaluator or workspace edit.
        source = '''
import asyncio, json, sys
from types import SimpleNamespace
from pathlib import Path
from pantheon.evolution.remote_run import EvolutionRunLease
from pantheon.evolution.lifetime import EvolutionCleanupError
async def main():
    binding = SimpleNamespace(root=Path(sys.argv[1]), run_id='run', binding_id='agent-evolution')
    owner = EvolutionRunLease(binding, {})
    try:
        await owner.acquire()
    except EvolutionCleanupError:
        await owner.close(completed=False)
        return
    raise AssertionError('Second process acquired the running Evolution identity')
asyncio.run(main())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', source, str(tmp_path / 'receipts'),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0, stderr.decode()
        assert len(calls) == 1 and not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        second = EvolutionTeam(config=config, remote_execution=bound)
        with pytest.raises(EvolutionCleanupError): await second.evolve('x = 1', EVALUATOR, 'increase x')
        assert len(calls) == 1
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_failed_analyzer_cleanup_keeps_whole_run_fenced(tmp_path, local_settings):
    from pantheon.apps.agent_execution_runner import ToolReceiptJournal
    from pantheon.toolset import ToolSet, tool
    class FailingTool(ToolSet):
        @tool
        async def ping(self) -> str:
            """Read the fixture status."""
            return 'ready'
        async def run_setup(self): pass
        async def cleanup(self): raise OSError('kernel shutdown unconfirmed')
    async def tools(workdir): return {'python': OwnedMutationTool(FailingTool('fixture'), reset_after_iteration=True)}
    calls = []
    async def engine(spec, invoke):
        calls.append(spec)
        return {'content': 'Replace x = 1 with x = 4 to improve the score.'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = configured(tmp_path, BoundClient(service), local_settings, [])
    team._remote_execution.analyzer_tool_factory = tools
    try:
        with pytest.raises(EvolutionCleanupError): await team.evolve('x = 1', EVALUATOR, 'increase x')
        assert len(calls) == 1 and not [p for p in team.database.programs.values() if p.parent_id]
        with pytest.raises(OSError):
            ToolReceiptJournal(tmp_path / 'receipts/run/_run', 'agent-evolution')
        with pytest.raises(RuntimeError, match='cleanup recovery'):
            await team.evolve('x = 1', EVALUATOR, 'increase x')
    finally:
        await service.close()
        # The fixture has no actual kernel; emulate process exit after checking
        # that both helper and run ownership survive an unconfirmed shutdown.
        for helper in team._remote_helpers.values(): helper.session.journal.close()
        if team._run_lease and team._run_lease.journal: team._run_lease.journal.close()
