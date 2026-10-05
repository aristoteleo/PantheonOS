"""Modal's chunked stdio contract, also exercised against the real App host."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pantheon.apps.modal_app_transport import ModalAppTransport, AppMethodError, AppTransportLost


class Stream:
    def __init__(self):
        self.queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        if isinstance(value, Exception):
            raise value
        return value

    def send(self, value):
        self.queue.put_nowait(json.dumps(value) + '\n')


class Sandbox:
    def __init__(self):
        self.object_id = 'sb-fixture'
        self.stdout, self.stderr = Stream(), Stream()
        self.frames, self.buffer = [], bytearray()
        self.drains = 0
        self.fail = False
        self.release = asyncio.Event()
        self.release.set()
        async def drain():
            self.drains += 1
            await self.release.wait()
            if self.fail:
                raise OSError('sensitive upstream error')
            if self.buffer.endswith(b'\n'):
                self.frames.append(json.loads(self.buffer))
                self.buffer.clear()
        self.stdin = SimpleNamespace(write=self.buffer.extend, drain=SimpleNamespace(aio=drain))

    def ready(self):
        self.stdout.send({'ready': True, 'api': 1, 'methods': ['effect']})


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.005)


@pytest.mark.asyncio
async def test_chunked_interleaved_responses_and_callbacks(tmp_path):
    backend = Sandbox()
    callbacks = []
    async def callback(method, args):
        callbacks.append((method, args))
        return {'url': 'scoped-artifact'}
    pipe = ModalAppTransport(backend, callback=callback)
    try:
        backend.stdout.queue.put_nowait('{"ready":true,"api":1,')
        backend.stdout.queue.put_nowait('"methods":["effect"]}\n')
        await pipe.ready()
        calls = [asyncio.create_task(pipe.invoke('effect', {'text': 'x' * 150000})),
                 asyncio.create_task(pipe.invoke('effect', {}))]
        await until(lambda: len(backend.frames) == 2)
        assert backend.drains == 4
        backend.stdout.send({'id': 'q1', 'method': 'ctx.serve', 'params': {'path': 'artifact'}})
        backend.stdout.send({'id': 'n2', 'method': 'ctx.log', 'params': {'message': 'notice'}})
        await until(lambda: len(backend.frames) == 3)
        assert backend.frames[-1]['result'] == {'url': 'scoped-artifact'}
        for frame in reversed(backend.frames[:2]):
            backend.stdout.send({'id': frame['id'], 'result': frame['params']['args']})
        result = await asyncio.gather(*calls)
        assert result[0]['text'] == 'x' * 150000 and result[1] == {}
        assert callbacks == [('ctx.serve', {'path': 'artifact'})]
        assert b'notice' in pipe.stderr_tail
        backend.stderr.queue.put_nowait('z' * 200000)
        await until(lambda: pipe.stderr_tail == b'z' * pipe.LOG_TAIL)
    finally:
        await pipe.disconnect()


@pytest.mark.asyncio
async def test_cancelled_observer_does_not_cancel_or_replay_effect():
    backend = Sandbox()
    pipe = ModalAppTransport(backend)
    backend.ready()
    await pipe.ready()
    call = asyncio.create_task(pipe.invoke('effect', {}))
    await until(lambda: backend.frames)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert len(pipe._calls) == 1
    backend.stdout.send({'id': backend.frames[0]['id'], 'result': 'done'})
    await until(lambda: not pipe._calls)
    await pipe.disconnect()
    assert len(backend.frames) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['write', 'eof', 'malformed', 'oversize'])
async def test_unknown_effect_fails_closed_without_reconnection(fault):
    backend = Sandbox()
    pipe = ModalAppTransport(backend)
    backend.ready()
    await pipe.ready()
    if fault == 'write':
        backend.fail = True
    call = asyncio.create_task(pipe.invoke('effect', {}))
    if fault != 'write':
        await until(lambda: backend.frames)
        if fault == 'eof':
            backend.stdout.queue.put_nowait(None)
        elif fault == 'malformed':
            backend.stdout.queue.put_nowait('not-json\n')
        else:
            pipe.MAX_FRAME = 1024
            backend.stdout.queue.put_nowait('x' * 1024)
    with pytest.raises(AppTransportLost):
        await call
    with pytest.raises(AppTransportLost):
        await pipe.invoke('effect', {})
    await pipe.disconnect()
    assert backend.drains == 1


@pytest.mark.asyncio
async def test_missing_callback_binding_is_denied_and_method_error_is_correlated():
    backend = Sandbox()
    pipe = ModalAppTransport(backend)
    backend.ready()
    await pipe.ready()
    backend.stdout.send({'id': 'q1', 'method': 'ctx.serve', 'params': {'path': '/private'}})
    await until(lambda: backend.frames)
    assert 'error' in backend.frames[0]
    call = asyncio.create_task(pipe.invoke('effect', {}))
    await until(lambda: len(backend.frames) == 2)
    backend.stdout.send({'id': backend.frames[1]['id'], 'error': {'message': 'failed'}})
    with pytest.raises(AppMethodError, match='failed'):
        await call
    assert pipe._failure is None
    await pipe.disconnect()


@pytest.mark.asyncio
async def test_disconnect_joins_delayed_write_and_callback_after_repeated_cancel():
    backend = Sandbox()
    release, entered = asyncio.Event(), asyncio.Event()
    async def callback(*_):
        entered.set()
        await release.wait()
        return {}
    pipe = ModalAppTransport(backend, callback=callback)
    backend.ready()
    await pipe.ready()
    backend.stdout.send({'id': 'q1', 'method': 'ctx.serve', 'params': {}})
    await entered.wait()
    backend.release.clear()
    call = asyncio.create_task(pipe.invoke('effect', {}))
    await until(lambda: backend.drains)
    close = asyncio.create_task(pipe.disconnect())
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    backend.release.set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    with pytest.raises(AppTransportLost):
        await call
    assert not pipe._calls and not pipe._callbacks


@pytest.mark.asyncio
async def test_diagnostic_disconnect_revokes_new_callback_admission():
    backend = Sandbox()
    calls = []
    async def callback(*args):
        calls.append(args)
    pipe = ModalAppTransport(backend, callback=callback)
    backend.ready()
    await pipe.ready()
    backend.stderr.queue.put_nowait(OSError('stream lost'))
    await until(lambda: pipe._failure is not None)
    backend.stdout.send({'id': 'q1', 'method': 'ctx.serve', 'params': {}})
    await until(lambda: pipe._stdout.done())
    await pipe.disconnect()
    assert not calls and not backend.frames


BACKEND = '''
import asyncio
def register(ctx):
    release = asyncio.Event()
    ctx.begin_shutdown = release.set
    @ctx.method
    async def effect(text):
        ctx.log('working')
        (ctx.workspace / 'effect').write_text(text)
        return {'size': len(text)}
    @ctx.method
    async def waiting():
        await release.wait()
        return await ctx.serve('artifact')
    @ctx.on_cleanup
    async def cleanup():
        (ctx.state_dir / 'cleaned').write_text('yes')
'''


@asynccontextmanager
async def local_sandbox(tmp_path):
    package, state, work = (tmp_path / name for name in ('app', 'state', 'work'))
    for path in (package, state, work):
        path.mkdir()
    (package / 'app.json').write_text('{}')
    (package / 'backend').mkdir()
    (package / 'backend/__init__.py').write_text(BACKEND)
    runtime = Path(__file__).resolve().parents[1] / 'apps/desktop/app_runtime.py'
    proc = await asyncio.create_subprocess_exec(sys.executable, str(runtime), '--app-dir', str(package),
        '--app-id', 'fixture', '--workspace', str(work), '--state-dir', str(state),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def chunks(reader):
        while value := await reader.read(8192):
            yield value
    sandbox = SimpleNamespace(object_id='local-process',
        stdin=SimpleNamespace(write=proc.stdin.write, drain=SimpleNamespace(aio=proc.stdin.drain)),
        stdout=chunks(proc.stdout), stderr=chunks(proc.stderr))
    try:
        yield sandbox, proc
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


@pytest.mark.asyncio
async def test_actual_host_large_source_and_callback_during_shutdown(tmp_path):
    async with local_sandbox(tmp_path) as (sandbox, proc):
        async def callback(method, args):
            assert method == 'ctx.serve' and args == {'path': 'artifact'}
            return {'url': 'scoped-artifact'}
        pipe = ModalAppTransport(sandbox, callback=callback)
        try:
            await pipe.ready()
            assert await pipe.invoke('effect', {'text': 'code' * 60000}) == {'size': 240000}
            assert (tmp_path / 'work/effect').read_text() == 'code' * 60000
            call = asyncio.create_task(pipe.invoke('waiting', {}))
            await until(lambda: pipe._pending)
            await pipe.shutdown()
            assert await call == 'scoped-artifact'
            assert await asyncio.wait_for(proc.wait(), 5) == 0
            assert (tmp_path / 'state/cleaned').read_text() == 'yes'
        finally:
            await pipe.disconnect()
