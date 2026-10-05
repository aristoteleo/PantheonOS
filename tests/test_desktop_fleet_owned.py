"""Explicit Desktop owner control over a real Controller, NATS and Fleet node."""
import asyncio
import builtins
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nats
import pytest

from pantheon.apps.builtin.desktop.data_server import DataServerConfig, LiveViewDataServer
from pantheon.apps.builtin.desktop.files_binding import DesktopFilesBinding
from pantheon.apps.builtin.desktop.fleet_binding import DesktopFleetBinding
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import consumer_package


def files(root, node='node'):
    return DesktopFilesBinding(workspace=root, app_roots=[(root/'apps', 'user')],
        data_roots=[root], server=LiveViewDataServer(config=DataServerConfig()), node_id=node)


async def settled(desktop, node, receipt):
    assert receipt['success'], receipt
    operation_id = receipt['operation']['request']['operation_id']
    async with asyncio.timeout(30):
        while True:
            state = await desktop.desktop_app_lifecycle(node)
            assert state['success'], state
            operation = state['operations'][operation_id]
            if operation['state'] == 'succeeded':
                return state
            assert operation['state'] in ('queued', 'running'), operation
            await asyncio.sleep(.05)


@pytest.mark.asyncio
async def test_desktop_owned_fleet_install_lease_and_close_without_ambient_identity(tmp_path, binaries, monkeypatch):
    (tmp_path/'apps').mkdir()
    consumer_package(tmp_path/'apps/model-consumer')
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://must-not-join.invalid')
    monkeypatch.setenv('FLEET_KEY', 'wrong-ambient-identity')
    async with LocalFleet(tmp_path/'profile', binaries, workspace=tmp_path) as runtime:
        info = runtime.coordinates
        children = list(runtime._children)
        async def create(root):
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            binding = DesktopFleetBinding(fleet_id=info.fleet_id, node_id=info.node_id,
                user_seed=info.fleet_id, workspace=root, connection=nc)
            return nc, DesktopToolSet(files_binding=files(root, info.node_id), fleet_binding=binding)
        nc, desktop = await create(tmp_path)
        sibling_root = tmp_path/'sibling'; sibling_root.mkdir()
        other_nc, other = await create(sibling_root)
        # Scope the import guard to the App calls. Test infrastructure is allowed
        # to configure the isolated profile, never the Desktop implementation.
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name in ('pantheon.agent', 'pantheon.settings', 'pantheon.chatroom', 'pantheon.factory'):
                raise AssertionError('Desktop consulted ambient Agent state: ' + name)
            return original_import(name, *args, **kwargs)
        def no_shared(*args, **kwargs):
            raise AssertionError('Desktop used the process-wide resolver')
        try:
            with monkeypatch.context() as bound:
                bound.setattr(builtins, '__import__', guarded)
                bound.setattr('pantheon.apps.resolver.get_shared_resolver', no_shared)
                options = await desktop.desktop_app_placement('options', 'model-consumer')
                assert options['success'] and options['nodes'][0]['node_id'] == info.node_id, options
                assert not options['nodes'][0]['reason']
                saved = await desktop.desktop_app_placement('set', 'model-consumer', info.node_id)
                assert saved['success'], saved
                assert (await other.desktop_app_placement())['defaults'] == {}
                assert not (await desktop.desktop_app_lifecycle('not-in-this-fleet'))['success']
                install = await desktop.desktop_app_install_on_node('model-consumer', info.node_id,
                    operation_id='desktop-install')
                state = await settled(desktop, info.node_id, install)
                digest = install['digest']
                start = await desktop.desktop_app_lifecycle(info.node_id, 'start', digest,
                    operation_id='desktop-start')
                state = await settled(desktop, info.node_id, start)
                instance = next(row for row in state['instances'].values() if row['digest'] == digest)
                identity = dict(node_id=info.node_id, instance_id=instance['instance_id'],
                    revision=digest, generation=instance['generation'])
                record = await desktop.desktop_app_placement('binding', 'model-consumer', binding=identity)
                assert record['success'] and record['manifest']['id'] == 'model-consumer', record
                lease = await desktop.desktop_app_usage(**identity, action='lease', lease_id='viewport-test')
                assert lease['success'], lease
                assert not (await desktop.desktop_app_usage(**{**identity, 'generation': identity['generation'] + 1},
                    action='lease', lease_id='stale'))['success']
                assert (await desktop.desktop_app_usage(**identity, action='lease', lease_id='viewport-test', release=True))['success']
                # Closing Desktop control leaves the independently running App
                # and another Desktop owner connection intact.
                await desktop.cleanup()
                assert nc.is_closed and other_nc.is_connected
                assert not (await desktop.desktop_app_lifecycle(info.node_id))['success']
                assert not (await desktop.desktop_app_usage(**identity, action='lease', lease_id='closed'))['success']
                snapshot = await other.desktop_app_lifecycle(info.node_id)
                assert snapshot['success'] and snapshot['instances'][identity['instance_id']]['state'] == 'ready'
                stop = await other.desktop_app_lifecycle(info.node_id, 'stop', digest,
                    generation=identity['generation'], operation_id='desktop-stop')
                await settled(other, info.node_id, stop)
                uninstall = await other.desktop_app_lifecycle(info.node_id, 'uninstall', digest,
                    operation_id='desktop-uninstall')
                await settled(other, info.node_id, uninstall)
        finally:
            await desktop.cleanup()
            await other.cleanup()
        assert nc.is_closed and other_nc.is_closed
    assert_stopped(children, info)


@pytest.mark.asyncio
async def test_retired_binding_refuses_new_work_but_retries_failed_close(tmp_path):
    connection = SimpleNamespace(is_connected=True, close=AsyncMock(side_effect=[OSError('try again'), None]))
    binding = DesktopFleetBinding(fleet_id='fleet', node_id='node', user_seed='owner',
        workspace=tmp_path, connection=connection)
    with pytest.raises(OSError):
        await binding.close()
    with pytest.raises(RuntimeError, match='closed'):
        binding.resolver
    await binding.close()
    assert connection.close.await_count == 2


@pytest.mark.asyncio
async def test_owned_disconnect_does_not_join_ambient_fleet(tmp_path, monkeypatch):
    connection = SimpleNamespace(is_connected=True, close=AsyncMock())
    binding = DesktopFleetBinding(fleet_id='fleet', node_id='node', user_seed='owner',
        workspace=tmp_path, connection=connection)
    service = DesktopToolSet(fleet_binding=binding, files_binding=files(tmp_path))
    connection.is_connected = False
    connect = AsyncMock(side_effect=AssertionError('must not join another Fleet'))
    monkeypatch.setattr(nats, 'connect', connect)
    try:
        result = await service.desktop_app_lifecycle('node')
        assert not result['success'] and 'Explicit Fleet connection' in result['error']
        connect.assert_not_called()
    finally:
        await service.cleanup()


@pytest.mark.parametrize('change', ['connection', 'node_id', 'fleet_id', 'user_seed', 'workspace'])
def test_incomplete_fleet_binding_is_rejected(tmp_path, change):
    args = dict(fleet_id='fleet', node_id='node', user_seed='owner', workspace=tmp_path,
                connection=SimpleNamespace(is_connected=True))
    args[change] = None if change == 'connection' else (Path('relative') if change == 'workspace' else '')
    with pytest.raises(ValueError):
        DesktopFleetBinding(**args)
