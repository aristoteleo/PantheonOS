import json
import sys

import pytest

from test_model_engines import load
from pantheon.models.managed import package, validate


def config():
    return dict(recipe_id='speaches-0.9.0-rc.3-linux-amd64-cpu', model_recipe_id='kokoro-82m-v1',
                context_length=512, parallel=1, keep_alive_seconds=0, load_policy='resident',
                resources=dict(memory_bytes=4 << 30, devices=[]))


def test_pinned_speech_package_owns_private_cache_and_cpu_memory():
    value = config()
    with package(value, 'linux-amd64') as path:
        manifest = json.loads((path / 'fleet.json').read_text())
        component, = manifest['components']
        assert component['runtime'] == 'container' and component['run_as_owner'] is True
        assert component['resources'] == value['resources']
        assert component['image'].endswith('2163775b6df5e451a71200e8f675fed68dbd8ab184fc604453d549e486f22fd2')
        assert len(component['read_only_mounts']) == 2
        assert set(component['read_only_mounts'].values()) == {'/fleet/weights', '/fleet/package'}
        assert manifest['dependencies']['container_engine']['provision'] == 'never'
        assert component['readiness']['argv'][-1] == 'ready'
        assert (path / 'speech-models.json').is_file()
        assert not manifest.get('hooks')


@pytest.mark.parametrize('change', [dict(parallel=2), dict(load_policy='warm', keep_alive_seconds=30),
    dict(model_recipe_id='untrusted'), dict(context_length=8192),
    dict(resources=dict(memory_bytes=1 << 30, devices=[])),
    dict(resources=dict(memory_bytes=4 << 30, devices=[dict(id='GPU-1')]))])
def test_speech_configuration_rejects_unaccounted_models_and_resources(change):
    with pytest.raises(ValueError):
        validate({**config(), **change}, 'linux-amd64')


def test_speech_environment_is_offline_without_inherited_credentials(monkeypatch):
    monkeypatch.setitem(sys.modules, 'speech_models', load('speech_models'))
    runtime = load('speaches_runtime')
    env = runtime.environment({'HF_TOKEN': 'secret', 'API_KEY': 'other', 'PANTHEON_APP_RPC_TOKEN': 'owner',
                               'HTTP_PROXY': 'https://remote', 'PRELOAD_MODELS': '["unbudgeted"]', 'PATH': '/usr/bin'})
    assert env['HF_HUB_OFFLINE'] == '1'
    assert env['HF_HUB_CACHE'] == '/fleet/weights/hub'
    assert env['PRELOAD_MODELS'] == '[]'
    assert env['WHISPER__INFERENCE_DEVICE'] == 'cpu'
    assert not {'HF_TOKEN', 'API_KEY', 'HTTP_PROXY', 'PANTHEON_APP_RPC_TOKEN'} & env.keys()


def test_managed_speech_identity_and_memory_status(tmp_path, monkeypatch):
    from test_model_services import connector_module
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-test')
    connector = connector_module.Connector(tmp_path / 'connector')
    monkeypatch.setattr(connector.module('engines'), 'native_platform', lambda: 'linux-amd64')
    value = config()
    value.pop('resources')
    value.update(memory_bytes=4 << 30, scope='engine-test')
    connector.configure(dict(engine='speaches', endpoint='http://127.0.0.1:8000'), managed=value)
    control = connector.model_control()
    models = connector.module('speech_models')
    selected = models.model(value['model_recipe_id'])
    observed = dict(model=selected['model'], loaded=True, sha256=models.source(selected)['sha256'], revision=selected['revision'])
    monkeypatch.setattr(control, 'request', lambda *args, **kwargs: observed)
    try:
        assert control.status()['models'][0]['loaded'] is True
        assert control.status()['models'][0]['memory_bytes'] is None
        assert control.inference_model({'model': selected['model']}, prepare=True)['id'] == selected['model']
        with pytest.raises(ValueError, match='does not match'):
            control.inference_model({'model': 'other'})
        with pytest.raises(ValueError, match='resident'):
            control.submit('unload-test', 'unload', model_id=selected['model'])
        observed['loaded'] = False
        assert control.status()['models'][0]['inference_ready'] is False
        observed['sha256'] = 'f' * 64
        with pytest.raises(ValueError, match='identity changed'):
            control.status()
    finally:
        control.owner.__exit__(None, None, None)
        control.db.close()
        connector.downloads().close()
