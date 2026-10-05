"""Evolution ownership and actual worker/process lifetime before App packaging."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.evolution.evolution_toolset import EvolutionManager, EvolutionSession, EvolutionToolSet
from pantheon.apps.toolset_backend import register_toolset
from pantheon.evolution import EvolutionConfig, EvolutionTeam, HybridEvaluator


class HeldEvolution(EvolutionToolSet):
    def __init__(self, root):
        super().__init__(workdir=str(root), manager=EvolutionManager(root))
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancellation, self.reap = asyncio.Event(), asyncio.Event()
        self.calls = 0

    async def _run_evolution(self, identity, *args):
        self.calls += 1
        session = self.manager.get_session(identity)
        session.status = 'running'
        session.save()
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancellation.set()
            await self.reap.wait()
            raise
        session.status = 'completed'
        session.best_score = 1.0
        return SimpleNamespace(best_score=1.0)

    async def _run_evolution_codebase(self, identity, *args):
        return await self._run_evolution(identity, *args)


async def launch(service, **options):
    return await service.evolve_code('result = 1', 'def evaluate(path): return {}', 'test', **options)


@pytest.mark.asyncio
async def test_sync_timeout_keeps_the_original_task_without_replay(tmp_path):
    service = HeldEvolution(tmp_path / 'evolution')
    try:
        result = await launch(service, async_mode=False, timeout=.02)
        assert result['status'] == 'running'
        await service.entered.wait()
        session = service.manager.get_session(result['evolution_id'])
        task = session.task
        assert service.calls == 1 and not task.done() and not service.cancellation.is_set()
        # Restoring this manager must not detach its live task from its identity.
        service.manager.restore_from_workdir(service.workdir)
        assert service.manager.get_session(result['evolution_id']) is session
        service.release.set()
        await task
        assert service.calls == 1 and session.status == 'completed'
        assert EvolutionSession.load(session.workspace_path).status == 'completed'
    finally:
        service.reap.set()
        await service.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('codebase', [False, True])
async def test_disconnected_sync_caller_does_not_cancel_owned_execution(tmp_path, codebase):
    service = HeldEvolution(tmp_path / 'evolution')
    source = tmp_path / 'source'; source.mkdir(); (source / 'main.py').write_text('x = 1')
    call = asyncio.create_task(service.evolve_codebase(str(source), 'def evaluate(p): return {}', 'test', async_mode=False)
                               if codebase else launch(service, async_mode=False))
    try:
        await service.entered.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError): await call
        [session] = service.manager.list_sessions()
        assert not service.cancellation.is_set() and not session.task.done()
        service.release.set()
        await session.task
        assert session.status == 'completed' and service.calls == 1
    finally:
        service.reap.set()
        await service.cleanup()


@pytest.mark.asyncio
async def test_ordinary_host_stop_joins_only_its_own_background_runs(tmp_path):
    first, second = HeldEvolution(tmp_path / 'one'), HeldEvolution(tmp_path / 'two')
    ctx = AppContext('evolution', tmp_path, tmp_path / 'state', None)
    await register_toolset(ctx, first)
    try:
        result = await ctx._methods['evolve'](type='code', code='x = 1', evaluator_code='def evaluate(p): return {}', objective='test')
        other = await launch(second)
        await first.entered.wait(); await second.entered.wait()
        stopping = asyncio.create_task(ctx.before_stop())
        await first.cancellation.wait()
        assert not stopping.done()
        with pytest.raises(RuntimeError, match='stopping'):
            await ctx._methods['evolve'](type='code', code='x', evaluator_code='x', objective='test')
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError): await stopping
        assert not second.cancellation.is_set()
        first.reap.set()
        await ctx.before_stop()
        session = first.manager.get_session(result['evolution_id'])
        assert session.task.done() and session.status == 'cancelled'
        assert EvolutionSession.load(session.workspace_path).status == 'cancelled'
        assert second.manager.get_session(other['evolution_id']).status == 'running'
        assert first.manager.get_session(other['evolution_id']) is None
        second.release.set()
        await second.manager.get_session(other['evolution_id']).task
        assert not (await launch(first))['success']
    finally:
        first.reap.set(); second.reap.set()
        await ctx._cleanup(); await second.cleanup()


@pytest.mark.asyncio
async def test_cancel_waits_for_execution_exit_and_atomic_terminal_record(tmp_path):
    service = HeldEvolution(tmp_path / 'evolution')
    try:
        result = await launch(service)
        await service.entered.wait()
        cancel = asyncio.create_task(service.cancel_evolution(result['evolution_id']))
        await service.cancellation.wait()
        assert not cancel.done()
        session = service.manager.get_session(result['evolution_id'])
        assert EvolutionSession.load(session.workspace_path).status == 'cancelling'
        service.reap.set()
        assert (await cancel)['success']
        assert session.task.cancelled() and EvolutionSession.load(session.workspace_path).status == 'cancelled'
    finally:
        service.reap.set(); await service.cleanup()


@pytest.mark.asyncio
async def test_cancel_before_task_starts_is_persisted(tmp_path):
    service = HeldEvolution(tmp_path / 'evolution')
    result = await launch(service)
    # No event-loop handoff before cancellation: the coroutine never runs.
    await service._cancel_and_wait(service.manager.get_session(result['evolution_id']))
    assert service.calls == 0
    [session] = service.manager.list_sessions()
    assert EvolutionSession.load(session.workspace_path).status == 'cancelled'
    await service.cleanup()


@pytest.mark.asyncio
async def test_stop_cancels_all_runs_even_when_a_session_save_fails(tmp_path, monkeypatch):
    service = HeldEvolution(tmp_path / 'evolution')
    first = await launch(service)
    second = await launch(service)
    while service.calls < 2:
        await asyncio.sleep(0)
    original = EvolutionSession.save
    def save(session):
        if session.evolution_id == first['evolution_id']:
            raise OSError('injected full disk')
        return original(session)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(EvolutionSession, 'save', save)
            stop = asyncio.create_task(service.begin_shutdown())
            await service.cancellation.wait()
            while any(not session.task.cancelling() for session in service.manager.list_sessions()):
                await asyncio.sleep(0)
            service.reap.set()
            with pytest.raises(RuntimeError, match='persist all sessions'):
                await stop
        assert all(session.task.done() for session in service.manager.list_sessions())
        assert EvolutionSession.load(service.manager.get_session(second['evolution_id']).workspace_path).status == 'cancelled'
        assert not (await launch(service))['success']
    finally:
        service.reap.set()
        await asyncio.gather(*(session.task for session in service.manager.list_sessions()), return_exceptions=True)


def test_owned_restore_marks_interrupted_work_without_restarting_or_cross_root_access(tmp_path):
    root = tmp_path / 'one'; root.mkdir()
    manager = EvolutionManager(root)
    session = manager.create_session('run', {'objective': 'keep me'}, str(root / 'run'))
    session.status = 'running'; session.files = {'main.py': 'x = 1'}; session.save()
    reopened = EvolutionManager(root)
    reopened.restore_from_workdir(root)
    restored = reopened.get_session('run')
    assert restored.status == 'failed' and restored.task is None and restored.files == session.files
    assert 'not restarted' in restored.error
    assert EvolutionSession.load(str(root / 'run')).status == 'failed'
    with pytest.raises(ValueError): reopened.restore_from_workdir(tmp_path / 'two')
    with pytest.raises(ValueError): reopened.create_session('../escape', {}, str(tmp_path / 'escape'))
    second = EvolutionManager(tmp_path / 'two'); second.restore_from_workdir(tmp_path / 'two')
    assert second.list_sessions() == []


def test_corrupt_state_refuses_owned_restore_and_failed_replace_preserves_previous_bytes(tmp_path, monkeypatch):
    root = tmp_path / 'one'; root.mkdir()
    manager = EvolutionManager(root)
    session = manager.create_session('run', {}, str(root / 'run'))
    path = root / 'run/session_state.json'; before = path.read_bytes()
    def fail(*args): raise OSError('injected replacement failure')
    with monkeypatch.context() as patch:
        patch.setattr('os.replace', fail)
        session.status = 'running'
        with pytest.raises(OSError): session.save()
    assert path.read_bytes() == before and not list(path.parent.glob('.session-*'))
    path.write_bytes(b'{incomplete')
    with pytest.raises(ValueError, match='Invalid Evolution session'):
        EvolutionManager(root).restore_from_workdir(root)
    assert path.read_bytes() == b'{incomplete'


@pytest.mark.asyncio
async def test_parallel_engine_cancellation_joins_workers(tmp_path):
    config = EvolutionConfig(num_workers=2, max_iterations=2, llm_weight=0, function_weight=1,
                             workspace_path=str(tmp_path), log_level='ERROR')
    evaluator = HybridEvaluator('def evaluate(path): return {"score": 1, "fitness_weights": {"score": 1}}',
                                llm_weight=0, function_weight=1, workspace_base=str(tmp_path))
    team = EvolutionTeam(config=config, evaluator=evaluator)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    started, finished = [], []
    async def worker(index, *args):
        started.append(index)
        if len(started) == 2: entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release.wait()
            finished.append(index)
    team._worker = worker
    task = asyncio.create_task(team.evolve('x = 1', evaluator.evaluator_code, 'test'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel(); await cancelled.wait()
        assert not task.done()
        task.cancel()  # Repeated host stop must not abandon worker teardown.
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert sorted(finished) == [0, 1]
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('timeout', [False, True])
async def test_real_evaluator_processes_exit_on_cancel_or_timeout(tmp_path, timeout):
    if os.name != 'posix': pytest.skip('POSIX descendant process-group acceptance')
    code = '''def evaluate(path):
 import os, sys, json, time, subprocess
 from pathlib import Path
 child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
 Path(path, 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))
 time.sleep(60)
 return {'score': 1}
'''
    evaluator = HybridEvaluator(code, llm_weight=0, timeout=.5 if timeout else 60)
    task = asyncio.create_task(evaluator._run_with_subprocess(str(tmp_path)))
    pids = []
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / 'pids.json').exists(): await asyncio.sleep(.01)
        pids = json.loads((tmp_path / 'pids.json').read_text())
        if timeout:
            assert (await task)['error'] == 'Evaluation timed out'
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        async with asyncio.timeout(5):
            while any(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE for pid in pids):
                await asyncio.sleep(.02)
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for pid in pids:
            try: os.kill(pid, 9)
            except ProcessLookupError: pass


@pytest.mark.asyncio
@pytest.mark.parametrize('workers', [1, 2])
async def test_real_engine_retains_mutation_evaluation_and_checkpoint_results(tmp_path, workers):
    class Mutator:
        calls = 0
        async def run(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(content="<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE", details=None)
    mutator = Mutator()
    config = EvolutionConfig(num_workers=workers, max_iterations=2, llm_weight=0, function_weight=1,
                             single_agent_mutation=False, use_analyzer=False,
                             workspace_path=str(tmp_path / 'work'), db_path=str(tmp_path / 'db'), log_level='ERROR')
    evaluator = HybridEvaluator("def evaluate(path):\n from pathlib import Path\n score = 1.0 if 'x = 2' in Path(path, 'main.py').read_text() else 0.2\n return {'score': score, 'fitness_weights': {'score': 1}}",
                                llm_weight=0, function_weight=1, workspace_base=str(tmp_path / 'work'))
    team = EvolutionTeam(config=config, evaluator=evaluator, mutator=mutator)
    result = await team.evolve('x = 1', evaluator.evaluator_code, 'raise score')
    assert mutator.calls == 2 and len(result.iteration_results) == 2
    assert any(program.snapshot.files['main.py'] == 'x = 2' and program.metrics['score'] == 1
               for program in team.database.programs.values())
    assert (tmp_path / 'db/evolution_state.json').exists()


@pytest.mark.asyncio
async def test_cancel_during_real_evaluator_spawn_still_reaps_process(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.create_subprocess_exec
    created = []
    async def slow_spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        created.append(process)
        entered.set()
        await release.wait()
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', slow_spawn)
    evaluator = HybridEvaluator('def evaluate(path):\n import time\n time.sleep(60)\n return {}', llm_weight=0)
    task = asyncio.create_task(evaluator._run_with_subprocess(str(tmp_path)))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert len(created) == 1 and created[0].returncode is not None
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_evaluator_spawn_remains_an_execution_error(tmp_path):
    evaluator = HybridEvaluator('def evaluate(path): return {}', llm_weight=0)
    result = await evaluator._run_with_subprocess(str(tmp_path / 'missing'))
    assert 'error' in result and result['function_score'] == 0
