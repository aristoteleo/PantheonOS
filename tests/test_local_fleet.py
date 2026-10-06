"""Real bundled Controller/NATS/Runner; no Hub or installed Fleet daemon."""
import asyncio
import base64
import json
import os
from pathlib import Path
import platform
import shlex
import socket
import subprocess
import sys
from urllib.parse import urlsplit

import nats
import httpx
import pytest

from pantheon.platform.local_fleet import LocalFleet, LocalFleetBinaries, _local_environment


def test_gui_launch_uses_selected_python_without_activated_path(tmp_path, monkeypatch):
    # Reproduce Finder/native launch PATH choosing another Python installation.
    foreign = tmp_path/'foreign'; foreign.mkdir()
    python = foreign/'python3'
    python.write_text('#!/bin/sh\nprintf wrong-python\n'); python.chmod(0o700)
    monkeypatch.setenv('PATH', str(foreign) + os.pathsep + os.defpath)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'FLEET_KEY', 'NATS_SERVERS', 'PANTHEON_PROFILE'):
        monkeypatch.setenv(name, 'must-not-borrow')
    env = _local_environment()
    actual = json.loads(subprocess.check_output(['python3', '-c',
        'import sys,json;print(json.dumps([list(sys.version_info[:3]),sys.prefix]))'], env=env))
    assert actual == [list(sys.version_info[:3]), sys.prefix]
    assert str(foreign) in env['PATH']  # Other owner build tools remain available.
    assert not {'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'FLEET_KEY', 'NATS_SERVERS', 'PANTHEON_PROFILE'} & env.keys()


@pytest.fixture
def binaries():
    names = ('LOCAL_FLEET_CONTROLLER', 'LOCAL_FLEET_BROKER', 'LOCAL_FLEET_RUNNER')
    if any(not os.environ.get(name) for name in names):
        pytest.skip('Supply bundled local Controller, NATS and Runner executables')
    return LocalFleetBinaries(*(Path(os.environ[name]) for name in names))


async def inventory(coordinates):
    nc = await nats.connect(coordinates.nats, user_credentials=str(coordinates.credentials),
        inbox_prefix=('_INBOX_' + coordinates.fleet_id).encode(), connect_timeout=2)
    try:
        subject = f'fleet.{coordinates.fleet_id}.node.{coordinates.node_id}.cmd'
        response = await nc.request(subject, json.dumps({
            'type': 'app_lifecycle', 'protocol': 1, 'method': 'status'}).encode(), timeout=3)
        value = json.loads(response.data)
        assert 'error' not in value, value
        return value
    finally:
        await nc.close()


def assert_stopped(children, coordinates):
    assert all(child.returncode is not None for _, child in children)
    for endpoint in (coordinates.controller, coordinates.nats):
        address = urlsplit(endpoint)
        with socket.socket() as connection:
            connection.settimeout(.2)
            assert connection.connect_ex((address.hostname, address.port)) != 0


@pytest.mark.asyncio
async def test_local_fleet_real_readiness_restart_and_profile_exclusion(tmp_path, binaries, monkeypatch):
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://must-not-join.invalid')
    monkeypatch.setenv('FLEET_HUB_URL', 'https://must-not-login.invalid')
    root = tmp_path / 'profile'
    async with LocalFleet(root, binaries, workspace=tmp_path) as runtime:
        first = runtime.coordinates
        children = list(runtime._children)
        state = await inventory(first)
        assert 'instances' in state and 'operations' in state, state
        with pytest.raises(TimeoutError):
            async with LocalFleet(root, binaries, workspace=tmp_path):
                pytest.fail('Two owners admitted for one local profile')
        # A failed second owner cannot tear down the existing node.
        assert 'instances' in await inventory(first)
    assert_stopped(children, first)
    assert runtime.coordinates is None
    # Persisted runtime.json and credentials must not skip fresh readiness.
    async with LocalFleet(root, binaries, workspace=tmp_path) as runtime:
        second = runtime.coordinates
        children = list(runtime._children)
        assert (second.node_id, second.fleet_id) == (first.node_id, first.fleet_id)
        assert 'instances' in await inventory(second)
    assert_stopped(children, second)


@pytest.mark.asyncio
async def test_local_fleet_cancel_drains_only_owned_processes(tmp_path, binaries):
    started = asyncio.Event()
    evidence = []
    async def owner():
        async with LocalFleet(tmp_path / 'cancel', binaries, workspace=tmp_path) as runtime:
            evidence.extend([list(runtime._children), runtime.coordinates])
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(owner())
    waiter = asyncio.create_task(started.wait())
    try:
        done, _ = await asyncio.wait([task, waiter], timeout=50,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_stopped(*evidence)
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_broker_start_reaps_controller_and_releases_profile(tmp_path, binaries):
    broken = LocalFleetBinaries(binaries.controller, Path('/usr/bin/false'), binaries.runner)
    runtime = LocalFleet(tmp_path / 'failure', broken, workspace=tmp_path)
    with pytest.raises(RuntimeError, match='broker exited'):
        async with runtime:
            pytest.fail('A failed broker became ready')
    assert runtime._stack is None and not runtime._children
    async with LocalFleet(tmp_path / 'failure', binaries, workspace=tmp_path) as live:
        assert 'instances' in await inventory(live.coordinates)


@pytest.mark.asyncio
async def test_local_credentials_renew_after_original_expiry(tmp_path, binaries):
    wrapper = tmp_path / 'short-lived-controller'
    wrapper.write_text('#!/bin/sh\nexport FLEET_ACCESS_TTL=4s\nexec ' +
                       shlex.quote(str(binaries.controller)) + ' "$@"\n')
    wrapper.chmod(0o700)
    short = LocalFleetBinaries(wrapper, binaries.broker, binaries.runner)
    async with LocalFleet(tmp_path / 'renewal', short, workspace=tmp_path) as runtime:
        previous = runtime.coordinates.credentials.read_bytes()
        await asyncio.sleep(5)
        assert runtime.coordinates.credentials.read_bytes() != previous
        assert not runtime._renewal.done()
        # The broker disconnects an expired Runner connection; its existing
        # reconnect loop rereads the renewed credential. Observe recovery with
        # read-only queries; never retry a mutating App operation here.
        for attempt in range(30):
            try:
                assert 'instances' in await inventory(runtime.coordinates)
                break
            except (nats.errors.NoRespondersError, nats.errors.TimeoutError):
                if attempt == 29:
                    raise
                await asyncio.sleep(.1)


@pytest.mark.asyncio
async def test_sidecar_exit_is_visible_and_other_profile_survives(tmp_path, binaries):
    async with LocalFleet(tmp_path / 'other', binaries, workspace=tmp_path) as other:
        async with LocalFleet(tmp_path / 'crash', binaries, workspace=tmp_path) as runtime:
            watcher = asyncio.create_task(runtime.wait())
            controller = dict(runtime._children)['controller']
            controller.terminate()
            with pytest.raises(RuntimeError, match='controller exited'):
                await asyncio.wait_for(watcher, 3)
        assert 'instances' in await inventory(other.coordinates)


@pytest.mark.asyncio
async def test_revocation_reloads_only_its_own_local_broker(tmp_path, binaries):
    async with LocalFleet(tmp_path / 'other', binaries, workspace=tmp_path) as other:
        async with LocalFleet(tmp_path / 'revoked', binaries, workspace=tmp_path) as runtime:
            marker = 'Reloaded server configuration'
            other_before = (other.root / 'broker.log').read_text().count(marker)
            async with httpx.AsyncClient(trust_env=False, verify=runtime.coordinates.tls_context()) as http:
                response = await http.post(runtime.coordinates.controller + '/revoke', json={
                    'key': (runtime.root / 'owner.key').read_text().strip(),
                    'node_id': runtime.coordinates.node_id})
            assert response.status_code == 200 and response.json()['kicked'] is True
            for _ in range(30):
                if marker in (runtime.root / 'broker.log').read_text():
                    break
                await asyncio.sleep(.1)
            else:
                pytest.fail('Owned broker did not reload its revocation')
            assert (other.root / 'broker.log').read_text().count(marker) == other_before
            assert 'instances' in await inventory(other.coordinates)


@pytest.mark.asyncio
async def test_private_https_uses_existing_dependency_owner_client(tmp_path, binaries):
    from pantheon.apps.runtime_config import RuntimeCredential
    from pantheon.platform.dependency_control import OwnerDependencyLifecycle
    async with LocalFleet(tmp_path / 'tls', binaries, workspace=tmp_path) as runtime:
        info = runtime.coordinates
        assert info.controller.startswith('https://127.0.0.1:')
        async with httpx.AsyncClient(trust_env=False) as untrusted:
            with pytest.raises(httpx.ConnectError):
                await untrusted.get(info.controller + '/healthz')
        client = OwnerDependencyLifecycle(owner=info.fleet_id, credential=RuntimeCredential(
            endpoint=info.controller, key=(runtime.root / 'owner.key').read_text().strip()),
            tls_context=info.tls_context())
        try:
            status = await client.status(info.node_id)
            assert 'instances' in status, status
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_node_resume_retains_private_controller_trust(tmp_path, binaries):
    async with LocalFleet(tmp_path / 'resume', binaries, workspace=tmp_path) as runtime:
        info = runtime.coordinates
        state_dir = runtime.root / 'node'
        saved = json.loads((state_dir / 'fleet-state.json').read_text())
        assert saved['controller_ca'] == str(info.ca_certificate)
        assert saved['local_dependency_rpc'] is True
        runner = dict(runtime._children)['runner']
        runner.terminate()
        await asyncio.wait_for(runner.wait(), 20)
        runtime._children.remove(('runner', runner))
        (state_dir / 'runtime.json').unlink()
        # No controller, CA, key or join token on resume. The persisted trust
        # must apply before /token; this also exercises a real proof-of-possession.
        await runtime._spawn('runner', [binaries.runner, 'up', '--state-dir', state_dir,
            '--workdir', tmp_path, '--no-auto-update', '--no-capture-setup'],
            {k: v for k, v in os.environ.items() if not k.startswith(('FLEET_', 'PANTHEON_', 'NATS_'))})
        async def ready():
            return (state_dir / 'runtime.json').is_file()
        await runtime._wait(ready, asyncio.get_running_loop().time() + 30)
        assert 'instances' in await inventory(info)


@pytest.mark.asyncio
async def test_local_node_runs_the_ordinary_managed_shell_app(tmp_path, binaries):
    from pantheon.apps.client import AppClient
    from pantheon.apps.lifecycle import FleetLifecycle, build_artifact, CHUNK_SIZE
    root = Path(__file__).resolve().parents[1]
    package = tmp_path / 'shell-package'
    built = await asyncio.to_thread(subprocess.run, [sys.executable,
        str(root / 'apps/shell/build_managed.py'), '--output', str(package), '--os', sys.platform,
        '--arch', {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]],
        cwd=root, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    payload, digest = build_artifact(package)
    async with LocalFleet(tmp_path / 'apps', binaries, workspace=tmp_path) as runtime:
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        try:
            client = AppClient(nc, info.fleet_id)
            class Wire(FleetLifecycle):
                async def _request(self, node, method, **kwargs):
                    result = await client.lifecycle(node, method, **kwargs)
                    if result.get('error'):
                        raise RuntimeError(result['error'])
                    return result
            wire = Wire(None)
            for offset in range(0, len(payload), CHUNK_SIZE):
                await wire._request(info.node_id, 'stage', digest=digest, offset=offset,
                    data=base64.b64encode(payload[offset:offset+CHUNK_SIZE]).decode())
            async def operation(action, generation=0, **kwargs):
                receipt = await wire.submit(info.node_id, action, digest,
                                            scope='local-cli-tools', generation=generation, **kwargs)
                for _ in range(300):
                    status = await wire.status(info.node_id)
                    op = status['operations'][receipt['request']['operation_id']]
                    if op['state'] == 'succeeded':
                        return next((i for i in status['instances'].values() if i['digest'] == digest), None)
                    assert op['state'] in ('queued', 'running'), op
                    await asyncio.sleep(.05)
                pytest.fail('Local App operation timed out')
            await operation('install')
            prepared = await operation('prepare_start', operation_id='prepare-shell')
            await wire.configure(info.node_id, instance_id=prepared['instance_id'], revision=digest,
                generation=prepared['generation'], preparation_id='prepare-shell', components={
                    'backend': {'values': {'shell': {'workspace': str(tmp_path)}}}})
            installed = await operation('start', prepared['generation'], start_preparation_id='prepare-shell')
            exact = {'instance_id': installed['instance_id'], 'revision': digest,
                     'generation': installed['generation']}
            async def call(method, **args):
                response = await client.invoke(info.node_id, 'shell', exact, method, args, 5)
                assert 'error' not in response, response
                value = response['response']
                assert value.get('success') is True, value
                return value['result']
            lease = await call('resource_session_acquire', owner_ref='cli-agent',
                               lease_id='a'*64, kind='shell', ttl_seconds=300)
            result = await call('run_command', shell_id=lease['session_id'],
                                command='printf LOCAL_APP_OK', timeout=3)
            assert 'LOCAL_APP_OK' in result['output'], result
            await call('resource_session_release', owner_ref='cli-agent', lease_id='a'*64)
            stopped = await operation('stop', installed['generation'])
            assert stopped['state'] == 'stopped'
        finally:
            await nc.close()
