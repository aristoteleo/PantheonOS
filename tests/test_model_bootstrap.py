"""Recover owner startup without duplicating the generic Fleet operation ledger."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.models.bootstrap import ModelServiceBootstrap, recipe
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
    rows, registrations = [], []
    class Manager:
        def __init__(self):
            self.client = self
            self.lose_reply = False
            self.admission = True
        async def deployments(self): return deepcopy(rows)
        async def register_prepared(self, deployment_id, name, binding, config, selected):
            instance = nodes.states[binding['node_id']]['instances'][binding['instance_id']]
            assert instance['state'] == 'ready' and instance['generation'] == binding['generation']
            assert not nodes.states['worker']['instances'], 'Consumer cannot start before registration'
            registrations.append(binding)
            row = dict(deployment_id=deployment_id, name=name, binding=binding, models=selected,
                       config_revision='d'*64, revision=1)
            if rows: assert rows == [row]
            else: rows.append(row)
            if self.lose_reply:
                self.lose_reply = False
                raise TimeoutError('lost directory save reply')
            return deepcopy(row)
        async def rpc(self, binding, method):
            assert method in ('status','activity')
            return dict(config_revision='d'*64, accepting=self.admission, active_model_operations=0)
    manager = Manager()
    deployment = coordinator(tmp_path/'owner', nodes, Authority(nodes))
    def restart(): return ModelServiceBootstrap(deployment, manager, tmp_path/'bootstrap')
    return SimpleNamespace(nodes=nodes, manager=manager, spec=spec, restart=restart, rows=rows,
                           registrations=registrations, deployment=deployment, root=tmp_path)


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
