"""Ordinary execution dependency with real Evolution tools, budgets and archive."""
import asyncio
import json
from pathlib import Path
import sqlite3
import shlex
import sys
import os
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import urllib.request

import pytest

from pantheon.evolution import EvolutionConfig, EvolutionTeam
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.remote_execution import RemoteEvolutionBinding, OwnedMutationTool
from pantheon.evolution.local_shell import LocalShellToolSet
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.python import PythonInterpreterToolSet
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from test_agent_execution_runner import BoundClient
from test_evolution_worker_resources import local_settings, exited


EVALUATOR = "def evaluate(path):\n from pathlib import Path\n x = int(Path(path, 'main.py').read_text().split('=')[1])\n return {'score': x, 'fitness_weights': {'score': 1}}"


def configuration(tmp_path, **kwargs):
    return EvolutionConfig(max_iterations=1, max_tool_calls_per_mutation=6,
        max_evaluations_per_mutation=1, llm_weight=0, function_weight=1,
        mutator_model='openai/gpt-4o-mini',
        workspace_path=str(tmp_path / 'work'), db_path=str(tmp_path / 'archive'), log_level='ERROR', **kwargs)


def binding(tmp_path, client, settings, *, run_id='run', captures=None):
    async def tools(workdir):
        py = PythonInterpreterToolSet('owned-python', str(workdir), strict_lifecycle=True)
        if captures is not None: captures.append(py)
        return {'files': OwnedMutationTool(FileManagerToolSet('owned-files', str(workdir),
                        file_settings=settings, template_fallback=False)),
                'python': OwnedMutationTool(py, cancel_on_stop=True, reset_after_iteration=True),
                'shell': OwnedMutationTool(LocalShellToolSet('owned-shell', str(workdir)), cancel_on_stop=True)}
    return RemoteEvolutionBinding(client, tmp_path / 'receipts', run_id=run_id,
        binding_id='agent-evolution', tool_factory=tools)


@pytest.mark.asyncio
async def test_parallel_remote_workers_preserve_tools_budgets_and_archive(tmp_path, monkeypatch, local_settings):
    arrived, responses, kernels = [], [], []
    both = asyncio.Event()
    async def engine(spec, invoke):
        assert {'files', 'python', 'shell', 'evolution'} == set(spec['tools'])
        arrived.append(spec)
        if len(arrived) == 2: both.set()
        await asyncio.wait_for(both.wait(), 10)
        response = await invoke('shell', 'run_command', {'command': 'pwd'})
        root = Path(response['output'].strip())
        value = int(root.parent.name.split('-')[-1]) + 2
        response = await invoke('python', 'run_python_code', {'code':
            f"from pathlib import Path\nimport os\nowned={value}\nPath({str(root / 'main.py')!r}).write_text('x = '+str(owned))\nPath({str(root / 'kernel.pid')!r}).write_text(str(os.getpid()))\nprint(owned)"})
        assert str(value) in str(response)
        read = await invoke('files', 'read_file', {'file_path': 'main.py'})
        assert f'x = {value}' in str(read)
        verified = await invoke('evolution', 'run_evaluator', {})
        assert verified['success'] and verified['evaluations_left'] == 0
        blocked = await invoke('evolution', 'run_evaluator', {})
        assert 'Evaluation budget exhausted' in blocked['error']
        assert blocked['actions_left'] == 1
        await invoke('evolution', 'think', {'thought': 'Finalize'})
        denied = await invoke('shell', 'run_command', {'command': 'touch forbidden'})
        assert 'Action budget exhausted' in denied
        assert not (root / 'forbidden').exists()
        assert 'Submitted' in await invoke('evolution', 'submit', {'summary': f'value {value}'})
        responses.append(value)
        return {'content': 'done', 'details': {'messages': [
            {'role': 'assistant', '_metadata': {'current_cost': .02}}]}}

    def forbid(*args, **kwargs): raise AssertionError('Evolution must not construct a local Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    config = configuration(tmp_path, num_workers=2)
    config.max_iterations = 2
    team = EvolutionTeam(config=config, remote_execution=binding(tmp_path, BoundClient(service), local_settings, captures=kernels))
    try:
        result = await asyncio.wait_for(team.evolve('x = 1', EVALUATOR, 'increase x'), 45)
        assert sorted(responses) == [2, 3], [r.error for r in result.iteration_results]
        assert all(r.error is None and r.llm_cost == .02 for r in result.iteration_results)
        children = [p for p in team.database.programs.values() if p.parent_id]
        assert {p.snapshot.files['main.py'] for p in children} == {'x = 2', 'x = 3'}
        assert {p.mutation_summary for p in children} == {'value 2', 'value 3'}
        assert all(not py.kernels.sessions for py in kernels)
        await exited([int((py.workdir / 'kernel.pid').read_text()) for py in kernels])
        states = []
        for path in (tmp_path / 'receipts').rglob('mutation-state.sqlite3'):
            with sqlite3.connect(path) as db:
                states.extend(json.loads(row[0]) for row in db.execute('SELECT record FROM mutations'))
        assert len(states) == 2 and all(s['phase'] == 'finalized' for s in states)
        assert all(s['state']['_mut_tool_calls_used'] == 6 and s['state']['_mut_eval_count'] == 1 for s in states)
        assert len({s['id'] for s in states}) == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_existing_mutation_does_not_reset_its_workspace_or_repeat_inference(tmp_path, local_settings):
    calls = []
    async def engine(spec, invoke):
        calls.append('model')
        await invoke('shell', 'run_command', {'command': "printf 'x = 7' > main.py"})
        await invoke('evolution', 'submit', {'summary': 'seven'})
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    try:
        def team(): return EvolutionTeam(config=configuration(tmp_path),
            remote_execution=binding(tmp_path, BoundClient(service), local_settings))
        await team().evolve('x = 1', EVALUATOR, 'increase x')
        target = tmp_path / 'work/_mutation_wt/main.py'
        target.write_text('x = 99')
        with pytest.raises(EvolutionCleanupError):
            await team().evolve('x = 1', EVALUATOR, 'increase x')
        assert target.read_text() == 'x = 99'
        assert calls == ['model']
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_turn_winddown_and_unlimited_requests_preserve_config(tmp_path, local_settings):
    specs = []
    async def engine(spec, invoke):
        specs.append(spec)
        await invoke('evolution', 'submit', {'summary': 'unchanged'})
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    try:
        for index, budget in enumerate((None, 10)):
            root = tmp_path / str(index)
            config = configuration(root)
            config.max_tool_calls_per_mutation = None
            config.max_mutation_turns = budget
            team = EvolutionTeam(config=config, remote_execution=binding(root, BoundClient(service), local_settings,
                                                                         run_id=f'run-{index}'))
            await team.evolve('x = 1', EVALUATOR, 'keep x')
        assert specs[0]['max_turns'] is None and specs[0]['turn_messages'] == []
        assert specs[1]['max_turns'] == 12
        assert [m['turn'] for m in specs[1]['turn_messages']] == [7, 8, 9, 10]
        assert specs[1]['turn_messages'][-1]['repeat'] is True
        assert 'FINAL turn' in specs[1]['turn_messages'][-1]['content']
    finally:
        await service.close()


@pytest.fixture
def evolution_model():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/responses'):
                self.send_response(404); self.end_headers(); return
            calls.append(body)
            tools = [m for m in body['messages'] if m['role'] == 'tool']
            actions = [
                ('shell__run_command', {'command': "printf 'x = 8' > main.py"}),
                ('python__run_python_code', {'code': 'print(6 * 7)'}),
                ('evolution__run_evaluator', {}),
                ('evolution__submit', {'summary': 'Verified eight using the caller evaluator'}),
            ]
            if any('You are an expert code reviewer.' in str(m.get('content', ''))
                   for m in body['messages'] if m['role'] == 'system'):
                delta, finish = {'role': 'assistant', 'content': json.dumps({
                    'score': 80, 'summary': 'Reviewed by the Agent App',
                    'issues': [], 'suggestions': ['Keep the measured improvement']})}, 'stop'
            elif len(tools) < len(actions):
                name, args = actions[len(tools)]
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call-{len(tools)}',
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'role': 'assistant', 'content': 'Submitted verified eight'}, 'stop'
            events = [
                {'id': 'evolution_fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'],
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'evolution_fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'],
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}},
            ]
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            for event in events: self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', calls=calls)
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize("feedback", [False, True])
async def test_actual_evolution_uses_independent_agent_app_process(tmp_path, local_settings, evolution_model, monkeypatch, feedback):
    from test_agent_native_process import native_process, request
    from pantheon.apps.agent_execution_client import AgentExecutionClient
    from pantheon.apps.dependency_client import DependencyClient
    from pantheon.apps.runtime_config import RuntimeCredential
    root = tmp_path / 'native'
    root.mkdir()
    def forbid(*args, **kwargs): raise AssertionError('Caller constructed a local Agent')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    kernels = []
    with native_process(root, evolution_model.url) as (child, base):
        async with asyncio.timeout(15):
            while True:
                try:
                    if (await request(base, '/health'))['ready']: break
                except OSError:
                    assert child.poll() is None, (root / 'process.log').read_text()[-15000:]
                await asyncio.sleep(.05)
        class LocalGrant(DependencyClient):
            def invoke(self, method, args, **kwargs):
                req = urllib.request.Request(base + '/rpc', json.dumps({'method': method,
                    'args': {'consumer_id': 'evolution-consumer', **args}}).encode(),
                    {'Content-Type': 'application/json', 'X-Fleet-RPC-Token': 'native-test-token'})
                with urllib.request.urlopen(req, timeout=20) as response: return json.load(response)
        sdk = AgentExecutionClient(LocalGrant(RuntimeCredential('https://bound.example/rpc', 'a' * 64)))
        config = configuration(tmp_path)
        if feedback:
            config.llm_weight, config.function_weight = .3, .7
        config.max_tool_calls_per_mutation = None
        config.max_mutation_turns = 7
        team = EvolutionTeam(config=config,
            remote_execution=binding(tmp_path, sdk, local_settings, captures=kernels))
        try:
            result = await asyncio.wait_for(team.evolve('x = 1', EVALUATOR, 'increase x'), 45)
            assert len(result.iteration_results) == 1 and result.iteration_results[0].error is None
            [program] = [p for p in team.database.programs.values() if p.parent_id]
            assert program.snapshot.files['main.py'] == 'x = 8'
            assert program.mutation_summary == 'Verified eight using the caller evaluator'
            reviews = [c for c in evolution_model.calls if any(
                'You are an expert code reviewer.' in str(m.get('content', ''))
                for m in c['messages'] if m['role'] == 'system')]
            mutations = [c for c in evolution_model.calls if c not in reviews]
            assert len(mutations) == 5
            assert len(reviews) == (3 if feedback else 0)
            if feedback:
                assert program.metrics['llm_score'] == .8
                assert program.llm_feedback == 'Reviewed by the Agent App'
                assert all('Current Evaluation Metrics' in str(c['messages']) for c in reviews)
            assert '42' in str(mutations[2]['messages'])
            assert 'evaluations_left' in str(mutations[3]['messages'])
            assert 'Only 3 turn(s)' in str(mutations[3]['messages'])
            assert 'Only 2 turn(s)' in str(mutations[4]['messages'])
            assert all(not py.kernels.sessions for py in kernels)
            assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
        finally:
            await sdk.close()


@pytest.mark.asyncio
async def test_remote_failure_does_not_salvage_or_repeat_ambiguous_mutation(tmp_path, local_settings):
    async def engine(spec, invoke):
        await invoke('shell', 'run_command', {'command': "printf 'x = 8' > main.py"})
        return {'content': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    class LostReply(BoundClient):
        async def reply(self, *args):
            raise OSError('reply outcome unknown')
    team = EvolutionTeam(config=configuration(tmp_path),
        remote_execution=binding(tmp_path, LostReply(service), local_settings))
    try:
        with pytest.raises(EvolutionCleanupError):
            await team.evolve('x = 1', EVALUATOR, 'increase x')
        assert (tmp_path / 'work/_mutation_wt/main.py').read_text() == 'x = 8'
        assert not [p for p in team.database.programs.values() if p.parent_id]
        assert team._cleanup_failed
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_remote_mutation_joins_shell_parent_and_descendant(tmp_path, local_settings):
    if os.name != 'posix': pytest.skip('POSIX process group acceptance')
    script = "import subprocess,sys,os,json,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path('pids.json').write_text(json.dumps([os.getpid(), child.pid])); time.sleep(60)"
    async def engine(spec, invoke):
        return await invoke('shell', 'run_command', {'command': shlex.quote(sys.executable) + ' -c ' + shlex.quote(script)})
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = EvolutionTeam(config=configuration(tmp_path),
        remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    task = asyncio.create_task(team.evolve('x = 1', EVALUATOR, 'increase x'))
    pids = []
    try:
        async with asyncio.timeout(10):
            while not (tmp_path / 'work/_mutation_wt/pids.json').exists(): await asyncio.sleep(.01)
        pids = json.loads((tmp_path / 'work/_mutation_wt/pids.json').read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        await exited(pids)
        assert not [p for p in team.database.programs.values() if p.parent_id]
        # The tool's stop receipt is settled even though inference was cancelled.
        with sqlite3.connect(tmp_path / 'agent/requests.sqlite3') as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT record FROM executions')]
        assert len(records) == 1 and records[0]['state'] == 'cancelled'
        assert all(call['state'] == 'settled' for call in records[0]['calls'].values())
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()
        for pid in pids:
            try: os.kill(pid, 9)
            except ProcessLookupError: pass


@pytest.mark.asyncio
async def test_mutation_state_write_failure_prevents_tool_effect(tmp_path, local_settings, monkeypatch):
    from pantheon.evolution.remote_execution import RemoteEvolutionMutation
    original = RemoteEvolutionMutation._save
    async def fail_after_charge(self, **kwargs):
        if self.team._mut_tool_calls_used:
            raise OSError('state write failed')
        return await original(self, **kwargs)
    monkeypatch.setattr(RemoteEvolutionMutation, '_save', fail_after_charge)
    async def engine(spec, invoke):
        return await invoke('shell', 'run_command', {'command': 'touch forbidden'})
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = EvolutionTeam(config=configuration(tmp_path),
        remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    try:
        with pytest.raises(EvolutionCleanupError): await team.evolve('x = 1', EVALUATOR, 'increase x')
        assert not (tmp_path / 'work/_mutation_wt/forbidden').exists()
        assert not [p for p in team.database.programs.values() if p.parent_id]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_partial_setup_closes_every_owned_tool_before_unlock(tmp_path):
    from pantheon.evolution.remote_execution import RemoteEvolutionMutation
    from pantheon.apps.agent_execution_runner import ToolReceiptJournal
    closed = []
    root = tmp_path / 'receipts'
    class Tool:
        tool_functions = {}
        def __init__(self, name): self.name = name
        async def run_setup(self): raise RuntimeError('provider setup failed')
        async def cleanup(self):
            with pytest.raises(OSError): ToolReceiptJournal(root, 'binding')
            closed.append(self.name)
    async def factory(workdir):
        return {name: OwnedMutationTool(Tool(name)) for name in ('one', 'two')}
    async def hook(*args): pass
    team = SimpleNamespace(_mut_workdir=tmp_path / 'work')
    bound = RemoteEvolutionBinding(None, root, run_id='run', binding_id='binding', tool_factory=factory)
    mutation = RemoteEvolutionMutation(bound, root, team, hook, hook)
    try:
        with pytest.raises(RuntimeError, match='provider setup failed'):
            await mutation.setup([])
    finally:
        await mutation.close()
    assert closed == ['two', 'one']
    journal = ToolReceiptJournal(root, 'binding')
    journal.close()


@pytest.mark.asyncio
async def test_failed_tool_shutdown_retains_exclusive_mutation_owner(tmp_path):
    from pantheon.evolution.remote_execution import RemoteEvolutionMutation
    from pantheon.apps.agent_execution_runner import ToolReceiptJournal
    root = tmp_path / 'receipts'
    class Tool:
        tool_functions = {}
        async def run_setup(self): pass
        async def cleanup(self): raise RuntimeError('kernel still owns resources')
    async def factory(workdir): return {'python': OwnedMutationTool(Tool())}
    async def hook(*args): pass
    bound = RemoteEvolutionBinding(None, root, run_id='run', binding_id='binding', tool_factory=factory)
    mutation = RemoteEvolutionMutation(bound, root, SimpleNamespace(_mut_workdir=tmp_path), hook, hook)
    await mutation.setup([])
    try:
        with pytest.raises(EvolutionCleanupError): await mutation.close()
        with pytest.raises(OSError): ToolReceiptJournal(root, 'binding')
    finally:
        # This fixture has no process to reap. Simulate owner process exit only
        # after proving failed cleanup cannot admit a second writer.
        mutation._session.journal.close()


@pytest.mark.asyncio
async def test_deleted_mutation_receipt_blocks_effect_before_dispatch(tmp_path, monkeypatch, local_settings):
    from pantheon.evolution.remote_execution import RemoteEvolutionMutation
    original = RemoteEvolutionMutation._invoke
    async def remove_receipt(self, *args):
        with sqlite3.connect(self._path) as db:
            db.execute('DELETE FROM mutations')
        return await original(self, *args)
    monkeypatch.setattr(RemoteEvolutionMutation, '_invoke', remove_receipt)
    async def engine(spec, invoke):
        return {'content': await invoke('shell', 'run_command', {'command': 'touch forbidden'})}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    team = EvolutionTeam(config=configuration(tmp_path),
        remote_execution=binding(tmp_path, BoundClient(service), local_settings))
    try:
        with pytest.raises(EvolutionCleanupError): await team.evolve('x = 1', EVALUATOR, 'increase x')
        assert not (tmp_path / 'work/_mutation_wt/forbidden').exists()
        assert not [p for p in team.database.programs.values() if p.parent_id]
    finally:
        await service.close()
