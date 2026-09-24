import copy
import json
import sys

import pytest

from test_model_engines import load
from pantheon.models.managed import package, validate


def config(operation='image'):
    return dict(recipe_id=('sglang-wan-0.5.20-linux-amd64' if operation == 'video' else 'sglang-diffusion-0.5.20-linux-amd64'),
        model_recipe_id=('wan2-1-t2v-1-3b-0fad780a' if operation == 'video' else 'sdxl-turbo-71153311'),
        context_length=512, parallel=1, keep_alive_seconds=0, load_policy='resident',
        resources=dict(memory_bytes=(48 if operation == 'video' else 24) << 30, devices=[dict(id='GPU-fixture', backend='cuda',
                                                         memory_bytes=20 << 30, exclusive=True)]))


@pytest.mark.parametrize('operation', ['image', 'video'])
def test_owned_diffusion_package_uses_exact_readonly_weights_and_resources(operation):
    value = config(operation)
    models = load('diffusion_models')
    digest = models.source(models.model(value['model_recipe_id']))['sha256']
    with package(value, 'linux-amd64') as path:
        manifest = json.loads((path / 'fleet.json').read_text())
        component, = manifest['components']
        assert component['resources'] == value['resources']
        assert component['image'] == 'lmsysorg/sglang@sha256:4bf342cb756a7105e6df9ae81abeb62e891ff70d34b83fdd7a891fa46a494eca'
        assert component['read_only_mounts'] == {'package': '/fleet/package', 'cache/diffusion-models/' + digest: '/fleet/weights'}
        assert component['ports'] == {'http': 30000}
        assert component['argv'] == ['python3', '/fleet/package/sglang_diffusion_runtime.py', 'start']
        assert manifest['dependencies']['container_engine']['provision'] == 'never'
        assert not manifest.get('hooks')
        assert (path / 'pinned_models.py').is_file()
        assert (path / 'diffusion-models.json').is_file()


@pytest.mark.parametrize('change', [dict(model_recipe_id='kokoro-82m-v1'), dict(model_artifact_sha256='a'*64),
    dict(parallel=2), dict(context_length=4096), dict(keep_alive_seconds=30), dict(load_policy='on_demand'),
    dict(load_policy='manual'), dict(resources=dict(memory_bytes=8 << 30, devices=config()['resources']['devices']))])
@pytest.mark.parametrize('operation', ['image', 'video'])
def test_diffusion_config_refuses_unaccounted_resources_and_lifetime(operation, change):
    with pytest.raises(ValueError):
        validate({**config(operation), **change}, 'linux-amd64')


@pytest.mark.parametrize('change', [dict(memory_bytes=8 << 30), dict(exclusive=False), dict(backend='metal'), dict(id='apple-metal')])
@pytest.mark.parametrize('operation', ['image', 'video'])
def test_diffusion_requires_budgeted_exclusive_cuda_device(operation, change):
    value = config(operation)
    value['resources']['devices'][0].update(change)
    with pytest.raises(ValueError):
        validate(value, 'linux-amd64')


@pytest.mark.parametrize('operation', ['image', 'video'])
def test_wrapper_checks_exact_identity_and_actual_device_memory(operation, monkeypatch):
    models = load('diffusion_models')
    monkeypatch.setitem(sys.modules, 'diffusion_models', models)
    monkeypatch.setitem(sys.modules, 'engines', load('engines'))
    wrapper = load('sglang_diffusion_runtime')
    value = config(operation)
    source = models.source(models.model(value['model_recipe_id']))
    argv = wrapper.launch(value, {'source': source}, 24 << 30)
    assert argv[argv.index('--served-model-name') + 1] == 'fleet-diffusion-' + source['sha256']
    assert argv[argv.index('--model-path') + 1].endswith('/snapshots/' + source['revision'])
    assert argv[argv.index('--backend') + 1] == 'diffusers'
    assert argv[argv.index('--num-gpus') + 1] == '1'
    assert ('--text-encoder-cpu-offload' in argv) == (operation == 'video')
    assert '--trust-remote-code' not in argv
    with pytest.raises(ValueError, match='budget'):
        wrapper.launch(value, {'source': source}, 16 << 30)
    with pytest.raises(ValueError, match='exact pinned'):
        wrapper.launch(value, {'source': {**source, 'sha256': 'f' * 64}}, 24 << 30)
    env = wrapper.environment(dict(PATH='/usr/bin', HF_TOKEN='secret', API_KEY='secret',
        HTTP_PROXY='http://remote', PANTHEON_APP_RPC_TOKEN='owner', SGLANG_OTHER='override', CUDA_VISIBLE_DEVICES='0'))
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
    assert env['CUDA_VISIBLE_DEVICES'] == '0'
    assert not {'HF_TOKEN', 'API_KEY', 'HTTP_PROXY', 'PANTHEON_APP_RPC_TOKEN', 'SGLANG_OTHER'} & env.keys()


def test_owned_image_plan_uses_explicit_resolution_budget_without_changing_attached_capabilities():
    driver = load('job_drivers')
    value = dict(engine='sglang', managed=config())
    body = dict(job_id='image-test', operation='image', model='exact', input={'text': 'A blue cup'}, parameters={})
    assert driver.prepare(value, body)['payload']['size'] == '512x512'
    large = {**body, 'parameters': {'size': '1024x1024'}}
    with pytest.raises(ValueError, match='512'):
        driver.prepare(value, large)
    assert driver.prepare({'engine': 'sglang'}, large)['payload']['size'] == '1024x1024'
    wrong = {**value, 'managed': {**config(), 'recipe_id': 'sglang-0.5.20-linux-amd64'}}
    with pytest.raises(ValueError, match='SGLang Diffusion'):
        driver.prepare(wrong, body)


@pytest.mark.parametrize('operation', ['image', 'video'])
def test_connector_model_identity_and_image_only_inference(operation, tmp_path, monkeypatch):
    from test_model_services import connector_module
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-image-test')
    connector = connector_module.Connector(tmp_path / 'connector')
    monkeypatch.setattr(connector.module('engines'), 'native_platform', lambda: 'linux-amd64')
    value = config(operation)
    resources = value.pop('resources')
    value.update(memory_bytes=resources['memory_bytes'], scope='engine-image-test')
    connector.configure(dict(engine='sglang', endpoint='http://127.0.0.1:30000'), managed=value)
    control = connector.model_control()
    models = connector.module('diffusion_models')
    selected = models.model(value['model_recipe_id'])
    model_id = models.served_name(selected)
    monkeypatch.setattr(models, 'prepared', lambda *args: {'source': models.source(selected)})
    observed = {'data': [{'id': model_id}]}
    monkeypatch.setattr(control, 'request', lambda *args, **kwargs: observed)
    try:
        state = control.status()['models'][0]
        assert state['loaded'] and state['inference_ready'] and state['operations'] == [operation]
        assert state['memory_bytes'] is None
        assert control.inference_model({'model': model_id, 'prompt': 'cup'})['id'] == model_id
        with pytest.raises(ValueError, match='does not match'):
            control.inference_model({'model': 'other', 'prompt': 'cup'})
        with pytest.raises(ValueError, match=operation + ' requests only'):
            control.inference_model({'model': model_id, 'messages': []})
        with pytest.raises(ValueError, match='resident'):
            control.submit('unload', 'unload', model_id=model_id)
        observed['data'] = []
        assert control.status()['models'][0]['inference_ready'] is False
        observed['data'] = [{'id': 'another-model'}]
        with pytest.raises(ValueError, match='identity changed'):
            control.status()
    finally:
        control.owner.__exit__(None, None, None)
        control.db.close()
        connector.downloads().close()


@pytest.mark.parametrize('operation', ['image', 'video'])
def test_manager_checks_weight_preparation_before_starting_owned_engine(operation, monkeypatch):
    import asyncio
    from pantheon.models.manager import ModelServiceManager
    row = dict(deployment_id='image-test', name='Image test', node_id='gpu', engine='sglang',
               mode='managed', state='draft', managed=config(operation), models=[], revision=0)
    class Client:
        async def save(self, value):
            return copy.deepcopy(value)
    manager = ModelServiceManager(client=Client())
    calls = []
    async def node(*args, **kwargs):
        return dict(capability=dict(os='linux', arch='amd64', runtimes={'app-readonly-mounts': '1'}))
    async def ensure(value, **kwargs):
        calls.append(kwargs)
        return {'instance_id': 'owned-connector'}
    async def rpc(binding, method, args=None):
        if method == 'engines_catalog':
            return {'recipes': [load('engines').recipe(config(operation)['recipe_id'], target='linux-amd64')]}
        assert method == 'diffusion_models' and args['action'] == 'status'
        return {'ready': False}
    manager.node, manager.ensure, manager.rpc = node, ensure, rpc
    with pytest.raises(ValueError, match='Finish preparing'):
        asyncio.run(manager.start_managed(row))
    assert calls == [{}]  # only connector; no engine installed or started
