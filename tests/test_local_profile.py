"""Product local-profile orchestration over native Fleet and original models."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import platform
import signal
import sys

import nats
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.connector_package import build_package
from pantheon.models.dependency import DependencyModelServices
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile, manifest, local_values, private_json
from pantheon.platform.model_dependency_package import build_package as build_control
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import consumer_package, model_endpoint


def profile_manifest(tmp_path, endpoint):
    target = sys.platform + '-' + {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    paths = {'connector': build_package(tmp_path/'connector', target),
             'control': build_control(tmp_path/'control', target), 'consumer': tmp_path/'consumer'}
    consumer_package(paths['consumer'])
    packages = {name: {'path': str(path), 'revision': build_artifact(path)[1]} for name, path in paths.items()}
    def app(package, scope, components):
        return dict(package=package, scope=scope, components=components, bindings={})
    control = {'backend': {'values': {'model_services': {'protocol': 1,
        'http_origin': {'$local': 'controller'}, 'trust_roots_pem': {'$local': 'trust_roots_pem'},
        'directory_root': {'$local': 'directory_root'}, 'policies': {'consumer': {
            'consumer': {'$app': 'consumer'}, 'deployments': {'local': {'$model': 'connector'}},
            'routes': {}, 'allow_wake': False}}}}, 'credentials': {'hub': {'$local': 'owner_credential'}}}}
    return dict(protocol=1, packages=packages, apps={
        'consumer': app('consumer', 'consumer', {}), 'control': app('control', 'control', control)},
        model_apps={'connector': dict(deployment_id='local', name='Profile model',
            models=[{'id': 'example:8b', 'context_limit': 4096}], app=app('connector', 'model-local', {
                'backend': {'values': {'connector': {'engine': 'ollama', 'endpoint': endpoint}}}}))})


async def settle(session, method):
    async with asyncio.timeout(90):
        while True:
            result = await getattr(session, method)()
            if result['state'] in ('ready', 'stopped'): return result
            await asyncio.sleep(.05)


@pytest.mark.asyncio
async def test_profile_runs_two_clean_lifetimes_preserving_original_model_choices(tmp_path, binaries, model_endpoint, monkeypatch):
    spec = profile_manifest(tmp_path, model_endpoint.url)
    old = None
    for cycle in (1, 2):
        if cycle == 2:
            def no_rebuild(*args, **kwargs): raise AssertionError('Installed immutable packages must be reused')
            monkeypatch.setattr('pantheon.platform.local_profile.build_artifact', no_rebuild)
        async with LocalFleet(tmp_path/'profile', binaries, workspace=tmp_path) as runtime:
            info = runtime.coordinates
            children = list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                assert (await settle(session, 'advance'))['cycle'] == cycle
                assert session.status()['state'] == 'ready'
                recipe = session._record['recipe']
                consumer = session.deploy.inspect(owner=info.fleet_id, operation_id=session._consumer_id(recipe))['prepared']
                identity = {**consumer['consumer'], 'generation': consumer['consumer']['generation'] + 1}
                control = {**consumer['control'], 'generation': consumer['control']['generation'] + 1,
                           'component': 'backend', 'port': 'http'}
                row = await session.directory.deployment('local')
                if old:
                    assert row['models'] == old['models']
                    assert row['binding']['instance_id'] == old['binding']['instance_id']
                    assert row['binding']['generation'] == old['binding']['generation'] + 3
                grant = await session.authority.issue({'operation_id': 'profile-inference-' + str(cycle),
                    'consumer': identity, 'provider': control, 'app_id': 'model-services-control',
                    'methods': {'model_services_control': {'arguments': ['operation', 'arguments'],
                                                          'bound': {'policy_id': 'consumer'}}}, 'ttl_seconds': 300})
                client = DependencyModelServices(DependencyClient(
                    RuntimeCredential(grant['endpoint'], grant['access_token']), info.tls_context()), direct_executable='')
                try:
                    assert await client.deployments() == [row]
                    result = await client.complete('fleet-model://local/example%3A8b', [{'role': 'user', 'content': 'profile call'}])
                    assert result['content'] == 'scoped reply'
                finally:
                    await client.aclose()
                old = row
                assert (await settle(session, 'stop'))['state'] == 'stopped'
                stopped = await session.directory.deployment('local')
                assert stopped['state'] == 'stopped'
                assert await session.stop() == session.status()
                assert await session.directory.deployment('local') == stopped
            finally:
                # Test failure cleanup is explicit; never claim startup completed
                # or change the product's refusal to heal uncertain generations.
                state = await session.wire.status(info.node_id)
                for item in state['instances'].values():
                    if item['state'] == 'stopped': continue
                    op = await session.wire.submit(info.node_id, 'stop', item['digest'],
                                                   generation=item['generation'], scope=item['scope'])
                    async with asyncio.timeout(90):
                        while (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]['state'] in ('queued', 'running'):
                            await asyncio.sleep(.05)
                await resolver.close()
        assert_stopped(children, info)
    assert len([c for c in model_endpoint.requests if c[0] == '/v1/chat/completions']) == 2


def minimal_manifest(tmp_path):
    consumer = tmp_path/'app'
    consumer_package(consumer)
    return dict(protocol=1, packages={'consumer': {'path': str(consumer), 'revision': build_artifact(consumer)[1]}},
                apps={'consumer': {'package': 'consumer', 'scope': 'sample', 'components': {}, 'bindings': {}}}, model_apps={})


@pytest.mark.asyncio
async def test_real_command_starts_and_handles_sigint_with_clean_shutdown(tmp_path, binaries):
    spec = minimal_manifest(tmp_path)
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(spec)); path.chmod(0o600)
    for cycle in (1, 2):
        log = (tmp_path/'command.log').open('ab')
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'local',
            '--profile', str(tmp_path/'profile'), '--workspace', str(tmp_path), '--manifest', str(path),
            '--controller', str(binaries.controller), '--broker', str(binaries.broker), '--runner', str(binaries.runner),
            stdout=asyncio.subprocess.PIPE, stderr=log, stdin=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(60):
                line = await proc.stdout.readline()
            assert line, (tmp_path/'command.log').read_text()
            ready = json.loads(line)
            assert ready['state'] == 'ready' and ready['cycle'] == cycle, ready
            proc.send_signal(signal.SIGINT)
            async with asyncio.timeout(60):
                line = await proc.stdout.readline()
                assert json.loads(line)['state'] == 'stopped', line
                assert await proc.wait() == 0, (tmp_path/'command.log').read_text()
            saved = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
            assert saved['phase'] == 'stopped'
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()
            log.close()


@pytest.mark.parametrize('value', [{'$local': 'missing'}, {'$local': 'controller', 'extra': 1}, {'$local': {}}])
def test_unknown_or_ambiguous_local_template_values_are_rejected(value):
    with pytest.raises(AssemblyError): local_values(value, {'controller': 'https://127.0.0.1:1234'})


def test_manifest_is_copied_and_keys_never_enter_public_context(tmp_path):
    spec = minimal_manifest(tmp_path)
    saved = deepcopy(spec)
    assert manifest(spec) == saved
    assert local_values({'token': {'$local': 'owner_credential'}}, {'owner_credential': {
        'ref': 'node-secret://owner', 'endpoint': 'https://127.0.0.1:1'}})['token']['ref'] == 'node-secret://owner'
    path = tmp_path/'manifest.json'; path.write_text(json.dumps(spec)); path.chmod(0o600)
    assert private_json(path) == spec
    path.chmod(0o644)
    with pytest.raises(RuntimeError): private_json(path)


def offline_session(tmp_path):
    from types import SimpleNamespace
    from pantheon.platform.local_fleet import LocalFleetCoordinates
    from pantheon.platform.local_tls import prepare_tls
    spec = minimal_manifest(tmp_path)
    root = tmp_path/'profile'; root.mkdir(mode=0o700)
    ca, _ = prepare_tls(root)
    coordinates = LocalFleetCoordinates('https://127.0.0.1:18900', 'nats://127.0.0.1:18901',
                                       'f_owner', 'node', root/'owner.creds', ca)
    runtime = SimpleNamespace(root=root, workspace=tmp_path, coordinates=coordinates)
    return runtime, spec, LocalAppProfile(runtime, spec, object())


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['starting', 'ready', 'stopping'])
async def test_new_authority_refuses_incomplete_previous_profile_before_node_work(tmp_path, phase):
    from dataclasses import replace
    from unittest.mock import AsyncMock
    runtime, spec, original = offline_session(tmp_path)
    await original._open()
    record = original._record
    record['phase'] = phase
    await original._checkpoint(original.path, record)
    saved = original.path.read_bytes()
    runtime.coordinates = replace(runtime.coordinates, controller='https://127.0.0.1:18902')
    fresh = LocalAppProfile(runtime, spec, object())
    fresh.wire = AsyncMock()
    with pytest.raises(AssemblyError, match='explicit recovery'):
        await fresh.advance()
    assert original.path.read_bytes() == saved
    assert not fresh.wire.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['manifest', 'workspace', 'trust', 'owner', 'operation', 'protocol'])
async def test_profile_identity_and_configuration_cannot_silently_change(tmp_path, change):
    from unittest.mock import AsyncMock
    runtime, spec, original = offline_session(tmp_path)
    await original._open()
    record = original._record
    if change == 'manifest': spec['apps']['consumer']['scope'] = 'changed'
    elif change == 'workspace': runtime.workspace = tmp_path/'other'
    elif change == 'trust': record['ca_hash'] = '0'*64
    elif change == 'owner': record['recipe']['owner'] = 'other'
    elif change == 'operation': record['recipe']['operation_id'] = 'other'
    else: record['protocol'] = True
    await original._checkpoint(original.path, record)
    fresh = LocalAppProfile(runtime, spec, object())
    fresh.wire = AsyncMock()
    with pytest.raises(AssemblyError): await fresh.advance()
    assert not fresh.wire.mock_calls


@pytest.mark.asyncio
async def test_changed_uninstalled_package_fails_before_lifecycle_or_credential_delivery(tmp_path):
    from unittest.mock import AsyncMock
    runtime, spec, session = offline_session(tmp_path)
    (Path(spec['packages']['consumer']['path'])/'server.py').write_text('changed')
    session.wire = AsyncMock()
    session.wire.status.return_value = {'owner': 'f_owner', 'node_id': 'node', 'installations': {}}
    with pytest.raises(AssemblyError, match='package changed'): await session.advance()
    session.wire.submit.assert_not_called()
    session.wire._request.assert_not_called()
    assert session.status()['state'] == 'starting'


@pytest.mark.asyncio
async def test_maintenance_reports_failure_and_recovery_without_replaying_apps():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pantheon.platform.local_profile import maintain_dependencies
    calls = 0
    recovered = asyncio.Event()
    reports = []
    async def reconcile():
        nonlocal calls
        calls += 1
        if calls <= 2: raise OSError('private/path and private credential')
        if calls == 3: return {'deferred': 1}
        return {'renewed': 2}
    session = SimpleNamespace(deploy=SimpleNamespace(starter=SimpleNamespace(reconcile_once=reconcile)),
        status=lambda: {'state': 'ready', 'cycle': 1}, advance=AsyncMock(), stop=AsyncMock())
    async def report(value):
        reports.append(value)
        if value['dependency_maintenance'] == dict(expired=0, invalid=0, deferred=0): recovered.set()
    task = asyncio.create_task(maintain_dependencies(session, report, interval=.001))
    try:
        async with asyncio.timeout(3): await recovered.wait()
        assert [r['dependency_maintenance'] for r in reports] == [
            {'unavailable': True}, dict(expired=0, invalid=0, deferred=1), dict(expired=0, invalid=0, deferred=0)]
        assert 'private' not in json.dumps(reports)
        session.advance.assert_not_called()
        session.stop.assert_not_called()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task


@pytest.mark.asyncio
async def test_product_host_retains_healthy_fleet_and_resumes_lost_start_ack(tmp_path, binaries, monkeypatch):
    import pantheon.platform.local_profile as host
    spec = minimal_manifest(tmp_path)
    commands, reports, sessions, runtimes = asyncio.Queue(), [], [], []
    recovered = asyncio.Event()
    original_fleet, original_profile = host.LocalFleet, host.LocalAppProfile
    original_maintain = host.maintain_dependencies
    class RecordedFleet(original_fleet):
        async def __aenter__(self):
            result = await super().__aenter__()
            runtimes.append((self, list(self._children), self.coordinates))
            return result
    class InterruptedProfile(original_profile):
        def __init__(self, *args):
            super().__init__(*args)
            sessions.append(self)
            self.lost = False
            reconcile = self.deploy.starter.reconcile_once
            self.failed_read = False
            async def temporary_storage_failure():
                if not self.failed_read:
                    self.failed_read = True
                    raise OSError('private startup journal path')
                return await reconcile()
            self.deploy.starter.reconcile_once = temporary_storage_failure
        async def advance(self):
            result = await super().advance()
            if result['state'] == 'ready' and not self.lost:
                self.lost = True
                raise TimeoutError('private endpoint and key in lost response')
            return result
    async def maintenance(session, report):
        await original_maintain(session, report, interval=.01)
    async def report(value):
        reports.append(value)
        if value.get('dependency_maintenance') == dict(expired=0, invalid=0, deferred=0): recovered.set()
        if value.get('needs_attention'):
            assert all(child.returncode is None for _, child in runtimes[0][1])
            assert sessions[0]._record['cycle'] == 1
            commands.put_nowait('retry')
        elif value['state'] == 'ready' and 'dependency_maintenance' not in value:
            async with asyncio.timeout(3): await recovered.wait()
            state = await sessions[0].wire.status(sessions[0].info.node_id)
            assert len([o for o in state['operations'].values() if o['request']['action'] == 'start']) == 1
            commands.put_nowait('stop')
    monkeypatch.setattr(host, 'LocalFleet', RecordedFleet)
    monkeypatch.setattr(host, 'LocalAppProfile', InterruptedProfile)
    monkeypatch.setattr(host, 'maintain_dependencies', maintenance)
    async with asyncio.timeout(90):
        await host.serve(tmp_path/'profile', binaries, tmp_path, spec, on_status=report, commands=commands)
    assert len(sessions) == 1 and sessions[0].status()['state'] == 'stopped'
    assert len([r for r in reports if r.get('needs_attention')]) == 1
    assert 'private startup journal path' not in json.dumps(reports)
    assert 'private endpoint and key in lost response' not in json.dumps(reports)
    assert reports[-1]['state'] == 'stopped'
    assert_stopped(runtimes[0][1], runtimes[0][2])


@pytest.mark.asyncio
async def test_profile_construction_failure_closes_connection_and_owned_children(tmp_path, binaries, monkeypatch):
    import pantheon.platform.local_profile as host
    captured, runtimes = [], []
    original_connect, original_fleet = nats.connect, host.LocalFleet
    async def connect(*args, **kwargs):
        result = await original_connect(*args, **kwargs)
        captured.append(result)
        return result
    class RecordedFleet(original_fleet):
        async def __aenter__(self):
            result = await super().__aenter__()
            runtimes.append((list(self._children), self.coordinates))
            return result
    monkeypatch.setattr(nats, 'connect', connect)
    monkeypatch.setattr(host, 'LocalFleet', RecordedFleet)
    with pytest.raises(AssemblyError, match='manifest'):
        await host.serve(tmp_path/'profile', binaries, tmp_path, {})
    assert captured and all(connection.is_closed for connection in captured)
    assert_stopped(*runtimes[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['reply', 'error', 'interrupt', 'self-cancel'])
async def test_foreground_client_is_joined_and_apps_drain_before_profile_exit(tmp_path, binaries, outcome):
    from pantheon.platform.local_profile import serve
    commands, sessions, children, reports = asyncio.Queue(), [], [], []
    finalized = asyncio.Event()
    class ClientFailure(RuntimeError): pass
    async def foreground(session):
        sessions.append(session)
        children.extend(session.runtime._children)
        assert session.status()['state'] == 'ready'
        try:
            if outcome == 'error': raise ClientFailure('foreground failed')
            if outcome == 'self-cancel': raise asyncio.CancelledError
            if outcome == 'interrupt':
                commands.put_nowait('stop')
                await asyncio.Event().wait()
        finally:
            finalized.set()
    async def report(value): reports.append(value)
    async with asyncio.timeout(90):
        if outcome == 'reply':
            await serve(tmp_path/'profile', binaries, tmp_path, minimal_manifest(tmp_path),
                        commands=commands, on_status=report, on_ready=foreground)
        else:
            with pytest.raises(ClientFailure if outcome == 'error' else AssemblyError):
                await serve(tmp_path/'profile', binaries, tmp_path, minimal_manifest(tmp_path),
                            commands=commands, on_status=report, on_ready=foreground)
    assert finalized.is_set() and len(sessions) == 1
    assert reports[-1]['state'] == 'stopped'
    assert_stopped(children, sessions[0].info)
