"""Warm replica admission and persistence against a real HTTP engine fixture."""
import json
import threading

import httpx
import pytest

from test_model_lifetime import running, call, MODEL, OTHER
from test_model_services import connector_module, serve


def preload(control, *, job='preload', model=MODEL, revision=0):
    receipt = control.submit(job, 'preload', model_id=model, pool_revision=revision)
    control.worker.join(3)
    assert not control.worker.is_alive()
    return receipt


def test_preload_is_durable_idempotent_and_only_warms_from_owner_job(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, url, _, _):
        result = preload(control)
        assert engine['loads'] == [(MODEL, -1)] and not engine['requests']
        assert engine['warmups'] == [MODEL]
        assert control.status()['preload']['ready'] is True
        assert control.submit('preload', 'preload', model_id=MODEL, pool_revision=0) == result
        before = control.status()
        connector.resume(connector.revision)
        assert control.status()['jobs'] == before['jobs']  # no jobs/loads on a warm resume
        assert call(connector, url, 'warm').status_code == 200
        assert engine['loads'] == [(MODEL, -1)]
        assert call(connector, url, 'other', model=OTHER).status_code == 400
        assert engine['requests'] == [MODEL] and not engine['unloads']
        assert engine['warmups'] == [MODEL]
        route = connector.route_state()
        assert next(m for m in route['models'] if m['id'] == OTHER)['inference_ready'] is False
        for action in ('unload', 'load'):
            with pytest.raises(ValueError, match='preloading'):
                control.submit('manual-' + action, action, model_id=MODEL if action == 'unload' else OTHER)
        with pytest.raises(ValueError, match='current preload receipt'):
            control.forget('preload')


def test_preload_restores_only_after_owner_verifies_restarted_connector(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, _, _, _):
        preload(control)
        connector.path.write_text(json.dumps(connector.config))
        control.close()
        connector._model_control = None
        connector._downloads.close()
        connector._downloads = None
        engine['loaded'].clear()  # engine stopped; weights remain imported
        fresh = connector_module.Connector(connector.data)
        try:
            assert fresh.lifetime_pending and engine['loads'] == [(MODEL, -1)]
            assert fresh.model_control().status()['preload']['model_id'] == MODEL
            assert engine['loads'] == [(MODEL, -1)]  # metadata does not preload
            with serve(connector_module.handler(fresh)) as url:
                assert call(fresh, url, 'unverified').status_code != 200
                fresh.resume(fresh.revision)
                assert fresh.model_control().status()['preload']['ready'] is True
                assert call(fresh, url, 'restored').status_code == 200
                assert engine['loads'] == [(MODEL, -1), (MODEL, -1)]
                assert engine['requests'] == [MODEL]
                assert engine['warmups'] == [MODEL, MODEL]
        finally:
            if fresh._model_control:
                fresh._model_control.close()
            if fresh._downloads:
                fresh._downloads.close()


def test_uncertain_preload_is_not_replayed_by_recovery(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, _, _, _):
        original = control.request
        def lost(path, payload=None, **kwargs):
            result = original(path, payload, **kwargs)
            if path == '/api/generate':
                raise OSError('lost load acknowledgement')
            return result
        monkeypatch.setattr(control, 'request', lost)
        preload(control)
        assert control.status()['preload']['state'] == 'unknown'
        monkeypatch.setattr(control, 'request', original)
        for _ in range(2):
            connector.resume(connector.revision)
        assert engine['loads'] == [(MODEL, -1)]
        assert control.status()['preload']['ready'] is False
        preload(control, job='owner-retry', revision=1)
        assert control.status()['preload']['ready'] is True
        # A new explicit owner attempt reestablishes exact loading settings;
        # recovery alone never issues this second load.
        assert engine['loads'] == [(MODEL, -1), (MODEL, -1)]


@pytest.mark.parametrize('failure', ['stale', 'missing', 'budget', 'policy'])
def test_invalid_preload_preserves_selection_and_memory(tmp_path, monkeypatch, failure):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, _, _, _):
        revision, model = 0, MODEL
        if failure == 'stale':
            revision = 99
        elif failure == 'missing':
            model = 'fleet/' + 'd' * 64 + ':latest'
        elif failure == 'budget':
            connector.config['managed']['memory_bytes'] = 256 << 20
        elif failure == 'policy':
            connector.config['managed']['load_policy'] = 'warm'
        before = control.status()
        with pytest.raises(ValueError):
            control.submit('invalid', 'preload', model_id=model, pool_revision=revision)
        assert control.status() == before
        assert not engine['loads'] and not engine['unloads']


def test_change_selection_and_unpin_are_explicit_and_budgeted(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (_, control, engine, _, _, _):
        preload(control)
        with pytest.raises(ValueError, match='selection changed'):
            control.submit('stale', 'preload', model_id=OTHER, pool_revision=0)
        preload(control, job='other', model=OTHER, revision=1)
        assert engine['loaded'] == {OTHER} and engine['unloads'] == [MODEL]
        control.submit('unpin', 'unpin', model_id=OTHER, pool_revision=2)
        control.worker.join(3)
        assert control.status()['preload']['state'] == 'disabled'
        assert engine['loaded'] == {OTHER}  # disabling is not deleting or unloading
        control.submit('unload', 'unload', model_id=OTHER)
        control.worker.join(3)
        assert not engine['loaded']
        control.forget('other')


def test_preload_drain_waits_and_never_reopens_admission(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident', hold=True) as (connector, control, engine, _, loading, release):
        control.submit('warm', 'preload', model_id=MODEL, pool_revision=0)
        assert loading.wait(2)
        assert connector.drain()['safe_to_stop'] is False
        with pytest.raises(ValueError, match='active'):
            control.submit('racing', 'preload', model_id=OTHER, pool_revision=1)
        release.set()
        control.worker.join(3)
        assert connector.drain()['safe_to_stop'] is True
        assert not connector.accepting and not engine['requests']


def test_preload_requires_management_credential(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (_, _, engine, url, _, _):
        response = httpx.post(url + '/rpc', json={'method': 'models_submit', 'args': {
            'job_id': 'forbidden', 'action': 'preload', 'model_id': MODEL, 'pool_revision': 0}})
        assert response.status_code == 403 and not engine['loads']


def test_preload_configuration_change_requires_revalidation(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, _, _, _):
        preload(control)
        config = {k: v for k, v in connector.config.items() if k != 'managed'}
        managed = dict(connector.config['managed'])
        before = connector.revision
        with pytest.raises(ValueError, match='Disable preloading'):
            connector.configure(config, {**managed, 'load_policy': 'on_demand'})
        assert connector.revision == before and not engine['unloads']
        connector.configure(config, {**managed, 'memory_bytes': managed['memory_bytes'] - 1})
        assert connector.revision != before and control.status()['preload']['ready']
        assert engine['loads'] == [(MODEL, -1), (MODEL, -1)] and engine['unloads'] == [MODEL]
        assert control.status()['preload']['config_revision'] == connector.revision


def test_first_preload_reestablishes_settings_for_previously_loaded_memory(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (_, control, engine, _, _, _):
        engine['loaded'].add(MODEL)  # memory has not been verified by a preload receipt
        preload(control)
        assert engine['unloads'] == [MODEL] and engine['loads'] == [(MODEL, -1)]
        preload(control, job='same-model', revision=1)
        assert engine['unloads'] == [MODEL] and engine['loads'] == [(MODEL, -1)]
        assert engine['warmups'] == [MODEL]


def test_unknown_compute_warmup_is_not_replayed_or_admitted(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, url, _, _):
        original = control.request
        def lost(path, payload=None, **kwargs):
            result = original(path, payload, **kwargs)
            if path == '/v1/chat/completions':
                raise OSError('lost compute acknowledgement')
            return result
        monkeypatch.setattr(control, 'request', lost)
        preload(control)
        assert engine['warmups'] == [MODEL]
        assert control.status()['preload']['state'] == 'unknown'
        assert control.status()['preload']['ready'] is False
        monkeypatch.setattr(control, 'request', original)
        for _ in range(2):
            connector.resume(connector.revision)
            assert not control.status()['preload']['ready']
        assert engine['warmups'] == [MODEL]
        assert call(connector, url, 'cannot-run').status_code == 400
        preload(control, job='explicit-retry', revision=1)
        assert control.status()['preload']['ready']
        assert engine['warmups'] == [MODEL, MODEL]


def test_legacy_load_receipt_needs_owner_verified_compute_warmup(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, url, _, _):
        preload(control)
        # Simulate persisted 0.1.10 state. Its selection table remains unchanged.
        control.db.execute('DROP TABLE preload_warmup'); control.db.commit()
        control.preload = connector.module('preload').Preload(control)
        assert not control.status()['preload']['ready']
        assert call(connector, url, 'legacy').status_code == 400
        assert len(engine['warmups']) == 1
        connector.resume(connector.revision)
        assert control.status()['preload']['ready']
        assert engine['loads'] == [(MODEL, -1)]  # preserve already loaded memory
        assert engine['warmups'] == [MODEL, MODEL]


@pytest.mark.parametrize('response', [
    {'choices': [{'finish_reason': 'length'}], 'usage': {'completion_tokens': 3}},
    {'choices': [{'finish_reason': 'length'}], 'usage': {'completion_tokens': 0}},
    {'choices': [{'finish_reason': 'error'}], 'usage': {'completion_tokens': 2}},
    {},
])
def test_invalid_warmup_confirmation_never_becomes_ready(tmp_path, monkeypatch, response):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, _, _, _, _):
        original = control.request
        def invalid(path, payload=None, **kwargs):
            return response if path == '/v1/chat/completions' else original(path, payload, **kwargs)
        monkeypatch.setattr(control, 'request', invalid)
        preload(control)
        state = control.status()['preload']
        assert state['state'] == 'failed' and not state['ready']
        assert not next(m for m in connector.route_state()['models'] if m['id'] == MODEL)['inference_ready']


def test_warmup_keeps_admission_closed_and_stop_waits(tmp_path, monkeypatch):
    with running(tmp_path, monkeypatch, 'resident') as (connector, control, engine, url, _, _):
        entered, release = threading.Event(), threading.Event()
        original = control.request
        def hold(path, payload=None, **kwargs):
            if path == '/v1/chat/completions':
                entered.set()
                assert release.wait(5)
            return original(path, payload, **kwargs)
        monkeypatch.setattr(control, 'request', hold)
        try:
            control.submit('warm', 'preload', model_id=MODEL, pool_revision=0)
            assert entered.wait(2)
            status = control.status()
            assert not status['preload']['ready'] and status['jobs'][0]['phase'] == 'Warming inference kernels'
            assert call(connector, url, 'too-soon').status_code == 503
            assert connector.drain()['safe_to_stop'] is False
            for _ in range(3):
                assert not control.status()['preload']['ready']
            assert not engine['requests'] and not engine['warmups']
        finally:
            release.set()
            control.worker.join(3)
        assert engine['warmups'] == [MODEL]
        assert not connector.accepting and connector.drain()['safe_to_stop'] is True
