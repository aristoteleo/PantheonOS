"""Container-side ordinary tools, exercised locally; these are not isolation tests."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys

import pytest

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.python import PythonInterpreterToolSet
from pantheon.evolution.local_shell import LocalShellToolSet
from pantheon.evolution.sandbox.tool_backend import register_sandbox_mutation
from pantheon.evolution.lifetime import EvolutionCleanupError
from pantheon.evolution.evaluator import HybridEvaluator
from pantheon.evolution.program import CodebaseSnapshot, Program
from pantheon.settings import Settings
from test_evolution_worker_resources import exited


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = "def evaluate(path):\n from pathlib import Path\n x=int(Path(path,'main.py').read_text().split('=')[1])\n return {'score':x/10,'fitness_weights':{'score':1}}"


def context(tmp_path):
    state = tmp_path / 'state'
    state.mkdir(parents=True, exist_ok=True)
    return AppContext('isolated-tools', tmp_path / 'work', state, None)


async def compose(ctx, **kwargs):
    async def factory(work):
        settings = Settings(Path(ctx.state_dir) / 'settings', isolated_env=True, environment={},
                            user_home=Path(ctx.state_dir) / 'home')
        return {'files': FileManagerToolSet('files', str(work), file_settings=settings, template_fallback=False),
                'python': PythonInterpreterToolSet('python', str(work), strict_lifecycle=True),
                'shell': LocalShellToolSet('shell', str(work))}
    return await register_sandbox_mutation(ctx, tool_factory=factory, parent_files={'main.py': 'x=1'},
        evaluator_code=EVALUATOR, objective='Improve score', **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('submit', [True, False])
async def test_real_tools_initial_evaluation_submit_and_salvage(tmp_path, submit):
    ctx = context(tmp_path)
    backend = await compose(ctx, inspirations=[{'files': {'main.py': 'x=3'}, 'score': 3}])
    try:
        assert set(ctx._methods) == {'describe', 'invoke_tool', 'finish', 'evaluate_initial'}
        assert set(backend.describe()['tools']) == {'files', 'python', 'shell', 'evolution'}
        assert 'inspirations' in backend.describe()['prompt']
        assert (ctx.workspace / 'inspirations/insp_1/main.py').read_text() == 'x=3'
        assert (await backend.evaluate_initial())['metrics']['score'] == .1
        await backend.invoke_tool('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        python = await backend.invoke_tool('python', 'run_python_code', {'code':
            "import os\nfrom pathlib import Path\nPath('kernel.pid').write_text(str(os.getpid()))\nprint(6*7)"})
        assert '42' in str(python)
        pid = int((ctx.workspace / 'kernel.pid').read_text())
        assert (await backend.invoke_tool('evolution', 'run_evaluator', {}))['metrics']['score'] == .8
        with pytest.raises(ValueError, match='framework-only'):
            await backend.invoke_tool('shell', 'run_command', {'command': 'true', 'context_variables': {}})
        if submit:
            await backend.invoke_tool('evolution', 'submit', {'summary': 'improved'})
            with pytest.raises(ValueError, match='already been submitted'):
                await backend.invoke_tool('evolution', 'submit', {'summary': 'again'})
        result = await backend.finish('' if submit else 'deadline')
        assert result['submitted'] and result['child_files'] == {'main.py': 'x=8'}
        assert result['metrics']['score'] == .8
        assert result == await backend.finish()
        with pytest.raises(RuntimeError, match='no longer'):
            await backend.invoke_tool('shell', 'run_command', {'command': 'touch late'})
        assert not (ctx.workspace / 'late').exists()
    finally:
        await ctx.begin_shutdown()
        await ctx._cleanup()
    await exited([pid])
    with pytest.raises(ValueError, match='fresh'):
        await compose(context(tmp_path))
    assert (ctx.workspace / 'main.py').read_text() == 'x=8'


@pytest.mark.asyncio
async def test_stop_joins_actual_shell_and_children(tmp_path):
    ctx = context(tmp_path)
    backend = await compose(ctx)
    call = asyncio.create_task(backend.invoke_tool('shell', 'run_command', {
        'command': 'echo $$ > shell.pid; sleep 60 & echo $! > child.pid; wait'}))
    async with asyncio.timeout(10):
        while not (ctx.workspace / 'child.pid').exists():
            await asyncio.sleep(.01)
    pids = [int((ctx.workspace / name).read_text()) for name in ('shell.pid', 'child.pid')]
    await backend.close()
    assert call.cancelled()
    await exited(pids)


@pytest.mark.asyncio
@pytest.mark.parametrize('files', [
    {'../escape': 'bad'}, {'/tmp/escape': 'bad'}, {'a/../escape': 'bad'},
    {'a': 'one', 'a/b': 'two'}, {'a\\b': 'bad'}, {'a': 42}, {},
])
async def test_invalid_source_rejected_before_materialization(tmp_path, files):
    ctx = context(tmp_path)
    async def factory(_):
        raise AssertionError('Tools must not be constructed')
    with pytest.raises(ValueError):
        await register_sandbox_mutation(ctx, tool_factory=factory, parent_files=files,
            evaluator_code=EVALUATOR, objective='test')
    assert not ctx.workspace.exists()


@pytest.mark.asyncio
async def test_failed_provider_setup_closes_all_returned_tools_once(tmp_path):
    from pantheon.toolset import ToolSet, tool
    closed = []
    class Service(ToolSet):
        @tool
        async def ping(self):
            return True
        async def run_setup(self):
            if self.toolset_name == 'second':
                raise RuntimeError('setup failed')
        async def cleanup(self):
            closed.append(self.toolset_name)
    async def factory(_):
        return {name: Service(name) for name in ('first', 'second', 'third')}
    ctx = context(tmp_path)
    with pytest.raises(RuntimeError, match='setup failed'):
        await register_sandbox_mutation(ctx, tool_factory=factory, parent_files={'main.py': 'x=1'},
            evaluator_code=EVALUATOR, objective='test')
    assert sorted(closed) == ['first', 'second', 'third']
    assert not ctx._methods


@pytest.mark.asyncio
async def test_evaluator_cleanup_failure_cannot_be_turned_into_metrics(tmp_path, monkeypatch):
    evaluator = HybridEvaluator(EVALUATOR, function_weight=1, llm_weight=0, workspace_base=str(tmp_path))
    async def fail(_):
        raise EvolutionCleanupError([RuntimeError('process still alive')])
    monkeypatch.setattr(evaluator, '_run_function_evaluation', fail)
    with pytest.raises(EvolutionCleanupError):
        await evaluator.evaluate(Program(id='test', snapshot=CodebaseSnapshot(files={'main.py': 'x=1'})))


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['evaluate_initial', 'finish'])
async def test_stop_reaps_initial_and_finish_evaluator_processes(tmp_path, operation):
    ctx = context(tmp_path)
    marker = tmp_path / 'evaluator.pid'
    code = f"def evaluate(path):\n import os,time\n from pathlib import Path\n Path({str(marker)!r}).write_text(str(os.getpid()))\n time.sleep(60)\n return {{'score':1}}"
    async def tools(_):
        return {}
    backend = await register_sandbox_mutation(ctx, tool_factory=tools, parent_files={'main.py': 'x=1'},
        evaluator_code=code, objective='test')
    call = asyncio.create_task(getattr(backend, operation)())
    try:
        async with asyncio.timeout(10):
            while not marker.exists():
                await asyncio.sleep(.01)
        pid = int(marker.read_text())
        await backend.close()
        assert call.cancelled()
        await exited([pid])
    finally:
        await backend.close()


@asynccontextmanager
async def stdio_tools(tmp_path):
    """Local ordinary tool process, deliberately forbidding model/Agent imports."""
    from types import SimpleNamespace
    from pantheon.apps.modal_app_transport import ModalAppTransport
    package = tmp_path / 'package'
    package.mkdir(parents=True)
    state = tmp_path / 'state'
    state.mkdir()
    (package / 'app.json').write_text(json.dumps({'id': 'isolated-tools', 'entry': {'backend': 'backend.py'}}))
    (package / 'backend.py').write_text(f'''
import sys
import importlib.abc
sys.path.insert(0, {str(ROOT)!r})
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pantheon.agent' or fullname.startswith(('pantheon.chatroom', 'openai', 'anthropic', 'litellm')):
            raise AssertionError('Forbidden sandbox dependency: ' + fullname)
sys.meta_path.insert(0, Deny())
from pantheon.evolution.sandbox.tool_backend import register_sandbox_mutation
from pantheon.evolution.local_shell import LocalShellToolSet
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.python import PythonInterpreterToolSet
from pantheon.settings import Settings
async def register(ctx):
    async def tools(work):
        settings = Settings(ctx.state_dir / 'settings', isolated_env=True, environment={{}}, user_home=ctx.state_dir / 'home')
        return {{'shell': LocalShellToolSet('shell', str(work)),
                 'files': FileManagerToolSet('files', str(work), file_settings=settings, template_fallback=False),
                 'python': PythonInterpreterToolSet('python', str(work), strict_lifecycle=True)}}
    await register_sandbox_mutation(ctx, tool_factory=tools, parent_files={{'main.py':'x=1'}},
        evaluator_code={EVALUATOR!r}, objective='Improve score')
''')
    proc = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'apps/desktop/app_runtime.py'),
        '--app-dir', str(package), '--app-id', 'isolated-tools', '--workspace', str(tmp_path / 'work'),
        '--state-dir', str(state), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def chunks(reader):
        while value := await reader.read(8192):
            yield value
    pipe = ModalAppTransport(SimpleNamespace(object_id=f'local-tools-{proc.pid}',
        stdin=SimpleNamespace(write=proc.stdin.write, drain=SimpleNamespace(aio=proc.stdin.drain)),
        stdout=chunks(proc.stdout), stderr=chunks(proc.stderr)))
    try:
        await pipe.ready()
        class Backend:
            identity = pipe.backend_id
            calls = []
            async def invoke(self, name, args):
                self.calls.append(name)
                return await pipe.invoke(name, args)
            async def terminate(self):
                if proc.returncode is None:
                    await pipe.shutdown()
                    assert await asyncio.wait_for(proc.wait(), 10) == 0, pipe.stderr_tail.decode()
                await pipe.disconnect()
                return {'backend_id': self.identity, 'stopped': True}
        yield Backend()
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        await pipe.disconnect()


@pytest.mark.asyncio
async def test_stdio_app_runs_without_agent_or_model_sdks(tmp_path):
    async with stdio_tools(tmp_path) as backend:
        rpc = backend.invoke
        assert (await rpc('evaluate_initial', {}))['metrics']['score'] == .1
        assert '42' in str(await rpc('invoke_tool', {'provider':'python', 'name':'run_python_code', 'args':{'code':'print(6*7)'}}))
        await rpc('invoke_tool', {'provider':'shell', 'name':'run_command', 'args':{'command':"printf 'x=9' > main.py"}})
        result = await rpc('finish', {})
        assert result['submitted'] and result['metrics']['score'] == .9
        assert (await backend.terminate())['stopped']
