"""Release candidates copy only stopped owned state and use ordinary deployment."""
from copy import deepcopy
import hashlib

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment_upgrade import AppUpgradePreparation
from pantheon.apps.deployment_stop import AppDeploymentStop
from test_app_deployment_stop import setup, finish


async def fixture(tmp_path):
    r = await setup(tmp_path)
    await finish(r)
    new = 'c' * 64
    r.nodes.manifests[new] = deepcopy(r.nodes.manifests['b' * 64])
    r.nodes.manifests[new].update(revision=new)
    r.nodes.manifests[new]['manifest']['version'] = '1.1.0'
    r.nodes.states['worker']['installations'][new] = {'state': 'installed'}
    r.data = {'source': ['chat one', 'tool result']}
    original_finish = r.nodes.finish
    def finish_clones():
        for node, state in r.nodes.states.items():
            for op in state['operations'].values():
                req = op['request']
                if req['action'] != 'clone_data' or op['state'] != 'queued': continue
                source = r.instance('agent')
                assert source['state'] == 'stopped' and source['generation'] == req['data_source']['generation']
                key = hashlib.sha256(f"{node}:{req['digest']}:{req['scope']}".encode()).hexdigest()[:32]
                state['instances'][key] = dict(app_id='test-agent', digest=req['digest'], scope=req['scope'],
                    generation=0, state='stopped', resources=[], data_source=req['data_source'])
                r.data[key] = deepcopy(r.data['source'])
                op['state'] = 'succeeded'
        original_finish()
    r.nodes.finish = finish_clones
    r.upgrade = lambda: AppUpgradePreparation(r.deploy, tmp_path/'upgrades')
    r.upgrade_args = dict(owner='owner', source_operation_id='deployment-one', operation_id='upgrade-one',
                          apps=['agent', 'allocator'], revisions={'agent': new})
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_reply', [False, True])
async def test_candidate_uses_original_deployment_and_preserves_source(tmp_path, lost_reply):
    r = await fixture(tmp_path)
    old, shared = deepcopy(r.instance('agent')), deepcopy(r.instance('shared'))
    if lost_reply:
        r.nodes.loss = 'clone_data'
        with pytest.raises(TimeoutError): await r.upgrade().advance(**r.upgrade_args)
    result = await r.upgrade().advance(**r.upgrade_args)
    assert result['state'] == 'pending'
    with pytest.raises(AssemblyError, match='Wait for all'):
        await r.upgrade().prepared_recipe(owner='owner', operation_id='upgrade-one')
    r.nodes.finish()
    assert (await r.upgrade().advance(owner='owner', operation_id='upgrade-one'))['state'] == 'prepared'
    recipe = await r.upgrade().prepared_recipe(owner='owner', operation_id='upgrade-one')
    assert recipe['apps']['agent']['generation'] == 0
    assert recipe['apps']['allocator']['generation'] == 3
    for _ in range(20):
        result = await r.deploy.advance(**recipe)
        if result['state'] == 'ready': break
        r.nodes.finish()
    assert result['state'] == 'ready'
    target = result['prepared']['agent']['instance_id']
    assert r.data[target] == r.data['source']
    r.data[target].append('candidate only')
    assert r.data['source'] == ['chat one', 'tool result']
    assert r.instance('agent') == old and r.instance('shared') == shared
    assert len([c for c in r.nodes.calls if c[1]=='clone_data']) == 1
    with pytest.raises(AssemblyError):
        await r.upgrade().advance(owner='owner', operation_id='upgrade-one')
    stopper = AppDeploymentStop(r.deploy, tmp_path/'candidate-stops')
    for _ in range(10):
        result = await stopper.advance(owner='owner', operation_id='stop-candidate',
                                       source_operation_id='upgrade-one', apps=['agent','allocator'])
        if result['state']=='stopped': break
        r.nodes.finish()
    assert result['state']=='stopped'
    rollback = await r.upgrade().rollback_recipe(owner='owner', operation_id='upgrade-one', rollback_operation_id='rollback')
    assert rollback['data_policy']=='retained-source-data'
    assert rollback['recipe']['apps']['agent']['revision']=='b'*64
    assert rollback['recipe']['apps']['agent']['generation']==3
    assert rollback['recipe']['apps']['allocator']['generation']==6
    for _ in range(20):
        result = await r.deploy.advance(**rollback['recipe'])
        if result['state']=='ready': break
        r.nodes.finish()
    assert result['state']=='ready' and r.instance('shared')==shared
    assert r.data[target][-1]=='candidate only'
    assert r.data['source']==['chat one', 'tool result']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['running-source', 'foreign-owner', 'wrong-app', 'incompatible',
                                  'used-target', 'missing-install', 'missing-dependent'])
async def test_rejects_invalid_upgrade_before_copy(tmp_path, change):
    r = await fixture(tmp_path)
    if change == 'running-source': r.instance('agent')['state'] = 'ready'
    elif change == 'foreign-owner': r.upgrade_args['owner'] = 'other'
    elif change == 'wrong-app': r.nodes.manifests['c'*64]['manifest']['id'] = 'other'
    elif change == 'incompatible': r.nodes.manifests['c'*64]['manifest']['dependencies']['dependency-binding']['range'] = '^2.0.0'
    elif change == 'used-target':
        r.nodes.states['worker']['instances']['unknown'] = dict(digest='c'*64, scope='deploy-agent', state='stopped', generation=0)
    elif change == 'missing-install': r.nodes.states['worker']['installations'].pop('c'*64)
    else: r.upgrade_args['apps'] = ['agent']
    before = deepcopy(r.nodes.states)
    with pytest.raises(AssemblyError): await r.upgrade().advance(**r.upgrade_args)
    assert r.nodes.states == before
    assert not [c for c in r.nodes.calls if c[1]=='clone_data']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['target-revision', 'source-generation', 'provider-generation', 'ledger-request', 'copy-missing'])
async def test_resume_rejects_changed_intent_or_observation(tmp_path, change):
    r = await fixture(tmp_path)
    await r.upgrade().advance(**r.upgrade_args)
    r.nodes.finish()
    args = dict(owner='owner', operation_id='upgrade-one')
    if change == 'target-revision': args['revisions'] = {'agent':'d'*64}
    elif change == 'source-generation': r.instance('agent')['generation'] += 1
    elif change == 'provider-generation': r.instance('shared')['generation'] += 1
    elif change == 'ledger-request':
        for op in r.nodes.states['worker']['operations'].values():
            if op['request']['action']=='clone_data':op['request']['data_source']['generation'] += 1
    else:
        state=r.nodes.states['worker']
        state['instances']={k:v for k,v in state['instances'].items() if v['digest']!='c'*64}
    with pytest.raises(AssemblyError): await r.upgrade().advance(**args)
    assert len([c for c in r.nodes.calls if c[1]=='clone_data']) == 1


@pytest.mark.asyncio
async def test_owner_api_resumes_lost_reply_without_disclosing_private_recipe(tmp_path):
    from pantheon.platform.fleet_api import FleetAPI
    r = await fixture(tmp_path)
    api = FleetAPI(); api._app_deployments = lambda:r.deploy
    r.nodes.loss='clone_data'
    result=await api.fleet_app_upgrade_prepare(**r.upgrade_args)
    assert not result['success'] and 'outcome is unknown' in result['error']
    assert 'private-vault' not in str(result)
    r.nodes.finish()
    result=await api.fleet_app_upgrade_prepare(owner='owner',operation_id='upgrade-one')
    assert result==dict(success=True,protocol=1,operation_id='upgrade-one',state='prepared',app='')
    assert not (await api.fleet_app_upgrade_prepare(owner='other',operation_id='upgrade-one'))['success']
    result=await api.fleet_app_upgrade_prepare(owner='owner',operation_id='upgrade-one',action='recipe')
    assert result['success'] and result['recipe']['apps']['agent']['revision']=='c'*64
    assert not (await api.fleet_app_upgrade_prepare(**r.upgrade_args,action='recipe'))['success']
    assert not (await api.fleet_app_upgrade_rollback_plan(owner='owner',operation_id='upgrade-one',rollback_operation_id='rollback'))['success']
    assert len([c for c in r.nodes.calls if c[1]=='clone_data'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source-generation', 'shared-generation', 'candidate-generation',
                                  'source-reservation', 'candidate-running', 'foreign-owner'])
async def test_rollback_refuses_changed_generations_without_mutation(tmp_path, change):
    r = await fixture(tmp_path)
    await r.upgrade().advance(**r.upgrade_args)
    r.nodes.finish()
    recipe = await r.upgrade().prepared_recipe(owner='owner', operation_id='upgrade-one')
    for _ in range(20):
        result = await r.deploy.advance(**recipe)
        if result['state'] == 'ready': break
        r.nodes.finish()
    assert result['state'] == 'ready'
    candidate_id = result['prepared']['agent']['instance_id']
    stopper = AppDeploymentStop(r.deploy, tmp_path/'candidate-stops')
    for _ in range(10):
        result = await stopper.advance(owner='owner', operation_id='stop-candidate',
                                       source_operation_id='upgrade-one', apps=['agent', 'allocator'])
        if result['state'] == 'stopped': break
        r.nodes.finish()
    assert result['state'] == 'stopped'
    candidate = r.nodes.states['worker']['instances'][candidate_id]
    owner = 'owner'
    if change == 'source-generation': r.instance('agent')['generation'] += 1
    elif change == 'shared-generation': r.instance('shared')['generation'] += 1
    elif change == 'candidate-generation': candidate['generation'] += 1
    elif change == 'source-reservation': r.instance('agent')['reservations'] = [{'kind': 'active'}]
    elif change == 'candidate-running': candidate['state'] = 'ready'
    else: owner = 'other'
    before, calls = deepcopy(r.nodes.states), list(r.nodes.calls)
    with pytest.raises(AssemblyError):
        await r.upgrade().rollback_recipe(owner=owner, operation_id='upgrade-one', rollback_operation_id='rollback')
    assert r.nodes.states == before and r.nodes.calls == calls
    assert not r.deploy._path('rollback').exists()
