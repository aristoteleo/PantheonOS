"""Platform lifetime and snapshot ownership do not depend on Agent lifetime."""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest

from pantheon.platform import bootstrap, state_sync


@pytest.fixture(autouse=True)
def state_env(monkeypatch):
    for key in ('PANTHEON_STATE_URL', 'PANTHEON_STATE_TOKEN',
                'PANTHEON_STATE_SYNC_OWNER', 'PANTHEON_STATE_HOME'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(state_sync, '_restored', False)


def configure(monkeypatch):
    monkeypatch.setenv('PANTHEON_STATE_URL', 'https://hub.test/state')
    monkeypatch.setenv('PANTHEON_STATE_TOKEN', 'test-token')


class Response(io.BytesIO):
    def __init__(self, data=b'', status=200):
        super().__init__(data)
        self.status = status


def test_restore_failure_never_launches_a_writer(tmp_path, monkeypatch):
    configure(monkeypatch)
    calls = []
    def request(method, *args, **kwargs):
        calls.append(method)
        raise OSError('offline')
    monkeypatch.setattr(state_sync, '_request', request)
    async def run(**kwargs):
        pytest.fail('service started before state was restored')
    with pytest.raises(RuntimeError, match='refusing to start writers'):
        asyncio.run(bootstrap.serve(SimpleNamespace(workspace_path=tmp_path, run=run),
            agent_command=[sys.executable, '-c', 'raise SystemExit(99)']))
    assert calls == ['GET']


def test_fresh_snapshot_is_authoritative_and_shutdown_flushes(tmp_path, monkeypatch):
    configure(monkeypatch)
    requests = []
    def request(method, url, token, body=None):
        requests.append((method, body))
        assert token == 'test-token'
        return Response(status=204)
    monkeypatch.setattr(state_sync, '_request', request)
    stopped = False
    async def scenario():
        stop = asyncio.Event()
        async def run(**kwargs):
            nonlocal stopped
            path = tmp_path / '.pantheon/projects.json'
            path.write_text('{"projects": ["last write"]}')
            stop.set()
            try:
                await asyncio.Event().wait()
            finally:
                # The final snapshot must also capture service cleanup writes.
                path.write_text('{"projects": ["after cleanup"]}')
                stopped = True
        await bootstrap.serve(SimpleNamespace(workspace_path=tmp_path, run=run), stop=stop)
    asyncio.run(scenario())
    assert stopped
    assert [method for method, _ in requests] == ['GET', 'PUT']
    with tarfile.open(fileobj=io.BytesIO(requests[-1][1]), mode='r:gz') as tar:
        assert json.load(tar.extractfile('.pantheon/projects.json')) == {'projects': ['after cleanup']}


def test_existing_snapshot_is_restored_before_service(tmp_path, monkeypatch):
    configure(monkeypatch)
    data = b'{"value": "existing"}'
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        item = tarfile.TarInfo('.pantheon/projects.json')
        item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
    monkeypatch.setattr(state_sync, '_request', lambda *a, **k: Response(archive.getvalue()))
    async def scenario():
        async def run(**kwargs):
            assert (tmp_path / '.pantheon/projects.json').read_bytes() == data
        await bootstrap.serve(SimpleNamespace(workspace_path=tmp_path, run=run))
    asyncio.run(scenario())


def test_agent_exit_does_not_stop_platform_and_cannot_own_snapshots(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(state_sync, '_request', lambda *a, **k: Response(status=204))
    result = tmp_path / 'child.json'
    child = '''
import json, os
from pathlib import Path
from pantheon.platform.state_sync import _config
# Simulate a user .env repopulating the variables removed at process launch.
assert 'PANTHEON_STATE_TOKEN' not in os.environ
os.environ['PANTHEON_STATE_URL'] = 'https://hub.test/state'
os.environ['PANTHEON_STATE_TOKEN'] = 'would-double-sync'
assert _config() is None
Path(os.environ['TEST_CHILD_RESULT']).write_text(json.dumps({'pid': os.getpid()}))
raise SystemExit(9)
'''
    monkeypatch.setenv('TEST_CHILD_RESULT', str(result))
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1]))
    async def scenario():
        stop = asyncio.Event()
        running = asyncio.Event()
        async def run(**kwargs):
            running.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(bootstrap.serve(
            SimpleNamespace(workspace_path=tmp_path, run=run), stop=stop,
            agent_command=[sys.executable, '-c', child]))
        await asyncio.wait_for(running.wait(), 5)
        deadline = asyncio.get_running_loop().time() + 10
        while not result.exists():
            assert not task.done()
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.05)
        pid = json.loads(result.read_text())['pid']
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.05)
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(scenario())


def test_partial_state_config_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_STATE_URL', 'https://hub.test/state')
    with pytest.raises(RuntimeError, match='both URL and token'):
        bootstrap.SnapshotSession(tmp_path)


def test_failed_push_remains_pending(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(state_sync, '_request', lambda *a, **k: Response(status=204))
    session = bootstrap.SnapshotSession(tmp_path)
    session.prepare()
    (tmp_path / '.pantheon').mkdir()
    (tmp_path / '.pantheon/projects.json').write_text('{}')
    before = session.digest
    monkeypatch.setattr(state_sync, '_request', lambda *a, **k: Response(status=503))
    with pytest.raises(RuntimeError, match='503'):
        session.flush()
    assert session.digest == before
    monkeypatch.setattr(state_sync, '_request', lambda *a, **k: Response(status=204))
    session.flush()
    assert session.digest != before


def test_second_snapshot_owner_cannot_restore(tmp_path, monkeypatch):
    configure(monkeypatch)
    calls = []
    def request(method, *a, **k):
        calls.append(method)
        return Response(status=204)
    monkeypatch.setattr(state_sync, '_request', request)
    async def scenario():
        started = asyncio.Event()
        stop = asyncio.Event()
        async def run(**kwargs):
            started.set()
            await asyncio.Event().wait()
        service = SimpleNamespace(workspace_path=tmp_path, run=run)
        task = asyncio.create_task(bootstrap.serve(service, stop=stop))
        try:
            await asyncio.wait_for(started.wait(), 5)
            with pytest.raises(TimeoutError):
                await bootstrap.serve(service)
            assert calls == ['GET']
        finally:
            stop.set()
            await task
    asyncio.run(scenario())


def test_worker_drain_waits_for_writes_and_rejects_new_calls():
    from unittest.mock import AsyncMock
    from pantheon.remote.backend.nats import NATSBackend
    async def scenario():
        worker = NATSBackend(['nats://unused']).create_worker('drain-test')
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []
        async def write():
            entered.set()
            await release.wait()
            writes.append('committed')
        worker.register(write)
        message = SimpleNamespace(data=json.dumps({'method': 'write', 'parameters': {}}).encode(),
                                  respond=AsyncMock())
        await worker._handle_request(message)
        await entered.wait()
        draining = asyncio.create_task(worker.drain())
        await asyncio.sleep(0)
        assert not draining.done()
        rejected = SimpleNamespace(data=message.data, respond=AsyncMock())
        await worker._handle_request(rejected)
        assert b'Service is stopping' in rejected.respond.await_args.args[0]
        release.set()
        await asyncio.wait_for(draining, 1)
        assert writes == ['committed']
        assert message.respond.await_count == 1
        assert not worker._request_tasks
    asyncio.run(scenario())


def test_failed_final_snapshot_reports_shutdown_failure(tmp_path, monkeypatch):
    configure(monkeypatch)
    def request(method, *args, **kwargs):
        return Response(status=204 if method == 'GET' else 503)
    monkeypatch.setattr(state_sync, '_request', request)
    async def scenario():
        stop = asyncio.Event()
        async def run(**kwargs):
            (tmp_path / '.pantheon/projects.json').write_text('{}')
            stop.set()
            await asyncio.Event().wait()
        with pytest.raises(RuntimeError, match='503'):
            await bootstrap.serve(SimpleNamespace(workspace_path=tmp_path, run=run), stop=stop)
    asyncio.run(scenario())


def test_oversized_snapshot_is_not_reported_saved(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(state_sync, '_request', lambda *a, **k: Response(status=204))
    session = bootstrap.SnapshotSession(tmp_path)
    session.prepare()
    (tmp_path / '.pantheon').mkdir()
    (tmp_path / '.pantheon/projects.json').write_text('{}')
    monkeypatch.setattr(state_sync, '_pack', lambda home: None)
    before = session.digest
    with pytest.raises(RuntimeError, match='not saved'):
        session.flush()
    assert session.digest == before


def test_worker_cleanup_failure_is_not_reported_clean(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        async def run(**kwargs):
            stop.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise RuntimeError('could not drain')
        with pytest.raises(RuntimeError, match='could not drain'):
            await bootstrap.serve(SimpleNamespace(workspace_path=tmp_path, run=run), stop=stop)
    asyncio.run(scenario())
