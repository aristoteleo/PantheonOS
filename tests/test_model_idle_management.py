from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.models import idle_management as idle
from test_model_recovery import setup as recovery_setup


def setup(monkeypatch, lost=''):
    manager, directory, lifecycle, state, connector = recovery_setup(monkeypatch, managed=True)
    directory.row['managed'] = dict(load_policy='on_demand', keep_alive_seconds=0)
    for instance in state['instances'].values():
        instance['state'] = 'ready'
    state['model_idle'] = {}
    manager.node.return_value['capability']['runtimes']['model-engine-idle-management'] = '1'
    monkeypatch.setattr(idle, 'FleetLifecycle', lambda _: lifecycle)
    calls = []
    faults = set()
    async def command(node, action, *, registration):
        assert node == directory.row['node_id']
        q = deepcopy(registration)
        calls.append((action, q))
        previous = state['model_idle'].get(q['id'])
        if action == 'register':
            assert not previous or previous['revision'] == q['revision']
            policy = dict(registration=q, id=q['id'], revision=q['revision']+1, enabled=True,
                state='active', idle_seconds=q['idle_seconds'], connector=deepcopy(q['connector']),
                engine=deepcopy(q['engine']), config_revision=q['config_revision'])
        else:
            assert action == 'cancel'
            if previous and previous['registration'] == q:
                policy = deepcopy(previous)
                if policy['enabled']:
                    policy['revision'] += 1
                policy.update(enabled=False, state='disabled')
            else:
                assert not previous or previous['revision'] == q['revision']
                policy = dict(registration=q, id=q['id'], revision=q['revision']+1, enabled=False,
                    state='disabled', idle_seconds=q['idle_seconds'], connector=deepcopy(q['connector']),
                    engine=deepcopy(q['engine']), config_revision=q['config_revision'])
        state['model_idle'][q['id']] = deepcopy(policy)
        if action == lost and action not in faults:
            faults.add(action)
            raise ConnectionError('lost acknowledgement')
        return deepcopy(policy)
    lifecycle.model_idle = command
    return SimpleNamespace(manager=manager, directory=directory, lifecycle=lifecycle,
                           state=state, connector=connector, calls=calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['register', 'cancel'])
async def test_original_intent_survives_lost_ack_without_duplicate_register(monkeypatch, lost):
    e = setup(monkeypatch, lost)
    if lost == 'register':
        with pytest.raises(ConnectionError):
            await e.manager.set_engine_idle('mac', 60, e.directory.row['revision'])
        assert e.directory.row['engine_idle']['phase'] == 'registering'
    row = await e.manager.set_engine_idle('mac', 60, e.directory.row['revision'])
    original = deepcopy(row['engine_idle']['registration'])
    assert row['engine_idle']['phase'] == 'enabled'
    assert [a for a, _ in e.calls].count('register') == 1
    if lost == 'cancel':
        with pytest.raises(ConnectionError):
            await idle.cancel(e.manager, row)
        assert e.directory.row['engine_idle']['phase'] == 'disabling'
    row = await idle.cancel(e.manager, await e.directory.deployment('mac'))
    assert row['engine_idle']['phase'] == 'disabled'
    assert row['engine_idle']['policy_revision'] == 2
    assert row['engine_idle']['registration'] == original
    assert not e.lifecycle.actions  # cancellation never wakes/stops/reinstalls
    assert e.manager.rpc.await_count == 0


@pytest.mark.asyncio
async def test_cancel_registration_that_never_arrived(monkeypatch):
    e = setup(monkeypatch)
    q = idle.registration(e.directory.row, 60)
    e.directory.row['engine_idle'] = dict(phase='registering', policy_revision=0, idle_seconds=60, registration=q)
    row = await idle.cancel(e.manager, await e.directory.deployment('mac'))
    assert row['engine_idle']['policy_revision'] == 1
    assert e.calls == [('cancel', q)]
    assert not e.lifecycle.actions


@pytest.mark.asyncio
async def test_stop_revokes_sleeping_policy_before_any_stop_without_waking(monkeypatch):
    e = setup(monkeypatch)
    await e.manager.set_engine_idle('mac', 60, 1)
    policy = e.state['model_idle']['mac']
    policy['state'] = 'sleeping'
    policy['engine']['generation'] += 1
    e.state['instances']['engine'].update(generation=3, state='stopped', resources=[])
    async def submit(node, action, digest, **args):
        assert action == 'stop' and args['scope'] == 'model-mac'
        assert e.directory.row['engine_idle']['phase'] == 'disabled'
        assert not e.state['model_idle']['mac']['enabled']
        e.lifecycle.actions.append((action, args['scope']))
        e.state['instances']['instance'].update(state='stopped', generation=3, resources=[])
        return {'request': {'operation_id': 'owner-stop'}}
    e.lifecycle.submit = submit
    result = await e.manager.set_running('mac', False)
    assert result['state'] == 'stopped' and result['engine_binding']['generation'] == 3
    assert e.lifecycle.actions == [('stop', 'model-mac')]
    assert [call.args[1] for call in e.manager.rpc.await_args_list] == ['drain']


@pytest.mark.asyncio
async def test_disable_waits_for_exact_inflight_start_then_recovers(monkeypatch):
    e = setup(monkeypatch)
    await e.manager.set_engine_idle('mac', 60, 1)
    policy = e.state['model_idle']['mac']
    policy.update(state='waking', start_operation='idle-start')
    policy['engine']['generation'] = 3  # stopped; a submitted start finishes after disable
    e.state['operations']['idle-start'] = dict(model_idle_id='mac', state='running', request=dict(
        action='start', scope='engine-mac', digest='e'*64, generation=3))
    observed = 0
    async def status(node):
        nonlocal observed
        if not e.state['model_idle']['mac']['enabled']:
            observed += 1
            if observed == 2:
                e.state['operations']['idle-start']['state'] = 'succeeded'
                e.state['instances']['engine'].update(generation=4, state='ready')
        return deepcopy(e.state)
    e.lifecycle.status = status
    result = await e.manager.set_engine_idle('mac', 0, e.directory.row['revision'])
    assert observed >= 2
    assert result['engine_binding']['generation'] == 4 and result['state'] == 'ready'
    assert e.connector.accepting
    assert not any(a == 'start' for a, _ in e.lifecycle.actions)


@pytest.mark.asyncio
async def test_disabled_recovery_accepts_only_cancelled_rebind_receipt(monkeypatch):
    e = setup(monkeypatch)
    await e.manager.set_engine_idle('mac', 60, 1)
    policy = e.state['model_idle']['mac']
    policy.update(state='rebinding', resume_revision='d'*64)
    e.connector.revision = 'd'*64  # resume succeeded but node/Hub ACK was lost
    original_rpc = e.manager.rpc.side_effect
    phase = 'stopped'
    async def rpc(binding, method, args=None):
        nonlocal phase
        if method == 'idle_reset':
            assert args == dict(suspend_id='cycle-1', config_revision='d'*64)
            phase = 'reset'
            return {}
        response = await original_rpc(binding, method, args)
        if method == 'status':
            response['engine_idle'] = dict(phase=phase, suspend_id='cycle-1')
        return response
    e.manager.rpc = AsyncMock(side_effect=rpc)
    result = await e.manager.recover('mac')
    assert result['state'] == 'ready' and result['config_revision'] == 'f'*64
    methods = [c.args[1] for c in e.manager.rpc.await_args_list]
    assert methods.index('idle_reset') < methods.index('configure') < methods.index('resume')
    assert e.connector.accepting and result['engine_idle']['phase'] == 'disabled'


@pytest.mark.asyncio
async def test_policy_validation_and_foreign_generation_never_mutate_lifecycle(monkeypatch):
    e = setup(monkeypatch)
    for value in (True, -1, 86401, '60'):
        with pytest.raises(ValueError):
            await e.manager.set_engine_idle('mac', value, 1)
    with pytest.raises(ValueError, match='changed'):
        await e.manager.set_engine_idle('mac', 60, 99)
    assert not e.calls and not e.directory.saves
    row = await e.manager.set_engine_idle('mac', 60, 1)
    e.state['instances']['engine']['generation'] += 2
    with pytest.raises(ValueError, match='generation changed'):
        await idle.cancel(e.manager, row)
    assert e.directory.row['engine_idle']['phase'] == 'disabling'
    assert not e.lifecycle.actions


@pytest.mark.asyncio
async def test_failed_tombstone_does_not_allow_stop(monkeypatch):
    e = setup(monkeypatch)
    await e.manager.set_engine_idle('mac', 60, 1)
    e.lifecycle.model_idle = AsyncMock(side_effect=ConnectionError('unavailable node'))
    with pytest.raises(ConnectionError):
        await e.manager.set_running('mac', False)
    assert e.directory.row['engine_idle']['phase'] == 'disabling'
    assert e.directory.row['state'] == 'ready'
    assert not e.lifecycle.actions and e.manager.rpc.await_count == 0


@pytest.mark.asyncio
async def test_stopped_idle_service_restart_resets_fence_and_readmits(monkeypatch):
    e = setup(monkeypatch)
    row = await e.manager.set_engine_idle('mac', 60, 1)
    row = await idle.cancel(e.manager, row)
    row['state'] = 'stopped'
    row['managed']['recipe_id'] = 'ollama-test'
    row = await e.directory.save(row)
    e.manager.node.return_value['capability'].update(os='darwin', arch='arm64')
    async def ensure(row, *, binding_key='binding', **_):
        return row[binding_key]
    e.manager.ensure = ensure
    calls = []
    phase = 'stopped'
    async def rpc(binding, method, args=None):
        nonlocal phase
        calls.append(method)
        if method == 'engines_catalog': return {'recipes': [{'id': 'ollama-test', 'prepared': True}]}
        if method == 'status': return {'config_revision': 'c'*64, 'engine_idle': {'phase': phase, 'suspend_id': 'cycle'}}
        if method == 'drain': return {'safe_to_stop': True}
        if method == 'idle_reset': phase = 'reset'; return {}
        if method == 'configure':
            assert phase == 'reset'
            return {'config_revision': 'f'*64}
        if method == 'resume':
            assert args == {'config_revision': 'f'*64}
            phase = 'resumed'
            return {'config_revision': 'f'*64, 'accepting': True}
        pytest.fail(method)
    e.manager.rpc = rpc
    result = await e.manager.set_running('mac', True)
    assert result['state'] == 'ready' and phase == 'resumed'
    assert calls.index('idle_reset') < calls.index('configure') < calls.index('resume')


@pytest.mark.asyncio
async def test_status_observation_never_submits_wake(monkeypatch):
    e = setup(monkeypatch)
    row = await e.manager.set_engine_idle('mac', 60, 1)
    snapshot = {**e.state['model_idle']['mac'], 'wake_requested': False}
    e.directory.hub_request = AsyncMock(return_value={'deployment': row, 'idle': snapshot})
    result = await e.manager.engine_idle_status('mac')
    assert result['state'] == 'active'
    e.directory.hub_request.assert_awaited_once_with('POST', '/api/model-services/mac/engine-idle',
        {'action': 'status', 'revision': row['revision']})
    assert not e.lifecycle.actions


@pytest.mark.asyncio
async def test_sleeping_model_status_and_job_history_do_not_wake(monkeypatch):
    from pantheon.models import idle as workload
    e = setup(monkeypatch)
    row = await e.manager.set_engine_idle('mac', 60, 1)
    e.state['instances']['engine'].update(state='stopped', resources=[], generation=3)
    observe = AsyncMock(return_value=(row, {'state': 'sleeping'}))
    wake = AsyncMock(side_effect=AssertionError('metadata must not wake'))
    monkeypatch.setattr(workload, 'observe', observe)
    monkeypatch.setattr(workload, 'wake', wake)
    e.manager.rpc = AsyncMock(return_value={'models': [{'id': 'cached', 'loaded': None}], 'jobs': []})
    result = await e.manager.model_operations('mac')
    assert result['engine_idle'] == 'sleeping' and result['models'][0]['loaded'] is None
    await e.manager.model_operations('mac', 'forget', job_id='old-job')
    assert [call.args[1] for call in e.manager.rpc.await_args_list] == ['models_status', 'models_forget']
    assert not wake.called and not e.lifecycle.actions


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['discover', 'publish', 'load'])
async def test_owner_model_actions_wake_before_rpc_and_publish_keeps_new_revision(monkeypatch, action):
    from pantheon.models import idle as workload
    e = setup(monkeypatch)
    row = await e.manager.set_engine_idle('mac', 60, 1)
    original_revision = row['revision']
    events = []
    async def wake(client, source):
        assert source['revision'] == original_revision
        events.append('wake')
        source['engine_binding']['generation'] += 2
        source['config_revision'] = 'd'*64
        e.state['instances']['engine']['generation'] = source['engine_binding']['generation']
        return await e.directory.save(source)
    monkeypatch.setattr(workload, 'wake', wake)
    async def rpc(binding, method, args=None):
        events.append(method)
        return {'models': [{'id': 'selected'}], 'config_revision': 'd'*64}
    e.manager.rpc = rpc
    if action == 'discover':
        await e.manager.discover('mac')
    elif action == 'publish':
        result = await e.manager.publish('mac', [{'id': 'selected'}], original_revision)
        assert result['revision'] == original_revision+2
        assert result['engine_binding']['generation'] == 4
    else:
        await e.manager.model_operations('mac', 'submit', job_id='load-model', operation='load', model_id='selected')
    assert events == ['wake', 'models_submit' if action == 'load' else 'discover']
