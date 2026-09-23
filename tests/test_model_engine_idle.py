"""Idle admission protocol: real connector HTTP, concurrent use and crash boundaries."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from test_model_lifetime import MODEL, call, running
from test_model_services import connector_module, serve


def idle_args(c, suspend_id='sleep-1'):
    return dict(suspend_id=suspend_id, config_revision=c.revision, idle_seconds=30,
                idle_epoch=c.idle.status()['idle_epoch'])


def age(c):
    with c.lock:
        c.idle.last_use = time.monotonic() - 60


def wake_args(c, request):
    return dict(suspend_id=request['suspend_id'], config_revision=request['config_revision'],
                config={k: v for k, v in c.config.items() if k != 'managed'}, managed=c.config['managed'])


def close_stores(c):
    if c._model_control is not None:
        c._model_control.close()
        c._model_control = None
    if c._downloads is not None:
        c._downloads.close()
        c._downloads = None
    c.activity.db.close()


def restart(c):
    engines = c.module('engines')
    close_stores(c)
    fresh = connector_module.Connector(c.data)
    fresh._modules['engines'] = engines
    return fresh


@pytest.mark.parametrize('policy', ['on_demand', 'warm'])
def test_idle_deadline_auth_fence_and_lost_ack(tmp_path, monkeypatch, policy):
    with running(tmp_path, monkeypatch, policy) as (c, _, state, url, _, _):
        request = idle_args(c)
        assert c.idle.drain(**request)['status'] == 'waiting'
        age(c)
        used = c.idle.last_use
        c.activity_status()
        c.route_state()
        assert used == c.idle.last_use
        for method in ('idle_drain', 'idle_resume', 'idle_reset'):
            response = httpx.post(url + '/rpc', json={'method': method, 'args': request},
                                  headers={'Authorization': 'Bearer inference-grant'})
            assert response.status_code == 403
        response = httpx.post(url + '/rpc', json={'method': 'idle_drain', 'args': request},
                              headers={'X-Fleet-RPC-Token': c.rpc_token})
        assert response.status_code == 200
        assert response.json()['safe_to_stop'] and not response.json()['accepting']
        assert c.idle.path.stat().st_mode & 0o777 == 0o600
        assert json.loads(c.idle.path.read_text())['phase'] == 'fenced'
        assert c.idle.drain(**request)['safe_to_stop']  # Lost response, same fence.
        assert call(c, url, 'after-fence').status_code == 503
        assert not state['loads'] and not state['requests'] and not state['unloads']
        with pytest.raises(ValueError, match='different operation'):
            c.idle.drain(**{**request, 'idle_seconds': 31})
        with pytest.raises(ValueError, match='idle operation'):
            c.resume(c.revision)
        with pytest.raises(ValueError, match='idle operation'):
            c.configure(wake_args(c, request)['config'], c.config['managed'])
        response = httpx.post(url + '/rpc', json={'method': 'idle_resume', 'args': wake_args(c, request)},
                              headers={'X-Fleet-RPC-Token': c.rpc_token})
        assert response.status_code == 200
        resumed = response.json()
        assert resumed['status'] == 'resumed' and resumed['accepting']
        assert not c.idle.drain(**request)['safe_to_stop']  # Old ACK cannot stop an awake engine.
        assert not c.idle.drain(**idle_args(c, 'sleep-2'))['safe_to_stop']  # New grace interval.
        assert call(c, url, 'awake').status_code == 200
        if policy == 'warm':
            state['loaded'].clear()  # Simulated vendor TTL, not forced eviction.
        age(c)
        second = idle_args(c, 'sleep-2')
        assert c.idle.drain(**second)['safe_to_stop']
        with pytest.raises(ValueError, match='generation changed'):
            c.idle.drain(**request)  # Even an older ID cannot replay after another idle cycle.


@pytest.mark.parametrize('policy', ['manual', 'resident'])
def test_idle_never_changes_explicit_model_lifetime(tmp_path, monkeypatch, policy):
    with running(tmp_path, monkeypatch, policy) as (c, _, state, _, _, _):
        age(c)
        with pytest.raises(ValueError, match='owned on-demand or warm'):
            c.idle.drain(**idle_args(c))
        assert c.accepting and not state['unloads']


def test_attached_engine_and_shortened_warm_lifetime_cannot_idle(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'warm') as (c, _, state, _, _, _):
        age(c)
        with pytest.raises(ValueError, match='preserve the model warm lifetime'):
            c.idle.drain(**{**idle_args(c), 'idle_seconds': 29})
        del c.config['managed']
        with pytest.raises(ValueError, match='owned on-demand or warm'):
            c.idle.drain(**idle_args(c))
        assert not state['loads'] and not state['unloads'] and c.accepting


def test_loaded_foreign_unknown_and_malformed_metadata_are_not_empty(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'warm') as (c, control, state, _, _, _):
        age(c)
        state['loaded'].add('not-imported-by-this-connector')
        assert not c.idle.drain(**idle_args(c))['safe_to_stop']
        assert state['loaded'] == {'not-imported-by-this-connector'} and state['unloads'] == []
        state['loaded'].clear()
        control.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', ('uncertain', '{}', 'unknown', '', 1, ''))
        control.db.commit()
        assert not c.idle.drain(**idle_args(c))['safe_to_stop']
        control.forget('uncertain')
        for body in ({}, {'models': None}, {'models': {}}, {'models': [None]}):
            monkeypatch.setattr(control, 'request', lambda *a, **kw: body)
            assert not c.idle.drain(**idle_args(c))['safe_to_stop']
            assert c.accepting
        monkeypatch.setattr(control, 'llmster', lambda: SimpleNamespace(catalog=lambda: [{'key': 'unknown'}]))
        assert not c.idle.drain(**idle_args(c))['safe_to_stop']
        monkeypatch.setattr(control, 'llmster', lambda: SimpleNamespace(catalog=lambda: [{'loaded_instances': [{}]}]))
        assert not c.idle.drain(**idle_args(c))['safe_to_stop']
        monkeypatch.setattr(control, 'llmster', lambda: SimpleNamespace(catalog=lambda: [{'loaded_instances': []}]))
        assert c.idle.drain(**idle_args(c))['safe_to_stop']


def test_new_request_during_idle_probe_invalidates_even_completed_use(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (c, control, state, url, _, _):
        entered, release = threading.Event(), threading.Event()
        def slow_observation():
            entered.set()
            assert release.wait(5)
            return True
        monkeypatch.setattr(control, 'empty_for_idle', slow_observation)
        age(c)
        request = idle_args(c)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(c.idle.drain, **request)
            assert entered.wait(2)
            try:
                assert c.idle.drain(**request)['status'] == 'waiting'
                assert call(c, url, 'arrived-during-idle-check').status_code == 200
                assert not c.calls and not c.maintenance and not state['loaded']
            finally:
                release.set()
            assert pending.result()['safe_to_stop'] is False
        assert c.accepting and not c.idle.path.exists()
        assert c.idle.drain(**request)['status'] == 'waiting'


@pytest.mark.parametrize('operation', ['inference', 'model-job'])
def test_busy_engine_is_not_fenced_and_gets_new_grace_after_completion(tmp_path, monkeypatch, operation):
    with running(tmp_path, monkeypatch, 'warm', hold=True) as (c, control, state, url, loading, release):
        age(c)
        with ThreadPoolExecutor(1) as pool:
            if operation == 'inference':
                pending = pool.submit(call, c, url, 'loading')
            else:
                control.submit('loading-job', 'load', model_id=MODEL)
            assert loading.wait(2)
            assert c.idle.drain(**idle_args(c))['status'] == 'busy'
            assert c.accepting
            release.set()
            if operation == 'inference':
                assert pending.result().status_code == 200
            else:
                control.worker.join(3)
                assert not control.worker.is_alive()
        state['loaded'].clear()
        assert c.idle.drain(**idle_args(c))['status'] == 'waiting'


def test_probe_failure_does_not_drain_or_hide_active_calls(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (c, control, _, url, _, _):
        def unavailable():
            raise OSError('engine is unavailable')
        monkeypatch.setattr(control, 'empty_for_idle', unavailable)
        age(c)
        response = httpx.post(url + '/rpc', json={'method': 'idle_drain', 'args': idle_args(c)},
                              headers={'X-Fleet-RPC-Token': c.rpc_token})
        assert response.status_code == 502
        assert c.accepting and not c.maintenance and not c.idle.checking
        assert call(c, url, 'after-failed-probe').status_code == 200


@pytest.mark.parametrize('field,value', [('context_length', 8192), ('parallel', 2),
    ('memory_bytes', 4 << 30), ('scope', 'engine-another'), ('recipe_id', 'another'),
    ('keep_alive_seconds', 60), ('load_policy', 'manual')])
def test_wake_cannot_change_owned_model_configuration(tmp_path, monkeypatch, field, value):
    with running(tmp_path, monkeypatch, 'warm') as (c, _, state, _, _, _):
        age(c)
        request = idle_args(c)
        assert c.idle.drain(**request)['safe_to_stop']
        wake = wake_args(c, request)
        wake['managed'] = {**wake['managed'], field: value}
        with pytest.raises(ValueError):
            c.idle.resume(**wake)
        assert c.idle.record['phase'] == 'fenced' and not c.accepting
        assert c.revision == request['config_revision']
        assert not state['loads'] and not state['unloads']


def test_failed_wake_keeps_durable_intent_and_can_resume_same_request(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (c, _, state, url, _, _):
        age(c)
        request = idle_args(c)
        wake = wake_args(c, request)
        assert c.idle.drain(**request)['safe_to_stop']
        # The owner already verified the engine; an unload acknowledgement can
        # still fail. A resumed connector must not admit inference on that basis.
        state['loaded'].add(MODEL)
        state['fail_unload'] = True
        with pytest.raises(ValueError, match='unload was not confirmed'):
            c.idle.resume(**wake)
        assert c.idle.record['phase'] == 'resuming' and not c.accepting
        assert call(c, url, 'failed-wake').status_code == 503
        state['fail_unload'] = False
        assert c.idle.resume(**wake)['accepting']
        assert not state['loaded']
        assert call(c, url, 'recovered-wake').status_code == 200


@pytest.mark.parametrize('policy', ['on_demand', 'warm'])
def test_fence_survives_restart_without_polling_or_waking_engine(tmp_path, monkeypatch, policy):
    with running(tmp_path, monkeypatch, policy) as (c, _, _, _, _, _):
        c.path.write_text(json.dumps(c.config))
        age(c)
        request = idle_args(c)
        wake = wake_args(c, request)
        assert c.idle.drain(**request)['safe_to_stop']
        fresh = restart(c)
        try:
            monkeypatch.setattr(fresh, 'model_control', lambda: pytest.fail('Idle preflight cannot touch the engine'))
            with serve(connector_module.handler(fresh)) as endpoint:
                assert not fresh.accepting
                assert fresh.idle.drain(**request)['safe_to_stop']
                assert fresh.route_state()['engine_idle']['admission_fenced']
                assert call(fresh, endpoint, 'after-restart').status_code == 503
                with pytest.raises(ValueError, match='idle operation'):
                    fresh.resume(fresh.revision)
            # No runtime/engine resources are acquired by this read-only test.
            assert not fresh.accepting and wake['config_revision'] == fresh.revision
        finally:
            close_stores(fresh)


@pytest.mark.parametrize('boundary', ['before-config-write', 'after-config-write'])
def test_interrupted_wake_recovers_exact_new_endpoint(tmp_path, monkeypatch, boundary):
    with running(tmp_path, monkeypatch, 'warm') as (c, _, _, url, _, _):
        c.path.write_text(json.dumps(c.config))
        age(c)
        request = idle_args(c)
        assert c.idle.drain(**request)['safe_to_stop']
        wake = wake_args(c, request)
        wake['config'] = {**wake['config'], 'endpoint': 'http://127.0.0.1:12345/v1'}
        def crash(*a):
            raise OSError('simulated lost process')
        monkeypatch.setattr(c, 'store_configuration' if boundary == 'before-config-write' else 'finish_idle', crash)
        with pytest.raises(OSError):
            c.idle.resume(**wake)
        assert not c.accepting and c.idle.record['phase'] == 'resuming'
        fresh = restart(c)
        try:
            assert not fresh.accepting
            changed = {**wake, 'config': {**wake['config'], 'endpoint': 'http://127.0.0.1:54321/v1'}}
            with pytest.raises(ValueError, match='same owned engine'):
                fresh.idle.resume(**changed)
            assert fresh.idle.resume(**wake)['accepting']
            assert fresh.config['endpoint'] == wake['config']['endpoint']
            assert json.loads(fresh.path.read_text()) == fresh.config
            assert fresh.idle.resume(**wake)['accepting']  # Lost success response; no duplicate reconfigure.
            fresh.drain()
            assert fresh.idle.resume(**wake)['accepting'] is False  # Old wake cannot undo a later Stop.
        finally:
            close_stores(fresh)


def test_stop_wins_over_wake_and_survives_restart(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'on_demand') as (c, control, _, url, _, _):
        c.path.write_text(json.dumps(c.config))
        age(c)
        request = idle_args(c)
        wake = wake_args(c, request)
        assert c.idle.drain(**request)['safe_to_stop']
        entered, release = threading.Event(), threading.Event()
        def slow_unload():
            entered.set()
            assert release.wait(5)
        monkeypatch.setattr(control, 'release_idle', slow_unload)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(c.idle.resume, **wake)
            assert entered.wait(2)
            assert not c.drain()['safe_to_stop']
            release.set()
            with pytest.raises(ValueError, match='stopped during idle wake'):
                pending.result()
        assert c.idle.record['phase'] == 'stopped'
        assert call(c, url, 'cannot-undo-stop').status_code == 503
        fresh = restart(c)
        try:
            with pytest.raises(ValueError, match='exact idle operation'):
                fresh.idle.resume(**wake)
            assert not fresh.idle.drain(**request)['safe_to_stop']
            assert not fresh.accepting
            # Explicit recovery invalidates the idle operation but still needs
            # ordinary owner resume after engine binding verification.
            assert not fresh.idle.reset(request['suspend_id'], fresh.revision)['accepting']
            assert fresh.resume(fresh.revision)['accepting']
            assert json.loads(fresh.idle.path.read_text())['phase'] == 'resumed'
        finally:
            close_stores(fresh)


def test_failed_fence_persistence_never_authorizes_shutdown(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'warm') as (c, _, _, _, _, _):
        def disk_full(*a):
            raise OSError('disk full')
        monkeypatch.setattr(c.module('idle'), 'atomic_json', disk_full)
        age(c)
        with pytest.raises(OSError):
            c.idle.drain(**idle_args(c))
        assert c.accepting and not c.idle.record and not c.idle.checking


def test_corrupt_saved_idle_state_never_opens_admission(tmp_path):
    (tmp_path / 'engine-idle.json').write_text('{"phase":"resumed"}')
    with pytest.raises(ValueError, match='Invalid saved engine idle state'):
        connector_module.Connector(tmp_path)
