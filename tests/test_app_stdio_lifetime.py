"""Ordinary App stdio shutdown through real child processes and callbacks."""
import asyncio
import json
from pathlib import Path
import sys

import pytest
import pytest_asyncio


RUNTIME = Path(__file__).resolve().parents[1] / 'apps/desktop/app_runtime.py'
BACKEND = '''
import asyncio
import time

def register(ctx):
    release = asyncio.Event()

    async def begin():
        ctx.state.set('stopping', True)
        release.set()
    ctx.begin_shutdown = begin

    @ctx.on_cleanup
    async def cleanup():
        ctx.state.set('cleaned', True)
        if (ctx.workspace / 'cleanup-error').exists():
            raise RuntimeError('cleanup refused')

    @ctx.method
    async def mutation():
        ctx.log('started')
        await release.wait()
        assert not ctx.state.get('cleaned')
        # Callback must still be answered after shutdown was requested.
        value = await ctx.serve('artifact.txt')
        ctx.state.set('result', value)
        return value

    @ctx.method
    def blocking():
        ctx.log('blocking')
        deadline = time.monotonic() + 5
        while not (ctx.workspace / 'release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('test release did not arrive')
            time.sleep(.01)
        assert not ctx.state.get('cleaned')
        ctx.state.set('effect', True)
        return 'done'

    @ctx.method
    def forbidden():
        ctx.state.set('late-effect', True)

    if (ctx.workspace / 'setup-error').exists():
        raise RuntimeError('setup refused')
'''


@pytest_asyncio.fixture
async def child(tmp_path):
    package = tmp_path / 'app'
    (package / 'backend').mkdir(parents=True)
    (package / 'app.json').write_text(json.dumps({'id': 'fixture'}))
    (package / 'backend/__init__.py').write_text(BACKEND)
    processes = []

    async def start(backend=None):
        if backend is not None:
            (package / 'backend/__init__.py').write_text(backend)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(RUNTIME), '--app-dir', str(package),
            '--app-id', 'fixture', '--workspace', str(tmp_path),
            '--state-dir', str(tmp_path), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        processes.append(proc)
        return proc

    yield start
    for proc in processes:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def read(proc):
    line = await asyncio.wait_for(proc.stdout.readline(), 8)
    assert line, (await proc.stderr.read()).decode()
    return json.loads(line)


async def send(proc, **message):
    proc.stdin.write((json.dumps(message) + '\n').encode())
    await proc.stdin.drain()


async def invoke(proc, name, mid=1):
    await send(proc, id=mid, method='invoke', params={'method': name})


@pytest.mark.asyncio
@pytest.mark.parametrize('termination', ['shutdown', 'signal'])
async def test_shutdown_joins_mutation_and_keeps_callback_channel(child, tmp_path, termination):
    proc = await child()
    assert (await read(proc))['ready']
    await invoke(proc, 'mutation')
    assert (await read(proc))['params']['message'] == 'started'
    if termination == 'signal':
        proc.terminate()
    else:
        await send(proc, method='shutdown')
    callback = await read(proc)
    assert callback['method'] == 'ctx.serve'
    # Repeated stop must not cancel drain or dispose twice.
    if termination == 'signal':
        proc.terminate()
    else:
        await send(proc, method='shutdown')
    await invoke(proc, 'forbidden', 2)
    assert 'stopping' in (await read(proc))['error']['message']
    assert proc.returncode is None
    await send(proc, id=callback['id'], result={'url': 'https://fixture/artifact'})
    assert (await read(proc))['result'] == 'https://fixture/artifact'
    assert await asyncio.wait_for(proc.wait(), 8) == 0
    state = json.loads((tmp_path / 'state.json').read_text())
    assert state == {'stopping': True, 'result': 'https://fixture/artifact', 'cleaned': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('termination', ['shutdown', 'eof'])
async def test_sync_effect_is_drained_and_does_not_block_reader(child, tmp_path, termination):
    proc = await child()
    assert (await read(proc))['ready']
    await invoke(proc, 'blocking')
    assert (await read(proc))['params']['message'] == 'blocking'
    await send(proc, id=3, method='ping')
    assert (await read(proc))['id'] == 3
    if termination == 'eof':
        proc.stdin.close()
        await proc.stdin.wait_closed()
    else:
        await send(proc, method='shutdown')
        await send(proc, id=4, method='ping')
        assert (await read(proc))['result']['stopping']
    (tmp_path / 'release').touch()
    assert (await read(proc))['result'] == 'done'
    assert await asyncio.wait_for(proc.wait(), 8) == 0
    state = json.loads((tmp_path / 'state.json').read_text())
    assert state['effect'] and state['cleaned'] and state['stopping']


@pytest.mark.asyncio
async def test_eof_fails_pending_callback_without_waiting_its_timeout(child, tmp_path):
    proc = await child()
    assert (await read(proc))['ready']
    await invoke(proc, 'mutation')
    await read(proc)
    await send(proc, method='shutdown')
    assert (await read(proc))['method'] == 'ctx.serve'
    proc.stdin.close()
    await proc.stdin.wait_closed()
    assert 'disconnected' in (await read(proc))['error']['message']
    assert await asyncio.wait_for(proc.wait(), 8) == 0
    assert json.loads((tmp_path / 'state.json').read_text())['cleaned']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['setup-error', 'cleanup-error'])
async def test_failure_does_not_report_clean_exit(child, tmp_path, failure):
    (tmp_path / failure).touch()
    proc = await child()
    hello = await read(proc)
    if failure == 'setup-error':
        assert hello['ready'] is False
    else:
        assert hello['ready']
        await send(proc, method='shutdown')
    assert await asyncio.wait_for(proc.wait(), 8) == 1
    assert json.loads((tmp_path / 'state.json').read_text())['cleaned']
    assert b'refused' in await proc.stderr.read()


@pytest.mark.asyncio
async def test_ordinary_toolset_interrupts_and_reaps_owned_process(child, tmp_path):
    root = str(RUNTIME.parents[2])
    backend = f'''
import sys
sys.path.insert(0, {root!r})
import asyncio
from pantheon.toolset import ToolSet, tool
from pantheon.apps.toolset_backend import register_toolset

async def register(ctx):
    class Service(ToolSet):
        def __init__(self):
            super().__init__('owned-child')
            self.process = None

        @tool
        async def execute(self):
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, '-c', 'import time; time.sleep(60)')
            ctx.log('child:' + str(self.process.pid))
            await self.process.wait()
            assert not ctx.state.get('cleaned')
            return {{'returncode': self.process.returncode}}

        async def begin_shutdown(self):
            if self.process and self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()

        async def cleanup(self):
            assert self.process.returncode is not None
            ctx.state.set('cleaned', True)

    await register_toolset(ctx, Service())
'''
    proc = await child(backend)
    hello = await read(proc)
    assert hello['ready'], hello
    await invoke(proc, 'execute')
    message = await read(proc)
    assert message['params']['message'].startswith('child:')
    pid = int(message['params']['message'].split(':')[1])
    try:
        await send(proc, method='shutdown')
        result = await read(proc)
        assert result['result']['returncode'] != 0
        assert await asyncio.wait_for(proc.wait(), 8) == 0
        assert json.loads((tmp_path / 'state.json').read_text())['cleaned']
        import os
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        import os
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
