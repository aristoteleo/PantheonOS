"""Real local tools under Evolution ownership; mutation decisions are fixtures."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import psutil
import pytest

from pantheon.apps.builtin.evolution.evolution_toolset import EvolutionManager, EvolutionToolSet
from pantheon.evolution import EvolutionConfig, EvolutionTeam
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.local_shell import LocalShellToolSet


async def exited(pids):
    async with asyncio.timeout(8):
        while any(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE for pid in pids):
            await asyncio.sleep(.02)


@pytest.fixture
def local_settings(tmp_path, monkeypatch):
    from pantheon.settings import Settings
    settings = Settings(tmp_path / 'settings', isolated_env=True, environment={}, user_home=tmp_path / 'home')
    monkeypatch.setattr('pantheon.settings._settings', settings)
    monkeypatch.setattr('pantheon.agent._resolve_model_tag', lambda _: ['openai/fixture'])
    return settings


async def call(agent, name, args):
    [message] = await agent._handle_tool_calls([{'id': name, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(args)}}], {}, 30)
    return message['content']


@pytest.mark.asyncio
async def test_parallel_coding_workers_keep_separate_files_kernels_budgets_and_submissions(tmp_path, monkeypatch, local_settings):
    from pantheon.team.pantheon import PantheonTeam
    arrivals, agents, kernels, replies, memories = [], [], [], {}, []
    both = asyncio.Event()

    async def scripted_run(team, prompt, memory, **kwargs):
        agent = team.team_agents[0]
        agents.append(agent); memories.append(memory)
        # Use the real attached toolsets and real Agent tool dispatch/hooks.
        py = agent.providers['evo-py'].toolset
        kernels.append(py)
        root = py.workdir
        value = int(root.parent.name.split('-')[-1]) + 2
        arrivals.append(root)
        if len(arrivals) == 2: both.set()
        await asyncio.wait_for(both.wait(), 10)
        result = await call(agent, 'evo-py__run_python_code', {'code':
            f"from pathlib import Path\nimport os\nowned = {value}\nPath({str(root / 'main.py')!r}).write_text('x = ' + str(owned))\nPath({str(root / 'kernel.pid')!r}).write_text(str(os.getpid()))\nprint(owned)"})
        assert str(value) in str(result), result
        # Keep both kernels alive simultaneously before one worker can shut down.
        while not all((path / 'kernel.pid').exists() for path in arrivals): await asyncio.sleep(.01)
        again = await call(agent, 'evo-py__run_python_code', {'code': 'print(owned)'})
        assert str(value) in str(again), again
        shell = await call(agent, 'evo-sh__run_command', {'command': 'pwd'})
        assert str(root) in str(shell), shell
        evaluated = await call(agent, 'run_evaluator', {})
        # Exactly four charged actions; the other worker must not spend this budget.
        assert 'actions_left' in str(evaluated) and '0' in str(evaluated), evaluated
        assert 'success' in str(evaluated), evaluated
        blocked = await call(agent, 'evo-sh__run_command', {'command': 'touch should-not-exist'})
        assert 'budget exhausted' in str(blocked).lower()
        submitted = await call(agent, 'submit', {'summary': f'worker value {value}'})
        assert 'Submitted' in str(submitted)
        replies[value] = evaluated
        return SimpleNamespace(details=None)

    monkeypatch.setattr(PantheonTeam, 'run', scripted_run)
    config = EvolutionConfig(num_workers=2, max_iterations=2, max_tool_calls_per_mutation=4,
        max_evaluations_per_mutation=1, llm_weight=0, function_weight=1,
        workspace_path=str(tmp_path / 'work'), db_path=str(tmp_path / 'archive'), log_level='ERROR')
    evaluator = "def evaluate(path):\n from pathlib import Path\n x = int(Path(path, 'main.py').read_text().split('=')[1])\n return {'score': x, 'fitness_weights': {'score': 1}}"
    engine = EvolutionTeam(config=config)
    result = await asyncio.wait_for(engine.evolve('x = 1', evaluator, 'increase x'), 45)
    assert len(agents) == 2 and agents[0] is not agents[1]
    assert len(set(arrivals)) == 2 and memories[0] is not memories[1]
    assert all(not (root / 'should-not-exist').exists() for root in arrivals)
    assert set(replies) == {2, 3}, [r.error for r in result.iteration_results]
    assert all(r.error is None for r in result.iteration_results)
    children = [program for program in engine.database.programs.values() if program.parent_id]
    assert {p.snapshot.files['main.py'] for p in children} == {'x = 2', 'x = 3'}
    assert {p.mutation_summary for p in children} == {'worker value 2', 'worker value 3'}
    assert all(not py.kernels.sessions and not py.clientid_to_interpreterid for py in kernels)
    await exited([int((root / 'kernel.pid').read_text()) for root in arrivals])
    assert (tmp_path / 'archive/evolution_state.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'stop'])
async def test_local_shell_reaps_parent_and_descendant_and_preserves_timeout_output(tmp_path, mode):
    if os.name != 'posix': pytest.skip('POSIX process-group acceptance')
    script = "import subprocess,sys,os,json,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path('pids.json').write_text(json.dumps([os.getpid(), child.pid])); print('before wait',flush=True); time.sleep(60)"
    service = LocalShellToolSet('owned-shell', str(tmp_path))
    task = asyncio.create_task(service.run_command(shlex.quote(sys.executable) + ' -c ' + shlex.quote(script),
                                                   timeout=.5 if mode == 'timeout' else 60))
    pids = []
    try:
        async with asyncio.timeout(10):
            while not (tmp_path / 'pids.json').exists(): await asyncio.sleep(.01)
        pids = json.loads((tmp_path / 'pids.json').read_text())
        if mode == 'timeout':
            result = await task
            assert result['status'] == 'timeout' and 'before wait' in result['output']
        else:
            if mode == 'cancel': task.cancel()
            else: await service.cleanup()
            with pytest.raises(asyncio.CancelledError): await task
        await exited(pids)
        await service.cleanup()
        with pytest.raises(RuntimeError, match='closed'): await service.run_command('echo should-not-run')
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.cleanup()
        for pid in pids:
            try: os.kill(pid, 9)
            except ProcessLookupError: pass


@pytest.mark.asyncio
async def test_shell_cancelled_during_spawn_still_joins_and_reaps(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    created = []
    original = asyncio.create_subprocess_shell
    async def spawn(*args, **kwargs):
        proc = await original(*args, **kwargs)
        created.append(proc); entered.set()
        await release.wait()
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_shell', spawn)
    service = LocalShellToolSet('shell', str(tmp_path))
    task = asyncio.create_task(service.run_command('sleep 60'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel(); await asyncio.sleep(0); task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert created[0].returncode is not None
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.cleanup()


@pytest.mark.asyncio
async def test_cleanup_failure_attempts_other_resources_and_prevents_clean_app_stop(tmp_path, monkeypatch):
    service = EvolutionToolSet(workdir=str(tmp_path), manager=EvolutionManager(tmp_path))
    cleaned = []
    async def failed_run(team, *args, **kwargs):
        async def last(): cleaned.append('last')
        async def fail(): cleaned.append('fail'); raise OSError('kernel did not exit')
        async def first(): cleaned.append('first')
        for callback in (last, fail, first): team._resources.own(callback)
        raise ValueError('initial execution error')
    monkeypatch.setattr(EvolutionTeam, '_evolve', failed_run)
    result = await service.evolve_code('x=1', 'def evaluate(p): return {}', 'test')
    session = service.manager.get_session(result['evolution_id'])
    with pytest.raises(EvolutionCleanupError): await session.task
    assert cleaned == ['first', 'fail', 'last'] and session.status == 'failed'
    with pytest.raises(RuntimeError, match='shutdown'):
        await service.begin_shutdown()


@pytest.mark.asyncio
async def test_iteration_waits_for_accepted_background_edits_before_evaluation(tmp_path, monkeypatch, local_settings):
    from pantheon.team.pantheon import PantheonTeam
    agents = []
    async def scripted_run(team, *args, **kwargs):
        agent = team.team_agents[0]; agents.append(agent)
        root = agent.providers['evo-py'].toolset.workdir
        async def edit():
            await asyncio.sleep(.05)
            (root / 'main.py').write_text('x = 5')
            return 'edited'
        agent._bg_manager.start(tool_name='edit', tool_call_id='edit', args={}, coro=edit(), source='explicit')
        return SimpleNamespace(details=None)
    monkeypatch.setattr(PantheonTeam, 'run', scripted_run)
    config = EvolutionConfig(max_iterations=1, llm_weight=0, function_weight=1,
                             workspace_path=str(tmp_path), log_level='ERROR')
    engine = EvolutionTeam(config=config)
    result = await engine.evolve('x = 1', "def evaluate(path):\n from pathlib import Path\n return {'score': int(Path(path, 'main.py').read_text().split('=')[1]), 'fitness_weights': {'score': 1}}", 'increase x')
    assert any(p.snapshot.files['main.py'] == 'x = 5' and p.metrics['score'] == 5 for p in engine.database.programs.values())
    assert not result.iteration_results[0].error
    assert all(task.asyncio_task.done() for task in agents[0]._bg_manager.list_tasks())


@pytest.mark.asyncio
async def test_app_stop_reaps_actual_coding_kernel_and_background_shell_before_ack(tmp_path, monkeypatch, local_settings):
    if os.name != 'posix': pytest.skip('POSIX process-group acceptance')
    from pantheon.team.pantheon import PantheonTeam
    from pantheon.apps.builtin.desktop.app_runtime import AppContext
    from pantheon.apps.toolset_backend import register_toolset
    entered = asyncio.Event()
    owned = []
    async def scripted_run(team, *args, **kwargs):
        agent = team.team_agents[0]
        py = agent.providers['evo-py'].toolset
        root = py.workdir
        owned.append(py)
        reply = await call(agent, 'evo-py__run_python_code', {'code':
            f"from pathlib import Path\nimport os\nPath({str(root / 'kernel.pid')!r}).write_text(str(os.getpid()))"})
        assert (root / 'kernel.pid').exists(), reply
        script = "import subprocess,sys,os,json,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path('shell.json').write_text(json.dumps([os.getpid(), child.pid])); time.sleep(60)"
        await call(agent, 'evo-sh__run_command', {'command': shlex.quote(sys.executable) + ' -c ' + shlex.quote(script), '_background': True})
        async with asyncio.timeout(8):
            while not (root / 'shell.json').exists(): await asyncio.sleep(.01)
        entered.set()
        return SimpleNamespace(details=None)
    monkeypatch.setattr(PantheonTeam, 'run', scripted_run)
    # Mutation decisions and feedback are controlled; no provider call is made.
    async def feedback(*args, **kwargs): return {'score': 50}
    monkeypatch.setattr('pantheon.evolution.evaluator.HybridEvaluator._get_llm_feedback', feedback)
    service = EvolutionToolSet(workdir=str(tmp_path / 'app'), manager=EvolutionManager(tmp_path / 'app'))
    ctx = AppContext('evolution', tmp_path, tmp_path / 'state', None)
    await register_toolset(ctx, service)
    pids = []
    try:
        result = await service.evolve_code('x = 1', 'def evaluate(p): return {}', 'stop test')
        await asyncio.wait_for(entered.wait(), 35)
        root = owned[0].workdir
        pids = [int((root / 'kernel.pid').read_text()), *json.loads((root / 'shell.json').read_text())]
        await asyncio.wait_for(ctx.before_stop(), 15)
        session = service.manager.get_session(result['evolution_id'])
        assert session.task.done() and session.status == 'cancelled'
        assert not owned[0].kernels.sessions
        await exited(pids)
    finally:
        await ctx._cleanup()
        for pid in pids:
            try: os.kill(pid, 9)
            except ProcessLookupError: pass


@pytest.mark.asyncio
async def test_parallel_cleanup_failure_reaches_collector_and_other_workers_close(tmp_path, monkeypatch):
    from pantheon.evolution.result import IterationResult
    ready = asyncio.Event()
    entered, cleaned = [], []
    async def iteration(worker, index, *args, worker_id=None):
        async def finish():
            cleaned.append(worker_id)
            if worker_id == 0: raise OSError('injected worker cleanup failure')
        worker._resources.own(finish)
        entered.append(worker_id)
        if len(entered) == 2: ready.set()
        await ready.wait()
        parent = next(iter(worker.database.programs))
        return IterationResult(iteration=index, parent_id=parent, child_id=parent,
            parent_score=1, child_score=1, improvement=0, accepted=False)
    monkeypatch.setattr(EvolutionTeam, '_run_iteration_single_agent', iteration)
    config = EvolutionConfig(num_workers=2, max_iterations=2, llm_weight=0, function_weight=1,
                             workspace_path=str(tmp_path), log_level='ERROR')
    team = EvolutionTeam(config=config)
    with pytest.raises(EvolutionCleanupError):
        await asyncio.wait_for(team.evolve('x=1', 'def evaluate(p): return {"score": 1}', 'test'), 10)
    assert sorted(cleaned) == [0, 1]
    with pytest.raises(RuntimeError, match='cleanup recovery'):
        await team.evolve('x=1', 'def evaluate(p): return {}', 'must not restart')


@pytest.mark.asyncio
async def test_cancelled_file_observer_cannot_release_a_still_writing_thread(tmp_path):
    from threading import Event
    from pantheon.apps.builtin.file import FileManagerToolSet
    from pantheon.toolset import tool
    from pantheon.evolution.local_tools import EvolutionLocalProvider
    entered, release = Event(), Event()
    target = tmp_path / 'accepted.txt'
    class Files(FileManagerToolSet):
        @tool
        def held_write(self):
            """Write after a controlled blocking disk operation."""
            entered.set()
            if not release.wait(10): raise TimeoutError('test did not release disk work')
            target.write_text('accepted mutation completed')
            return {'success': True}
    provider = EvolutionLocalProvider(Files('files', tmp_path))
    await provider.initialize()
    observer = asyncio.create_task(provider.call_tool('held_write', {}))
    stopping = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError): await observer
        stopping = asyncio.create_task(provider.shutdown())
        await asyncio.sleep(.02)
        assert not stopping.done() and not target.exists()
        stopping.cancel(); await asyncio.sleep(.02)
        assert not stopping.done(), 'Cancelled stop observer must still join the disk mutation'
        release.set()
        with pytest.raises(asyncio.CancelledError): await stopping
        await provider.shutdown()
        assert target.read_text() == 'accepted mutation completed'
        with pytest.raises(RuntimeError, match='closed'): await provider.call_tool('held_write', {})
    finally:
        release.set()
        await asyncio.gather(observer, *([stopping] if stopping else []), return_exceptions=True)
        await provider.shutdown()


@pytest.mark.asyncio
async def test_partial_mutation_tool_setup_releases_real_kernel(tmp_path, monkeypatch, local_settings):
    from pantheon.agent import Agent
    created = []
    original = Agent.toolset
    async def attach(agent, provider):
        if getattr(provider, 'toolset_name', '') == 'evo-py':
            py = provider.toolset; created.append(py)
            await provider.call_tool('run_python_code', {'code':
                f"import os\nfrom pathlib import Path\nPath({str(tmp_path / 'setup.pid')!r}).write_text(str(os.getpid()))"})
            raise RuntimeError('injected attachment failure')
        return await original(agent, provider)
    monkeypatch.setattr(Agent, 'toolset', attach)
    config = EvolutionConfig(max_iterations=1, llm_weight=0, function_weight=1,
                             workspace_path=str(tmp_path), log_level='ERROR')
    engine = EvolutionTeam(config=config)
    result = await engine.evolve('x=1', 'def evaluate(p): return {}', 'test')
    assert 'attachment failure' in result.iteration_results[0].error
    assert created and not created[0].kernels.sessions
    await exited([int((tmp_path / 'setup.pid').read_text())])


@pytest.mark.asyncio
async def test_cancelled_legacy_analyzer_reaps_its_kernel(tmp_path, monkeypatch, local_settings):
    from pantheon.agent import Agent
    entered = asyncio.Event()
    created = []
    async def analyzer_run(agent, *args, **kwargs):
        assert agent.name == 'code-analyzer', 'No external inference permitted in this test'
        py = agent.providers['analyzer-python'].toolset; created.append(py)
        await call(agent, 'analyzer-python__run_python_code', {'code':
            f"from pathlib import Path\nimport os\nPath({str(tmp_path / 'analyzer.pid')!r}).write_text(str(os.getpid()))"})
        entered.set()
        await call(agent, 'analyzer-python__run_python_code', {'code': 'import time; time.sleep(60)'})
    monkeypatch.setattr(Agent, 'run', analyzer_run)
    config = EvolutionConfig(max_iterations=1, llm_weight=0, function_weight=1,
        single_agent_mutation=False, use_analyzer=True, analyzer_use_python=True,
        workspace_path=str(tmp_path), log_level='ERROR')
    task = asyncio.create_task(EvolutionTeam(config=config).evolve('x=1', 'def evaluate(p): return {}', 'test'))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 15)
        assert not created[0].kernels.sessions
        await exited([int((tmp_path / 'analyzer.pid').read_text())])
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_foreground_agent_cancellation_joins_tool_teardown(local_settings):
    from pantheon.agent import Agent
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    agent = Agent('owner', '', model='openai/fixture')
    @agent.tool
    async def owned_tool():
        """Hold a resource until its asynchronous cleanup finishes."""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release.wait()
    task = asyncio.create_task(call(agent, 'owned_tool', {}))
    try:
        await entered.wait()
        task.cancel()
        await cancelled.wait()
        assert not task.done()
        task.cancel(); await asyncio.sleep(.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_cleanup_fault_stops_collector_without_waiting_for_unassigned_iterations(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise EvolutionCleanupError([OSError('resource still active')])
    monkeypatch.setattr(EvolutionTeam, '_run_iteration_single_agent', fail)
    team = EvolutionTeam(config=EvolutionConfig(num_workers=2, max_iterations=20,
        llm_weight=0, function_weight=1, workspace_path=str(tmp_path), log_level='ERROR'))
    with pytest.raises(EvolutionCleanupError):
        await asyncio.wait_for(team.evolve('x=1', 'def evaluate(p): return {}', 'test'), 5)
    with pytest.raises(RuntimeError, match='cleanup recovery'):
        await team.evolve('x=1', 'def evaluate(p): return {}', 'test')


@pytest.mark.asyncio
async def test_parallel_worker_directory_failure_does_not_hang_collector(tmp_path):
    root = tmp_path / 'not-a-directory'
    root.write_text('preserve this file')
    team = EvolutionTeam(config=EvolutionConfig(num_workers=2, max_iterations=5,
        llm_weight=0, function_weight=1, workspace_path=str(root), log_level='ERROR'))
    with pytest.raises(OSError):
        await asyncio.wait_for(team.evolve('x=1', 'def evaluate(p): return {}', 'test'), 5)
    assert root.read_text() == 'preserve this file'
