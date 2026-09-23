from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.models import manager as module, engine_upgrade
from test_model_services import deployment


TARGET = 'ollama-0.34.2-darwin'
TARGET_ID = hashlib.sha256(('owner\0mac-node\0' + 'b' * 64 + '\0engine-mac').encode()).hexdigest()[:32]


def setup(monkeypatch, lost=''):
    row = deployment()
    row.update(mode='managed', managed=dict(recipe_id='ollama-0.34.1-darwin', context_length=4096,
        parallel=1, keep_alive_seconds=300, resources=dict(memory_bytes=4 << 30,
        devices=[dict(id='apple-metal', backend='metal', memory_bytes=4 << 30, exclusive=False)])),
        engine_binding={**row['binding'], 'instance_id': 'engine', 'revision': 'e' * 64})
    state = {'protocol': 1, 'owner': 'owner', 'node_id': 'mac-node', 'instances': {}, 'operations': {}}
    for key, scope in [('binding', 'model-mac'), ('engine_binding', 'engine-mac')]:
        b = row[key]
        state['instances'][b['instance_id']] = dict(instance_id=b['instance_id'], digest=b['revision'],
            generation=b['generation'], scope=scope, app_id='model-service', state='ready',
            resources=[dict(component='backend', endpoints={'http': 'http://127.0.0.1:32123'})],
            reservations=[dict(memory_bytes=4 << 30)])
    directory = SimpleNamespace(row=deepcopy(row), saves=[], lost=False)
    def fail(point):
        if lost == point and not directory.lost:
            directory.lost = True
            raise ConnectionError('Lost ' + point + ' acknowledgement')
    async def save(value):
        assert value['revision'] == directory.row['revision']
        assert value['binding']['generation'] >= 1 and value['engine_binding']['generation'] >= 1
        directory.row = deepcopy(value)
        directory.row['revision'] += 1
        directory.saves.append(deepcopy(directory.row))
        fail('publish' if value['state'] == 'ready' else 'intent')
        return deepcopy(directory.row)
    async def get(_): return deepcopy(directory.row)
    directory.save, directory.deployment = save, get
    lifecycle = SimpleNamespace(actions=[])
    async def submit(node, action, digest, **args):
        assert node == row['node_id'] and args['scope'] == 'engine-mac'
        lifecycle.actions.append(action)
        operation_id = args.get('operation_id', str(len(lifecycle.actions)))
        if action == 'install':
            assert digest == 'b' * 64
        elif action == 'stop':
            assert digest == 'e' * 64 and args['generation'] == row['engine_binding']['generation']
            assert not connector.accepting
            state['instances']['engine'].update(state='stopped', generation=args['generation'] + 1,
                resources=[], reservations=[])
        elif action == 'start':
            assert args['generation'] == 0 and digest == 'b' * 64
            assert not state['instances']['engine']['reservations']
            state['instances'][TARGET_ID] = dict(instance_id=TARGET_ID, digest=digest, scope='engine-mac',
                app_id='model-service', state='ready', generation=1,
                resources=[dict(component='backend', endpoints={'http': 'http://127.0.0.1:33221'})],
                reservations=[dict(memory_bytes=4 << 30)])
        else:
            pytest.fail('Engine update must never clone/download or stop the connector')
        op = {'request': {'operation_id': operation_id, 'action': action, 'digest': digest, **args}, 'state': 'succeeded'}
        state['operations'][operation_id] = op
        fail(action)
        return deepcopy(op)
    lifecycle.submit = submit
    lifecycle.stage = AsyncMock(return_value='b' * 64)
    lifecycle.status = AsyncMock(side_effect=lambda _: deepcopy(state))
    lifecycle.usage = AsyncMock()
    for target_module in (module, engine_upgrade):
        monkeypatch.setattr(target_module, 'FleetLifecycle', lambda _: lifecycle)
    manager = module.ModelServiceManager(client=directory, resolver=object())
    manager.node = AsyncMock(return_value={'capability': {'os': 'darwin', 'arch': 'arm64'}})
    async def wait(_, op):
        assert op['state'] == 'succeeded', 'Failed start must not be replayed'
        return deepcopy(state)
    manager.wait = AsyncMock(side_effect=wait)
    connector = SimpleNamespace(config=row['config_revision'], accepting=True, prepared=True, models=deepcopy(row['models']))
    async def rpc(binding, method, args=None):
        assert binding == row['binding']
        if method == 'status': return dict(config_revision=connector.config, recovery_protocol=1)
        if method == 'engines_catalog': return {'recipes': [dict(id=TARGET, prepared=connector.prepared)]}
        if method == 'drain':
            connector.accepting = False
            fail('drain')
            return {'safe_to_stop': True}
        if method == 'preview_configuration':
            assert args['managed']['recipe_id'] == TARGET and args['managed']['context_length'] == 4096
            assert args['config']['endpoint'] == 'http://127.0.0.1:33221'
            return {'config_revision': 'd' * 64}
        if method == 'configure':
            assert args['expected_revision'] == connector.config
            connector.config = 'd' * 64
            fail('configure')
            return {'config_revision': connector.config}
        if method == 'discover': return {'config_revision': connector.config, 'models': connector.models}
        if method == 'resume':
            assert args['config_revision'] == connector.config
            connector.accepting = True
            fail('resume')
            return {'config_revision': connector.config}
        pytest.fail(method)
    manager.rpc = AsyncMock(side_effect=rpc)
    return manager, directory, lifecycle, state, connector


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['', 'install', 'intent', 'drain', 'stop', 'start', 'configure', 'resume', 'publish'])
async def test_engine_upgrade_resumes_exact_target_at_each_lost_ack(monkeypatch, lost):
    manager, directory, lifecycle, state, connector = setup(monkeypatch, lost)
    before = deepcopy(directory.row)
    if lost:
        with pytest.raises(ConnectionError): await manager.upgrade_engine('mac', TARGET)
        if lost not in {'install', 'publish'}:
            lifecycle.stage.return_value = 'f' * 64  # Agent update must not re-plan a pending target.
    result = await manager.upgrade_engine('mac', TARGET)
    assert result['state'] == 'ready' and result['engine_update'] is None
    assert result['binding'] == before['binding'] and result['models'] == before['models']
    assert result['managed'] == {**before['managed'], 'recipe_id': TARGET}
    assert result['engine_binding']['instance_id'] == TARGET_ID and result['engine_binding']['generation'] == 1
    assert result['config_revision'] == connector.config == 'd' * 64 and connector.accepting
    assert lifecycle.actions.count('start') == lifecycle.actions.count('stop') == 1
    assert state['instances']['instance']['generation'] == before['binding']['generation']
    assert not state['instances']['engine']['reservations']
    assert all(s['state'] == 'stopping' and s['managed'] == before['managed']
               and s['engine_binding'] == before['engine_binding'] for s in directory.saves if s.get('engine_update'))
    # Repeat after success is an artifact no-op, not another engine restart.
    lifecycle.stage.return_value = 'b' * 64
    await manager.upgrade_engine('mac', TARGET)
    assert lifecycle.actions.count('start') == 1


@pytest.mark.asyncio
async def test_upgrade_preflight_and_drain_keep_existing_engine_alive(monkeypatch):
    manager, directory, lifecycle, state, connector = setup(monkeypatch)
    connector.prepared = False
    with pytest.raises(ValueError, match='Prepare the selected'):
        await manager.upgrade_engine('mac', TARGET)
    assert not lifecycle.actions and not directory.saves
    connector.prepared = True
    manager.drain_binding = AsyncMock(side_effect=RuntimeError('Still draining'))
    with pytest.raises(RuntimeError, match='draining'): await manager.upgrade_engine('mac', TARGET)
    assert lifecycle.actions == ['install']
    assert state['instances']['engine']['state'] == 'ready'
    assert directory.row['engine_update']['phase'] == 'draining'
    for running in (True, False):
        with pytest.raises(ValueError, match='pending'): await manager.set_running('mac', running)
    with pytest.raises(ValueError): await manager.recover('mac')
    with pytest.raises(ValueError): await manager.upgrade_connector('mac')
    with pytest.raises(ValueError): await manager.discover('mac')
    with pytest.raises(ValueError, match='pinned'): await manager.upgrade_engine('mac', 'another-version')
    assert lifecycle.actions == ['install']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['target-generation', 'start-operation', 'failed-start', 'config', 'models', 'source-generation'])
async def test_upgrade_refuses_unrelated_or_failed_targets(monkeypatch, change):
    manager, directory, lifecycle, state, connector = setup(monkeypatch, 'start')
    with pytest.raises(ConnectionError): await manager.upgrade_engine('mac', TARGET)
    op = next(o for o in state['operations'].values() if o['request']['action'] == 'start')
    if change == 'target-generation': state['instances'][TARGET_ID]['generation'] = 3
    elif change == 'start-operation': op['request']['generation'] = 2
    elif change == 'failed-start': op['state'] = 'failed'
    elif change == 'config': connector.config = 'f' * 64
    elif change == 'models': connector.models = []
    elif change == 'source-generation': state['instances']['engine'].update(state='ready', generation=10)
    with pytest.raises((ValueError, AssertionError)): await manager.upgrade_engine('mac', TARGET)
    assert directory.row['state'] == 'stopping' and not connector.accepting
    assert lifecycle.actions.count('start') == lifecycle.actions.count('stop') == 1


@pytest.mark.asyncio
async def test_attached_engine_is_never_updated(monkeypatch):
    manager, directory, lifecycle, _, _ = setup(monkeypatch)
    directory.row.update(mode='attached', engine_binding=None, managed=None)
    with pytest.raises(ValueError, match='owned engine'): await manager.upgrade_engine('mac', TARGET)
    assert not lifecycle.actions and not directory.saves


@pytest.mark.asyncio
async def test_restart_uses_pinned_installation_without_repackaging(monkeypatch):
    manager, directory, _, _, _ = setup(monkeypatch)
    row = directory.row
    row['state'] = 'stopped'
    row['managed']['recipe_id'] = TARGET
    async def ensure(value, *, binding_key='binding', **kwargs):
        assert 'directory' not in kwargs
        return {**value[binding_key], 'generation': 4}
    manager.ensure = AsyncMock(side_effect=ensure)
    manager.managed_configuration = AsyncMock(return_value={'config': {}})
    manager.rpc = AsyncMock(side_effect=lambda _, method, *args: (
        {'recipes': [{'id': TARGET, 'prepared': True}]} if method == 'engines_catalog'
        else {'config_revision': 'd' * 64}))
    monkeypatch.setattr(engine_upgrade.managed, 'package', lambda *args: pytest.fail('Restart repackaged engine'))
    result = await manager.set_running('mac', True)
    assert result['engine_binding']['revision'] == 'e' * 64
    assert result['engine_binding']['generation'] == 4 and result['state'] == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('selection', ['current', 'invalid', 'unprepared', 'already-used', 'stage-failed'])
async def test_idle_upgrade_preflight_never_wakes_or_changes_policy(monkeypatch, selection):
    from pantheon.models import recovery
    manager, directory, lifecycle, state, connector = setup(monkeypatch)
    directory.row['engine_idle'] = {'phase': 'enabled', 'policy_revision': 1}
    state['instances']['engine'].update(state='stopped', generation=3, resources=[], reservations=[])
    recipe = TARGET
    error = ValueError
    if selection == 'current':
        lifecycle.stage.return_value = directory.row['engine_binding']['revision']
    elif selection == 'invalid':
        recipe = 'not-a-recipe'
    elif selection == 'unprepared':
        connector.prepared = False
    elif selection == 'already-used':
        state['instances'][TARGET_ID] = dict(digest='b' * 64, scope='engine-mac', generation=1, state='stopped')
    elif selection == 'stage-failed':
        lifecycle.stage.side_effect = ConnectionError('stage unavailable')
        error = ConnectionError
    recover = AsyncMock(side_effect=AssertionError('Preflight woke an idle engine'))
    monkeypatch.setattr(recovery, 'recover_locked', recover)
    before, fleet_before = deepcopy(directory.row), deepcopy(state)
    if selection == 'current':
        assert await manager.upgrade_engine('mac', recipe) == before
    else:
        with pytest.raises(error):
            await manager.upgrade_engine('mac', recipe)
    recover.assert_not_awaited()
    assert not lifecycle.actions and not directory.saves
    assert directory.row == before and state == fleet_before
    assert all(c.args[1] in {'status', 'engines_catalog'} for c in manager.rpc.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [False, True])
async def test_real_idle_engine_upgrade_recovers_after_preflight_and_rechecks_binding(monkeypatch, changed):
    from pantheon.models import recovery
    manager, directory, lifecycle, state, _ = setup(monkeypatch)
    directory.row['engine_idle'] = {'phase': 'enabled', 'policy_revision': 1}
    async def recover(_, row):
        lifecycle.stage.assert_awaited_once()
        assert not lifecycle.actions  # preflight precedes any lifecycle mutation
        if changed:
            state['instances']['engine']['generation'] += 10
        return row
    recover_mock = AsyncMock(side_effect=recover)
    monkeypatch.setattr(recovery, 'recover_locked', recover_mock)
    if changed:
        with pytest.raises(ValueError):
            await manager.upgrade_engine('mac', TARGET)
        assert not lifecycle.actions and not directory.saves
    else:
        result = await manager.upgrade_engine('mac', TARGET)
        assert result['state'] == 'ready' and result['engine_binding']['instance_id'] == TARGET_ID
        assert lifecycle.actions == ['install', 'stop', 'start']
    recover_mock.assert_awaited_once()
