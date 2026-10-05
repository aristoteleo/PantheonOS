"""Real packaged tool subprocesses through the actual Evolution controller.

Local subprocess placement is a test boundary, not production OS isolation.
The Agent service/model replies are controlled fixtures.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pantheon.apps.modal_app_transport import ModalAppTransport
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from pantheon.evolution import EvolutionConfig, EvolutionTeam, HybridEvaluator
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.remote_execution import RemoteEvolutionBinding
from pantheon.evolution.sandbox.package import build_package
from test_agent_execution_runner import BoundClient
from test_evolution_tools_package import BOOT, EVALUATOR


class LocalPlacement:
    def __init__(self, package, root):
        self.package, self.root = package, root
        self.backend_id = 'local-' + root.parent.name + '-' + root.parent.parent.name
        self.process = self.pipe = self.closing = None
        self.calls = []

    async def start(self):
        self.root.mkdir(parents=True)
        state = self.root / 'state'
        state.mkdir()
        env = {key: value for key, value in os.environ.items()
               if key in ('PATH', 'LANG', 'TMPDIR', 'SYSTEMROOT', 'WINDIR')}
        self.process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', BOOT,
            str(self.package / '.fleet-runtime/app_runtime.py'), '--app-dir', str(self.package),
            '--app-id', 'evolution-tools', '--workspace', str(self.root / 'work'), '--state-dir', str(state),
            cwd=self.root, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def chunks(reader):
            while value := await reader.read(8192):
                yield value
        self.pipe = ModalAppTransport(SimpleNamespace(object_id=self.backend_id,
            stdin=SimpleNamespace(write=self.process.stdin.write, drain=SimpleNamespace(aio=self.process.stdin.drain)),
            stdout=chunks(self.process.stdout), stderr=chunks(self.process.stderr)))
        await self.pipe.ready(timeout=15)
        return self

    async def invoke(self, method, args):
        self.calls.append(method)
        return await self.pipe.invoke(method, args)

    async def close(self):
        async def dispose():
            if self.pipe is not None:
                await self.pipe.shutdown()
            if self.process is not None:
                await asyncio.wait_for(self.process.wait(), 15)
            if self.pipe is not None:
                await self.pipe.disconnect()
            return {'backend_id': self.backend_id, 'stopped': True}
        if self.closing is None:
            self.closing = asyncio.create_task(dispose())
        return await asyncio.shield(self.closing)


@pytest.mark.asyncio
@pytest.mark.parametrize('workers,review', [(1, False), (2, False), (1, True), (2, True)])
async def test_controller_initial_evaluation_mutation_review_archive_and_stop(tmp_path, monkeypatch, workers, review):
    package = build_package(tmp_path / 'package')
    from pantheon.evolution.sandbox.remote_operation import RemoteSandboxOperation
    operations = []
    original = RemoteSandboxOperation.__init__
    def record(self, *args):
        original(self, *args)
        operations.append(self)
    monkeypatch.setattr(RemoteSandboxOperation, '__init__', record)
    placements, calls = [], []
    def factory(root, *, operation_id):
        placed = LocalPlacement(package, root)
        placements.append(placed)
        return placed
    async def engine(spec, invoke):
        calls.append(spec)
        if not spec['tools']:
            return {'content': json.dumps({'score': 90, 'summary': 'Review retained', 'issues': [], 'suggestions': []})}
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        assert '42' in str(await invoke('python', 'run_python_code', {'code': 'print(6*7)'}))
        assert (await invoke('evolution', 'run_evaluator', {}))['metrics']['score'] == .8
        await invoke('evolution', 'submit', {'summary': 'Improved in isolated App'})
        return {'content': 'Submitted'}
    def forbid(*_, **__):
        raise AssertionError('Controller used embedded Agent, host evaluation or ambient provider env')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    monkeypatch.setattr(HybridEvaluator, 'evaluate', forbid)
    monkeypatch.setattr(EvolutionTeam, '_sandbox_provider_env', forbid)
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = EvolutionConfig(max_iterations=workers, num_workers=workers, sandbox_mutation=True,
        function_weight=1, llm_weight=.2 if review else 0, workspace_path=str(tmp_path / 'work'),
        db_path=str(tmp_path / 'archive'), evaluation_timeout=30, mutation_timeout=60, log_level='ERROR')
    binding = RemoteEvolutionBinding(BoundClient(service), tmp_path / 'receipts', run_id='test',
        binding_id='evolution', sandbox_factory=factory)
    team = EvolutionTeam(config=config, remote_execution=binding)
    try:
        result = await team.evolve('x=1', EVALUATOR, 'Improve score')
        assert len(result.iteration_results) == workers
        assert all(r.error is None for r in result.iteration_results)
        assert len(placements) == workers + 1
        assert all(o.request is None and o.execution is None and o.task is None and o.placement is None
                   for o in operations), 'Completed iterations retained their source/tool/Agent payloads'
        assert placements[0].calls == ['initialize', 'evaluate_initial']
        assert all(p.process.returncode == 0 for p in placements)
        children = [p for p in team.database.programs.values() if p.parent_id]
        assert len(children) == workers
        assert all(p.snapshot.files == {'main.py': 'x=8'} and p.metrics['score'] == .8 for p in children)
        if review:
            assert all(p.metrics['llm_score'] == .9 and p.llm_feedback == 'Review retained'
                       for p in team.database.programs.values())
        assert len([c for c in calls if c['tools']]) == workers
        previous = len(placements)
        with pytest.raises(EvolutionCleanupError):
            await EvolutionTeam(config=config, remote_execution=binding).evolve('x=1', EVALUATOR, 'Improve score')
        assert len(placements) == previous
    finally:
        await asyncio.gather(*(p.close() for p in placements), return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['evaluate_initial', 'finish'])
async def test_lost_result_fences_entire_run_without_repeating_initial_or_mutation(tmp_path, monkeypatch, lost):
    package = build_package(tmp_path / 'package')
    placements, calls = [], []
    class LostResult(LocalPlacement):
        async def invoke(self, method, args):
            value = await super().invoke(method, args)
            if method == lost:
                raise OSError('Lost response after remote effect')
            return value
    def factory(root, *, operation_id):
        p = LostResult(package, root)
        placements.append(p)
        return p
    async def engine(spec, invoke):
        calls.append(spec)
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        await invoke('evolution', 'submit', {'summary': 'eight'})
        return {'content': 'submitted'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = EvolutionConfig(max_iterations=1, sandbox_mutation=True, llm_weight=0,
        workspace_path=str(tmp_path / 'work'), db_path=str(tmp_path / 'archive'), log_level='ERROR')
    binding = RemoteEvolutionBinding(BoundClient(service), tmp_path / 'receipts', run_id='lost',
        binding_id='evolution', sandbox_factory=factory)
    team = EvolutionTeam(config=config, remote_execution=binding)
    try:
        with pytest.raises(EvolutionCleanupError):
            await team.evolve('x=1', EVALUATOR, 'Improve score')
        assert all(p.process.returncode == 0 for p in placements)
        assert len(calls) == (0 if lost == 'evaluate_initial' else 1)
        assert not [p for p in team.database.programs.values() if p.parent_id]
        previous = len(placements)
        with pytest.raises(RuntimeError, match='requires cleanup recovery'):
            await team.evolve('x=1', EVALUATOR, 'Improve score')
        with pytest.raises(EvolutionCleanupError):
            await EvolutionTeam(config=config, remote_execution=binding).evolve('x=1', EVALUATOR, 'Improve score')
        assert len(placements) == previous
    finally:
        await asyncio.gather(*(p.close() for p in placements), return_exceptions=True)
        await service.close()
