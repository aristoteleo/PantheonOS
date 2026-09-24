"""Real HTTP transport for rank-zero admission, streaming, drain and cancellation.

The engine/peer supervisor is a fixture; this is not a GPU inference claim.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
import socket
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from test_model_engines import load
from test_model_services import connector_module, serve
from test_model_sglang_group import group, plan as original_plan, record
from pantheon.models import group_inference


def plan():
    value = original_plan()
    value.update(inference_protocol=1, underlay=['192.168.20.10:18441', '192.168.20.11:18441'])
    for member in value['members']:
        member['interface'] = 'wg0'
    return value


TOKEN = 'owner-generation-token-1234567890'
IDENTITY = dict(instance_id='a' * 32, generation=1)


@pytest.fixture
def module(monkeypatch, group):
    monkeypatch.setitem(sys.modules, 'server', connector_module)
    monkeypatch.setitem(sys.modules, 'group_inference', group_inference)
    monkeypatch.setitem(sys.modules, 'sglang_group', group)
    return load('group_connector')


@pytest.fixture
def connector(module, tmp_path):
    live = threading.Event(); live.set()
    run = SimpleNamespace(ready=live.is_set, live=live,
        engine=SimpleNamespace(listener_identity=lambda port: ('owned-listener',)))
    return module.GroupConnector(tmp_path, run, plan(), record(), IDENTITY, TOKEN)


@contextmanager
def leader(module, connector):
    server, thread = module.serve(connector, 0)
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
        assert not thread.is_alive()
        assert not connector.calls
        connector.activity.db.close()


def headers(connector, request_id='request-1'):
    return {'X-Pantheon-App-Token': TOKEN, 'X-Model-Config': connector.revision,
            'X-Model-Request': request_id}


def rpc(client, endpoint, method, args=None):
    return client.post(endpoint + '/rpc', headers={'X-Fleet-RPC-Token': TOKEN},
        json=dict(method=method, args=args or {}))


def activate(client, endpoint, connector):
    response = rpc(client, endpoint, 'resume', {'config_revision': connector.revision})
    assert response.status_code == 200, response.text


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail('Expected state did not arrive')
        threading.Event().wait(.01)


def redirect_engine(monkeypatch, endpoint):
    # Exercise the exact connector HTTP code; only redirect the fixed engine's
    # socket to an ephemeral test listener, avoiding a fixed test port collision.
    original = connector_module.HTTPConnection
    def connection(host, port, **kwargs):
        assert (host, port) == ('127.0.0.1', 30000)
        return original(host, int(endpoint.rsplit(':', 1)[1]), **kwargs)
    monkeypatch.setattr(connector_module, 'HTTPConnection', connection)


def test_owner_activation_auth_model_identity_and_live_route_state(module, connector):
    with leader(module, connector) as endpoint, httpx.Client(timeout=3) as client:
        assert client.get(endpoint + '/ready').status_code == 401
        assert client.get(endpoint + '/ready', headers=headers(connector)).json() == dict(protocol=1, ready=True, **IDENTITY)
        assert client.post(endpoint + '/rpc', headers=headers(connector), json={'method': 'resume'}).status_code == 403
        assert rpc(client, endpoint, 'configure', {'config': {'engine': 'api', 'endpoint': 'https://elsewhere'}}).status_code == 400
        assert client.post(endpoint + '/v1/embeddings', headers=headers(connector), json={}).status_code == 404
        assert client.get(endpoint + '/route-state', headers=headers(connector)).json()['ready'] is False
        assert client.post(endpoint + '/v1/chat/completions', headers=headers(connector), json={'model': connector.model}).status_code == 503
        assert rpc(client, endpoint, 'resume', {'config_revision': 'f'*64}).status_code == 400
        activate(client, endpoint, connector)
        assert rpc(client, endpoint, 'discover').json() == dict(models=[dict(id=connector.model, operations=['text'])], config_revision=connector.revision)
        assert client.post(endpoint + '/v1/chat/completions', headers=headers(connector, 'wrong-model'), json={'model': 'other'}).status_code == 400
        wrong = {**headers(connector), 'X-Model-Config': 'f'*64}
        assert client.post(endpoint + '/v1/chat/completions', headers=wrong, json={'model': connector.model}).status_code == 409
        connector.run.live.clear()
        state = client.get(endpoint + '/route-state', headers=headers(connector)).json()
        assert not state['ready'] and not state['models'][0]['inference_ready']
        assert client.post(endpoint + '/v1/chat/completions', headers=headers(connector, 'peer-gone'), json={'model': connector.model}).status_code == 503
        assert rpc(client, endpoint, 'status').json()['accepting'] is False
        assert rpc(client, endpoint, 'activity').json()['accepting'] is False
        assert rpc(client, endpoint, 'discover').status_code == 400


def test_stream_preserves_tool_reasoning_usage_and_does_not_forward_credentials(module, connector, monkeypatch):
    payload = {'model': connector.model, 'stream': True, 'messages': [{'role': 'user', 'content': 'test'}],
        'tools': [{'type': 'function', 'function': {'name': 'measure'}}]}
    event = dict(choices=[dict(delta=dict(reasoning_content='reason', tool_calls=[dict(index=0,
        id='call-1', type='function', function=dict(name='measure', arguments='{}'))]))],
        usage=dict(prompt_tokens=7, completion_tokens=3, total_tokens=10))
    expected = b'data: ' + json.dumps(event).encode() + b'\n\ndata: [DONE]\n\n'
    calls = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            assert self.path == '/v1/chat/completions'
            assert not any(self.headers.get(h) for h in ('X-Fleet-RPC-Token', 'X-Pantheon-App-Token', 'Authorization'))
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(expected); self.wfile.flush()
    with serve(Engine) as upstream, leader(module, connector) as endpoint, httpx.Client(timeout=3) as client:
        redirect_engine(monkeypatch, upstream)
        activate(client, endpoint, connector)
        response = client.post(endpoint + '/v1/chat/completions', headers=headers(connector), json=payload)
        assert response.status_code == 200 and response.content == expected
        wait_for(lambda: not connector.calls)
        assert calls == [payload]
        row = rpc(client, endpoint, 'activity').json()['requests'][0]
        assert row['state'] == 'completed' and row['usage'] == event['usage']
        assert 'test' not in json.dumps(row) and 'reasoning_content' not in json.dumps(row)
        assert client.post(endpoint + '/v1/chat/completions', headers=headers(connector), json=payload).status_code == 409
        assert len(calls) == 1


@pytest.mark.parametrize('action', ['drain', 'peer_failure'])
def test_queued_work_fenced_and_active_cancel_survives_drain(module, connector, monkeypatch, action, tmp_path):
    # Capacity one is supplied by the immutable deployment plan.
    connector.activity.db.close()
    value = plan(); value['parallel'] = 1
    connector = module.GroupConnector(tmp_path / 'one', connector.run, value, record(), IDENTITY, TOKEN)
    first = threading.Event(); release = threading.Event(); calls = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'); self.wfile.flush()
            first.set(); release.wait(5)
    with serve(Engine) as upstream, leader(module, connector) as endpoint, httpx.Client(timeout=5) as client, ThreadPoolExecutor(2) as pool:
        redirect_engine(monkeypatch, upstream)
        activate(client, endpoint, connector)
        payload = {'model': connector.model, 'stream': True}
        def submit(request_id):
            with httpx.Client(timeout=5) as worker:
                return worker.post(endpoint + '/v1/chat/completions', headers=headers(connector, request_id), json=payload)
        running = pool.submit(submit, 'active')
        try:
            assert first.wait(3)
            queued = pool.submit(submit, 'queued')
            wait_for(lambda: connector.queue == ['queued'])
            if action == 'drain':
                result = rpc(client, endpoint, 'drain').json()
                assert result['safe_to_stop'] is False
                assert connector.fence.exists()
            else:
                connector.run.live.clear()
            assert queued.result(3).status_code in (409, 503)
            assert len(calls) == 1
            assert client.post(endpoint + '/cancel', headers=headers(connector), json={'request_id': 'active'}).json() == {'cancelled': True}
            assert running.result(3).status_code == 200
            wait_for(lambda: not connector.calls)
            assert rpc(client, endpoint, 'drain').json()['safe_to_stop'] is True
            assert client.post(endpoint + '/v1/chat/completions', headers=headers(connector, 'late'), json=payload).status_code == 503
            rows = {r['request_id']: r for r in rpc(client, endpoint, 'activity').json()['requests']}
            assert rows['active']['state'] == 'cancelled'
            assert rows['queued']['state'] != 'completed'
            connector.run.live.set()
            assert rpc(client, endpoint, 'resume', {'config_revision': connector.revision}).status_code == 400
        finally:
            release.set()
            connector.cancel('active')
            running.result(5)
            wait_for(lambda: not connector.calls)
    restored = module.GroupConnector(tmp_path / 'one', connector.run, value, record(), IDENTITY, TOKEN)
    try:
        assert restored.drained and not restored.accepting
        with pytest.raises(ValueError, match='undrained'):
            restored.resume(restored.revision)
    finally:
        restored.activity.db.close()


def test_drain_persistence_failure_cannot_reopen_admission(module, connector, monkeypatch):
    connector.resume(connector.revision)
    monkeypatch.setattr(connector.module('idle'), 'atomic_json', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    try:
        with pytest.raises(OSError): connector.drain()
        assert not connector.accepting and connector.drained
        with pytest.raises(ValueError): connector.resume(connector.revision)
    finally:
        connector.activity.db.close()


def test_bounded_server_rejects_socket_when_all_slots_are_taken(module, connector):
    server, thread = module.serve(connector, 0)
    try:
        for _ in range(64): assert server.connections.acquire(False)
        with socket.create_connection(server.server_address, timeout=2) as connection:
            assert connection.recv(1) == b''
        for _ in range(64): server.connections.release()
        with httpx.Client(timeout=3) as client:
            assert client.get(f'http://127.0.0.1:{server.server_port}/ready', headers=headers(connector)).status_code == 200
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
        connector.activity.db.close()


@pytest.mark.parametrize('changed', [False, True])
def test_inference_rejects_missing_or_replaced_original_listener(module, connector, monkeypatch, changed):
    calls = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"must not forward"}}]}')
    identities = iter([('original',), ('replacement',)] if changed else [None])
    connector.run.engine.listener_identity = lambda port: next(identities)
    with serve(Engine) as upstream, leader(module, connector) as endpoint, httpx.Client(timeout=3) as client:
        redirect_engine(monkeypatch, upstream)
        activate(client, endpoint, connector)
        response = client.post(endpoint + '/v1/chat/completions', headers=headers(connector), json={'model': connector.model})
        assert response.status_code == 502 and 'must not forward' not in response.text
        wait_for(lambda: not connector.calls)
        assert len(calls) == int(changed)
        row = rpc(client, endpoint, 'activity').json()['requests'][0]
        assert row['state'] != 'completed'


def test_consumer_inference_uses_fleet_admission_not_the_node_rpc_token(module, connector):
    """The relay gateway injects the Hub-signed instance credential; direct peers send
    none. Neither is the node RPC token, so consumer paths must not require it."""
    with leader(module, connector) as endpoint, httpx.Client(timeout=3) as client:
        activate(client, endpoint, connector)
        for consumer in ({'X-Pantheon-App-Token': 'hub-signed-instance-credential'}, {}):
            request = {**headers(connector, 'consumer-' + str(len(consumer))), **consumer}
            if not consumer:
                request.pop('X-Pantheon-App-Token')
            state = client.get(endpoint + '/route-state', headers=request)
            assert state.status_code == 200 and state.json()['ready'] is True
            # Not 401: the request reaches admission (no engine in this fixture).
            wrong_model = client.post(endpoint + '/v1/chat/completions', headers=request, json={'model': 'other'})
            assert wrong_model.status_code == 400
        # Owner control and Fleet readiness still require their node tokens.
        assert client.post(endpoint + '/rpc', headers={'X-Pantheon-App-Token': TOKEN},
                           json={'method': 'discover'}).status_code == 403
        assert client.get(endpoint + '/ready', headers={'X-Pantheon-App-Token': 'hub-signed'}).status_code == 401


def test_process_rank_leader_targets_its_reserved_engine_port(module, tmp_path):
    live = threading.Event(); live.set()
    probed = []
    run = SimpleNamespace(ready=live.is_set, live=live,
        engine=SimpleNamespace(listener_identity=lambda port: probed.append(port) or ('owned-listener',)))
    container = module.GroupConnector(tmp_path / 'a', run, plan(), record(), IDENTITY, TOKEN)
    process = module.GroupConnector(tmp_path / 'b', run, plan(), record(), IDENTITY, TOKEN, engine_port=41234)
    # Same pinned configuration and revision as Hub derives; only the socket differs.
    assert process.revision == container.revision
    assert ':30000/' in container.request_spec('/chat/completions', {}).full_url
    assert ':41234/' in process.request_spec('/chat/completions', {}).full_url
    process.accepting = True
    call = {'model': process.model, 'cancelled': False}
    with pytest.raises(OSError):  # no engine listens here; only the port choice matters
        process.inference_request('/chat/completions', {'model': process.model}, call)
    assert probed and set(probed) == {41234}
