from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.models import manager as module, recovery
from test_model_services import deployment, connector_module


def setup(monkeypatch, dead=False, managed=False, lost=''):
    row = deployment()
    state = {'instances': {}, 'operations': {}, 'installations': {
        row['binding']['revision']: {'definition': {'version': '0.1.2'}}}}
    if managed:
        row.update(mode='managed', engine_binding={**row['binding'], 'instance_id': 'engine', 'revision': 'e' * 64})
    for key, scope in [('binding', 'model-mac'), ('engine_binding', 'engine-mac')]:
        if b := row.get(key):
            state['instances'][b['instance_id']] = dict(instance_id=b['instance_id'], digest=b['revision'],
                scope=scope, app_id='model-service', state='unknown', generation=b['generation'],
                resources=[] if dead else [{'id': key}])
    directory = SimpleNamespace(row=deepcopy(row), saves=[], lost=False)
    async def save(value):
        assert value['revision'] == directory.row['revision']
        directory.row = deepcopy(value)
        directory.row['revision'] += 1
        directory.saves.append(deepcopy(directory.row))
        if lost == 'publish' and value['state'] == 'ready' and not directory.lost:
            directory.lost = True
            raise ConnectionError('Lost publish acknowledgement')
        return deepcopy(directory.row)
    async def get(_): return deepcopy(directory.row)
    directory.save, directory.deployment = save, get
    lifecycle = SimpleNamespace(actions=[], lost=False)
    async def submit(node, action, digest, **args):
        assert node == row['node_id']
        current = next(i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == args['scope'])
        assert current['generation'] == args['generation']
        lifecycle.actions.append((action, args['scope']))
        if action == 'recover':
            if current['resources']:
                current['state'] = 'ready'
            elif current['state'] != 'stopped':
                current.update(state='stopped', generation=current['generation'] + 1)
        elif action == 'start':
            current.update(state='ready', generation=current['generation'] + 1, resources=[{'id': 'new'}])
        else:
            pytest.fail('recovery must not install/download/stop')
        operation_id = args.get('operation_id', str(len(lifecycle.actions)))
        op = {'request': {'operation_id': operation_id, 'action': action, 'digest': digest, **args}, 'state': 'succeeded'}
        state['operations'][operation_id] = op
        if lost == action and not lifecycle.lost:
            lifecycle.lost = True
            raise ConnectionError('Lost Fleet acknowledgement')
        return deepcopy(op)
    lifecycle.submit = submit
    lifecycle.status = AsyncMock(side_effect=lambda _: deepcopy(state))
    lifecycle.usage = AsyncMock()
    monkeypatch.setattr(module, 'FleetLifecycle', lambda _: lifecycle)
    monkeypatch.setattr(recovery, 'FleetLifecycle', lambda _: lifecycle)
    manager = module.ModelServiceManager(client=directory, resolver=object())
    manager.node = AsyncMock(return_value={'capability': {'runtimes': {'app-recovery': '1'}}})
    manager.wait = AsyncMock(side_effect=lambda *_: deepcopy(state))
    manager.managed_configuration = AsyncMock(return_value={'config': {'engine': 'ollama', 'endpoint': 'http://127.0.0.1:9999'}})
    connector = SimpleNamespace(revision=row['config_revision'], accepting=True, lost=False)
    async def rpc(binding, method, args=None):
        current = state['instances'][binding['instance_id']]
        assert binding['generation'] == current['generation'] and current['state'] == 'ready'
        if method == 'status':
            return {'config_revision': connector.revision, 'recovery_protocol': 1}
        if method == 'drain':
            connector.accepting = False
            return {'safe_to_stop': True}
        if method == 'preview_configuration': return {'config_revision': 'f' * 64}
        if method == 'configure':
            assert args['expected_revision'] == connector.revision
            connector.revision = 'f' * 64
        if method == 'resume':
            assert args['config_revision'] == connector.revision
            connector.accepting = True
        if lost == method and not connector.lost:
            connector.lost = True
            raise ConnectionError('Lost connector acknowledgement')
        return {'config_revision': connector.revision}
    manager.rpc = AsyncMock(side_effect=rpc)
    return manager, directory, lifecycle, state, connector


@pytest.mark.asyncio
@pytest.mark.parametrize('dead', [False, True])
@pytest.mark.parametrize('managed', [False, True])
async def test_recovery_preserves_pinned_config_and_models_without_downloads(monkeypatch, dead, managed):
    manager, directory, lifecycle, state, connector = setup(monkeypatch, dead, managed)
    original = deepcopy(directory.row)
    result = await manager.recover('mac')
    assert result['state'] == 'ready' and result['recovery'] is None
    assert result['models'] == original['models']
    assert result['binding']['revision'] == original['binding']['revision']
    assert result['binding']['generation'] == (4 if dead else 2)
    assert connector.accepting
    assert sum(action == 'start' for action, _ in lifecycle.actions) == ((2 if managed else 1) if dead else 0)
    assert all(r['state'] == 'recovering' for r in directory.saves if r.get('recovery'))
    assert result['config_revision'] == ('f' * 64 if managed else original['config_revision'])
    if not managed:
        assert all(scope == 'model-mac' for _, scope in lifecycle.actions)


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['recover', 'start', 'configure', 'resume', 'publish'])
async def test_recovery_resumes_after_lost_acknowledgement(monkeypatch, lost):
    manager, directory, lifecycle, state, _ = setup(monkeypatch, dead=True, managed=True, lost=lost)
    with pytest.raises(ConnectionError): await manager.recover('mac')
    if lost == 'publish':
        assert directory.row['state'] == 'ready' and not directory.row['recovery']
    result = await manager.recover('mac')
    assert result['state'] == 'ready' and not result['recovery']
    assert sum(action == 'start' for action, _ in lifecycle.actions) == 2


@pytest.mark.asyncio
async def test_recovery_fences_generation_config_and_conflicting_operations(monkeypatch):
    manager, directory, lifecycle, state, connector = setup(monkeypatch, dead=True, lost='start')
    with pytest.raises(ConnectionError): await manager.recover('mac')
    state['instances']['instance']['generation'] += 1
    with pytest.raises(ValueError, match='generation'): await manager.recover('mac')
    for running in [True, False]:
        with pytest.raises(ValueError, match='Resume service recovery'): await manager.set_running('mac', running)
    with pytest.raises(ValueError, match='Resume service recovery'): await manager.upgrade_connector('mac')
    state['instances']['instance']['generation'] -= 1
    connector.revision = 'd' * 64
    with pytest.raises(ValueError, match='configuration changed'): await manager.recover('mac')
    assert directory.row['state'] == 'recovering' and not connector.accepting


@pytest.mark.asyncio
async def test_old_connector_and_fleet_are_rejected_before_any_side_effect(monkeypatch):
    manager, directory, lifecycle, state, _ = setup(monkeypatch)
    manager.node.return_value = {'capability': {'runtimes': {}}}
    with pytest.raises(ValueError, match='Update Fleet'): await manager.recover('mac')
    manager.node.return_value = {'capability': {'runtimes': {'app-recovery': '1'}}}
    state['installations'][directory.row['binding']['revision']]['definition']['version'] = '0.1.1'
    with pytest.raises(ValueError, match='Update the connector'): await manager.recover('mac')
    assert not lifecycle.actions and not directory.saves


def test_connector_resume_and_configuration_compare_and_swap(tmp_path):
    connector = connector_module.Connector(tmp_path)
    config = {'engine': 'ollama', 'endpoint': 'http://127.0.0.1:11434'}
    before = connector.preview_configuration(config)
    assert connector.config is None and not connector.path.exists()
    assert connector.configure(config) == before
    connector.drain()
    with pytest.raises(ValueError, match='Configuration changed'):
        connector.resume('wrong')
    with pytest.raises(ValueError, match='Configuration changed'):
        connector.configure(config, expected_revision='wrong')
    assert not connector.accepting
    assert connector.resume(before['config_revision'])['accepting']
    connector.drain()
    connector.maintenance = True
    with pytest.raises(ValueError, match='active requests'):
        connector.resume(before['config_revision'])
    assert not connector.accepting
