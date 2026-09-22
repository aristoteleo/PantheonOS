from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.models import manager as module, operation_stop
from test_model_services import deployment


def setup(monkeypatch, kind='recovery', managed=False, replacement=True, lost=''):
    row = deployment()
    row['mode'] = 'managed' if managed else 'attached'
    if managed:
        row.update(managed={'recipe_id': 'old'}, engine_binding={**row['binding'],
            'instance_id': 'engine', 'revision': 'e' * 64})
    row['state'] = 'recovering' if kind == 'recovery' else 'stopping'
    pending = dict(operation_id='1' * 32, binding=deepcopy(row['binding']),
        engine_binding=deepcopy(row.get('engine_binding')))
    if kind != 'recovery':
        pending = dict(operation_id='1' * 32,
            source=deepcopy(row['engine_binding' if kind == 'engine_update' else 'binding']),
            target_revision='b' * 64, target_instance_id='replacement', target_config={'recipe_id': 'new'})
    row[kind] = pending
    state = dict(instances={}, operations={})
    for key, scope in [('binding', 'model-mac'), ('engine_binding', 'engine-mac')]:
        if not row.get(key): continue
        b = row[key]
        state['instances'][b['instance_id']] = dict(instance_id=b['instance_id'], scope=scope,
            digest=b['revision'], generation=b['generation'], app_id='model-service',
            state='ready', resources=[{'component': 'backend'}], reservations=[{'memory_bytes': 4096}])
    if kind != 'recovery' and replacement:
        key = 'engine' if kind == 'engine_update' else 'instance'
        original = state['instances'][key]
        original.update(state='stopped', generation=3, resources=[], reservations=[])
        state['instances']['replacement'] = dict(instance_id='replacement', scope=original['scope'],
            digest='b' * 64, generation=1, app_id='model-service', state='failed', resources=[],
            reservations=[{'memory_bytes': 4096}],
            data_source={'digest': original['digest'], 'generation': 3})
        state['operations']['engine-start-' + pending['operation_id']] = dict(state='failed',
            request=dict(action='start', scope=original['scope'], digest='b' * 64, generation=0))
    directory = SimpleNamespace(row=deepcopy(row), saves=[], lost=False)
    async def get(_): return deepcopy(directory.row)
    async def save(value):
        assert value['revision'] == directory.row['revision']
        directory.row = deepcopy(value)
        directory.row['revision'] += 1
        directory.saves.append(deepcopy(directory.row))
        phase = 'publish' if value['state'] == 'stopped' else 'intent'
        if lost == phase and not directory.lost:
            directory.lost = True
            raise ConnectionError('Lost ' + phase)
        return deepcopy(directory.row)
    directory.deployment, directory.save = get, save
    lifecycle = SimpleNamespace(actions=[], lost=False)
    async def submit(node, action, digest, **args):
        assert directory.row.get('operation_stop'), 'Persist stop intent before any side effect'
        assert action in {'stop', 'recover'}, 'Stop cannot start/install/download an engine'
        current = next(i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == args['scope'])
        assert current['generation'] == args['generation']
        lifecycle.actions.append((action, current['instance_id']))
        current.update(state='stopped', generation=current['generation'] + 1, resources=[], reservations=[])
        op = dict(state='succeeded', request=dict(action=action, digest=digest, **args))
        state['operations'][str(len(lifecycle.actions))] = op
        if (lost == action or (lost == 'stop' and action == 'recover')) and not lifecycle.lost:
            lifecycle.lost = True
            raise ConnectionError('Lost ' + action)
        return deepcopy(op)
    lifecycle.submit = submit
    lifecycle.status = AsyncMock(side_effect=lambda _: deepcopy(state))
    monkeypatch.setattr(operation_stop, 'FleetLifecycle', lambda _: lifecycle)
    manager = module.ModelServiceManager(client=directory, resolver=object())
    manager.node = AsyncMock()
    manager.drain_binding = AsyncMock()
    manager.wait = AsyncMock(side_effect=lambda *_: deepcopy(state))
    return manager, directory, lifecycle, state


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,managed', [('recovery', False), ('recovery', True),
    ('connector_update', False), ('connector_update', True), ('engine_update', True)])
@pytest.mark.parametrize('lost', ['', 'intent', 'stop', 'publish'])
async def test_stop_retains_data_and_releases_owned_resources_after_lost_ack(monkeypatch, kind, managed, lost):
    manager, directory, lifecycle, state = setup(monkeypatch, kind, managed, lost=lost)
    original = deepcopy(directory.row)
    if lost:
        with pytest.raises(ConnectionError): await manager.stop_operation('mac', directory.row['revision'])
    result = await manager.stop_operation('mac', directory.row['revision'])
    assert result['state'] == 'stopped' and not any(result.get(k) for k in operation_stop.PENDING)
    assert result['operation_stop'] is None and result['last_operation_stop']['kind'] == kind
    assert result['models'] == original['models'] and result['config_revision'] == original['config_revision']
    assert all(operation_stop.stopped(i) for i in state['instances'].values())
    assert len(lifecycle.actions) == len(set(lifecycle.actions)), 'Lost acknowledgement must not repeat stop'
    if kind == 'engine_update':
        assert result['managed'] == {'recipe_id': 'new'} and result['engine_binding']['instance_id'] == 'replacement'
    if kind == 'connector_update': assert result['binding']['instance_id'] == 'replacement'
    if not managed: assert all(i != 'engine' for _, i in lifecycle.actions)
    if managed:
        connector_stops = [n for n, (_, i) in enumerate(lifecycle.actions) if state['instances'][i]['scope'] == 'model-mac']
        engine_stops = [n for n, (_, i) in enumerate(lifecycle.actions) if state['instances'][i]['scope'] == 'engine-mac']
        assert max(connector_stops) < min(engine_stops)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['connector_update', 'engine_update'])
async def test_unused_or_absent_replacement_keeps_original_version(monkeypatch, kind):
    for exists in (False, True):
        manager, directory, lifecycle, state = setup(monkeypatch, kind, True, replacement=exists)
        if exists: state['instances']['replacement'].update(state='stopped', generation=0, resources=[], reservations=[])
        result = await manager.stop_operation('mac', directory.row['revision'])
        assert result['managed']['recipe_id'] == 'old'
        assert result['binding']['instance_id'] == 'instance'
        assert result['engine_binding']['instance_id'] == 'engine'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revision', 'generation', 'identity', 'active', 'unplanned', 'clone', 'start'])
async def test_conflicting_state_is_rejected_before_stopping_any_process(monkeypatch, change):
    kind = 'engine_update' if change == 'start' else 'connector_update'
    manager, directory, lifecycle, state = setup(monkeypatch, kind, True)
    revision = directory.row['revision']
    if change == 'revision': revision -= 1
    elif change == 'generation': state['instances']['engine']['generation'] += 5
    elif change == 'identity': state['instances']['engine']['scope'] = 'unrelated'
    elif change == 'active': state['operations']['pending'] = dict(state='running', request={'scope': 'model-mac'})
    elif change == 'unplanned': state['instances']['other'] = dict(state['instances']['engine'], instance_id='other')
    elif change == 'clone': state['instances']['replacement']['data_source']['generation'] = 99
    elif change == 'start': state['operations'].clear()
    with pytest.raises((ValueError, RuntimeError)): await manager.stop_operation('mac', revision)
    assert not lifecycle.actions


@pytest.mark.asyncio
async def test_recovery_start_failure_generation_and_recover_timeout(monkeypatch):
    manager, directory, lifecycle, state = setup(monkeypatch)
    source = state['instances']['instance']
    source.update(state='failed', generation=4)
    state['operations']['recover-' + '1' * 32 + '-binding'] = dict(state='failed',
        request=dict(action='start', scope='model-mac', digest='a' * 64, generation=3))
    async def submit(node, action, digest, **args):
        assert action == 'recover' and args['generation'] == 4
        op = dict(state='running', request=dict(action=action, digest=digest, **args))
        state['operations']['slow'] = op
        return op
    lifecycle.submit = submit
    manager.wait.side_effect = RuntimeError('Timed out waiting for recovery')
    with pytest.raises(RuntimeError, match='still running'):
        await manager.stop_operation('mac', directory.row['revision'])
    assert directory.row['operation_stop'] and source['generation'] == 4
    assert not lifecycle.actions


@pytest.mark.asyncio
async def test_pending_stop_blocks_all_other_coordinators(monkeypatch):
    manager, directory, lifecycle, state = setup(monkeypatch, lost='intent')
    with pytest.raises(ConnectionError): await manager.stop_operation('mac', directory.row['revision'])
    for action in [manager.recover('mac'), manager.upgrade_connector('mac'),
                   manager.upgrade_engine('mac', 'other'), manager.set_running('mac', True)]:
        with pytest.raises(ValueError, match='stopping'): await action
    assert not lifecycle.actions
