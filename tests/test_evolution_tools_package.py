"""Independent mutation release, exercised with no repository import path."""
import asyncio
import json
import inspect
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.modal_app_transport import ModalAppTransport, AppMethodError
from pantheon.evolution.sandbox.package import build_package


EVALUATOR = "def evaluate(path):\n from pathlib import Path\n return {'score':int(Path(path,'main.py').read_text().split('=')[1])/10,'fitness_weights':{'score':1}}"


def test_release_is_ordinary_deterministic_and_contains_no_agent(tmp_path):
    a = build_package(tmp_path / 'a')
    b = build_package(tmp_path / 'b')
    first, second = build_artifact(a), build_artifact(b)
    assert first == second
    manifest = json.loads((a / 'app.json').read_text())
    assert manifest['id'] == 'evolution-tools' and manifest['surface'] == 'headless'
    vendor = a / 'backend/_vendor/pantheon'
    assert not (vendor / 'agent.py').exists() and not (vendor / 'chatroom').exists()
    assert not (vendor / 'evolution/remote_execution.py').exists()
    assert not (vendor / 'apps/agent_execution_runner.py').exists()
    assert '--hash=sha256:' in (a / 'requirements.txt').read_text()
    assert not any((a / name).exists() for name in ('.env', '.git', 'state', 'workspace'))
    with pytest.raises(FileExistsError):
        build_package(a)


@pytest.mark.asyncio
async def test_declared_api_matches_registered_signatures(tmp_path):
    from pantheon.apps.builtin.desktop.app_runtime import AppContext
    from pantheon.evolution.sandbox.app import register
    package = build_package(tmp_path / 'app')
    declaration = json.loads((package / 'app.json').read_text())['provides']
    state = tmp_path / 'state'
    state.mkdir()
    ctx = AppContext('evolution-tools', tmp_path / 'work', state, None)
    await register(ctx)
    try:
        assert set(declaration['interfaces'][0]['tools']) == set(ctx._methods)
        for entry in declaration['tools']:
            actual = inspect.signature(ctx._methods[entry['name']]).parameters
            assert set(actual) == {p['name'] for p in entry['params']}
            for parameter in entry['params']:
                value = actual[parameter['name']]
                assert parameter['required'] == (value.default is inspect.Parameter.empty)
                if not parameter['required']:
                    assert parameter['default'] == value.default
    finally:
        await ctx._cleanup()


BOOT = '''
import importlib.abc, runpy, sys
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name == 'pantheon.agent' or name.startswith(('pantheon.chatroom','openai','anthropic','litellm')):
   raise AssertionError('Forbidden tool dependency: '+name)
sys.meta_path.insert(0,Deny())
path=sys.argv.pop(1)
runpy.run_path(path,run_name='__main__')
'''


@pytest.mark.asyncio
async def test_packaged_source_input_files_python_evaluation_and_shutdown(tmp_path):
    package = build_package(tmp_path / 'app')
    state, work = tmp_path / 'state', tmp_path / 'work'
    state.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'LANG', 'TMPDIR', 'SYSTEMROOT', 'WINDIR')}
    proc = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', BOOT,
        str(package / '.fleet-runtime/app_runtime.py'), '--app-dir', str(package),
        '--app-id', 'evolution-tools', '--workspace', str(work), '--state-dir', str(state),
        cwd=tmp_path, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def chunks(reader):
        while value := await reader.read(8192):
            yield value
    pipe = ModalAppTransport(SimpleNamespace(object_id='package-process',
        stdin=SimpleNamespace(write=proc.stdin.write, drain=SimpleNamespace(aio=proc.stdin.drain)),
        stdout=chunks(proc.stdout), stderr=chunks(proc.stderr)))
    try:
        await pipe.ready(timeout=15)
        with pytest.raises(AppMethodError, match='not initialized'):
            await pipe.invoke('describe', {})
        config = {'parent_files': {'main.py': 'x=1'}, 'evaluator_code': EVALUATOR,
                  'objective': 'Improve score', 'timeout': 60}
        assert await pipe.invoke('initialize', config) == {'initialized': True}
        description = await pipe.invoke('describe', {})
        assert set(description['tools']) == {'files', 'python', 'shell', 'evolution'}
        assert (await pipe.invoke('evaluate_initial', {}))['metrics']['score'] == .1
        async def tool(provider, name, **args):
            return await pipe.invoke('invoke_tool', {'provider': provider, 'name': name, 'args': args})
        read = await tool('files', 'read_file', file_path='main.py')
        assert 'x=1' in str(read) and read.get('success') is not False, read
        assert '42' in str(await tool('python', 'run_python_code', code='print(6*7)'))
        await tool('shell', 'run_command', command="printf 'x=8' > main.py")
        with pytest.raises(AppMethodError, match='single-use'):
            await pipe.invoke('initialize', config)
        assert (work / 'main.py').read_text() == 'x=8'
        result = await pipe.invoke('finish', {})
        assert result['submitted'] and result['metrics']['score'] == .8
        await pipe.shutdown()
        assert await asyncio.wait_for(proc.wait(), 10) == 0, pipe.stderr_tail.decode()
    except Exception as exc:
        raise AssertionError(pipe.stderr_tail.decode()[-10000:]) from exc
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        await pipe.disconnect()
