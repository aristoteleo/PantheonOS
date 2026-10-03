"""Real CLI processes and RPCs: stop is a lifecycle operation, not SIGKILL."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time
import uuid

import pytest

from pantheon.apps.host_lifecycle import AppShutdownError, serve_toolset
from pantheon.remote.backend.tcp import TCPBackend
from pantheon.toolset import ToolSet
from pantheon.utils.misc import generate_service_id

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = '''
import asyncio, socket, time
from pathlib import Path
from pantheon.toolset import ToolSet, tool
class LifecycleApp(ToolSet):
    def __init__(self, name, workdir, mode='normal', **kwargs):
        super().__init__(name, **kwargs)
        self.root, self.mode = Path(workdir), mode
        self.resource = None
    def note(self, name):
        with (self.root/'events').open('a') as f:
            f.write(name+'\\n')
    async def run_setup(self):
        self.resource = socket.socket()
        self.resource.bind(('127.0.0.1', 0))
        (self.root/'port').write_text(str(self.resource.getsockname()[1]))
        self.note('setup')
        if self.mode == 'fail':
            raise RuntimeError('fixture setup failed')
        if self.mode == 'setup-wait':
            await asyncio.Event().wait()
    @tool
    def write_once(self):
        self.note('accepted')
        deadline = time.monotonic() + 15
        while not (self.root/'release-call').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('test did not release mutation')
            time.sleep(.01)
        self.note('mutation')
        return {'written': True}
    @tool
    async def probe(self):
        self.note('probe')
        return 'ready'
    async def begin_shutdown(self):
        self.note('stopping')
    async def cleanup(self):
        self.note('cleanup')
        if self.mode == 'cleanup-wait':
            while not (self.root/'release-cleanup').exists():
                await asyncio.sleep(.01)
        if self.resource:
            self.resource.close()
        self.note('cleaned')
        if self.mode == 'cleanup-fail':
            raise RuntimeError('fixture cleanup failed')
'''
BOOT = '''
import importlib.abc, runpy, sys
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if any(name == p or name.startswith(p+'.') for p in
               ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team', 'pantheon.factory')):
            raise AssertionError('App host imported Agent: '+name)
sys.meta_path.insert(0, NoAgent())
runpy.run_module('pantheon.apphost', run_name='__main__')
'''


def events(path):
    log = path / 'events'
    return log.read_text().splitlines() if log.exists() else []


async def until(check, process=None):
    deadline = asyncio.get_running_loop().time() + 15
    while not check():
        if process is not None:
            assert process.poll() is None, 'App exited before expected state'
        assert asyncio.get_running_loop().time() < deadline, 'Expected state did not arrive'
        await asyncio.sleep(.02)


@contextmanager
def app_process(path, mode='normal', no_remote=False, extra_env=None):
    catalog = path / 'catalog' / 'lifecycle'
    catalog.mkdir(parents=True)
    (catalog / 'app.json').write_text(json.dumps({
        'id': 'lifecycle', 'name': 'Lifecycle', 'version': '1.0.0',
        'runtime': 'process', 'entry': {'backend': 'host_fixture:LifecycleApp'},
        'placement': {'requires': ['fs:workspace']},
    }))
    (path / 'host_fixture.py').write_text(FIXTURE)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('PANTHEON_', 'NATS_', 'FLEET_'))}
    env.update(PYTHONPATH=os.pathsep.join((str(path), str(ROOT))), HOME=str(path),
               PANTHEON_APPS_ROOT=str(catalog.parent), PANTHEON_REMOTE_BACKEND='tcp',
               PANTHEON_TCP_REGISTRY=str(path / 'registry'))
    env.update(extra_env or {})
    with (path / 'host.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-c', BOOT,
            '--app-id', 'lifecycle', '--workdir', str(path), '--id-hash', 'fixture',
            '--set', 'allow_in_place_restart=true',
            '--set', 'mode=' + mode, *(['--no-remote'] if no_remote else [])],
            env=env, cwd=path, stdout=log, stderr=log)
        try:
            yield process
        finally:
            (path / 'release-call').touch()
            (path / 'release-cleanup').touch()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def assert_clean(path):
    observed = events(path)
    assert observed.count('cleanup') == observed.count('cleaned') == 1, observed
    assert not list((path / 'registry').glob('*.json'))
    # The real resource opened in setup has been released, even on partial setup.
    with socket.socket() as resource:
        resource.bind(('127.0.0.1', int((path / 'port').read_text())))


@pytest.mark.asyncio
@pytest.mark.parametrize('no_remote', [False, True])
async def test_partial_setup_failure_unwinds_once(tmp_path, no_remote):
    with app_process(tmp_path, 'fail', no_remote) as process:
        code = await asyncio.to_thread(process.wait, 15)
        assert code != 0
        assert 'fixture setup failed' in (tmp_path / 'host.log').read_text()
        assert events(tmp_path) == ['setup', 'stopping', 'cleanup', 'cleaned']
        assert_clean(tmp_path)


@pytest.mark.asyncio
async def test_sigterm_during_setup(tmp_path):
    with app_process(tmp_path, 'setup-wait') as process:
        await until(lambda: 'setup' in events(tmp_path), process)
        process.terminate()
        assert await asyncio.to_thread(process.wait, 10) == 0, (tmp_path / 'host.log').read_text()
        assert_clean(tmp_path)


@pytest.mark.asyncio
async def test_sigterm_drains_real_tcp_mutation_and_rejects_new_calls(tmp_path):
    with app_process(tmp_path) as process:
        record = tmp_path / 'registry' / (generate_service_id('fixture') + '.json')
        await until(record.exists, process)
        service = await TCPBackend(str(record.parent)).connect(generate_service_id('fixture'))
        pending = None
        try:
            assert await service.invoke('probe') == 'ready'
            pending = asyncio.create_task(service.invoke('write_once'))
            await until(lambda: 'accepted' in events(tmp_path), process)
            process.terminate()
            await until(lambda: 'stopping' in events(tmp_path), process)
            assert not record.exists()
            with pytest.raises(Exception, match='stopping'):
                await service.invoke('probe')
            assert events(tmp_path).count('probe') == 1
            assert 'cleanup' not in events(tmp_path) and process.poll() is None
            (tmp_path / 'release-call').touch()
            assert await asyncio.wait_for(pending, 5) == {'written': True}
            assert await asyncio.to_thread(process.wait, 10) == 0, (tmp_path / 'host.log').read_text()
            assert events(tmp_path).index('mutation') < events(tmp_path).index('cleanup')
            assert events(tmp_path).count('mutation') == 1
            assert_clean(tmp_path)
        finally:
            (tmp_path / 'release-call').touch()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await service.close()


@pytest.mark.asyncio
async def test_repeated_signals_do_not_interrupt_cleanup(tmp_path):
    with app_process(tmp_path, 'cleanup-wait') as process:
        await until(lambda: bool(list((tmp_path / 'registry').glob('*.json'))), process)
        process.terminate()
        await until(lambda: 'cleanup' in events(tmp_path), process)
        process.send_signal(signal.SIGINT)
        process.terminate()
        await asyncio.sleep(.1)
        assert process.poll() is None
        (tmp_path / 'release-cleanup').touch()
        assert await asyncio.to_thread(process.wait, 10) == 0, (tmp_path / 'host.log').read_text()
        assert_clean(tmp_path)


@pytest.mark.asyncio
async def test_cleanup_failure_is_not_clean_exit(tmp_path):
    with app_process(tmp_path, 'cleanup-fail', no_remote=True) as process:
        assert await asyncio.to_thread(process.wait, 15) != 0
        assert 'fixture cleanup failed' in (tmp_path / 'host.log').read_text()
        assert_clean(tmp_path)


@pytest.mark.asyncio
async def test_embedded_host_cancellation_waits_for_cleanup():
    entered, release = asyncio.Event(), asyncio.Event()
    class Service(ToolSet):
        count = 0
        async def run_setup(self):
            await asyncio.Event().wait()
        async def cleanup(self):
            self.count += 1
            entered.set()
            await release.wait()
    service = Service('cancel')
    host = asyncio.create_task(serve_toolset(service, remote=False))
    await asyncio.sleep(0)
    host.cancel()
    await asyncio.wait_for(entered.wait(), 1)
    host.cancel()
    await asyncio.sleep(0)
    assert not host.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(host, 1)
    assert service.count == 1


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_initial_failure():
    class Service(ToolSet):
        async def run_setup(self):
            raise ValueError('setup failed')
        async def cleanup(self):
            raise OSError('cleanup failed')
    with pytest.raises(AppShutdownError) as error:
        await serve_toolset(Service('errors'), remote=False)
    assert isinstance(error.value.errors[0], ValueError)
    assert isinstance(error.value.errors[1], AppShutdownError)
    assert isinstance(error.value.errors[1].errors[0], OSError)


@pytest.mark.asyncio
async def test_authenticated_nats_sigterm_flushes_accepted_reply(tmp_path, monkeypatch):
    from nats.errors import NoRespondersError
    from pantheon.remote.backend.nats import NATSBackend
    binary = Path(sys.executable).parent / 'nats-server'
    binary = str(binary) if binary.is_file() else shutil.which('nats-server')
    if not binary:
        pytest.skip('local nats-server required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = uuid.uuid4().hex
    url = f'nats://127.0.0.1:{port}'
    monkeypatch.setenv('NATS_ENABLE_JETSTREAM', 'false')
    backend = NATSBackend([url], user='agent', password=token, max_reconnect_attempts=0,
                          connect_timeout=.3)
    broker = subprocess.Popen([binary, '-a', '127.0.0.1', '-p', str(port),
        '--user', 'agent', '--pass', token], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with app_process(tmp_path, extra_env={
            'PANTHEON_REMOTE_BACKEND': 'nats', 'NATS_SERVERS': url,
            'NATS_TOKEN': token, 'NATS_ENABLE_JETSTREAM': 'false',
        }) as process:
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                assert process.poll() is None, (tmp_path / 'host.log').read_text()
                try:
                    service = await asyncio.wait_for(backend.connect(generate_service_id('fixture')), 1)
                    assert await asyncio.wait_for(service.invoke('probe'), 1) == 'ready'
                    break
                except (TimeoutError, ConnectionError, OSError, NoRespondersError):
                    assert asyncio.get_running_loop().time() < deadline, (tmp_path / 'host.log').read_text()
                    await asyncio.sleep(.05)
            # The host prohibits the old Agent-specific process-killing re-exec.
            with pytest.raises(Exception, match='not found'):
                await service.invoke('_restart_in_place')
            pending = asyncio.create_task(service.invoke('write_once'))
            try:
                await until(lambda: 'accepted' in events(tmp_path), process)
                process.terminate()
                await until(lambda: 'stopping' in events(tmp_path), process)
                with pytest.raises(Exception):
                    await asyncio.wait_for(service.invoke('probe'), 1)
                assert events(tmp_path).count('probe') == 1
                assert 'cleanup' not in events(tmp_path)
                (tmp_path / 'release-call').touch()
                assert await asyncio.wait_for(pending, 5) == {'written': True}
                assert await asyncio.to_thread(process.wait, 10) == 0, (tmp_path / 'host.log').read_text()
                assert events(tmp_path).index('mutation') < events(tmp_path).index('cleanup')
                assert_clean(tmp_path)
            finally:
                (tmp_path / 'release-call').touch()
                await asyncio.gather(pending, return_exceptions=True)
    finally:
        await backend.close()
        broker.terminate()
        broker.wait(timeout=5)


@pytest.mark.asyncio
async def test_dual_channel_failure_stops_sibling_before_cleanup(monkeypatch):
    from pantheon.remote import RemoteBackendFactory
    other_started = asyncio.Event()
    observed = []
    class Worker:
        servers = []
        service_name = service_id = 'fixture'
        def __init__(self, name):
            self.name = name
        def register(self, *args, **kwargs):
            pass
        async def run(self):
            if self.name == 'primary':
                await other_started.wait()
                raise RuntimeError('primary failed')
            try:
                other_started.set()
                await asyncio.Event().wait()
            finally:
                observed.append('other-ended')
        async def stop(self):
            observed.append(self.name + '-stop')
        async def drain(self):
            observed.append(self.name + '-drain')
    class Backend:
        def __init__(self, name):
            self.name = name
        def create_worker(self, *args, **kwargs):
            return Worker(self.name)
        async def close(self):
            observed.append(self.name + '-closed')
    backends = iter((Backend('primary'), Backend('other')))
    monkeypatch.setattr(RemoteBackendFactory, 'create_backend', lambda *args: next(backends))
    monkeypatch.setenv('PANTHEON_REMOTE_BACKEND', 'tcp')
    monkeypatch.setenv('PANTHEON_FRONTEND_BACKEND', 'nats')
    class Service(ToolSet):
        _enable_frontend_channel = True
        async def cleanup(self):
            observed.append('cleanup')
    with pytest.raises(RuntimeError, match='primary failed'):
        await asyncio.wait_for(serve_toolset(Service('dual')), 3)
    assert observed.count('cleanup') == 1
    assert observed.index('other-ended') < observed.index('cleanup')
    assert observed[-2:] == ['primary-closed', 'other-closed']


@pytest.mark.asyncio
async def test_transport_failure_still_disposes_app_and_other_resources(monkeypatch):
    from pantheon.remote import RemoteBackendFactory
    observed = []
    class Backend:
        def create_worker(self, *args, **kwargs):
            raise RuntimeError('cannot create worker')
        async def close(self):
            observed.append('closed')
    monkeypatch.setattr(RemoteBackendFactory, 'create_backend', lambda: Backend())
    class Service(ToolSet):
        async def run_setup(self):
            raise AssertionError('no setup after worker construction failure')
        async def cleanup(self):
            observed.append('cleanup')
    with pytest.raises(RuntimeError, match='cannot create worker'):
        await serve_toolset(Service('failure'))
    assert observed == ['cleanup', 'closed']
