"""Preparation is a durable owner boundary, never a running-instance alias."""
from copy import deepcopy

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment_abort import AppDeploymentAbort
from test_app_deployment import Authority, apps, coordinator, finish_deployment
from test_app_deployment_stop import StopNodes
from test_model_bootstrap import rig, finish
from test_model_bootstrap_abort import abort_rig


async def prepared(tmp_path, nodes, authority, recipe=None):
    for _ in range(20):
        result = await coordinator(tmp_path, nodes, authority).prepare(
            owner='owner', operation_id='deployment-one', apps=recipe)
        recipe = None
        if result['state'] == 'prepared':
            return result
        nodes.finish()
    pytest.fail('preparation did not settle')


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', [None, 'install', 'prepare_start'])
async def test_preparation_reopens_without_starting_then_advances_exact_instances(tmp_path, loss):
    nodes = StopNodes()
    authority = Authority(nodes)
    nodes.loss = loss
    if loss:
        with pytest.raises(TimeoutError):
            await prepared(tmp_path, nodes, authority, apps())
        nodes.finish()
    result = await prepared(tmp_path, nodes, authority, apps())
    calls = deepcopy(nodes.calls)
    assert len(calls) == 4
    assert not nodes.configurations and not authority.grants
    assert all(i['state'] == 'prepared' for state in nodes.states.values() for i in state['instances'].values())
    assert await prepared(tmp_path, nodes, authority) == result
    assert nodes.calls == calls
    final = await finish_deployment(tmp_path, nodes, authority)
    assert final['prepared'] == result['prepared']
    assert len(nodes.calls) == 6
    with pytest.raises(AssemblyError, match='entered startup'):
        await prepared(tmp_path, nodes, authority)
    assert len(nodes.calls) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['queued-start', 'external-running', 'resources', 'generation', 'owner', 'recipe'])
async def test_preparation_rechecks_live_boundary_and_immutable_identity(tmp_path, change):
    nodes = StopNodes()
    authority = Authority(nodes)
    result = await prepared(tmp_path, nodes, authority, apps())
    deployment = coordinator(tmp_path, nodes, authority)
    identity = result['prepared']['agent']
    state = nodes.states[identity['node_id']]
    instance = state['instances'][identity['instance_id']]
    kwargs = dict(owner='owner', operation_id='deployment-one')
    if change == 'queued-start':
        recipe = deployment._load(deployment._path('deployment-one'))['recipe']
        state['operations'][deployment.operation_id(recipe, 'agent', 'start')] = {'state': 'queued'}
    elif change == 'external-running': instance.update(state='starting', generation=2)
    elif change == 'resources': instance['resources'] = [{'pid': 123}]
    elif change == 'generation': instance['generation'] = 3
    elif change == 'owner': kwargs['owner'] = 'other'
    else:
        kwargs['apps'] = apps()
        kwargs['apps']['agent']['scope'] = 'replacement'
    calls = deepcopy(nodes.calls)
    with pytest.raises(AssemblyError): await deployment.prepare(**kwargs)
    assert nodes.calls == calls and not nodes.configurations and not authority.grants


@pytest.mark.asyncio
async def test_prepared_deployment_aborts_without_start_or_grants(tmp_path):
    nodes = StopNodes()
    authority = Authority(nodes)
    await prepared(tmp_path, nodes, authority, apps())
    for _ in range(10):
        result = await AppDeploymentAbort(coordinator(tmp_path, nodes, authority)).advance(
            owner='owner', source_operation_id='deployment-one', operation_id='abort-prepared')
        if result['state'] == 'aborted': break
        nodes.finish()
    assert result['state'] == 'aborted'
    assert [c[1] for c in nodes.calls].count('stop') == 2
    assert not nodes.configurations and not authority.grants
    with pytest.raises(AssemblyError, match='fenced for abort'):
        await prepared(tmp_path, nodes, authority)


@pytest.mark.asyncio
@pytest.mark.parametrize('abort', [False, True])
async def test_model_preparation_runs_only_providers_and_consumers_resume_or_abort(abort_rig, abort):
    rig = abort_rig
    for _ in range(30):
        result = await rig.restart().prepare(**rig.spec)
        if result['state'] == 'prepared': break
        rig.nodes.finish()
    assert result['state'] == 'prepared'
    calls = deepcopy(rig.nodes.calls)
    assert len(rig.registrations) == 1
    assert [c[1] for c in calls].count('start') == 1
    assert rig.nodes.states['worker']['instances']
    assert all(i['state'] == 'prepared' for i in rig.nodes.states['worker']['instances'].values())
    assert await rig.restart().prepare(**rig.spec) == result
    assert rig.nodes.calls == calls
    if abort:
        for _ in range(20):
            result = await rig.restart().abort(owner='owner', source_operation_id='model-start',
                                               operation_id='abort-prepared')
            if result['state'] == 'aborted': break
            rig.nodes.finish()
        assert result['state'] == 'aborted'
        assert [c[1] for c in rig.nodes.calls].count('start') == 1
    else:
        assert (await finish(rig))['state'] == 'ready'
        calls = deepcopy(rig.nodes.calls)
        with pytest.raises(AssemblyError, match='entered startup'):
            await rig.restart().prepare(**rig.spec)
        assert rig.nodes.calls == calls


@pytest.mark.asyncio
async def test_lost_prepared_checkpoint_reconciles_without_starting(tmp_path, monkeypatch):
    nodes = StopNodes()
    authority = Authority(nodes)
    deployment = coordinator(tmp_path, nodes, authority)
    write = deployment._write
    def interrupted(path, record):
        if record['state'] == 'prepared':
            raise OSError('preparation checkpoint unavailable')
        write(path, record)
    monkeypatch.setattr(deployment, '_write', interrupted)
    with pytest.raises(OSError, match='checkpoint unavailable'):
        for _ in range(20):
            await deployment.prepare(owner='owner', operation_id='deployment-one', apps=apps())
            nodes.finish()
    calls = deepcopy(nodes.calls)
    assert not nodes.configurations and not authority.grants
    assert (await prepared(tmp_path, nodes, authority))['state'] == 'prepared'
    assert calls == nodes.calls


@pytest.mark.asyncio
async def test_lost_start_reply_never_allows_reentry_to_preparation(tmp_path):
    nodes = StopNodes()
    authority = Authority(nodes)
    await prepared(tmp_path, nodes, authority, apps())
    nodes.loss = 'start'
    with pytest.raises(TimeoutError):
        await coordinator(tmp_path, nodes, authority).advance(owner='owner', operation_id='deployment-one')
    calls = deepcopy(nodes.calls)
    with pytest.raises(AssemblyError, match='entered startup'):
        await prepared(tmp_path, nodes, authority)
    assert calls == nodes.calls
    nodes.finish()
    assert (await finish_deployment(tmp_path, nodes, authority))['state'] == 'ready'
