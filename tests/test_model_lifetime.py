"""Real connector HTTP admission against a controllable local engine fixture."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
import threading
import time

import httpx
import pytest

from test_model_control import configured
from test_model_services import connector_module, serve


MODEL = 'fleet/' + 'a' * 64 + ':latest'
OTHER = 'fleet/' + 'c' * 64 + ':latest'


@contextmanager
def running(tmp_path, monkeypatch, policy, *, hold=False, parallel=1):
    state = dict(loaded=set(), loads=[], unloads=[], requests=[], digest='b' * 64,
                 fail_unload=False, warmups=[])
    loading, release = threading.Event(), threading.Event()
    if not hold:
        release.set()

    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/api/tags':
                self.reply({'models': [{'name': m, 'digest': state['digest']} for m in (MODEL, OTHER)]})
            elif self.path == '/api/ps':
                self.reply({'models': [{'name': m} for m in state['loaded']]})
            else:
                raise AssertionError(self.path)

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            model = data['model']
            if self.path == '/api/generate':
                assert data['prompt'] == '' and data['options']['num_ctx'] == 4096
                if data['keep_alive']:
                    state['loads'].append((model, data['keep_alive']))
                    loading.set()
                    assert release.wait(5)
                    state['loaded'].add(model)
                else:
                    if state['fail_unload']:
                        return self.reply({'error': 'fixture unload failure'}, 503)
                    state['unloads'].append(model)
                    state['loaded'].discard(model)
                self.reply({'done': True})
            elif self.path == '/v1/chat/completions':
                assert model in state['loaded']
                if data.get('messages') == [{'role': 'user', 'content': 'Warm up the model. Respond with two short words.'}]:
                    assert data['max_tokens'] == 2 and data['stream'] is False
                    state['warmups'].append(model)
                    return self.reply({'choices': [{'message': {'content': 'Ready now'}, 'finish_reason': 'length'}],
                                       'usage': {'completion_tokens': 2}})
                state['requests'].append(model)
                self.reply({'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
            else:
                raise AssertionError(self.path)

    with serve(Engine) as endpoint:
        connector = configured(tmp_path, monkeypatch, endpoint)
        connector.config['managed'].update(load_policy=policy, parallel=parallel,
            keep_alive_seconds=30 if policy in {'warm', 'manual'} else 0)
        control = connector.model_control()
        for model in (MODEL, OTHER):
            control.save_model(dict(id=model, name=model, artifact={'size': 100}, manifest_digest='b' * 64))
        with serve(connector_module.handler(connector)) as url:
            try:
                yield connector, control, state, url, loading, release
            finally:
                release.set()
        if connector._model_control is not None:
            connector._model_control.close()
        if connector._downloads is not None:
            connector._downloads.close()


def call(connector, url, request, model=MODEL, **extra):
    return httpx.post(url + '/v1/chat/completions', json={'model': model, **extra},
        headers={'X-Model-Config': connector.revision, 'X-Model-Request': request}, timeout=8)


@pytest.mark.parametrize('driver', ['ollama', 'lmstudio'])
def test_model_jobs_remain_observable_during_engine_catalog_failure(tmp_path, monkeypatch, driver):
    from types import SimpleNamespace
    with running(tmp_path, monkeypatch, 'resident', hold=True) as (connector, control, state, url, loading, release):
        control.submit('loading', 'load', model_id=MODEL)
        assert loading.wait(2)
        original_request, original_driver = control.request, control.llmster
        def unavailable(*args, **kwargs):
            raise ValueError('Vendor model does not exist while loading; private engine diagnostic')
        if driver == 'lmstudio':
            monkeypatch.setattr(control, 'llmster', lambda: SimpleNamespace(observed=unavailable))
        else:
            monkeypatch.setattr(control, 'request', unavailable)
        response = httpx.post(url + '/rpc', json={'method': 'models_status'},
            headers={'X-Fleet-RPC-Token': connector.rpc_token})
        assert response.status_code == 200
        status = response.json()
        assert status['jobs'][0]['job_id'] == 'loading' and status['jobs'][0]['state'] == 'running'
        assert status['observation_error'] and 'private engine diagnostic' not in response.text
        assert all(m['loaded'] is None and m['inference_ready'] is False for m in status['models'])
        assert all(m['memory_bytes'] is None and m['gpu_memory_bytes'] is None for m in status['models'])
        assert all(m['inference_ready'] is False for m in connector.route_state()['models'])
        with pytest.raises(ValueError, match='discovery is temporarily unavailable'):
            connector.discover()
        monkeypatch.setattr(control, 'request', original_request)
        monkeypatch.setattr(control, 'llmster', original_driver)
        release.set()
        control.worker.join(3)
        assert not control.worker.is_alive()
        status = control.status()
        assert not status.get('observation_error') and status['jobs'][0]['state'] == 'succeeded'
        assert next(m for m in status['models'] if m['id'] == MODEL)['loaded'] is True


@pytest.mark.parametrize('policy,ttl', [('warm', 30), ('resident', -1), ('on_demand', -1)])
def test_policy_autoload_reuse_switch_and_release(tmp_path, monkeypatch, policy, ttl):
    with running(tmp_path, monkeypatch, policy) as (connector, control, state, url, _, _):
        # Metadata probes never load a cold candidate.
        assert connector.route_state()['models'][0]['loaded'] is False
        assert state['loads'] == []
        assert call(connector, url, 'first').status_code == 200
        assert state['loads'] == [(MODEL, ttl)]
        assert state['loaded'] == (set() if policy == 'on_demand' else {MODEL})
        assert call(connector, url, 'second').status_code == 200
        assert len(state['loads']) == (2 if policy == 'on_demand' else 1)
        # An engine TTL expiring is not a permanent failure: the next admitted
        # call reloads the same imported model with the same budget and context.
        state['loaded'].clear()
        assert call(connector, url, 'expired').status_code == 200
        assert call(connector, url, 'switch', OTHER).status_code == 200
        assert state['loaded'] == (set() if policy == 'on_demand' else {OTHER})
        assert connector.activity_status()['active_calls'] == 0
        assert connector.lifetime_error == ''
        assert control.load_estimates([{'id': MODEL}])[0]['load_samples'] >= 2


def test_parallel_cold_calls_share_one_load_then_release_batch(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand', hold=True, parallel=2) as (connector, _, state, url, loading, release):
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(call, connector, url, 'one')
            assert loading.wait(3)
            second = pool.submit(call, connector, url, 'two')
            deadline = time.monotonic() + 3
            while connector.activity_status()['active_calls'] < 2 and time.monotonic() < deadline:
                time.sleep(.01)
            assert connector.activity_status()['active_calls'] == 2
            assert state['loads'] == [(MODEL, -1)]
            release.set()
            assert first.result().status_code == second.result().status_code == 200
        assert state['requests'] == [MODEL, MODEL]
        assert state['unloads'] == [MODEL] and not state['loaded']


def test_cancel_and_drain_during_cold_load_never_submit_inference(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand', hold=True) as (connector, _, state, url, loading, release):
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(call, connector, url, 'cancel-loading')
            assert loading.wait(3)
            started = time.monotonic()
            assert connector.cancel('cancel-loading')['cancelled']
            assert time.monotonic() - started < .2
            assert connector.drain()['safe_to_stop'] is False
            release.set()
            # The cancelled HTTP client may see EOF; activity is authoritative.
            try:
                pending.result()
            except httpx.RemoteProtocolError:
                pass
        assert state['requests'] == [] and not state['loaded']
        assert connector.activity_status()['requests'][0]['state'] == 'cancelled'
        assert connector.drain()['safe_to_stop'] is True


def test_invalid_target_or_unknown_loaded_model_is_not_evicted(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'warm') as (connector, _, state, url, _, _):
        state['loaded'] = {OTHER}
        assert call(connector, url, 'override', load_policy='on_demand').status_code == 400
        state['digest'] = 'd' * 64
        assert call(connector, url, 'changed').status_code == 400
        assert state['loaded'] == {OTHER} and state['unloads'] == []
        state['digest'] = 'b' * 64
        state['loaded'] = {'foreign-model'}
        assert call(connector, url, 'unknown').status_code == 400
        assert state['loaded'] == {'foreign-model'} and state['unloads'] == []
        assert not state['requests'] and not state['loads']


def test_unload_failure_blocks_new_inference_until_owner_recovers(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (connector, _, state, url, _, _):
        state['fail_unload'] = True
        assert call(connector, url, 'first').status_code == 200
        assert state['loaded'] == {MODEL}
        assert connector.activity_status()['lifetime_error']
        assert not connector.activity_status()['accepting']
        assert not connector.route_state()['ready']
        assert call(connector, url, 'blocked').status_code == 503
        assert state['requests'] == [MODEL]
        with pytest.raises(ValueError, match='unload was not confirmed'):
            connector.resume(connector.revision)
        state['fail_unload'] = False
        assert connector.resume(connector.revision)['accepting']
        assert not state['loaded'] and not connector.lifetime_pending
        assert call(connector, url, 'recovered').status_code == 200
        assert not state['loaded'] and connector.lifetime_error == ''


def test_activity_write_failure_still_releases_model_and_admission(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (connector, _, state, url, _, _):
        update = connector.activity.update
        def failing_update(request, **values):
            if 'ended_at' in values:
                raise OSError('fixture disk unavailable')
            return update(request, **values)
        monkeypatch.setattr(connector.activity, 'update', failing_update)
        assert call(connector, url, 'disk-failure').status_code == 200
        assert not state['loaded'] and not connector.calls and not connector.maintenance
        assert not connector.lifetime_pending


def test_restart_waits_for_verified_owner_resume_before_eviction(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (old, control, state, _, _, _):
        # Simulate a connector crash leaving its engine and a running record.
        old.path.write_text(json.dumps(old.config))
        state['loaded'] = {MODEL}
        old.activity.create('interrupted', MODEL, 'chat/completions', old.revision)
        old.activity.update('interrupted', state='running')
        control.close()
        old._model_control = None
        old._downloads.close()
        old._downloads = None
        fresh = connector_module.Connector(old.data)
        try:
            with serve(connector_module.handler(fresh)) as url:
                assert fresh.lifetime_pending
                assert state['unloads'] == []  # Saved port alone does not confer ownership.
                assert not fresh.route_state()['ready']
                assert call(fresh, url, 'before-recovery').status_code == 503
                assert state['unloads'] == []
                assert fresh.activity_status()['requests'][0]['state'] == 'unknown'
                with pytest.raises(ValueError, match='Configuration changed'):
                    fresh.resume('wrong-revision')
                assert state['unloads'] == []
                assert fresh.resume(fresh.revision)['accepting']
                assert state['unloads'] == [MODEL]
                assert call(fresh, url, 'after-recovery').status_code == 200
                assert not state['loaded']
        finally:
            if fresh._model_control is not None:
                fresh._model_control.close()
            fresh.downloads().close()


def test_concurrent_stop_wins_over_slow_resume(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (connector, control, state, url, _, _):
        started, finish = threading.Event(), threading.Event()
        original = control.release_idle
        def slow_release():
            started.set()
            assert finish.wait(3)
            return original()
        monkeypatch.setattr(control, 'release_idle', slow_release)
        with ThreadPoolExecutor(1) as pool:
            resuming = pool.submit(connector.resume, connector.revision)
            assert started.wait(3)
            assert not connector.drain()['safe_to_stop']
            finish.set()
            with pytest.raises(ValueError, match='drained during recovery'):
                resuming.result()
        assert not connector.accepting and connector.drain()['safe_to_stop']
        assert call(connector, url, 'after-stop').status_code == 503
        assert not state['loads']
