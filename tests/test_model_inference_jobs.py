"""Durable typed jobs against a real local HTTP engine fixture, not a GPU model."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from test_model_services import connector_module, serve


def request(job='job-1', **extra):
    return {'job_id': job, 'model': 'local-reranker', 'operation': 'rerank',
            'input': {'query': 'private query', 'documents': ['first secret document', 'second secret document']},
            'parameters': {'top_n': 1}, **extra}


def wait_done(connector, job):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = connector.inference_jobs().status(job)
        if record['state'] not in {'queued', 'running', 'cancelling'} and job not in connector.calls:
            return record
        time.sleep(.01)
    pytest.fail('Inference did not finish')


def engine(calls, *, entered=None, release=None, mode='normal'):
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/v1/models'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"local-reranker"}]}')
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/rerank'
            assert body['return_documents'] is False
            calls.append(body)
            if entered:
                entered.set()
            if release:
                release.wait(5)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            if mode == 'lost':
                return
            rows = [{'index': 1, 'score': .9, 'document': '/do-not-forward/provider/path'}]
            if mode == 'bad':
                rows = [{'index': 10, 'score': float('inf')}]
            try:
                self.wfile.write(json.dumps(rows).encode())
            except OSError:
                pass
    return Engine


def close(connector):
    assert not connector.calls
    if connector._media_store:
        connector._media_store.close()


def test_job_http_is_durable_after_submitter_disconnect_and_idempotent(tmp_path):
    calls, entered, release = [], threading.Event(), threading.Event()
    c = connector_module.Connector(tmp_path)
    try:
        with serve(engine(calls, entered=entered, release=release)) as upstream:
            c.configure({'engine': 'sglang', 'endpoint': upstream})
            with serve(connector_module.handler(c)) as url:
                headers = {'X-Model-Config': c.revision}
                with httpx.Client(base_url=url, headers=headers) as client:
                    assert client.post('/inference/jobs', json=request(), headers={'X-Model-Config': 'stale'}).status_code == 409
                    assert c._inference_jobs is None
                    result = client.post('/inference/jobs', json=request())
                    assert result.status_code == 202 and result.json()['job_id'] == 'job-1'
                    assert entered.wait(3)
                    assert client.post('/inference/jobs', json=request()).status_code == 202
                    assert client.post('/inference/jobs', json=request(parameters={'top_n': 2})).status_code == 409
                    assert client.delete('/inference/jobs/job-1').status_code == 409
                # Losing/closing the initiating HTTP session must not cancel the job.
                assert c.inference_jobs().status('job-1')['state'] == 'running'
                release.set()
                result = wait_done(c, 'job-1')
                assert result['state'] == 'succeeded'
                assert result['result'] == {'results': [{'index': 1, 'relevance_score': .9}], 'usage': {}}
                activity = c.activity.list()[0]
                assert activity['state'] == 'completed' and activity['elapsed_ms'] >= 0
                assert activity['queue_ms'] >= 0 and 'result' not in activity
                with httpx.Client(base_url=url, headers=headers) as client:
                    assert client.post('/inference/jobs', json=request()).json() == result
                    assert client.get('/inference/jobs/job-1').json() == result
                    listed = client.get('/inference/jobs').json()['jobs']
                    assert listed[0]['job_id'] == 'job-1' and 'result' not in listed[0]
                assert len(calls) == 1
        # Ledger retains a fingerprint/results but not the query/document bodies.
        records = c.media_store().db.execute('SELECT fingerprint,record FROM inference_jobs').fetchall()
        assert 'private query' not in str([tuple(r) for r in records])
        assert 'secret document' not in str([tuple(r) for r in records])
        close(c)
        c = connector_module.Connector(tmp_path)
        assert c.inference_jobs().status('job-1') == result
        assert c.inference_jobs().submit(request(), c.revision) == result
        assert len(calls) == 1
    finally:
        release.set()
        close(c)


@pytest.mark.parametrize('cancel_before', [False, True])
def test_cancel_queued_or_running_does_not_replay(tmp_path, cancel_before):
    c = connector_module.Connector(tmp_path)
    calls, entered, release = [], threading.Event(), threading.Event()
    try:
        with serve(engine(calls, entered=entered, release=release)) as upstream:
            c.configure({'engine': 'api', 'endpoint': upstream})
            jobs = c.inference_jobs()
            if cancel_before:
                jobs.cancel('job-1')
                with pytest.raises(ValueError):
                    jobs.submit(request(), c.revision)
                assert not calls and not c.calls
            else:
                jobs.submit(request(), c.revision)
                assert entered.wait(3)
                assert c.drain()['safe_to_stop'] is False
                jobs.cancel('job-1')
                record = wait_done(c, 'job-1')
                assert record['state'] == 'cancelled'
                assert record['submitted'] and record['upstream_cancel_confirmed'] is False
                assert c.drain()['safe_to_stop'] is True
                assert jobs.submit(request(), c.revision) == record
                assert len(calls) == 1
            release.set()
    finally:
        release.set()
        close(c)


def test_queued_cancellation_never_reaches_engine(tmp_path):
    c = connector_module.Connector(tmp_path)
    calls = []
    try:
        with serve(engine(calls)) as upstream:
            c.configure({'engine': 'api', 'endpoint': upstream})
            # Occupancy from other consumers prevents this job's admission.
            c.calls.update({f'busy-{i}': {'state': 'running'} for i in range(c.capacity)})
            jobs = c.inference_jobs()
            jobs.submit(request(), c.revision)
            assert jobs.status('job-1')['state'] == 'queued'
            jobs.cancel('job-1')
            assert wait_done(c, 'job-1')['state'] == 'cancelled'
            assert not calls
            for i in range(c.capacity):
                c.calls.pop(f'busy-{i}')
    finally:
        c.calls.clear()
        close(c)


@pytest.mark.parametrize('mode', ['lost', 'bad'])
def test_uncertain_or_invalid_upstream_is_not_success_or_retried(tmp_path, mode):
    calls = []
    c = connector_module.Connector(tmp_path)
    try:
        with serve(engine(calls, mode=mode)) as upstream:
            c.configure({'engine': 'api', 'endpoint': upstream})
            jobs = c.inference_jobs()
            jobs.submit(request(), c.revision)
            record = wait_done(c, 'job-1')
            assert record['state'] == 'unknown' and record['result'] is None
            assert jobs.submit(request(), c.revision) == record
            assert len(calls) == 1
    finally:
        close(c)


def test_restart_marks_active_jobs_without_replaying_payloads(tmp_path):
    c = connector_module.Connector(tmp_path)
    c.configure({'engine': 'api', 'endpoint': 'https://unused.invalid/v1'})
    jobs = c.inference_jobs()
    # Persist crash-time ledger states without creating running local workers.
    with jobs.store.transaction():
        for state in ('queued', 'running', 'cancelling'):
            jobs.put(state, 'test-fingerprint', {'job_id': state, 'state': state, 'created_at': time.time(), 'result': None})
    close(c)
    restarted = connector_module.Connector(tmp_path)
    try:
        rows = {r['job_id']: r for r in restarted.inference_jobs().list()}
        assert rows['queued']['state'] == 'cancelled'
        assert rows['running']['state'] == rows['cancelling']['state'] == 'unknown'
        assert not restarted.calls
    finally:
        close(restarted)


def test_conflicting_text_request_and_worker_start_failure_leave_other_consumers_intact(tmp_path, monkeypatch):
    c = connector_module.Connector(tmp_path)
    c.configure({'engine': 'api', 'endpoint': 'https://unused.invalid/v1'})
    try:
        jobs = c.inference_jobs()
        other = {'model': 'text-model', 'state': 'running'}
        c.calls['job-1'] = other
        c.activity.create('job-1', 'text-model', 'text', c.revision)
        with pytest.raises(ValueError, match='already recorded'):
            jobs.submit(request(), c.revision)
        assert c.calls['job-1'] is other
        c.calls.pop('job-1')
        def fail(*args):
            raise RuntimeError('unable to start worker')
        monkeypatch.setattr(threading.Thread, 'start', fail)
        with pytest.raises(RuntimeError):
            jobs.submit(request('worker-failure'), c.revision)
        assert jobs.status('worker-failure')['state'] == 'failed'
        assert not c.calls and not c.queue
    finally:
        close(c)


def test_history_capacity_is_explicit_and_rejects_routing_overrides(tmp_path, monkeypatch):
    c = connector_module.Connector(tmp_path)
    c.configure({'engine': 'api', 'endpoint': 'https://unused.invalid/v1'})
    try:
        jobs = c.inference_jobs()
        monkeypatch.setattr(c.module('inference_jobs'), 'LIMIT', 1)
        jobs.cancel('before')
        with pytest.raises(ValueError, match='history is full'):
            jobs.submit(request(), c.revision)
        jobs.remove('before')
        for params in ({'base_url': 'http://node/private'}, {'top_n': True}, {'return_documents': 'true'}):
            with pytest.raises(ValueError):
                jobs.submit(request(parameters=params), c.revision)
        assert jobs.list() == []
    finally:
        close(c)


def test_wall_deadline_closes_quiet_upstream_without_reporting_success(tmp_path, monkeypatch):
    c = connector_module.Connector(tmp_path)
    calls, entered, release = [], threading.Event(), threading.Event()
    try:
        with serve(engine(calls, entered=entered, release=release)) as upstream:
            c.configure({'engine': 'api', 'endpoint': upstream})
            jobs = c.inference_jobs()
            monkeypatch.setattr(c.module('inference_jobs'), 'TIMEOUT_SECONDS', .2)
            jobs.submit(request(), c.revision)
            assert entered.wait(3)
            result = wait_done(c, 'job-1')
            assert result['state'] == 'unknown' and result['reason'] == 'job_deadline'
            assert jobs.submit(request(), c.revision) == result
            assert len(calls) == 1
            release.set()
    finally:
        release.set()
        close(c)
