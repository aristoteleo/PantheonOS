"""Exact-generation stop intents over the original asynchronous node ledger."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment_restart import plan_restart
from pantheon.apps.deployment_stop import AppDeploymentStop
from test_app_deployment import Nodes, Authority, apps, coordinator, finish_deployment


class StopNodes(Nodes):
    def finish(self):
        for state in self.states.values():
            for op in state['operations'].values():
                req = op['request']
                if req['action'] != 'stop' or op['state'] != 'queued':
                    continue
                matches = [i for i in state['instances'].values()
                           if i['digest'] == req['digest'] and i['scope'] == req['scope']]
                assert len(matches) == 1 and matches[0]['generation'] == req['generation']
                matches[0].update(state='stopped', generation=req['generation'] + 1,
                                  resources=[], reservations=[])
                op['state'] = 'succeeded'
        super().finish()


async def setup(tmp_path):
    nodes = StopNodes()
    auth = Authority(nodes)
    source = apps()
    source['shared'] = deepcopy(source['allocator'])
    source['shared']['scope'] = 'shared-files'
    source['shared']['components']['backend']['values']['dependency_binding']['policies'] = {}
    source['allocator']['components']['backend']['values']['dependency_binding']['shared'] = {'$app': 'shared'}
    ready = await finish_deployment(tmp_path, nodes, auth, source)
    deploy = coordinator(tmp_path, nodes, auth)
    def fresh(): return AppDeploymentStop(coordinator(tmp_path, nodes, auth), tmp_path/'stops')
    def instance(name):
        b = ready['prepared'][name]
        return nodes.states[b['node_id']]['instances'][b['instance_id']]
    args = dict(owner='owner', operation_id='stop-one', source_operation_id='deployment-one',
                apps=['agent', 'allocator'])
    return SimpleNamespace(nodes=nodes, deploy=deploy, ready=ready, fresh=fresh, instance=instance, args=args)


async def finish(r):
    for _ in range(10):
        result = await r.fresh().advance(**r.args)
        if result['state'] == 'stopped': return result
        r.nodes.finish()
    pytest.fail('stop did not settle')


@pytest.mark.asyncio
async def test_stop_consumers_before_providers_preserves_shared_apps_and_can_restart(tmp_path):
    r = await setup(tmp_path)
    retained = deepcopy(r.instance('shared'))
    result = await finish(r)
    assert result['stopped'] == ['agent', 'allocator']
    operations = [c for c in r.nodes.calls if c[1] == 'stop']
    assert [c[0] for c in operations] == ['worker', 'platform']
    assert r.instance('shared') == retained
    assert r.instance('agent')['generation'] == r.instance('allocator')['generation'] == 3
    count = len(r.nodes.calls)
    assert await r.fresh().advance(owner='owner', operation_id='stop-one') == result
    assert r.fresh().inspect(owner='owner', operation_id='stop-one') == result
    assert len(r.nodes.calls) == count
    plan = await plan_restart(r.deploy, owner='owner', source_operation_id='deployment-one',
                              operation_id='restart-one', apps=['agent', 'allocator'])
    assert set(plan['apps']) == {'agent', 'allocator'}
    assert all(app['generation'] == 3 for app in plan['apps'].values())


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['reply', 'checkpoint', 'cancelled-observer'])
async def test_interrupted_stop_resumes_exact_operation_without_repeating_or_stopping_provider(tmp_path, failure):
    r = await setup(tmp_path)
    stopper = r.fresh()
    if failure == 'reply':
        r.nodes.loss = 'stop'
        with pytest.raises(TimeoutError): await stopper.advance(**r.args)
    elif failure == 'checkpoint':
        await stopper.advance(**r.args)
        r.nodes.finish()
        original = stopper._write
        def fail(path, record):
            if record['stopped']: raise OSError('interrupted checkpoint')
            original(path, record)
        stopper._write = fail
        with pytest.raises(OSError): await stopper.advance(**r.args)
    else:
        original = r.nodes.submit
        submitted = asyncio.Event()
        async def cancelled(*args, **kwargs):
            await original(*args, **kwargs)
            submitted.set()
            await asyncio.Future()
        r.nodes.submit = cancelled
        task = asyncio.create_task(stopper.advance(**r.args))
        await asyncio.wait_for(submitted.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        r.nodes.submit = original
    assert r.instance('allocator')['state'] == 'ready'
    original_id = [c[2] for c in r.nodes.calls if c[1] == 'stop'][0]
    r.nodes.finish()
    await finish(r)
    assert [c[2] for c in r.nodes.calls].count(original_id) == 1


@pytest.mark.asyncio
async def test_pending_drain_never_stops_dependencies_or_infers_death(tmp_path):
    r = await setup(tmp_path)
    await r.fresh().advance(**r.args)
    op = next(o for o in r.nodes.states['worker']['operations'].values() if o['request']['action'] == 'stop')
    op['state'] = 'running'
    r.instance('agent').update(state='draining', ready_generation=0)
    for _ in range(3):
        result = await r.fresh().advance(**r.args)
        assert result['state'] == 'pending' and not result['stopped']
    assert len([c for c in r.nodes.calls if c[1] == 'stop']) == 1
    assert r.instance('allocator')['state'] == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'selection', 'partial-startup', 'stale-generation',
                                  'replaced-artifact', 'missing-instance', 'unknown-drain'])
async def test_preflight_rejects_invalid_source_or_any_stale_target_before_stopping(tmp_path, change):
    r = await setup(tmp_path)
    if change == 'owner': r.args['owner'] = 'other'
    elif change == 'selection': r.args['apps'] = ['allocator']
    elif change == 'partial-startup':
        path = r.deploy._path('deployment-one')
        source = r.deploy._load(path); source['state'] = 'pending'
        r.deploy._write(path, source)
    elif change == 'stale-generation': r.instance('allocator')['generation'] += 3
    elif change == 'replaced-artifact': r.instance('allocator')['digest'] = 'e'*64
    elif change == 'missing-instance': r.nodes.states['platform']['instances'].pop(r.ready['prepared']['allocator']['instance_id'])
    else: r.instance('allocator')['state'] = 'draining'
    calls = deepcopy(r.nodes.calls)
    with pytest.raises(AssemblyError): await r.fresh().advance(**r.args)
    assert r.nodes.calls == calls


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['failed-drain', 'conflicting-operation', 'new-generation', 'resources-remain'])
async def test_bad_stop_observation_never_advances_to_dependencies(tmp_path, change):
    r = await setup(tmp_path)
    await r.fresh().advance(**r.args)
    op = next(o for o in r.nodes.states['worker']['operations'].values() if o['request']['action'] == 'stop')
    if change == 'failed-drain':
        op['state'] = 'failed'; r.instance('agent')['state'] = 'stop_blocked'
    elif change == 'conflicting-operation': op['request']['generation'] = 99
    else:
        r.nodes.finish()
        if change == 'new-generation': r.instance('agent').update(state='ready', generation=5)
        else: r.instance('agent')['resources'] = [{'pid': 1}]
    calls = deepcopy(r.nodes.calls)
    with pytest.raises(AssemblyError): await r.fresh().advance(**r.args)
    assert r.nodes.calls == calls and r.instance('allocator')['state'] == 'ready'


@pytest.mark.asyncio
async def test_already_stopped_exact_generation_is_observed_without_another_stop(tmp_path):
    r = await setup(tmp_path)
    r.instance('agent').update(state='stopped', generation=3)
    await finish(r)
    assert [c[0] for c in r.nodes.calls if c[1] == 'stop'] == ['platform']


@pytest.mark.asyncio
async def test_stop_intent_and_completed_source_remain_immutable(tmp_path):
    r = await setup(tmp_path)
    await r.fresh().advance(**r.args)
    with pytest.raises(AssemblyError, match='immutable'):
        await r.fresh().advance(**{**r.args, 'apps': ['agent', 'allocator', 'shared']})
    path = r.deploy._path('deployment-one')
    source = r.deploy._load(path)
    source['recipe']['apps']['agent']['components']['backend']['values']['agent']['namespace'] = 'changed'
    r.deploy._write(path, source)
    with pytest.raises(AssemblyError, match='changed'):
        await r.fresh().advance(**r.args)
    assert len([c for c in r.nodes.calls if c[1] == 'stop']) == 1


@pytest.mark.asyncio
async def test_platform_stop_api_keeps_unknown_outcome_and_private_intent_out_of_responses(tmp_path):
    from pantheon.platform.fleet_api import FleetAPI
    r = await setup(tmp_path)
    api = FleetAPI()
    api._app_deployments = lambda: r.deploy
    r.nodes.loss = 'stop'
    lost = await api.fleet_app_deployment_stop(**r.args)
    assert lost['success'] is False and 'outcome is unknown' in lost['error']
    public = await api.fleet_app_deployment_stop(owner='owner', operation_id='stop-one', action='inspect')
    assert public['success'] is True and public['state'] == 'pending'
    assert set(public) == {'success', 'protocol', 'operation_id', 'state', 'app', 'stopped', 'observation'}
    assert not (await api.fleet_app_deployment_stop(owner='other', operation_id='stop-one'))['success']
    for _ in range(10):
        r.nodes.finish()
        result = await api.fleet_app_deployment_stop(owner='owner', operation_id='stop-one')
        assert result['success']
        if result['state'] == 'stopped': break
    else: pytest.fail('platform stop did not settle')
    assert result['stopped'] == ['agent', 'allocator']
    assert not (await api.fleet_app_deployment_stop(**r.args, action='inspect'))['success']
    assert not (await api.fleet_app_deployment_stop(**r.args, action='force'))['success']
    assert len([c for c in r.nodes.calls if c[1] == 'stop']) == 2
