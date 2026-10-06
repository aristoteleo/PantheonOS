"""Local product failure cleanup, full-owner restart and explicit stop requests."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import nats
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import profile_manifest, minimal_manifest, offline_session, settle


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['starting', 'reserved', 'prepared'])
async def test_abort_retries_durable_fence_before_observing_or_mutating_nodes(tmp_path, monkeypatch, phase):
    _, _, session = offline_session(tmp_path)
    await session._open()
    session._record['phase'] = phase
    await session._checkpoint(session.path, session._record)
    checkpoint = session._checkpoint
    attempts = []
    async def fail_twice(path, value):
        attempts.append(value['phase'])
        if len(attempts) <= 2: raise OSError('checkpoint unavailable')
        await checkpoint(path, value)
    monkeypatch.setattr(session, '_checkpoint', fail_twice)
    session.wire = AsyncMock()
    session.wire.status.return_value = dict(owner=session.info.fleet_id, node_id=session.info.node_id,
        operations={}, instances={})
    for _ in range(2):
        with pytest.raises(OSError, match='checkpoint unavailable'):
            await session.stop()
        assert session._load()['phase'] == phase
        session.wire.status.assert_not_awaited()
        session.wire.submit.assert_not_awaited()
    assert (await session.stop())['state'] == 'stopped'
    assert session._load()['startup_abort'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['node-operation', 'generation', 'running', 'resources', 'owner'])
async def test_missing_child_journal_never_adopts_changed_node_work(tmp_path, change):
    _, _, session = offline_session(tmp_path)
    await session._open()
    recipe = session._record['recipe']
    app = recipe['apps']['consumer']
    state = dict(owner=session.info.fleet_id, node_id=session.info.node_id, operations={}, instances={})
    if change == 'node-operation':
        state['operations'][session.deploy.operation_id(recipe, 'consumer', 'prepare_start')] = {}
    elif change == 'owner': state['owner'] = 'foreign'
    else:
        state['instances']['unexpected'] = dict(digest=app['revision'], scope=app['scope'],
            generation=1 if change == 'generation' else 0,
            state='running' if change == 'running' else 'stopped',
            resources=['occupied'] if change == 'resources' else [])
    session.wire = AsyncMock()
    session.wire.status.return_value = state
    with pytest.raises(AssemblyError): await session.stop()
    assert session._load()['phase'] == 'aborting'
    session.wire.submit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,marker', [('starting', True), ('ready', True), ('stopping', True),
    ('aborting', False), ('stopped', False), ('stopped', 1)])
async def test_cleanup_marker_cannot_bypass_profile_phase_admission(tmp_path, phase, marker):
    _, _, session = offline_session(tmp_path)
    await session._open()
    session._record.update(phase=phase, startup_abort=marker)
    await session._checkpoint(session.path, session._record)
    with pytest.raises(AssemblyError): session._load()


def gated_probe(spec, package, allow):
    root = Path(spec['packages'][package]['path'])
    path = root/'fleet.json'
    definition = json.loads(path.read_text())
    probe = definition['components'][0]['readiness']
    probe['argv'] = [probe['argv'][0], '-c',
        'import subprocess,sys;from pathlib import Path;'
        'subprocess.run(sys.argv[1:],check=True);assert Path(' + repr(str(allow)) + ').exists()',
        *probe['argv']]
    probe['timeout_seconds'] = 2
    path.write_text(json.dumps(definition))
    spec['packages'][package]['revision'] = build_artifact(root)[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['plain', 'consumer', 'provider', 'registration-reply', 'before-stage', 'prepared-plain', 'prepared-model'])
async def test_failed_profile_can_stop_and_reopen_under_new_authority(tmp_path, binaries, model_endpoint, failure):
    spec = minimal_manifest(tmp_path) if failure in ('plain', 'prepared-plain') else profile_manifest(tmp_path, model_endpoint.url)
    allow = tmp_path/'allow-readiness'
    if failure in ('plain', 'consumer', 'provider'):
        gated_probe(spec, 'connector' if failure == 'provider' else 'consumer', allow)
    markers = []
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', binaries, workspace=tmp_path) as runtime:
            info = runtime.coordinates; children = list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                   inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                if cycle == 1:
                    if failure == 'registration-reply':
                        register = session.manager.register_prepared
                        async def lost(*args):
                            await register(*args)
                            raise TimeoutError('lost registration acknowledgement')
                        session.manager.register_prepared = lost
                    elif failure == 'before-stage':
                        async def interrupted(): raise OSError('artifact staging interrupted')
                        session._stage = interrupted
                    if failure.startswith('prepared-'):
                        async with asyncio.timeout(90):
                            while (await session.prepare())['state'] != 'prepared':
                                await asyncio.sleep(.05)
                        candidate = await session.prepared_app('consumer')
                        state = await session.wire.status(info.node_id)
                        assert state['instances'][candidate['identity']['instance_id']]['state'] == 'prepared'
                        session = LocalAppProfile(runtime, spec, resolver)
                        await session._open()
                        assert session.status()['state'] == 'prepared'
                        assert await session.prepared_app('consumer') == candidate
                    else:
                        with pytest.raises((AssemblyError, TimeoutError, OSError)):
                            await settle(session, 'advance')
                    for identity in (await session.wire.status(info.node_id))['instances']:
                        data = runtime.root/'node/apps'/info.fleet_id/'data'/identity
                        data.mkdir(parents=True, exist_ok=True)
                        marker = data/'retained-through-startup-cleanup'
                        marker.write_text('keep')
                        markers.append(marker)
                    assert (await settle(session, 'stop'))['state'] == 'stopped'
                    assert session._record['startup_abort'] is True
                    if failure == 'consumer':
                        models = session._record['models']
                        assert models
                        session._record['models'] = {}
                        with pytest.raises(AssemblyError, match='missing model cleanup receipts'):
                            await session._restart_state(session._record)
                        session._record['models'] = models
                    with pytest.raises(AssemblyError, match='stop before another start'):
                        await session.advance()
                    before = (await session.wire.status(info.node_id))['operations']
                    assert (await session.stop())['state'] == 'stopped'
                    assert (await session.wire.status(info.node_id))['operations'] == before
                else:
                    assert (await settle(session, 'advance'))['cycle'] == 2
                    assert all(marker.read_text() == 'keep' for marker in markers)
                    if spec['model_apps']:
                        row = await session.directory.deployment('local')
                        assert row['state'] == 'ready' and row['models'][0]['id'] == 'example:8b'
                    assert 'startup_abort' not in session._record
                    assert (await settle(session, 'stop'))['state'] == 'stopped'
                assert all(i['state'] == 'stopped' and not i.get('resources') and not i.get('reservations')
                    for i in (await session.wire.status(info.node_id))['instances'].values())
            finally:
                await resolver.close()
        assert_stopped(children, info)
        allow.touch()


@pytest.mark.asyncio
async def test_host_stop_interrupts_pending_startup_without_waiting_for_readiness(tmp_path, binaries, monkeypatch):
    import pantheon.platform.local_profile as host
    spec = minimal_manifest(tmp_path)
    commands = asyncio.Queue()
    sessions, runtimes, reports = [], [], []
    original_profile, original_fleet = host.LocalAppProfile, host.LocalFleet
    class Session(original_profile):
        async def advance(self):
            result = await super().advance()
            if not sessions:
                sessions.append(self)
                assert result['state'] == 'starting'
                commands.put_nowait('stop')
            else:
                raise AssertionError('Stop must be observed before another startup advance')
            return result
    class Fleet(original_fleet):
        async def __aenter__(self):
            result = await super().__aenter__()
            runtimes.append((list(self._children), self.coordinates))
            return result
    async def report(value): reports.append(value)
    monkeypatch.setattr(host, 'LocalAppProfile', Session)
    monkeypatch.setattr(host, 'LocalFleet', Fleet)
    async with asyncio.timeout(90):
        await host.serve(tmp_path/'profile', binaries, tmp_path, spec, commands=commands, on_status=report)
    assert reports[-1]['state'] == 'stopped'
    assert not any(r.get('needs_attention') for r in reports)
    assert sessions[0]._record['startup_abort'] is True
    assert_stopped(*runtimes[0])


@pytest.mark.asyncio
async def test_host_failed_start_can_be_stopped_through_existing_control_queue(tmp_path, binaries, monkeypatch):
    import pantheon.platform.local_profile as host
    spec = minimal_manifest(tmp_path)
    gated_probe(spec, 'consumer', tmp_path/'never-ready')
    reports, runtimes = [], []
    commands = asyncio.Queue()
    original_fleet = host.LocalFleet
    class Fleet(original_fleet):
        async def __aenter__(self):
            result = await super().__aenter__()
            runtimes.append((list(self._children), self.coordinates))
            return result
    async def report(value):
        reports.append(value)
        if value.get('needs_attention'): commands.put_nowait('stop')
    monkeypatch.setattr(host, 'LocalFleet', Fleet)
    async with asyncio.timeout(90):
        await host.serve(tmp_path/'profile', binaries, tmp_path, spec, commands=commands, on_status=report)
    assert sum(bool(r.get('needs_attention')) for r in reports) == 1
    assert reports[-1]['state'] == 'stopped'
    assert_stopped(*runtimes[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['prepared', 'reserved'])
async def test_initialization_host_retries_same_preparation_then_stops_without_backend_start(tmp_path, binaries, monkeypatch, model_endpoint, boundary):
    import pantheon.platform.local_profile as host
    spec = (minimal_manifest(tmp_path) if boundary == 'prepared'
            else profile_manifest(tmp_path, model_endpoint.url))
    commands = asyncio.Queue()
    attempts, reports, runtimes = [], [], []
    original_fleet, original_profile = host.LocalFleet, host.LocalAppProfile
    class Fleet(original_fleet):
        async def __aenter__(self):
            value = await super().__aenter__()
            runtimes.append((list(self._children), self.coordinates))
            return value
    class Profile(original_profile):
        async def advance(self):
            raise AssertionError('Initialization-only host must not start consumer backends')
    async def initialize(session):
        candidate = await session.prepared_app('consumer')
        attempts.append(candidate)
        if len(attempts) == 1:
            raise AssemblyError('Initialization interrupted; retry the same request')
        assert attempts[0] == attempts[1]
        state = await session.wire.status(session.info.node_id)
        assert all(i['state'] == 'prepared' and not i.get('resources') for i in state['instances'].values())
        assert all(op['request']['action'] != 'start' for op in state['operations'].values())
    async def report(value):
        reports.append(value)
        if value.get('needs_attention'): commands.put_nowait('retry')
    monkeypatch.setattr(host, 'LocalFleet', Fleet)
    monkeypatch.setattr(host, 'LocalAppProfile', Profile)
    async with asyncio.timeout(90):
        await host.serve(tmp_path/'profile', binaries, tmp_path, spec, commands=commands,
            on_status=report, initialize_only=True, **{'on_' + boundary: initialize})
    assert len(attempts) == 2 and reports[-1]['state'] == 'stopped'
    assert sum(bool(r.get('needs_attention')) for r in reports) == 1
    assert_stopped(*runtimes[0])
