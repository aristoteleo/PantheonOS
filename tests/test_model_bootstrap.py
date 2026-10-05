"""Recover owner startup without duplicating the generic Fleet operation ledger."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.models.bootstrap import ModelServiceBootstrap, recipe
from pantheon.models.managed import module
from pantheon.platform.app_preset import read_preset
from pantheon.platform.service import PlatformService
from test_app_deployment import Nodes, Authority, coordinator, apps
from test_platform_app_preset import settled


@pytest.fixture
def rig(tmp_path):
    nodes = Nodes()
    nodes.manifests['c'*64] = dict(protocol=1, revision='c'*64,
        manifest={'apiVersion':2, 'id':'model-service', 'version':'0.1.24'},
        definition={'components':[{'name':'backend', 'configuration':{'values':{'connector':{'required':True}}}}]})
    spec = dict(kind='model-services', owner='owner', operation_id='model-start', apps=apps(), model_apps={
        'connector':dict(deployment_id='local', name='Local', models=[{'id':'chat','context_limit':8192}],
            app=dict(node_id='platform', revision='c'*64, scope='model-local', generation=0, bindings={},
                     components={'backend':{'values':{'connector':{'engine':'ollama','endpoint':'http://127.0.0.1:11434/v1'}}}}))})
    spec['apps']['agent']['components']['backend']['values']['agent']['model_binding'] = {'$model':'connector'}
    rows, registrations, rebindings = [], [], []
    class Manager:
        def __init__(self):
            self.client = self
            self.lose_reply = False
            self.admission = True
        async def deployments(self): return deepcopy(rows)
        async def deployment(self, deployment_id):
            return next((deepcopy(row) for row in rows if row['deployment_id'] == deployment_id), None)
        async def rebind_prepared(self, *, previous, binding, configuration):
            instance = nodes.states[binding['node_id']]['instances'][binding['instance_id']]
            assert instance['state'] == 'ready' and instance['generation'] == binding['generation']
            assert binding == {**previous['binding'], 'generation': previous['binding']['generation'] + 2}
            assert all(i['state'] == 'stopped' for i in nodes.states['worker']['instances'].values())
            desired = {**deepcopy(previous), 'state': 'ready', 'binding': binding, 'revision': previous['revision'] + 1}
            if rows != [desired]:
                assert rows == [previous]
                rows[:] = [desired]
                rebindings.append(deepcopy(binding))
            if self.lose_reply:
                self.lose_reply = False
                raise TimeoutError('lost rebind reply')
            return deepcopy(desired)
        async def register_prepared(self, deployment_id, name, binding, config, selected):
            instance = nodes.states[binding['node_id']]['instances'][binding['instance_id']]
            assert instance['state'] == 'ready' and instance['generation'] == binding['generation']
            assert not nodes.states['worker']['instances'], 'Consumer cannot start before registration'
            registrations.append(binding)
            row = dict(deployment_id=deployment_id, name=name, binding=binding, models=selected,
                       config_revision=module('server').configuration_revision(module('server').validate_config(config)), revision=1, node_id=binding['node_id'], mode='attached',
                       engine=config['engine'], state='ready')
            if rows: assert rows == [row]
            else: rows.append(row)
            if self.lose_reply:
                self.lose_reply = False
                raise TimeoutError('lost directory save reply')
            return deepcopy(row)
        async def rpc(self, binding, method):
            assert method in ('status','activity')
            return dict(config_revision=rows[0]['config_revision'], accepting=self.admission, active_model_operations=0)
    manager = Manager()
    deployment = coordinator(tmp_path/'owner', nodes, Authority(nodes))
    def restart(): return ModelServiceBootstrap(deployment, manager, tmp_path/'bootstrap')
    return SimpleNamespace(nodes=nodes, manager=manager, spec=spec, restart=restart, rows=rows,
                           registrations=registrations, rebindings=rebindings, deployment=deployment, root=tmp_path)


async def finish(rig, bootstrap=None):
    bootstrap = bootstrap or rig.restart()
    for _ in range(30):
        result = await bootstrap.advance(**rig.spec)
        if result['state'] == 'ready': return result
        rig.nodes.finish()
    raise AssertionError('startup did not settle')


@pytest.mark.asyncio
async def test_restart_reuses_one_registration_and_exact_consumer_model_binding(rig):
    result = await finish(rig)
    assert result['state'] == 'ready' and len(rig.nodes.calls) == 9
    assert len(rig.registrations) == 1
    bindings = [v['backend']['values']['agent']['model_binding'] for (node, _, _), v in rig.nodes.configurations.items()
                if node == 'worker']
    assert bindings == [rig.rows[0]['binding']]
    assert await finish(rig, rig.restart()) == result
    assert len(rig.nodes.calls) == 9 and len(rig.registrations) == 1
    public = rig.restart().inspect(owner='owner', operation_id='model-start')
    assert set(public) == {'protocol','state','phase','app','operation_id','observation'}
    assert 'endpoint' not in json.dumps(public)


@pytest.mark.asyncio
async def test_lost_registration_ack_never_starts_consumer_until_reconciled(rig):
    rig.manager.lose_reply = True
    with pytest.raises(TimeoutError): await finish(rig)
    assert len(rig.rows) == 1 and len(rig.nodes.calls) == 3
    assert not rig.nodes.states['worker']['instances']
    await finish(rig, rig.restart())
    assert len(rig.rows) == 1 and len(rig.nodes.calls) == 9


@pytest.mark.asyncio
async def test_lost_consumer_start_preserves_provider_and_original_operation_id(rig):
    original = rig.manager.register_prepared
    async def register(*args):
        row = await original(*args)
        rig.nodes.loss = 'install'
        return row
    rig.manager.register_prepared = register
    with pytest.raises(TimeoutError): await finish(rig)
    lost_id = rig.nodes.calls[-1][2]
    rig.nodes.finish()
    await finish(rig, rig.restart())
    assert len(rig.registrations) == 1 and len(rig.nodes.calls) == 9
    assert [c[2] for c in rig.nodes.calls].count(lost_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['directory','generation','drain','recipe','owner'])
async def test_changes_require_explicit_recovery_without_restarting_apps(rig, change):
    await finish(rig)
    calls = deepcopy(rig.nodes.calls)
    if change == 'directory': rig.rows[0]['name'] = 'changed'
    elif change == 'generation':
        for i in rig.nodes.states['platform']['instances'].values():
            if i['app_id'] == 'model-service': i.update(state='stopped',generation=3)
    elif change == 'drain': rig.manager.admission = False
    elif change == 'recipe': rig.spec['model_apps']['connector']['name'] = 'changed'
    else: rig.spec['owner'] = 'another'
    with pytest.raises(AssemblyError): await finish(rig, rig.restart())
    assert rig.nodes.calls == calls


@pytest.mark.parametrize('change', ['unknown-reference','reference-extra','duplicate-deployment','wrong-scope','inline-key','overlap'])
def test_recipe_rejected_before_node_side_effects(rig, change):
    spec = rig.spec
    item = spec['model_apps']['connector']
    if change == 'unknown-reference': spec['apps']['agent']['components']['backend']['values']['agent'] = {'$model':'missing'}
    elif change == 'reference-extra': spec['apps']['agent']['components']['backend']['values']['agent'] = {'$model':'connector','extra':True}
    elif change == 'duplicate-deployment': spec['model_apps']['other'] = deepcopy(item)
    elif change == 'wrong-scope': item['app']['scope'] = 'another'
    elif change == 'inline-key': item['app']['components']['backend']['values']['connector']['key'] = 'private'
    else: spec['apps']['agent'] = deepcopy(item['app'])
    with pytest.raises((AssemblyError, ValueError)): recipe(**spec)
    assert not rig.nodes.calls


@pytest.mark.asyncio
async def test_platform_file_startup_uses_model_sequence_and_remains_agent_independent(rig):
    path = rig.root/'startup.json'; path.write_text(json.dumps(rig.spec)); path.chmod(0o600)
    assert read_preset(path) == rig.spec
    service = PlatformService(workspace_path=rig.root, app_preset=path)
    service._model_service_bootstrap = rig.restart
    service._app_preset.interval = .001
    service._start_dependency_maintenance = lambda: None
    original = rig.nodes.status
    async def status(node):
        rig.nodes.finish()
        return await original(node)
    rig.nodes.status = status
    try:
        await service.run(remote=False)
        assert (await settled(service._app_preset))['state'] == 'ready'
        assert len(rig.nodes.calls) == 9 and len(rig.registrations) == 1
    finally:
        await service.cleanup()


@pytest.mark.asyncio
async def test_registration_receipt_checkpoint_loss_is_reconciled_before_consumer_start(rig):
    bootstrap = rig.restart()
    original = bootstrap._write
    def interrupted(path, record):
        if record['registered']:
            raise OSError('checkpoint write interrupted')
        return original(path, record)
    bootstrap._write = interrupted
    with pytest.raises(OSError): await finish(rig, bootstrap)
    assert len(rig.rows) == 1 and not rig.nodes.states['worker']['instances']
    await finish(rig, rig.restart())
    assert len(rig.rows) == 1 and len(rig.nodes.calls) == 9


def budget_startup(rig):
    item=rig.spec['model_apps']['connector']
    item['credential_source']='platform-budget'
    config=dict(engine='api',endpoint='https://hub.test/litellm/v1',secret_ref='node-secret://budget')
    item['app']['components']['backend']['values']['connector']=config
    calls=[]
    async def prepare(**kwargs):
        calls.append(kwargs)
        assert not rig.nodes.calls, 'Credentials must be ready before any provider deployment'
        return dict(protocol=1,owner=rig.spec['owner'],node_id=item['app']['node_id'],source='platform-budget',
                    model_mode='direct',connector=deepcopy(config))
    def restart():
        return ModelServiceBootstrap(rig.deployment,rig.manager,rig.root/'model-bootstrap',prepare_credentials=prepare)
    rig.restart=restart
    return calls,prepare


@pytest.mark.asyncio
async def test_budget_prepared_once_before_providers_and_preserved_across_restart(rig):
    calls,_=budget_startup(rig)
    assert read_recipe_file(rig)==rig.spec
    await finish(rig)
    assert len(calls)==1 and calls[0]['owner']=='owner' and calls[0]['node_id']=='platform'
    assert calls[0]['lifecycle'] is rig.deployment.starter.lifecycle
    await finish(rig,rig.restart())
    assert len(calls)==1
    saved=json.loads((rig.root/'model-bootstrap/model-start.json').read_text())
    assert saved['credential_receipts']['connector']['source']=='platform-budget'
    assert 'credential_receipts' not in rig.restart().inspect(owner='owner',operation_id='model-start')


def read_recipe_file(rig):
    path=rig.root/'budget-startup.json';path.write_text(json.dumps(rig.spec));path.chmod(0o600)
    return read_preset(path)


@pytest.mark.asyncio
async def test_budget_requires_explicit_owner_preparer_before_any_node_mutation(rig):
    budget_startup(rig)
    bootstrap=ModelServiceBootstrap(rig.deployment,rig.manager,rig.root/'model-bootstrap')
    with pytest.raises(AssemblyError,match='explicit owner'):
        await bootstrap.advance(**rig.spec)
    assert not rig.nodes.calls and not rig.registrations
    await finish(rig)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['prepare-reply','receipt-checkpoint'])
async def test_budget_unknown_outcome_retries_same_credential_intent_before_apps(rig,failure):
    calls,prepare=budget_startup(rig)
    bootstrap=rig.restart()
    if failure=='prepare-reply':
        async def lost(**kwargs):
            await prepare(**kwargs)
            raise TimeoutError('lost credential acknowledgement')
        bootstrap.prepare_credentials=lost
    else:
        original=bootstrap._write
        def lost(path,record):
            if record.get('credential_receipts'):raise OSError('lost receipt checkpoint')
            original(path,record)
        bootstrap._write=lost
    with pytest.raises((TimeoutError,OSError)):
        await bootstrap.advance(**rig.spec)
    assert not rig.nodes.calls
    await finish(rig,rig.restart())
    assert len(calls)==2 and calls[0]==calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['owner','node','endpoint','source','raw-secret'])
async def test_invalid_preparation_receipt_never_saved_or_followed_by_app_start(rig,change):
    _,prepare=budget_startup(rig)
    bootstrap=rig.restart()
    async def bad(**kwargs):
        receipt=await prepare(**kwargs)
        if change=='owner':receipt['owner']='other'
        elif change=='node':receipt['node_id']='other'
        elif change=='endpoint':receipt['connector']['endpoint']='https://other.test/v1'
        elif change=='source':receipt['source']='byok'
        else:receipt['key']='NEVER-WRITE-THIS-SECRET'
        return receipt
    bootstrap.prepare_credentials=bad
    with pytest.raises(ValueError):await bootstrap.advance(**rig.spec)
    assert not rig.nodes.calls
    assert 'NEVER-WRITE-THIS-SECRET' not in (rig.root/'model-bootstrap/model-start.json').read_text()


@pytest.mark.parametrize('change',['unknown-source','null-source','engine','missing-ref','prefix','trailing-slash'])
def test_invalid_budget_recipe_rejected(rig,change):
    budget_startup(rig)
    item=rig.spec['model_apps']['connector'];config=item['app']['components']['backend']['values']['connector']
    if change=='unknown-source':item['credential_source']='unknown'
    elif change=='null-source':item['credential_source']=None
    elif change=='engine':config['engine']='ollama'
    elif change=='missing-ref':config.pop('secret_ref')
    elif change=='prefix':config['endpoint']='https://hub.test'
    else:config['endpoint']+='/'
    with pytest.raises((ValueError,AssemblyError)):recipe(**rig.spec)
    assert not rig.nodes.calls


@pytest.mark.asyncio
async def test_platform_budget_preparer_is_wired_to_real_bootstrap(rig):
    calls,prepare=budget_startup(rig)
    service=PlatformService(workspace_path=rig.root,model_credential_preparer=prepare)
    service._app_deployments=lambda:rig.deployment
    service._model_services_manager=lambda:rig.manager
    service._start_dependency_maintenance=lambda:None
    try:
        for _ in range(30):
            result=await service._advance_app_preset(**rig.spec)
            if result['state']=='ready':break
            rig.nodes.finish()
        assert result['state']=='ready' and result['success'] and len(calls)==1
    finally:await service.cleanup()


async def clean_restart(rig):
    await finish(rig)
    for state in rig.nodes.states.values():
        for instance in state['instances'].values():
            instance.update(state='stopped', generation=instance['generation'] + 1, resources=[])
    row = rig.rows[0]
    row.update(state='stopped', revision=row['revision'] + 3,
               binding={**row['binding'], 'generation': row['binding']['generation'] + 1})
    rig.spec['operation_id'] = 'clean-restart'
    entry = rig.spec['model_apps']['connector']
    entry['restart_from'] = deepcopy(row)
    entry['app']['generation'] = row['binding']['generation']
    for app in rig.spec['apps'].values():
        app['generation'] = 3


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['none', 'save-reply', 'receipt-checkpoint', 'provider-start'])
async def test_clean_restart_rebinds_once_before_consumers_and_resumes_original_intent(rig, failure):
    await clean_restart(rig)
    before = deepcopy(rig.spec)
    bootstrap = rig.restart()
    if failure == 'save-reply': rig.manager.lose_reply = True
    if failure == 'provider-start': rig.nodes.loss = 'start'
    if failure == 'receipt-checkpoint':
        original = bootstrap._write
        def lost(path, record):
            if record['registered']: raise OSError('lost receipt checkpoint')
            original(path, record)
        bootstrap._write = lost
    if failure != 'none':
        with pytest.raises((OSError, TimeoutError)): await finish(rig, bootstrap)
        assert all(i['state'] == 'stopped' for i in rig.nodes.states['worker']['instances'].values())
        rig.nodes.finish()
    await finish(rig, rig.restart())
    assert rig.spec == before
    assert len(rig.registrations) == len(rig.rebindings) == 1
    assert rig.rows[0]['binding']['generation'] == 5
    assert rig.rows[0]['models'] == before['model_apps']['connector']['restart_from']['models']
    configurations = [v['backend']['values']['agent']['model_binding']
        for (node, _, gen), v in rig.nodes.configurations.items() if node == 'worker' and gen == 4]
    assert configurations == [rig.rows[0]['binding']]
    calls = deepcopy(rig.nodes.calls)
    await finish(rig, rig.restart())
    assert rig.nodes.calls == calls and len(rig.rebindings) == 1
    assert 'restart_from' not in json.dumps(rig.restart().inspect(owner='owner', operation_id='clean-restart'))
    assert read_recipe_file(rig) == rig.spec


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['name', 'selection', 'node', 'artifact', 'generation', 'state',
                                  'pending', 'revision', 'config-revision', 'endpoint', 'engine', 'deployment-id'])
async def test_malformed_model_restart_is_rejected_before_node_work(rig, change):
    await clean_restart(rig)
    calls = deepcopy(rig.nodes.calls)
    entry = rig.spec['model_apps']['connector']
    previous = entry['restart_from']
    if change == 'name': entry['name'] = 'renamed'
    elif change == 'selection': entry['models'][0]['context_limit'] = 4096
    elif change == 'node': previous['binding']['node_id'] = 'other'
    elif change == 'artifact': previous['binding']['revision'] = 'e'*64
    elif change == 'generation': entry['app']['generation'] += 1
    elif change == 'state': previous['state'] = 'ready'
    elif change == 'pending': previous['recovery'] = {}
    elif change == 'revision': previous['revision'] = True
    elif change == 'config-revision': previous['config_revision'] = 'e'*64
    elif change in ('endpoint', 'engine'):
        entry['app']['components']['backend']['values']['connector'][change] = (
            'http://127.0.0.1:12345/v1' if change == 'endpoint' else 'lmstudio')
    else: previous['deployment_id'] = 'other'
    with pytest.raises((ValueError, AssemblyError)): await finish(rig)
    assert calls == rig.nodes.calls and not rig.rebindings
    assert not rig.restart()._path('clean-restart').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['name', 'deleted', 'revision', 'binding', 'selection'])
async def test_changed_stopped_directory_blocks_all_restart_operations(rig, change):
    await clean_restart(rig)
    calls = deepcopy(rig.nodes.calls)
    if change == 'name': rig.rows[0]['name'] = 'owner change'
    elif change == 'deleted': rig.rows.clear()
    elif change == 'revision': rig.rows[0]['revision'] += 1
    elif change == 'binding': rig.rows[0]['binding']['generation'] += 1
    else: rig.rows[0]['models'] = []
    with pytest.raises(AssemblyError, match='publication changed'): await finish(rig)
    assert calls == rig.nodes.calls and not rig.rebindings
