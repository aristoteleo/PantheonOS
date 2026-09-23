"""Owner attestation releases bookkeeping, never asserts GPU cancellation."""
import threading
from unittest.mock import AsyncMock

import httpx
import pytest

from test_model_services import connector_module, serve, deployment
from test_model_video_jobs import body, connector, engine, shutdown, wait


def parked(c):
    call = c.calls.get('video-1', {})
    return call.get('parked') and not call['worker'].is_alive()


def recovery(c, **args):
    return c.module('video_recovery').recover(c, 'video-1', c.revision, **args)


@pytest.mark.parametrize('acknowledged', [False, True])
def test_owner_release_is_durable_idempotent_and_does_not_contact_engine(tmp_path, acknowledged):
    state = {'calls': [], 'complete': threading.Event(), 'lost_ack': not acknowledged, 'missing': acknowledged}
    with serve(engine(state)) as endpoint:
        c = connector(tmp_path, endpoint)
        initial_capacity = c.slots._value
        try:
            jobs = c.inference_jobs(); jobs.submit(body(), c.revision)
            wait(lambda: parked(c))
            review = recovery(c)
            assert review['can_observe'] is acknowledged
            assert review['upstream_id'] == ('engine-1' if acknowledged else None)
            assert c.calls and not c.drain()['safe_to_stop']
            calls = list(state['calls'])
            for invalid in ({'confirmation': ''}, {'ticket': 'f' * 64}, {'ticket': False}):
                with pytest.raises(ValueError):
                    recovery(c, **({'action': 'release', 'ticket': review['ticket'],
                                    'confirmation': review['confirmation']} | invalid))
            args = dict(action='release', ticket=review['ticket'], confirmation=review['confirmation'])
            result = recovery(c, **args)
            assert result['state'] == 'unknown' and not result['upstream_pending']
            assert result['upstream_cancel_confirmed'] is False
            assert result['resolution']['verified_by_engine'] is False
            assert not c.calls and c.drain()['safe_to_stop']
            assert not jobs.store.db.execute('SELECT 1 FROM leases').fetchone()
            assert not jobs.store.outputs('inference-video-1')
            assert recovery(c, **args) == result
            assert c.slots._value == initial_capacity
            shutdown(c); c = connector(tmp_path)
            assert not c.calls
            assert recovery(c, **args) == result
            assert c.inference_jobs().submit(body(), c.revision) == result
            assert state['calls'] == calls
            c.inference_jobs().remove('video-1')
            assert not c.media_store().db.execute('SELECT 1 FROM video_jobs').fetchone()
        finally:
            shutdown(c)


def test_ticket_is_invalid_after_cancel_or_restart_and_live_worker_cannot_be_released(tmp_path):
    state = {'calls': [], 'complete': threading.Event(), 'ack_gate': threading.Event(), 'missing': True}
    with serve(engine(state)) as endpoint:
        c = connector(tmp_path, endpoint)
        try:
            jobs = c.inference_jobs(); jobs.submit(body(), c.revision)
            wait(lambda: bool(state['calls']))
            with pytest.raises(ValueError, match='stopped observer'):
                recovery(c)
            state['ack_gate'].set(); wait(lambda: parked(c))
            review = recovery(c)
            jobs.cancel('video-1')
            with pytest.raises(ValueError, match='state changed'):
                recovery(c, action='release', ticket=review['ticket'], confirmation=review['confirmation'])
            review = recovery(c)
            shutdown(c); c = connector(tmp_path)
            wait(lambda: parked(c))
            with pytest.raises(ValueError, match='state changed'):
                recovery(c, action='release', ticket=review['ticket'], confirmation=review['confirmation'])
            assert c.calls and not c.drain()['safe_to_stop']
        finally:
            state['ack_gate'].set(); shutdown(c)


def test_release_transaction_rolls_back_both_ledgers_and_committed_cleanup_is_retryable(tmp_path, monkeypatch):
    state = {'calls': [], 'complete': threading.Event(), 'lost_ack': True}
    with serve(engine(state)) as endpoint:
        c = connector(tmp_path, endpoint)
        try:
            jobs = c.inference_jobs(); jobs.submit(body(), c.revision); wait(lambda: parked(c))
            review = recovery(c)
            args = dict(action='release', ticket=review['ticket'], confirmation=review['confirmation'])
            transition = jobs.stage_transition
            def fail(*args, **kwargs):
                transition(*args, **kwargs)
                raise OSError('simulated commit interruption')
            monkeypatch.setattr(jobs, 'stage_transition', fail)
            with pytest.raises(OSError): recovery(c, **args)
            assert jobs.status('video-1')['upstream_pending']
            assert jobs.videos.journal.read('video-1', c.revision)['outstanding']
            assert c.calls
            monkeypatch.setattr(jobs, 'stage_transition', transition)
            cleanup = jobs.discard_outputs
            def fail_cleanup(*args): raise OSError('simulated unlink failure')
            monkeypatch.setattr(jobs, 'discard_outputs', fail_cleanup)
            with pytest.raises(OSError): recovery(c, **args)
            assert not jobs.status('video-1')['upstream_pending'] and not c.calls
            monkeypatch.setattr(jobs, 'discard_outputs', cleanup)
            assert recovery(c, **args)['state'] == 'unknown'
            assert not jobs.store.outputs('inference-video-1')
        finally:
            shutdown(c)


def test_capacity_release_requires_private_fleet_rpc_not_inference_access(tmp_path):
    state = {'calls': [], 'complete': threading.Event(), 'lost_ack': True}
    with serve(engine(state)) as upstream:
        c = connector(tmp_path, upstream)
        try:
            c.inference_jobs().submit(body(), c.revision); wait(lambda: parked(c))
            with serve(connector_module.handler(c)) as endpoint, httpx.Client() as client:
                request = {'method': 'video_recovery', 'args': {'job_id': 'video-1', 'config_revision': c.revision}}
                for headers in ({}, {'X-Model-Config': c.revision}, {'Authorization': 'Bearer workload-grant'}):
                    assert client.post(endpoint + '/rpc', json=request, headers=headers).status_code == 403
                assert client.post(endpoint + '/inference/jobs/video-1/release',
                    headers={'X-Model-Config': c.revision}).status_code == 404
                headers = {'X-Fleet-RPC-Token': c.rpc_token}
                review = client.post(endpoint + '/rpc', json=request, headers=headers).json()
                request['args'].update(action='release', ticket=review['ticket'], confirmation=review['confirmation'])
                assert client.post(endpoint + '/rpc', json=request, headers=headers).json()['state'] == 'unknown'
                assert len(state['calls']) == 1
        finally:
            shutdown(c)


@pytest.mark.asyncio
async def test_owner_manager_uses_original_binding_without_route_resolution():
    from pantheon.models.manager import ModelServiceManager
    row = deployment()
    directory = type('Directory', (), {'deployment': AsyncMock(return_value=row)})()
    manager = ModelServiceManager(client=directory, resolver=object())
    manager.rpc = AsyncMock(return_value={'protocol': 1})
    ref = 'fleet-job://mac/video-1'
    assert await manager.video_recovery(ref) == {'protocol': 1, 'ref': ref}
    directory.deployment.assert_awaited_once_with('mac')
    manager.rpc.assert_awaited_once_with(row['binding'], 'video_recovery', {
        'job_id': 'video-1', 'config_revision': row['config_revision'],
        'action': 'inspect', 'ticket': '', 'confirmation': ''})
    row['state'] = 'recovering'
    with pytest.raises(ValueError): await manager.video_recovery(ref, 'release')
    assert manager.rpc.await_count == 1
