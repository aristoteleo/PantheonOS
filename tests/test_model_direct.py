"""Real Python -> Fleet subprocess -> QUIC -> HTTP, with synthetic models only.

The Go build is shared by the suite; every test owns and closes its peers,
helper processes and loopback servers. No installed engine or Fleet is changed.
"""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest
import pytest_asyncio

from pantheon.models.client import ModelServices, ControlError, model_ref
from pantheon.models.direct import DirectHTTPTransport, DirectUnavailable, ProcessStream, ORIGIN
from test_model_services import deployment, serve, connector_module


@pytest.fixture(scope='module')
def binaries(tmp_path_factory):
    if not shutil.which('go'):
        pytest.skip('Go is required for the real Fleet QUIC integration tests')
    root = Path(__file__).resolve().parents[1] / 'fleet'
    output = tmp_path_factory.mktemp('direct-binaries')
    result = {}
    for name, package in [('fleet', './cmd/fleet'), ('node', './internal/appdirect/testdata/workload')]:
        path = output / (name + ('.exe' if __import__('os').name == 'nt' else ''))
        build = subprocess.run(['go', 'build', '-p', '2', '-o', str(path), package],
                               cwd=root, capture_output=True, text=True, timeout=180)
        assert build.returncode == 0, build.stderr
        result[name] = str(path)
    return result


@pytest_asyncio.fixture(autouse=True)
async def no_helper_leaks(monkeypatch):
    streams = []
    original = ProcessStream.__init__

    def track(self, *args, **kwargs):
        original(self, *args, **kwargs)
        streams.append(self)

    monkeypatch.setattr(ProcessStream, '__init__', track)
    yield streams
    deadline = time.monotonic() + 3
    while any(s.process.returncode is None or not s.close_task or not s.close_task.done() for s in streams):
        if time.monotonic() >= deadline:
            for s in streams:
                if s.process.returncode is None:
                    s.process.kill()
                    await s.process.wait()
            pytest.fail('Direct helper or its cleanup task leaked')
        await asyncio.sleep(.01)


class Node:
    def __init__(self, binaries, endpoint):
        self.binaries, self.endpoint = binaries, endpoint
        self.mode, self.status, self.policy = '', 200, 'direct_only'
        self.row = deployment()
        self.requests = []
        self.controls = []
        self.wire = httpx.AsyncClient(timeout=5, trust_env=False)
        self.transport = httpx.MockTransport(self.hub)

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(self.binaries['node'],
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self.process.stdin.write(json.dumps({'Endpoint': self.endpoint}).encode() + b'\n')
        await self.process.stdin.drain()
        async with asyncio.timeout(5):
            self.control = json.loads(await self.process.stdout.readline())['control']
        self.client = ModelServices('https://hub.test', 'synthetic-fleet-identity',
            self.transport, direct_executable=self.binaries['fleet'], prefer_direct=True)
        return self

    async def __aexit__(self, *args):
        try:
            await self.assert_released()
        finally:
            self.process.stdin.close()
            try:
                async with asyncio.timeout(3):
                    await self.process.wait()
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
            await self.wire.aclose()

    async def assert_released(self):
        async with asyncio.timeout(3):
            while (await self.wire.get(self.control + '/metrics')).json()['uses']:
                await asyncio.sleep(.01)

    async def issue(self, peer):
        response = await self.wire.post(self.control + '/grant', params={'mode': self.mode}, json={'peer_id': peer})
        response.raise_for_status()
        return response.json()

    async def hub(self, request):
        self.requests.append(request.url.path)
        if request.url.path == '/api/model-services':
            return httpx.Response(200, json={'deployments': [self.row]})
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json={'route': {'revision': 1, 'selection': 'ordered',
                'transport': self.policy, 'fallback': 'none'},
                'transport': 'fleet_direct' if self.policy == 'direct_only' else 'fleet_relay',
                'candidates': [{'deployment': self.row, 'model': self.row['models'][0], 'compute': 'node', 'billing': 'local'}]})
        if request.url.path.endswith('/workload-direct-connect'):
            if self.status != 200:
                return httpx.Response(self.status, json={'detail': 'Synthetic grant failure'})
            body = json.loads(request.content)
            peer = body.pop('peer_id')
            assert body == self.row['binding']
            return httpx.Response(200, json=await self.issue(peer))
        if request.url.path.endswith('/workload-connect'):
            assert json.loads(request.content) == deployment()['binding']
            return httpx.Response(200, json={'origin': 'https://relay.test', 'access_token': 'synthetic-relay-grant', 'expires': time.time() + 60})
        if request.url.host == 'relay.test':
            if request.url.path == '/cancel':
                assert request.headers['Authorization'] == 'Bearer synthetic-relay-grant'
                body = json.loads(request.content)
                assert set(body) == {'request_id'}
                assert request.headers['X-Model-Request'] == body['request_id']
                self.controls.append((body, request.headers['X-Model-Config']))
                return await self.wire.post(self.endpoint + '/cancel', json=body,
                    headers={'X-Model-Config': request.headers['X-Model-Config']})
            # Distinct marker proves a fallback occurred, without a second engine.
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"RELAY"}}]}\n\ndata: [DONE]\n\n')
        raise AssertionError(f'Unexpected request: {request.url.path}')


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        assert self.path == '/route-state'
        state = {'protocol': 1, 'config_revision': 'c' * 64, 'ready': True,
                 'active_calls': 0, 'capacity': 1, 'models': [{'id': 'example:8b', 'loaded': True}]}
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps(state).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert not self.headers.get('Authorization')
        assert self.headers['X-Pantheon-App-Token'] == 'test-credential-' * 4
        assert self.headers['X-Model-Config'] == 'c' * 64
        self.send_response(200); self.end_headers()
        if self.path == '/v1/embeddings':
            assert body['input'] == ['embedding input']
            self.wfile.write(b'{"data":[{"embedding":[0.25,0.75],"index":0}]}')
            return
        assert self.path == '/v1/chat/completions'
        assert body['model'] == 'example:8b' and body['stream'] is True
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
        self.wfile.flush()
        # Force a separate read. The client must see this before response EOF.
        time.sleep(.1)
        for chunk in [
            {'choices': [{'delta': {'reasoning_content': 'thought', 'tool_calls': [{'index': 0, 'id': 'call_1', 'function': {'name': 'weather', 'arguments': '{"city":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"SF"}'}}]}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}},
        ]:
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


@pytest.mark.asyncio
async def test_direct_model_tools_embeddings_and_alias(binaries):
    with serve(ModelHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            chunks = []
            async def chunk(delta): chunks.append(delta)
            tools = [{'type': 'function', 'function': {'name': 'weather', 'parameters': {'type': 'object'}}}]
            for ref in [model_ref('mac', 'example:8b'), 'fleet-route://direct']:
                result = await node.client.complete(ref, [{'role': 'user', 'content': 'Hello'}], tools=tools, process_chunk=chunk)
                assert result['content'] == 'first'
                assert result['reasoning_content'] == 'thought'
                assert result['tool_calls'][0]['function'] == {'name': 'weather', 'arguments': '{"city":"SF"}'}
                assert result['usage']['prompt_tokens'] == 10
                assert result['_metadata']['model_service']['transport'] == 'fleet_direct'
            assert len(chunks) == 6
            result = await node.client.complete(model_ref('mac', 'example:8b'), operation='embedding', inputs=['embedding input'])
            assert result['data']['data'][0]['embedding'] == [.25, .75]
            assert result['route']['transport'] == 'fleet_direct'
            assert '/api/fleet/apps/workload-connect' not in node.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['token', 'stale', 'malformed'])
async def test_rejected_direct_authority_never_falls_back(binaries, mode):
    with serve(ModelHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            node.mode = mode
            with pytest.raises(ValueError, match='authority'):
                await node.client.complete(model_ref('mac', 'example:8b'), [])
            assert '/api/fleet/apps/workload-connect' not in node.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 409, 502])
async def test_hub_authority_failure_never_falls_back(binaries, status):
    with serve(ModelHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            node.status = status
            with pytest.raises(ControlError):
                await node.client.complete(model_ref('mac', 'example:8b'), [])
            assert '/api/fleet/apps/workload-connect' not in node.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['old-node', 'relay-only'])
async def test_unavailable_direct_fallback_only_when_allowed(binaries, mode):
    with serve(ModelHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            if mode == 'old-node': node.status = 503
            else: node.mode = mode
            for _ in range(2):
                result = await node.client.complete(model_ref('mac', 'example:8b'), [])
                assert result['content'] == 'RELAY'
                assert result['_metadata']['model_service']['transport'] == 'fleet_relay'
            # The same unavailable generation is negatively cached, not probed
            # for every inference. A strict route still requires a fresh try.
            assert node.requests.count('/api/fleet/apps/workload-direct-connect') == 1
            before = len(node.requests)
            with pytest.raises(DirectUnavailable):
                async with node.client.connection(node.row, 'direct_only'):
                    pytest.fail('Strict direct route used Relay')
            assert '/api/fleet/apps/workload-connect' not in node.requests[before:]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['http503', 'truncated'])
async def test_submitted_inference_is_never_replayed(binaries, failure):
    received = []
    class FailedEngine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            received.append(self.path)
            self.send_response(503 if failure == 'http503' and self.path != '/cancel' else 200)
            self.end_headers()
            self.wfile.write(b'{}' if self.path == '/cancel' else b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
    with serve(FailedEngine) as endpoint:
        async with Node(binaries, endpoint) as node:
            with pytest.raises(RuntimeError):
                await node.client.complete(model_ref('mac', 'example:8b'), [])
            assert received == ['/v1/chat/completions', '/cancel']
            assert len(node.controls) == 1
            assert node.requests.count('/api/fleet/apps/workload-direct-connect') == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('content_type', ['text/event-stream', 'application/json'])
async def test_real_connector_cancel_precedes_stream_disconnect(binaries, tmp_path, content_type):
    first = asyncio.Event()
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
                    self.wfile.flush()
                    time.sleep(.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
    connector = connector_module.Connector(tmp_path)
    with serve(Engine) as upstream, serve(connector_module.handler(connector)) as endpoint:
        connector.configure({'engine': 'ollama', 'endpoint': upstream})
        async with Node(binaries, endpoint) as node:
            node.row['config_revision'] = connector.revision
            async def chunk(delta):
                first.set()
                await asyncio.Event().wait()
            task = asyncio.create_task(node.client.complete(model_ref('mac', 'example:8b'), [], process_chunk=chunk))
            try:
                await asyncio.wait_for(first.wait(), 5)
                original_config = connector.revision
                # Reproduce the real failure: inference works, but a fresh
                # ephemeral peer cannot reach the node for cancellation.
                node.mode = 'unreachable'
                # The directory also changes while this stream is active.
                # Cancellation must retain the stream's old binding/config.
                node.row = {**node.row, 'config_revision': 'd' * 64,
                            'binding': {**node.row['binding'], 'generation': 3}}
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
                async with asyncio.timeout(3):
                    while connector.calls: await asyncio.sleep(.01)
                activity = connector.activity_status()['requests']
                assert len(activity) == 1
                assert activity[0]['state'] == 'cancelled', activity
                assert activity[0]['reason'] == 'cancelled', activity
                assert node.controls == [({'request_id': activity[0]['request_id']}, original_config)]
                assert node.requests.count('/api/model-services') == 1
                assert node.requests.count('/api/fleet/apps/workload-direct-connect') == 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_unused_preflight_and_binary_backpressure_cleanup(binaries):
    payload = b'binary\x00response' * 100000
    class BinaryHandler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200); self.end_headers()
            try: self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError): pass
    with serve(BinaryHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            for _ in range(3):
                # Selected probe abandoned before sending even HTTP headers.
                transport = DirectHTTPTransport(binaries['fleet'], node.issue)
                await transport.prepare()
                await transport.aclose()
                assert not transport.streams
            transport = DirectHTTPTransport(binaries['fleet'], node.issue)
            await transport.prepare()
            async with httpx.AsyncClient(transport=transport) as client:
                result = await client.get(ORIGIN + '/binary')
                assert result.content == payload
                async with client.stream('GET', ORIGIN + '/binary') as response:
                    iterator = response.aiter_bytes()
                    assert await anext(iterator)
                    # Deliberately leave unread data in the subprocess pipe.
                    await asyncio.sleep(.05)
            assert not transport.streams


@pytest.mark.asyncio
async def test_cancel_during_grant_exchange_cleans_helper(binaries):
    entered = asyncio.Event()
    async def issue(peer):
        entered.set()
        await asyncio.Event().wait()
    transport = DirectHTTPTransport(binaries['fleet'], issue)
    task = asyncio.create_task(transport.prepare())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await transport.aclose()
    assert not transport.streams


@pytest.mark.asyncio
async def test_transport_admission_reserves_separate_cancel_connection(binaries, no_helper_leaks):
    with serve(ModelHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            limit = asyncio.Semaphore(1)
            first = DirectHTTPTransport(binaries['fleet'], node.issue, limit=limit)
            waiting = DirectHTTPTransport(binaries['fleet'], node.issue, limit=limit)
            await first.prepare()
            task = asyncio.create_task(waiting.prepare())
            try:
                await asyncio.sleep(.02)
                assert not task.done() and len(no_helper_leaks) == 1
                # A cancellation connection belongs to the admitted invocation,
                # so it does not need a second invocation slot.
                cancel_stream = await first.new_stream()
                assert len(no_helper_leaks) == 2
                await cancel_stream.aclose()
                await first.aclose()
                await asyncio.wait_for(task, 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await first.aclose()
                await waiting.aclose()
            assert not limit.locked()


@pytest.mark.asyncio
async def test_default_path_unchanged_until_measured_direct_opt_in(binaries):
    with serve(ModelHandler) as endpoint:
        async with Node(binaries, endpoint) as node:
            node.client.prefer_direct = False
            result = await node.client.complete(model_ref('mac', 'example:8b'), [])
            assert result['content'] == 'RELAY'
            assert '/api/fleet/apps/workload-direct-connect' not in node.requests
            # Explicit direct-only routes bypass the conservative default.
            result = await node.client.complete('fleet-route://direct', [])
            assert result['content'] == 'first' and result['route']['transport'] == 'fleet_direct'
