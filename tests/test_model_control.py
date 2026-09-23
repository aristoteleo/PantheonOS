import hashlib
import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from test_model_services import connector_module, serve


def configured(tmp_path, monkeypatch, endpoint):
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-test')
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(tmp_path / 'cache'))
    connector = connector_module.Connector(tmp_path / 'data')
    engines = connector.module('engines')
    monkeypatch.setattr(engines, 'recipe', lambda _: {'engine': 'ollama'})
    connector.configure({'engine': 'ollama', 'endpoint': endpoint}, managed=dict(
        scope='engine-test', recipe_id='test', context_length=4096, parallel=1,
        keep_alive_seconds=300, memory_bytes=2 << 30))
    return connector


def cache_weight(connector):
    content = b'GGUF' + b'fixture' * 5
    digest = hashlib.sha256(content).hexdigest()
    downloads = connector.downloads()
    (downloads.cache.root / digest).write_bytes(content)
    source = dict(url='https://example.invalid/model?secret=signed', sha256=digest,
                  name='fixture.gguf', format='gguf', revision='exact-commit', size=len(content))
    downloads.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', ('download', json.dumps(source), 'ready', len(content), 1, ''))
    downloads.db.commit()
    return content, 'fleet/' + digest + ':latest'


def test_owned_import_load_unload_and_inference_admission(tmp_path, monkeypatch):
    state = {'blob': None, 'model': None, 'loaded': False}
    loading, finish = threading.Event(), threading.Event()
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, body, status=200):
            data = json.dumps(body).encode(); self.send_response(status)
            self.send_header('Content-Length', str(len(data)));self.end_headers()
            if self.command != 'HEAD': self.wfile.write(data)
        def do_HEAD(self): self.reply({}, 200 if state['blob'] else 404)
        def do_GET(self):
            if self.path == '/api/tags':
                self.reply({'models': [{'name': state['model'], 'digest': 'b' * 64, 'details': {'quantization_level': 'Q4_K_M'}}]})
            elif self.path == '/api/ps':
                self.reply({'models': [{'name': state['model'], 'size': 123, 'size_vram': 100}] if state['loaded'] else []})
            else: raise AssertionError(self.path)
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.path.startswith('/api/blobs/'):
                state['blob'] = raw;self.reply({},201);return
            data = json.loads(raw)
            if self.path == '/api/create':
                state['model'] = data['model'];assert data['parameters']['num_ctx'] == 4096
            elif self.path == '/api/generate':
                assert data['prompt'] == '' and data['options']['num_ctx'] == 4096
                if data['keep_alive']:
                    loading.set();finish.wait(5)
                state['loaded'] = bool(data['keep_alive'])
            else: raise AssertionError(self.path)
            self.reply({'status':'success','done':True})
    with serve(Engine) as endpoint:
        connector = configured(tmp_path, monkeypatch, endpoint)
        expected, model_id = cache_weight(connector)
        control = connector.model_control()
        try:
            control.submit('import-1', 'import', 'download');control.worker.join(5)
            status = control.status()
            assert status['jobs'][0]['state'] == 'succeeded'
            assert status['jobs'][0]['elapsed_seconds'] >= 0
            assert status['jobs'][0]['started_at'] > 0
            elapsed = status['jobs'][0]['elapsed_seconds']
            assert state['blob'] == expected
            assert status['models'][0]['id'] == model_id and not status['models'][0]['loaded']
            assert 'secret' not in json.dumps(status)
            control.submit('import-1', 'import', 'download')  # Same request is never replayed.
            assert len(control.status()['jobs']) == 1
            assert control.status()['jobs'][0]['elapsed_seconds'] == elapsed
            with pytest.raises(ValueError, match='different operation'):
                control.submit('import-1', 'load', model_id=model_id)
            control.submit('load-1','load',model_id=model_id)
            assert loading.wait(2)
            with pytest.raises(ValueError, match='active requests'):
                control.submit('unload-race','unload',model_id=model_id)
            with pytest.raises(ValueError, match='active requests'):
                connector.configure({'engine':'ollama','endpoint':endpoint})
            with serve(connector_module.handler(connector)) as url, httpx.Client() as client:
                response = client.post(url+'/v1/chat/completions',json={'model':model_id},headers={
                    'X-Model-Config':connector.revision, 'X-Model-Request':'concurrent'})
                assert response.status_code == 503
                assert client.post(url+'/rpc',json={'method':'models_submit','args':{}}).status_code == 403
            finish.set();control.worker.join(5)
            assert control.status()['models'][0]['loaded']
            measurement = control.status()['models'][0]
            assert measurement['cold_load_ms'] > 0 and measurement['load_samples'] == 1
            control.submit('warm-noop', 'load', model_id=model_id);control.worker.join(5)
            assert control.status()['models'][0]['cold_load_ms'] == measurement['cold_load_ms']
            assert control.status()['models'][0]['load_samples'] == 1
            control.forget('warm-noop')
            control.submit('unload-1','unload',model_id=model_id);control.worker.join(5)
            assert not control.status()['models'][0]['loaded']
            probe = connector.route_state()['models'][0]
            assert probe['cold_load_ms'] == measurement['cold_load_ms'] and probe['load_samples'] == 1
            assert probe['inference_ready'] is True  # Ollama can load on inference.
            assert set(probe) == {'id', 'loaded', 'inference_ready', 'cold_load_ms', 'load_samples', 'load_measured_at'}
            assert all(j['state']=='succeeded' for j in control.status()['jobs'])
            assert (connector.downloads().cache.root / model_id.split('/')[1].split(':')[0]).exists()
            control.forget('load-1')
            assert len(control.status()['jobs']) == 2
            assert control.inference_model({'model': model_id})['id'] == model_id
            for body in [{'model': 'unimported'}, {'model': model_id, 'keep_alive': -1},
                         {'model': model_id, 'options': {'num_ctx': 100000}},
                         {'model': model_id, 'max_tokens': 10000}, {'model': model_id, 'n': 4}]:
                with pytest.raises(ValueError):
                    control.inference_model(body)
            state['model'] = 'different'
            with pytest.raises(ValueError, match='identity changed'):
                control.inference_model({'model': model_id})
        finally:
            finish.set();control.close();connector.downloads().close()


def test_model_control_is_explicitly_owned_and_rejects_paths(tmp_path, monkeypatch):
    connector = connector_module.Connector(tmp_path/'attached')
    connector.configure({'engine':'ollama','endpoint':'http://127.0.0.1:1'})
    with pytest.raises(ValueError, match='owned engine'):
        connector.model_control()
    connector = configured(tmp_path,monkeypatch,'http://127.0.0.1:1')
    control = connector.model_control()
    try:
        for kwargs in [dict(action='load', model_id='../../weights'),dict(action='import',artifact_job_id='/etc/passwd'),dict(action='shell')]:
            with pytest.raises(ValueError):control.submit('job',**kwargs)
        connector.calls['inference'] = {}
        with pytest.raises(ValueError,match='active requests'):
            control.submit('job','import','download')
        connector.calls.clear()
        # A transport interruption is durable and not silently replayed on reopen.
        control.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('lost','{}','running','Loading',1,''));control.db.commit()
        control.close()
        recovered = connector.module('model_control').ModelControl(connector)
        try:
            assert recovered.db.execute('SELECT state FROM jobs WHERE id=?',('lost',)).fetchone()[0] == 'unknown'
            assert recovered.worker is None
            monkeypatch.setattr(recovered, 'request', lambda *args, **kwargs: {'models': []})
            assert recovered.status()['jobs'][0]['elapsed_seconds'] is None
        finally:recovered.close()
    finally:
        connector.downloads().close()


@pytest.mark.parametrize('policy', ['manual', 'resident'])
def test_llmster_import_uses_owned_runtime_preserves_blobs_and_confirms_context(tmp_path, monkeypatch, policy):
    connector = configured(tmp_path, monkeypatch, 'http://127.0.0.1:1')
    connector.config['engine'] = 'lmstudio'
    content, model_id = cache_weight(connector)
    control = connector.model_control()
    driver = control.llmster()
    config = connector.config['managed']
    config.update(load_policy=policy, keep_alive_seconds=0 if policy == 'resident' else 300)
    home = tmp_path/'cache/models/lmstudio/engine-test'
    home.mkdir(parents=True)
    cli = tmp_path/'runtime/lms'
    cli.parent.mkdir(); cli.write_text('fixture')
    monkeypatch.setattr(driver, 'owned', lambda: (config, home, cli))
    rows, commands = [], []
    def run(args):
        commands.append(args)
        if args[0] == 'import':
            namespace = args[args.index('--user-repo')+1]
            path = home/'.lmstudio/models'/namespace/'model.gguf'
            path.parent.mkdir(parents=True)
            import os
            os.link(args[1], path)
            rows.append({'publisher': 'fleet', 'key':namespace.split('/')[1], 'format': 'gguf', 'size_bytes':len(content), 'loaded_instances': []})
        else:
            ttl = [] if policy == 'resident' else ['--ttl', '300']
            assert args == ['load', rows[0]['key'], '--identifier', model_id, '--context-length', '4096', '--parallel', '1', *ttl, '--gpu', 'max', '--yes']
            rows[0]['loaded_instances'] = [{'id': model_id, 'config': {'context_length':4096, 'parallel':1}}]
    monkeypatch.setattr(driver, 'command', run)
    monkeypatch.setattr(driver, 'catalog', lambda: rows)
    monkeypatch.setattr(control, 'llmster', lambda: driver)
    try:
        control.submit('import','import','download');control.worker.join(5)
        metadata = control.status()['models'][0]
        assert metadata['id'] == model_id and metadata['memory_bytes'] is None
        assert metadata['inference_ready'] is (policy == 'resident')
        assert '--hard-link' in commands[0]
        blob = connector.downloads().cache.root / metadata['artifact']['sha256']
        assert blob.read_bytes() == content
        if policy == 'resident':
            control.submit('load', 'preload', model_id=model_id, pool_revision=0)
        else:
            control.submit('load', 'load', model_id=model_id)
        control.worker.join(5)
        assert control.status()['jobs'][0]['state'] == 'succeeded'
        assert control.status()['models'][0]['load_samples'] == 1
        assert control.status()['models'][0]['inference_ready'] is True
        assert driver.memory(metadata, True) is None  # A warm no-op is not a cold sample.
        assert control.inference_model({'model':model_id})['id'] == model_id
        if policy == 'resident':
            assert control.status()['preload']['ready'] is True
            connector.resume(connector.revision)
            assert len(commands) == 2  # no repeated load for an already-warm replica
        rows[0]['loaded_instances'][0]['config']['context_length'] = 8192
        assert control.status()['models'][0]['inference_ready'] is False
        control.submit('bad-load', 'load', model_id=model_id);control.worker.join(5)
        assert control.status()['jobs'][0]['state'] == 'failed'
        assert control.status()['models'][0]['load_samples'] == 1
        with pytest.raises(ValueError, match='deployment settings'):
            control.inference_model({'model':model_id})
        rows[0]['loaded_instances'] = [{'id':'someone-else', 'config':{}}]
        with pytest.raises(ValueError, match='Unload the current model'):
            driver.memory(metadata, True)
    finally:
        control.close();connector.downloads().close()


def test_load_measurements_are_bounded_persistent_and_configuration_specific(tmp_path, monkeypatch):
    connector = configured(tmp_path, monkeypatch, 'http://127.0.0.1:1')
    control = connector.model_control()
    model_id = 'fleet/' + 'a' * 64 + ':latest'
    control.save_model({'id': model_id})
    try:
        for seconds in range(1, 21):
            control.record_load(model_id, seconds)
        for invalid in (0, -1, True, float('nan'), float('inf'), 601):
            control.record_load(model_id, invalid)
        control.record_load('not-imported', 1)
        measured = control.load_estimates([{'id': model_id}])[0]
        assert measured['load_samples'] == 16 and measured['cold_load_ms'] == 12500
        assert control.db.execute('SELECT COUNT(*) FROM loads').fetchone()[0] == 1
        control.close()
        control = connector.module('model_control').ModelControl(connector)
        assert control.load_estimates([{'id': model_id}])[0] == measured
        connector.config['managed']['context_length'] *= 2
        assert control.load_estimates([{'id': model_id}])[0]['cold_load_ms'] is None
        control.record_load(model_id, 3)
        assert control.load_estimates([{'id': model_id}])[0]['load_samples'] == 1
        clock = connector.module('model_control').time
        later = clock.time() + 8 * 86400
        monkeypatch.setattr(clock, 'time', lambda: later)
        assert control.load_estimates([{'id': model_id}])[0]['cold_load_ms'] is None
        control.record_load(model_id, 4)
        fresh = control.load_estimates([{'id': model_id}])[0]
        assert fresh['load_samples'] == 1 and fresh['cold_load_ms'] == 4000
    finally:
        control.close();connector.downloads().close()


def test_owned_tensor_parallel_config_is_retained_only_for_text_sglang():
    from types import SimpleNamespace
    from test_model_engines import load
    control = load('model_control')
    config = dict(scope='engine-test', recipe_id='sglang-0.5.20-linux-amd64',
                  context_length=4096, parallel=1, keep_alive_seconds=0,
                  memory_bytes=8 << 30, model_artifact_sha256='a' * 64,
                  tensor_parallel_size=2)
    recipes = SimpleNamespace(recipe=lambda _: {'engine': 'sglang'})
    assert control.management_config(config, 'sglang', 'model-test', recipes) == config
    for value in (0, 3, True, '2'):
        with pytest.raises(ValueError):
            control.management_config({**config, 'tensor_parallel_size': value}, 'sglang', 'model-test', recipes)
    other = {k: v for k, v in config.items() if k != 'model_artifact_sha256'}
    with pytest.raises(ValueError):
        control.management_config(other, 'ollama', 'model-test', SimpleNamespace(recipe=lambda _: {'engine': 'ollama'}))
