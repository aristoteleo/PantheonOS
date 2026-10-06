"""Partial startup cancellation is fenced, resumable and generation-specific."""
from copy import deepcopy
import asyncio

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment_abort import AppDeploymentAbort
from pantheon.apps.deployment_restart import plan_restart
from pantheon.platform.fleet_api import FleetAPI
from test_app_deployment import Authority, apps, coordinator
from test_app_deployment_stop import StopNodes


def setup(tmp_path):
    nodes = StopNodes()
    deployment = coordinator(tmp_path, nodes, Authority(nodes))
    recipe = dict(owner='owner', operation_id='source', apps=apps())
    return nodes, deployment, recipe


async def failed_start(nodes, deployment, recipe, *, consumed):
    for _ in range(20):
        await deployment.advance(**recipe)
        operations = nodes.states['worker']['operations']
        start = next((op for op in operations.values() if op['request']['action'] == 'start'), None)
        if start:
            if consumed:
                nodes.finish()
                next(iter(nodes.states['worker']['instances'].values()))['state'] = 'failed'
            start['state'] = 'failed'
            return
        nodes.finish()
    pytest.fail('failed start was not reached')


async def finish(nodes, deployment):
    for _ in range(15):
        result = await AppDeploymentAbort(deployment).advance(owner='owner', operation_id='abort', source_operation_id='source')
        if result['state'] == 'aborted': return result
        nodes.finish()
    pytest.fail('abort did not finish')


@pytest.mark.asyncio
@pytest.mark.parametrize('consumed', [False, True])
@pytest.mark.parametrize('lost_reply', [False, True])
async def test_abort_failed_start_drains_reverse_order_and_can_restart(tmp_path, consumed, lost_reply):
    nodes, deployment, recipe = setup(tmp_path)
    await failed_start(nodes, deployment, recipe, consumed=consumed)
    with pytest.raises(AssemblyError): await deployment.advance(**recipe)
    if lost_reply:
        nodes.loss = 'stop'
        with pytest.raises(TimeoutError):
            await AppDeploymentAbort(deployment).advance(owner='owner', operation_id='abort', source_operation_id='source')
        with pytest.raises(AssemblyError, match='fenced'):
            await deployment.advance(**recipe)
    result = await finish(nodes, deployment)
    assert result['stopped'] == ['agent', 'allocator']
    assert [call[0] for call in nodes.calls if call[1] == 'stop'] == ['worker', 'platform']
    assert all(i['state'] == 'stopped' and not i.get('reservations')
               for state in nodes.states.values() for i in state['instances'].values())
    before = list(nodes.calls)
    assert await finish(nodes, deployment) == result and nodes.calls == before
    with pytest.raises(AssemblyError, match='fenced'): await deployment.advance(**recipe)
    restarted = await plan_restart(deployment, owner='owner', source_operation_id='source',
                                   operation_id='restart', apps=list(recipe['apps']))
    with pytest.raises(AssemblyError, match='entire aborted'):
        await plan_restart(deployment, owner='owner', source_operation_id='source', operation_id='partial', apps=['agent'])
    with pytest.raises(AssemblyError, match='Recover the original'):
        await plan_restart(deployment, owner='owner', source_operation_id='source', operation_id='upgrade',
                           apps=list(recipe['apps']), allow_aborted=False)
    assert restarted['apps']['agent']['generation'] == (3 if consumed else 2)
    assert restarted['apps']['allocator']['generation'] == 3
    for _ in range(20):
        result = await deployment.advance(**restarted)
        if result['state'] == 'ready': break
        nodes.finish()
    assert result['state'] == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['install', 'prepare_start', 'start'])
async def test_pending_operations_are_observed_and_never_replaced(tmp_path, phase):
    nodes, deployment, recipe = setup(tmp_path)
    for _ in range(20):
        await deployment.advance(**recipe)
        if any(op['request']['action'] == phase and op['state'] == 'queued'
               for state in nodes.states.values() for op in state['operations'].values()): break
        nodes.finish()
    before = list(nodes.calls)
    for _ in range(3):
        result = await AppDeploymentAbort(deployment).advance(owner='owner', operation_id='abort', source_operation_id='source')
        assert result['state'] == 'pending' and not result['stopped']
    assert nodes.calls == before
    with pytest.raises(AssemblyError, match='fenced'): await deployment.advance(**recipe)
    nodes.finish()
    assert (await finish(nodes, deployment))['state'] == 'aborted'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'unknown-operation', 'conflicting-operation', 'generation', 'preparation', 'instance-id', 'new-abort-id'])
async def test_abort_rejects_uncertain_or_replaced_state_before_teardown(tmp_path, change):
    nodes, deployment, recipe = setup(tmp_path)
    await failed_start(nodes, deployment, recipe, consumed=False)
    args = dict(owner='owner', operation_id='abort', source_operation_id='source')
    state = nodes.states['worker']
    start = next(op for op in state['operations'].values() if op['request']['action'] == 'start')
    instance = next(iter(state['instances'].values()))
    if change == 'owner': args['owner'] = 'other'
    elif change == 'unknown-operation': start['state'] = 'unknown'
    elif change == 'conflicting-operation': start['request']['generation'] += 1
    elif change == 'generation': instance['generation'] += 1
    elif change == 'preparation': instance['start_preparation_id'] = 'foreign'
    elif change == 'instance-id':
        key = next(iter(state['instances']))
        state['instances']['replacement'] = state['instances'].pop(key)
    else:
        # Fence while a real start remains pending, before any stop is sent.
        start['state'] = 'running'
        await AppDeploymentAbort(deployment).advance(**args)
        args['operation_id'] = 'replacement'
    before = deepcopy(nodes.states)
    with pytest.raises(AssemblyError): await AppDeploymentAbort(deployment).advance(**args)
    assert nodes.states == before and not [c for c in nodes.calls if c[1] == 'stop']


@pytest.mark.asyncio
async def test_stop_unknown_keeps_dependencies_and_owner_api_private(tmp_path):
    nodes, deployment, recipe = setup(tmp_path)
    await failed_start(nodes, deployment, recipe, consumed=True)
    api = FleetAPI(); api._app_deployments = lambda: deployment
    args = dict(owner='owner', operation_id='abort', source_operation_id='source')
    nodes.loss = 'stop'
    result = await api.fleet_app_deployment_abort(**args)
    assert not result['success'] and 'unknown' in result['error'] and 'private-vault' not in str(result)
    stop = next(op for op in nodes.states['worker']['operations'].values() if op['request']['action'] == 'stop')
    stop['state'] = 'unknown'
    with pytest.raises(AssemblyError): await finish(nodes, deployment)
    assert len([c for c in nodes.calls if c[1] == 'stop']) == 1
    assert next(iter(nodes.states['platform']['instances'].values()))['state'] == 'ready'


@pytest.mark.asyncio
async def test_pending_drain_preserves_running_dependencies(tmp_path):
    nodes, deployment, recipe = setup(tmp_path)
    await failed_start(nodes, deployment, recipe, consumed=True)
    args = dict(owner='owner', operation_id='abort', source_operation_id='source')
    await AppDeploymentAbort(deployment).advance(**args)
    stop = next(op for op in nodes.states['worker']['operations'].values() if op['request']['action'] == 'stop')
    stop['state'] = 'running'
    next(iter(nodes.states['worker']['instances'].values()))['state'] = 'draining'
    for _ in range(3):
        result = await AppDeploymentAbort(deployment).advance(**args)
        assert result['state'] == 'pending' and result['app'] == 'agent'
    assert len([c for c in nodes.calls if c[1] == 'stop']) == 1
    assert next(iter(nodes.states['platform']['instances'].values()))['state'] == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['checkpoint', 'cancelled-observer'])
async def test_abort_survives_owner_interruption_without_new_start_or_stop(tmp_path, failure):
    nodes, deployment, recipe = setup(tmp_path)
    await failed_start(nodes, deployment, recipe, consumed=True)
    args = dict(owner='owner', operation_id='abort', source_operation_id='source')
    if failure == 'checkpoint':
        await AppDeploymentAbort(deployment).advance(**args)
        nodes.finish()
        original = deployment._write
        def interrupted(path, record):
            if record.get('abort', {}).get('stopped'): raise OSError('interrupted owner checkpoint')
            original(path, record)
        deployment._write = interrupted
        with pytest.raises(OSError): await AppDeploymentAbort(deployment).advance(**args)
        deployment._write = original
    else:
        original = nodes.status
        entered = asyncio.Event()
        async def interrupted(node):
            entered.set()
            await asyncio.Future()
        nodes.status = interrupted
        task = asyncio.create_task(AppDeploymentAbort(deployment).advance(**args))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        nodes.status = original
    with pytest.raises(AssemblyError, match='fenced'): await deployment.advance(**recipe)
    assert (await finish(nodes, deployment))['state'] == 'aborted'
    assert [call[0] for call in nodes.calls if call[1] == 'stop'] == ['worker', 'platform']


@pytest.mark.asyncio
async def test_upgrade_failed_candidate_aborts_then_rolls_back_to_retained_data(tmp_path):
    from test_app_deployment_upgrade import fixture
    r = await fixture(tmp_path)
    shared = deepcopy(r.instance('shared'))
    await r.upgrade().advance(**r.upgrade_args)
    r.nodes.finish()
    recipe = await r.upgrade().prepared_recipe(owner='owner', operation_id='upgrade-one')
    for _ in range(20):
        await r.deploy.advance(**recipe)
        start = next((op for op in r.nodes.states['worker']['operations'].values()
                      if op['request']['action'] == 'start' and op['request']['digest'] == 'c'*64), None)
        if start:
            r.nodes.finish(); start['state'] = 'failed'
            next(i for i in r.nodes.states['worker']['instances'].values() if i['digest'] == 'c'*64)['state'] = 'failed'
            break
        r.nodes.finish()
    assert start is not None
    for _ in range(15):
        result = await AppDeploymentAbort(r.deploy).advance(owner='owner', operation_id='abort-upgrade', source_operation_id='upgrade-one')
        if result['state'] == 'aborted': break
        r.nodes.finish()
    assert result['state'] == 'aborted'
    rollback = await r.upgrade().rollback_recipe(owner='owner', operation_id='upgrade-one', rollback_operation_id='restore')
    for _ in range(20):
        result = await r.deploy.advance(**rollback['recipe'])
        if result['state'] == 'ready': break
        r.nodes.finish()
    assert result['state'] == 'ready' and r.instance('shared') == shared
    assert r.data['source'] == ['chat one', 'tool result']
