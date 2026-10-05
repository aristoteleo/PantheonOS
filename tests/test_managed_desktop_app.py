"""Prepared Desktop on actual local buses/Fleet, without ambient Agent state."""
import asyncio
import base64
import builtins
from dataclasses import replace
import json
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time

import httpx
import nats
import pytest

from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.desktop import managed
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import consumer_package


@pytest.fixture
def event_bus(tmp_path):
    executable = shutil.which('nats-server') or '/opt/homebrew/bin/nats-server'
    if not Path(executable).is_file():
        pytest.skip('Requires a local NATS server')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (tmp_path/'events.log').open('w') as log:
        child = subprocess.Popen([executable, '-a', '127.0.0.1', '-p', str(port),
                                  '--auth', 'test-events-key'], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert child.poll() is None
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline
                    time.sleep(.02)
            yield f'nats://127.0.0.1:{port}'
        finally:
            child.terminate()
            child.wait(timeout=10)


def configuration(tmp_path, fleet_endpoint, fleet_key, events):
    workspace, state = tmp_path/'workspace', tmp_path/'state'
    workspace.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    (workspace/'apps').mkdir(exist_ok=True)
    value = {'user_seed': 'desktop-owner', 'fleet': {'auth': 'creds-base64'},
             'events': {'auth': 'token'}, 'event_prefix': 'prepared.desktop',
             'catalog': [{'path': str(workspace/'apps'), 'scope': 'user'}],
             'data_roots': [], 'store': {'origin': 'https://store.invalid'},
             'data': {'mode': 'loopback'}}
    config = RuntimeConfiguration({'desktop': value}, {
        'fleet': RuntimeCredential(fleet_endpoint, base64.b64encode(fleet_key.encode()).decode()),
        'events': RuntimeCredential(events, 'test-events-key')},
        'desktop-instance', 'a' * 64, 1, 'backend', 'fleet-test', 'node-test')
    return config, workspace, state


def prepare_environment(monkeypatch, config, state):
    value = {'protocol': 1, 'owner': config.owner, 'node_id': config.node_id,
             'instance_id': config.instance_id, 'revision': config.revision,
             'component': config.component, 'generation': config.generation,
             'values': config.values,
             'credentials': {key: {'endpoint': item.endpoint, 'key': item.key}
                             for key, item in config.credentials.items()}}
    path = state/'prepared.json'
    path.write_text(json.dumps(value))
    monkeypatch.setenv('PANTHEON_APP_CONFIG', str(path))
    for key, env in {'owner': 'PANTHEON_FLEET_ID', 'node_id': 'PANTHEON_NODE_ID',
                     'instance_id': 'PANTHEON_INSTANCE_ID', 'revision': 'PANTHEON_APP_REVISION',
                     'component': 'PANTHEON_COMPONENT_NAME', 'generation': 'PANTHEON_INSTANCE_GENERATION'}.items():
        monkeypatch.setenv(env, str(value[key]))
    monkeypatch.delenv('PANTHEON_PORT_DATA', raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("data_mode", ["loopback", "tunnel"])
async def test_prepared_desktop_real_fleet_events_files_and_reopen(tmp_path, binaries, event_bus, monkeypatch, data_mode):
    (tmp_path/'workspace').mkdir()
    async with LocalFleet(tmp_path/'fleet-profile', binaries, workspace=tmp_path/'workspace') as runtime:
        info = runtime.coordinates
        children = list(runtime._children)
        config, workspace, state = configuration(tmp_path, info.nats, info.credentials.read_text(), event_bus)
        config = replace(config, owner=info.fleet_id, node_id=info.node_id)
        data_port = 0
        if data_mode == 'tunnel':
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                data_port = sock.getsockname()[1]
            config.values['desktop']['data'] = {'mode': 'tunnel', 'credential': 'data'}
            config.credentials['data'] = RuntimeCredential(f'http://127.0.0.1:{data_port}', 'prepared-data-key')
        consumer_package(workspace/'apps/model-consumer')
        # Versioned catalog metadata must not depend on source-checkout builtins.
        manifest_file = workspace/'apps/model-consumer/app.json'
        manifest = json.loads(manifest_file.read_text())
        manifest['actions'] = [{'name': 'inspect', 'description': 'Inspect this app'}]
        manifest_file.write_text(json.dumps(manifest))
        prepare_environment(monkeypatch, config, state)
        monkeypatch.setenv('PANTHEON_PORT_DATA', str(data_port))
        monkeypatch.setenv('NATS_SERVERS', 'nats://must-not-use.invalid:4222')
        monkeypatch.setenv('LIVE_VIEW_DATA_TOKEN', 'must-not-use')
        monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://must-not-join.invalid')
        observer = await nats.connect(event_bus, token='test-events-key')
        subscription = await observer.subscribe('prepared.desktop.pantheon.stream.desktop')
        await observer.flush()
        original_import = builtins.__import__
        def guard(name, *args, **kwargs):
            if name in ('pantheon.agent', 'pantheon.settings', 'pantheon.chatroom',
                        'pantheon.factory', 'pantheon.remote', 'pantheon.store.auth'):
                raise AssertionError('Prepared Desktop accessed ambient implementation: ' + name)
            return original_import(name, *args, **kwargs)
        contexts = []
        try:
            with monkeypatch.context() as bound:
                bound.setattr(builtins, '__import__', guard)
                ctx = AppContext('desktop', workspace, state, None)
                contexts.append(ctx)
                await managed.register(ctx)
                assert ctx.require_rpc_token
                methods = ctx._methods
                status = await methods['desktop_app_lifecycle'](node_id=info.node_id)
                assert status['success'], status
                assert (await methods['desktop_app_placement'](action='options', app_id='model-consumer'))['success']
                opened = await methods['desktop_intent'](kind='open', args={'app_id': 'files', 'title': 'Prepared files'})
                assert opened['success'], opened
                message = json.loads((await subscription.next_msg(timeout=5)).data)
                assert message['type'] == 'custom' and message['session_id'] == 'desktop'
                assert message['data']['type'] == 'desktop.delta'
                (workspace/'hello.txt').write_text('from the prepared workspace')
                served = await methods['serve_local_data'](path=str(workspace/'hello.txt'), node_id=info.node_id)
                assert served['success'], served
                assert not (await methods['serve_local_data'](path=str(workspace/'hello.txt'), node_id='foreign-node'))['success']
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert (await client.get(served['url'])).text == 'from the prepared workspace'
                    if data_mode == 'tunnel':
                        assert served['url'].startswith(f'http://127.0.0.1:{data_port}/d/prepared-data-key/')
                        denied = await client.get(served['url'].replace('/d/prepared-data-key/', '/d/wrong-key/'))
                        assert denied.status_code == 403
                private = await methods['serve_local_data'](path=str(state/'prepared.json'))
                assert not private['success'], private
                assert list(state.rglob('.desktop-bus-*.creds'))
                await ctx.before_stop()
                assert not list(state.rglob('.desktop-bus-*.creds'))
                with pytest.raises(RuntimeError, match='stopping'):
                    await methods['desktop_session_get']()
                # A new prepared generation reconnects without losing windows.
                prepare_environment(bound, replace(config, generation=2), state)
                bound.setenv('PANTHEON_PORT_DATA', str(data_port))
                reopened = AppContext('desktop', workspace, state, None)
                contexts.append(reopened)
                await managed.register(reopened)
                snapshot = await reopened._methods['desktop_session_get']()
                assert opened['window_id'] in snapshot['session']['windows']
                assert (await reopened._methods['desktop_app_lifecycle'](node_id=info.node_id))['success']
                assert observer.is_connected
                await reopened.before_stop()
        finally:
            for ctx in reversed(contexts):
                if ctx._cleanup:
                    await ctx._cleanup()
            await observer.close()
    assert_stopped(children, info)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'private-root', 'symlink-root', 'store-symlink-root', 'wildcard', 'store-key-origin', 'remote-insecure', 'bad-port'])
async def test_configuration_rejected_before_connecting(tmp_path, monkeypatch, failure):
    config, workspace, state = configuration(tmp_path, 'nats://127.0.0.1:4222', 'unused', 'nats://127.0.0.1:4223')
    value = config.values['desktop']
    value['fleet'] = {'auth': 'token'}
    managed._prepare(config, workspace, state)
    port = 0
    if failure == 'missing':
        del value['store']
    elif failure == 'private-root':
        value['data_roots'] = [str(tmp_path)]
    elif failure == 'symlink-root':
        (workspace/'private').symlink_to(state, target_is_directory=True)
        value['data_roots'] = [str(workspace/'private')]
    elif failure == 'store-symlink-root':
        (workspace/'app-store').mkdir()
        (workspace/'app-store/snapshots').symlink_to(state, target_is_directory=True)
    elif failure == 'wildcard':
        value['event_prefix'] = 'other.>'
    elif failure == 'store-key-origin':
        value['store']['credential'] = 'store'
        config.credentials['store'] = RuntimeCredential('https://different.invalid', 'secret')
    elif failure == 'remote-insecure':
        config.credentials['fleet'] = RuntimeCredential('nats://remote.invalid:4222', 'secret')
    else:
        port = 12345
    async def forbidden(*args, **kwargs):
        pytest.fail('Invalid configuration opened a connection')
    monkeypatch.setattr(managed.OwnedBus, 'connect', forbidden)
    with pytest.raises(ValueError):
        await managed.create_service(config, workspace, state, data_port=port)
    assert not list(state.glob('.desktop-bus-*'))


@pytest.mark.asyncio
async def test_second_bus_failure_cleans_first_connection_and_credential(tmp_path, binaries, event_bus, monkeypatch):
    (tmp_path/'workspace').mkdir()
    async with LocalFleet(tmp_path/'fleet-profile', binaries, workspace=tmp_path/'workspace') as runtime:
        info = runtime.coordinates
        config, workspace, state = configuration(tmp_path, info.nats, info.credentials.read_text(), event_bus)
        config = replace(config, owner=info.fleet_id, node_id=info.node_id)
        config.credentials['events'] = RuntimeCredential(event_bus, 'incorrect-key')
        clients = []
        from nats.aio.client import Client
        class TrackedClient(Client):
            def __init__(self):
                super().__init__()
                clients.append(self)
        monkeypatch.setattr('nats.aio.client.Client', TrackedClient)
        with pytest.raises(Exception):
            await managed.create_service(config, workspace, state)
        assert len(clients) == 2 and all(client.is_closed for client in clients)
        assert not list(state.glob('.desktop-bus-*.creds'))


@pytest.mark.asyncio
async def test_owned_catalog_metadata_without_ambient_registry(tmp_path, monkeypatch):
    from pantheon.apps.builtin.desktop.files_binding import DesktopFilesBinding
    from pantheon.apps.builtin.desktop.data_server import LiveViewDataServer, DataServerConfig
    from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
    apps = tmp_path/'apps'
    app = apps/'custom'; app.mkdir(parents=True)
    (app/'app.json').write_text(json.dumps({'id': 'custom', 'name': 'Owned app',
        'entry': {'frontend': 'ui:custom'}, 'actions': [{'name': 'draw'}]}))
    def forbidden():
        pytest.fail('Owned metadata used global catalog')
    monkeypatch.setattr('pantheon.apps.registry.by_app_id', forbidden)
    service = DesktopToolSet(files_binding=DesktopFilesBinding(workspace=tmp_path,
        app_roots=[(apps, 'user')], data_roots=[tmp_path], server=LiveViewDataServer(config=DataServerConfig())))
    try:
        assert service._desktop_app_metadata('custom')['actions'] == ['draw']
        assert service._desktop_app_metadata('pkg:custom')['controllable']
        assert service._desktop_app_metadata('absent')['actions'] == []
    finally:
        await service.cleanup()


@pytest.mark.asyncio
async def test_cancelled_start_joins_cleanup_before_releasing_bus(tmp_path, event_bus, monkeypatch):
    config, workspace, state = configuration(tmp_path, event_bus, 'unused', event_bus)
    config.values['desktop']['fleet'] = {'auth': 'token'}
    config.credentials['fleet'] = RuntimeCredential(event_bus, 'test-events-key')
    original = managed.OwnedBus.connect.__func__
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    owners = []
    async def connect(cls, *args, **kwargs):
        if owners:
            entered.set()
            await asyncio.Future()
        owner = await original(cls, *args, **kwargs)
        original_close = owner._client.close
        async def held_close():
            closing.set()
            await release.wait()
            await original_close()
        owner._client.close = held_close
        owners.append(owner)
        return owner
    monkeypatch.setattr(managed.OwnedBus, 'connect', classmethod(connect))
    startup = asyncio.create_task(managed.create_service(config, workspace, state))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        startup.cancel()
        await asyncio.wait_for(closing.wait(), 5)
        startup.cancel()
        await asyncio.sleep(.02)
        assert not startup.done() and owners[0].is_connected
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await startup
        assert owners[0].is_closed
    finally:
        release.set()
        await asyncio.gather(startup, return_exceptions=True)
        for owner in owners:
            await owner.close()


@pytest.mark.asyncio
async def test_packaged_desktop_installed_configured_restarted_by_native_fleet(tmp_path, binaries, event_bus):
    from pantheon.apps.builtin.desktop.build_managed import build
    from pantheon.apps.client import AppClient
    from pantheon.apps.lifecycle import FleetLifecycle, ConfigurationBusy
    from pantheon.apps.resolver import AppInstanceResolver
    platform_id = ('darwin' if sys.platform == 'darwin' else 'linux') + '-' + {
        'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    package = build(tmp_path/'desktop-release', platform_id)
    # The installed artifact cannot import an Agent or global settings even if
    # the invoking development Python happens to have those modules available.
    vendor = package/'backend/_vendor/pantheon'
    assert not (vendor/'agent.py').exists() and not (vendor/'settings.py').exists()
    assert not (vendor/'chatroom').exists() and not (vendor/'factory').exists()
    workspace = tmp_path/'workspace'; workspace.mkdir()
    catalog = workspace/'apps'; catalog.mkdir()
    consumer_package(catalog/'model-consumer')
    (workspace/'data.txt').write_text('independent data')
    async with LocalFleet(tmp_path/'fleet-profile', binaries, workspace=workspace) as runtime:
        info = runtime.coordinates
        children = list(runtime._children)
        config, _, _ = configuration(tmp_path, info.nats, info.credentials.read_text(), event_bus)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(workspace), connection=nc)
        wire, client = FleetLifecycle(resolver), AppClient(nc, info.fleet_id)
        digest = None
        current = None
        async def action(name, generation=0, **kwargs):
            receipt = await wire.submit(info.node_id, name, digest, generation=generation, **kwargs)
            async with asyncio.timeout(180):
                while True:
                    status = await wire.status(info.node_id)
                    op = status['operations'][receipt['request']['operation_id']]
                    if op['state'] == 'succeeded':
                        return next((row for row in status['instances'].values() if row['digest'] == digest), None)
                    assert op['state'] in ('queued', 'running'), op
                    await asyncio.sleep(.1)
        async def invoke(method, *, allow_error=False, **args):
            response = await client.invoke(info.node_id, 'desktop', {
                'instance_id': current['instance_id'], 'revision': digest, 'generation': current['generation']},
                method, args, 20)
            assert not response.get('error'), response
            envelope = response['response']
            assert envelope.get('success'), envelope
            result = envelope['result']
            if not allow_error:
                assert not result.get('error'), result
            return result
        async def start(operation):
            prepared = await action('prepare_start', current['generation'] if current else 0, operation_id=operation)
            values = config.values['desktop']
            credentials = {alias: {'ref': 'node-secret://desktop-' + alias, 'endpoint': item.endpoint}
                           for alias, item in config.credentials.items()}
            async with asyncio.timeout(5):
                while True:
                    try:
                        await wire.configure(info.node_id, instance_id=prepared['instance_id'], revision=digest,
                            generation=prepared['generation'], preparation_id=operation,
                            components={'backend': {'values': {'desktop': values}, 'credentials': credentials}})
                        break
                    except ConfigurationBusy:
                        await asyncio.sleep(.05)
            return await action('start', prepared['generation'], start_preparation_id=operation)
        try:
            # Local administrator provisions only this isolated test node. Keys
            # travel through stdin, never command lines, logs or artifacts.
            for alias, credential in config.credentials.items():
                process = await asyncio.create_subprocess_exec(str(binaries.runner), 'credentials', 'ensure',
                    '--state-dir', str(runtime.root/'node'), '--fleet', info.fleet_id,
                    '--name', 'desktop-' + alias, '--endpoint', credential.endpoint, '--stdin',
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate(credential.key.encode())
                assert process.returncode == 0, 'Node rejected prepared bus credential: ' + stderr.decode()
            digest = await wire.stage(info.node_id, package)
            await action('install')
            current = await start('prepare-desktop')
            assert current['state'] == 'ready'
            assert (await invoke('desktop_app_lifecycle', node_id=info.node_id))['success']
            assert (await invoke('desktop_app_placement', action='options', app_id='model-consumer'))['success']
            window = await invoke('desktop_intent', kind='open', args={'app_id': 'files', 'title': 'Persisted'})
            assert window['success']
            # Use the host-owned workspace (DATA/workspace) as well as explicitly
            # prepared catalog roots. This exposes accidental source-cwd reliance.
            file = await invoke('serve_local_data', path=str(workspace/'data.txt'), allow_error=True)
            # Only configured catalogs plus the host workspace are served.
            assert not file['success']
            (catalog/'catalog.txt').write_text('independent catalog')
            file = await invoke('serve_local_data', path=str(catalog/'catalog.txt'), node_id=info.node_id)
            async with httpx.AsyncClient(trust_env=False) as http:
                assert (await http.get(file['url'])).text == 'independent catalog'
            first_generation = current['generation']
            current = await action('stop', first_generation)
            assert not list((runtime.root/'node').rglob('.desktop-bus-*.creds'))
            current = await start('prepare-desktop-reopen')
            assert current['generation'] > first_generation
            restored = await invoke('desktop_session_get')
            assert window['window_id'] in restored['session']['windows']
            current = await action('stop', current['generation'])
            await action('uninstall', current['generation'])
            current = None
        finally:
            try:
                if current is not None and current['state'] == 'ready':
                    await action('stop', current['generation'])
            finally:
                await resolver.close()
    assert_stopped(children, info)
