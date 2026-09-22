from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.models import manager as module
from test_model_services import deployment


def coordinator(monkeypatch, fail_at='', managed=False):
    row = deployment()
    old, new = row['binding']['revision'], 'b' * 64
    state = {'instances': {'instance': dict(instance_id='instance', digest=old, scope='model-mac',
        app_id='model-service', state='ready', generation=2, resources=[{'id': 'connector'}])}, 'operations': {}}
    if managed:
        row.update(mode='managed', engine_binding={**row['binding'], 'instance_id': 'engine', 'generation': 9})
        state['instances']['engine'] = dict(instance_id='engine', digest=old, scope='engine-mac',
            app_id='model-service', state='ready', generation=9, resources=[{'id': 'owned-engine'}])
    directory = SimpleNamespace(row=deepcopy(row), saves=[], failed=False)
    async def save(value):
        # The Hub accepts only real, started bindings (generation >= 1), even
        # while an update is pending. A copied generation-0 target stays local.
        assert value.get('binding', {}).get('generation', 1) >= 1
        assert value['revision'] == directory.row['revision'], 'CAS conflict'
        directory.row = deepcopy(value)
        directory.row['revision'] += 1
        directory.saves.append(deepcopy(directory.row))
        if fail_at == 'publish' and value['state'] == 'ready' and not directory.failed:
            directory.failed = True
            raise ConnectionError('Lost Hub acknowledgement')
        return deepcopy(directory.row)
    async def get(_): return deepcopy(directory.row)
    directory.save, directory.deployment = save, get
    lifecycle = SimpleNamespace(actions=[], failed=False)
    async def submit(node, action, digest, **args):
        assert node == 'mac-node' and args['scope'] == 'model-mac'
        lifecycle.actions.append(action)
        if action == 'stop':
            assert digest == old and args['generation'] == 2
            state['instances']['instance'].update(state='stopped', generation=3, resources=[])
        elif action == 'clone_data':
            assert args['data_source'] == {'digest': old, 'generation': 3}
            assert state['instances']['instance']['state'] == 'stopped'
            state['instances']['new'] = dict(instance_id='new', digest=new, scope='model-mac',
                app_id='model-service', state='stopped', generation=0, resources=[], data_source=args['data_source'])
        elif action == 'start':
            assert digest == new and args['generation'] == 0
            state['instances']['new'].update(state='ready', generation=1, resources=[{'id': 'new-connector'}])
        if action == fail_at and not lifecycle.failed:
            lifecycle.failed = True
            raise ConnectionError('Lost Fleet acknowledgement')
        return {'request': {'operation_id': action}}
    lifecycle.submit = submit
    lifecycle.stage = AsyncMock(return_value=new)
    lifecycle.status = AsyncMock(side_effect=lambda _: deepcopy(state))
    lifecycle.usage = AsyncMock()
    monkeypatch.setattr(module, 'FleetLifecycle', lambda _: lifecycle)
    manager = module.ModelServiceManager(client=directory, resolver=object())
    manager.node = AsyncMock(return_value={'capability': {'runtimes': {'app-data-clone': '1'}}})
    manager.wait = AsyncMock(side_effect=lambda *_: deepcopy(state))
    async def rpc(binding, method, args=None):
        if method == 'drain':
            assert binding['revision'] == old
            return {'safe_to_stop': True}
        assert method == 'status' and binding['revision'] == new
        return {'config_revision': row['config_revision']}
    manager.rpc = AsyncMock(side_effect=rpc)
    return manager, directory, lifecycle, state


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_at', ['', 'stop', 'clone_data', 'start', 'publish'])
async def test_connector_upgrade_preserves_identity_and_resumes_lost_ack(monkeypatch, fail_at):
    manager, directory, lifecycle, state = coordinator(monkeypatch, fail_at, managed=True)
    original_models = deepcopy(directory.row['models'])
    if fail_at:
        with pytest.raises(ConnectionError):
            await manager.upgrade_connector('mac')
        # A later Agent build must not silently replace the pending target.
        if fail_at != 'publish': lifecycle.stage.return_value = 'e' * 64
    result = await manager.upgrade_connector('mac')
    assert result['state'] == 'ready' and result['connector_update'] is None
    assert result['binding']['revision'] == 'b' * 64 and result['binding']['generation'] == 1
    assert result['models'] == original_models and result['config_revision'] == 'c' * 64
    assert result['engine_binding']['instance_id'] == 'engine'
    assert state['instances']['engine']['generation'] == 9
    assert lifecycle.actions.count('stop') == 1 and lifecycle.actions.count('start') == 1
    assert all(r['state'] == 'stopping' for r in directory.saves if r.get('connector_update'))
    assert all(r['binding']['revision'] == 'a' * 64 for r in directory.saves if r.get('connector_update'))
    assert all('credential' not in str(r) and 'endpoint' not in str(r) for r in directory.saves)


@pytest.mark.asyncio
async def test_upgrade_requires_fleet_support_and_fences_other_lifecycle_actions(monkeypatch):
    manager, directory, lifecycle, _ = coordinator(monkeypatch)
    manager.node.return_value = {'capability': {'runtimes': {}}}
    with pytest.raises(ValueError, match='Update Fleet'):
        await manager.upgrade_connector('mac')
    assert not lifecycle.actions and not directory.saves
    directory.row['connector_update'] = {'source': directory.row['binding'], 'target_revision': 'b' * 64}
    directory.row['state'] = 'stopping'
    for running in [True, False]:
        with pytest.raises(ValueError, match='Resume the pending'):
            await manager.set_running('mac', running)
    assert not lifecycle.actions


@pytest.mark.asyncio
async def test_upgrade_does_not_publish_changed_config_or_newer_target_generation(monkeypatch):
    manager, directory, lifecycle, state = coordinator(monkeypatch, 'start')
    with pytest.raises(ConnectionError): await manager.upgrade_connector('mac')
    state['instances']['new']['generation'] = 7
    with pytest.raises(ValueError, match='identity changed'): await manager.upgrade_connector('mac')
    assert directory.row['state'] == 'stopping'
    state['instances']['new']['generation'] = 1
    manager.rpc = AsyncMock(return_value={'config_revision': 'f' * 64})
    with pytest.raises(ValueError, match='configuration differs'): await manager.upgrade_connector('mac')
    assert directory.row['state'] == 'stopping'
    assert lifecycle.actions.count('start') == 1
