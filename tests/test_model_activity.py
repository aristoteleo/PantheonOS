import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from test_model_services import connector_module, serve


async def eventually(check):
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(.01)
    assert check()


@pytest.mark.asyncio
async def test_fifo_queue_cancel_disconnect_dedup_and_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_module.Connector, 'capacity', property(lambda _: 1))
    entered, release = [], threading.Event()
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            entered.append(body['model'])
            if body['model'] == 'first': release.wait(5)
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"PRIVATE_OUTPUT"}}]}\n\n')
            self.wfile.write(b'data: {"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\ndata: [DONE]\n\n')
    connector = connector_module.Connector(tmp_path)
    with serve(Engine) as upstream, serve(connector_module.handler(connector)) as url:
        connector.configure({'engine': 'ollama', 'endpoint': upstream})
        async with httpx.AsyncClient(timeout=5) as client:
            async def invoke(identity):
                return await client.post(url + '/v1/chat/completions', headers={
                    'X-Model-Config': connector.revision, 'X-Model-Request': identity},
                    json={'model': identity, 'messages': [{'role': 'user', 'content': 'PRIVATE_PROMPT'}], 'stream': True})
            tasks = []
            try:
                tasks.append(asyncio.create_task(invoke('first')))
                await eventually(lambda: entered == ['first'])
                assert (await invoke('first')).status_code == 409
                tasks.append(asyncio.create_task(invoke('cancelled')))
                await eventually(lambda: connector.queue == ['cancelled'])
                tasks.append(asyncio.create_task(invoke('disconnected')))
                await eventually(lambda: len(connector.queue) == 2)
                tasks.append(asyncio.create_task(invoke('last')))
                await eventually(lambda: len(connector.queue) == 3)
                result = await client.post(url + '/cancel', json={'request_id': 'cancelled'})
                assert result.json()['cancelled']
                assert (await tasks[1]).status_code == 409
                tasks[2].cancel()
                await asyncio.gather(tasks[2], return_exceptions=True)
                await eventually(lambda: connector.queue == ['last'])
                assert entered == ['first']
                release.set()
                responses = await asyncio.gather(tasks[0], tasks[3])
                assert all(r.status_code == 200 for r in responses)
                assert int(responses[1].headers['X-Model-Queue-Ms']) > 0
                await eventually(lambda: not connector.calls)
                assert entered == ['first', 'last']
                records = {r['request_id']: r for r in connector.activity.list()}
                assert records['cancelled']['state'] == records['disconnected']['state'] == 'cancelled'
                assert records['first']['state'] == records['last']['state'] == 'completed'
                assert records['last']['usage'] == {'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6}
                assert records['last']['first_token_ms'] >= records['last']['queue_ms']
                assert 'PRIVATE' not in json.dumps(records)
                assert b'PRIVATE' not in (tmp_path / 'activity.sqlite3').read_bytes()
                # A completed request is not sent again, even across connector restarts.
                reopened = connector_module.Connector(tmp_path)
                assert not reopened.activity.create('first', 'first', 'chat/completions', connector.revision)
                assert (await invoke('first')).status_code == 409
                assert len(entered) == 2
            finally:
                release.set()
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['timeout', 'drain'])
async def test_queued_request_never_submits_after_timeout_or_drain(tmp_path, monkeypatch, ending):
    monkeypatch.setattr(connector_module.Connector, 'capacity', property(lambda _: 1))
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'ollama', 'endpoint': 'http://127.0.0.1:1'})
    connector.calls['inflight'] = {'state': 'running', 'model': 'model'}
    connector.queue_timeout = .15 if ending == 'timeout' else 30
    with serve(connector_module.handler(connector)) as url, httpx.Client() as admin:
        async with httpx.AsyncClient(timeout=2) as client:
            request = asyncio.create_task(client.post(url + '/v1/chat/completions',
                headers={'X-Model-Config': connector.revision, 'X-Model-Request': 'queued'},
                json={'model': 'model', 'stream': True}))
            await eventually(lambda: bool(connector.queue))
            if ending == 'drain':
                assert connector.drain()['safe_to_stop'] is False
            assert (await request).status_code == (429 if ending == 'timeout' else 409)
            await eventually(lambda: not connector.queue)
            row = connector.activity.list()[0]
            assert row['state'] == ('failed' if ending == 'timeout' else 'cancelled')
            if ending == 'timeout': assert row['reason'] == 'queue_timeout'
            assert row['bytes_received'] == 0


def test_activity_is_bounded_and_restart_never_replays(tmp_path):
    connector = connector_module.Connector(tmp_path)
    ledger = connector.activity
    for i in range(140):
        assert ledger.create(str(i), 'model', 'chat/completions', 'a'*64)
        ledger.update(str(i), state='completed', ended_at=time.time())
    assert len(ledger.list()) == ledger.HISTORY
    assert ledger.create('interrupted', 'model', 'chat/completions', 'a'*64)
    ledger.update('interrupted', state='running')
    recovered = connector_module.Connector(tmp_path)
    row = next(r for r in recovered.activity.list() if r['request_id'] == 'interrupted')
    assert row['state'] == 'unknown' and row['reason'] == 'connector_restarted'
    assert not recovered.activity.create('interrupted', 'model', 'chat/completions', 'a'*64)
    assert recovered.cancel('cancel-before-admit')['cancelled']
    assert not recovered.activity.create('cancel-before-admit', 'model', '', '')


def test_stream_metrics_bounds_data_and_does_not_claim_truncation_success(tmp_path):
    module = connector_module.Connector(tmp_path).module('activity')
    metrics = module.StreamMetrics()
    for b in b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n':
        metrics.feed(bytes([b]))
    assert metrics.first_token and not metrics.done
    metrics.feed(b'x' * (1024 * 1024 + 1))
    assert metrics.disabled and not metrics.pending and not metrics.done


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect', [False, True])
async def test_cancel_before_upstream_headers_releases_capacity(tmp_path, disconnect):
    started, finish = threading.Event(), threading.Event()
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            started.set(); finish.wait(3)
            self.send_response(200); self.end_headers()
    connector = connector_module.Connector(tmp_path)
    with serve(Engine) as upstream, serve(connector_module.handler(connector)) as url:
        connector.configure({'engine': 'ollama', 'endpoint': upstream})
        async with httpx.AsyncClient(timeout=4) as client:
            task = asyncio.create_task(client.post(url + '/v1/chat/completions',
                headers={'X-Model-Config': connector.revision, 'X-Model-Request': 'cold'}, json={'model': 'model'}))
            try:
                await eventually(started.is_set)
                before = time.monotonic()
                if disconnect:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                else:
                    assert (await client.post(url + '/cancel', json={'request_id': 'cold'})).json()['cancelled']
                await eventually(lambda: not connector.calls)
                assert time.monotonic() - before < .5
                assert connector.activity.list()[0]['state'] == 'cancelled'
                if not disconnect:
                    await task
            finally:
                finish.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
