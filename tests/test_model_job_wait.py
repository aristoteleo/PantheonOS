"""Real HTTP long polling wakes on committed outcomes without replaying work."""
import concurrent.futures
import threading
import time

import httpx
import pytest

from test_model_inference_jobs import engine, request, wait_done, close
from test_model_services import connector_module, serve


@pytest.mark.parametrize('cancel', [False, True])
def test_http_observer_waits_for_completion_without_holding_inference_lock(tmp_path, cancel):
    calls, entered, release = [], threading.Event(), threading.Event()
    connector = connector_module.Connector(tmp_path)
    try:
        with serve(engine(calls, entered=entered, release=release)) as upstream:
            connector.configure({'engine': 'sglang', 'endpoint': upstream})
            with serve(connector_module.handler(connector)) as endpoint:
                headers = {'X-Model-Config': connector.revision}
                with httpx.Client(base_url=endpoint, headers=headers) as client:
                    assert client.post('/inference/jobs', json=request()).status_code == 202
                    assert entered.wait(3)
                    jobs = connector.inference_jobs()
                    observed = threading.Event()
                    original_wait = jobs.changed.wait
                    def wait(timeout):
                        observed.set()
                        return original_wait(timeout)
                    jobs.changed.wait = wait
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(client.get, '/inference/jobs/job-1', headers={'Prefer': 'wait=5'})
                        assert observed.wait(2) and not future.done()
                        # The worker can progress and cancel/read endpoints remain independent.
                        assert jobs.status('job-1')['state'] == 'running'
                        if cancel:
                            cancelled = client.post('/inference/jobs/job-1/cancel')
                            assert cancelled.status_code == 200
                            assert cancelled.json()['state'] in {'cancelling', 'cancelled'}
                        release.set()
                        response = future.result(timeout=2)
                        assert response.status_code == 200 and response.json()['state'] == ('cancelled' if cancel else 'succeeded')
                    assert len(calls) == 1
                    for invalid in ['wait=6', 'wait=-1', 'wait=NaN', 'wait=2, respond-async']:
                        assert client.get('/inference/jobs/job-1', headers={'Prefer': invalid}).status_code == 409
                    assert client.get('/inference/jobs/job-1', headers={'Prefer': 'wait=5', 'X-Model-Config': 'stale'}).status_code == 409
        wait_done(connector, 'job-1')
    finally:
        release.set()
        close(connector)


def test_wait_is_bounded_and_unknown_upstream_stays_outstanding(tmp_path):
    connector = connector_module.Connector(tmp_path)
    try:
        jobs = connector.inference_jobs()
        with jobs.store.transaction():
            jobs.put('pending', 'fingerprint', {'state': 'unknown', 'upstream_pending': True})
        started = time.monotonic()
        assert jobs.status('pending', wait_seconds=1)['upstream_pending']
        assert .9 <= time.monotonic() - started < 2
        # Capacity saturation falls back to immediate status, never queues an
        # unbounded observer or steals a model execution/cancellation slot.
        for _ in range(16):
            assert jobs.observers.acquire(blocking=False)
        try:
            started = time.monotonic()
            assert jobs.status('pending', wait_seconds=5)['state'] == 'unknown'
            assert time.monotonic() - started < .2
        finally:
            for _ in range(16):
                jobs.observers.release()
        with pytest.raises(KeyError):
            jobs.status('missing', wait_seconds=5)
        for invalid in [-1, 6, True, 1.5, '2']:
            with pytest.raises(ValueError):
                jobs.status('pending', wait_seconds=invalid)
        with jobs.store.transaction():
            jobs.stage_transition('pending', state='unknown', upstream_pending=False)
        started = time.monotonic()
        assert not jobs.status('pending', wait_seconds=5)['upstream_pending']
        assert time.monotonic() - started < .2
    finally:
        close(connector)
