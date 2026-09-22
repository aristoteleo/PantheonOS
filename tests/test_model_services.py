import asyncio
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from pantheon.models.client import ModelServices, model_ref, parse_ref, stream_events

spec = importlib.util.spec_from_file_location('model_connector', Path(__file__).parents[1] / 'apps/model-service/server.py')
connector_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connector_module)


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def deployment():
    return dict(deployment_id='mac', name='Mac Ollama', node_id='mac-node', node_name='Mac',
        engine='ollama', state='ready', revision=1, config_revision='c' * 64,
        binding=dict(node_id='mac-node', instance_id='instance', revision='a' * 64,
                     generation=2, component='backend', port='http'),
        models=[dict(id='example:8b', operations=['text', 'embedding'], tools=True, vision=True, structured_output=True)])


def test_connector_configuration_and_discovery(tmp_path):
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/v1/models'
            assert self.headers['Authorization'] == 'Bearer node-secret'
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"example:8b"}]}')
    key = tmp_path / 'credential'; key.write_text('node-secret')
    connector = connector_module.Connector(tmp_path / 'data')
    with serve(Engine) as endpoint:
        result = connector.configure(dict(engine='ollama', endpoint=endpoint, credential_file=str(key)))
        assert len(result['config_revision']) == 64
        assert connector.discover()['models'] == [{'id': 'example:8b'}]
        assert 'node-secret' not in connector.path.read_text()
        assert connector.path.stat().st_mode & 0o777 == 0o600
    assert connector_module.Connector(tmp_path / 'data').config == connector.config
    for endpoint in ('http://remote/v1', 'file:///etc/passwd', 'https://key@example/v1', 'https://example/v1?key=abc'):
        with pytest.raises(ValueError):
            connector_module.validate_config({'engine': 'api', 'endpoint': endpoint})
    with pytest.raises(ValueError, match='loopback'):
        connector_module.validate_config({'engine': 'ollama', 'endpoint': 'https://example/v1'})
    assert connector_module.validate_config({'engine': 'api', 'endpoint': 'https://example/v1',
        'credential_file': r'C:\Users\user\model-key.txt'})['credential_file'].startswith('C:')


def test_inference_access_cannot_configure_model_service(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_APP_RPC_TOKEN', 'node-instance-generation-secret')
    connector = connector_module.Connector(tmp_path)
    with serve(connector_module.handler(connector)) as endpoint, httpx.Client() as client:
        body = {'method': 'configure', 'args': {'config': {'engine': 'ollama', 'endpoint': 'http://127.0.0.1:11434'}}}
        for headers in ({}, {'Authorization': 'Bearer inference-grant'}, {'X-Fleet-RPC-Token': 'another-instance'}):
            assert client.post(endpoint + '/rpc', json=body, headers=headers).status_code == 403
        assert connector.config is None
        response = client.post(endpoint + '/rpc', json=body, headers={'X-Fleet-RPC-Token': connector.rpc_token})
        assert response.status_code == 200
        assert 'secret' not in response.text
        connector.drain()
        revision = response.json()['config_revision']
        body = {'method': 'resume', 'args': {'config_revision': revision}}
        assert client.post(endpoint + '/rpc', json=body).status_code == 403
        assert not connector.accepting
        assert client.post(endpoint + '/rpc', json=body,
            headers={'X-Fleet-RPC-Token': connector.rpc_token}).status_code == 200
        assert connector.accepting


def test_route_probe_does_not_load_models_and_observes_drain(tmp_path):
    metadata_calls = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            metadata_calls.append(self.path)
            assert self.path == '/v1/models'
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"model"}]}')
        def do_POST(self):
            pytest.fail('Route preflight cannot load a model or run inference')
    connector = connector_module.Connector(tmp_path)
    with serve(Engine) as upstream, serve(connector_module.handler(connector)) as endpoint:
        connector.configure({'engine': 'ollama', 'endpoint': upstream})
        with httpx.Client() as client:
            assert client.get(endpoint + '/route-state').status_code == 409
            headers = {'X-Model-Config': connector.revision}
            first = client.get(endpoint + '/route-state', headers=headers).json()
            assert first['ready'] is True and first['models'] == [{'id': 'model', 'loaded': None}]
            assert first['capacity'] == 4 and first['active_calls'] == 0
            connector.drain()
            assert client.get(endpoint + '/route-state', headers=headers).json()['ready'] is False
            assert metadata_calls == ['/v1/models']


def test_connector_streams_without_waiting_for_completion_and_drains(tmp_path):
    sent = threading.Event()
    finish = threading.Event()
    received = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            assert self.headers.get('X-Pantheon-App-Token') is None
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'); self.wfile.flush()
            sent.set(); finish.wait(3)
            try: self.wfile.write(b'data: [DONE]\n\n')
            except OSError: pass
    connector = connector_module.Connector(tmp_path)
    with serve(Engine) as upstream, serve(connector_module.handler(connector)) as url:
        connector.configure({'engine': 'ollama', 'endpoint': upstream})
        headers = {'X-Model-Request': 'test-request', 'X-Model-Config': connector.revision}
        with httpx.Client(timeout=5) as client:
            with client.stream('POST', url + '/v1/chat/completions', headers=headers,
                               json={'model': 'example:8b', 'messages': [], 'stream': True}) as response:
                assert response.status_code == 200
                assert 'first' in next(response.iter_lines())
                assert sent.is_set() and not finish.is_set()
                drained = client.post(url + '/drain', json={}, headers={'X-Control-Key': connector.control}).json()
                assert drained == {'status': 'waiting', 'safe_to_stop': False, 'message': 'Model calls or model operations are still active'}
                assert client.post(url + '/v1/chat/completions', headers={**headers, 'X-Model-Request': 'after-drain'}, json={}).status_code == 503
                with pytest.raises(ValueError, match='active requests'):
                    connector.configure(connector.config)
                assert client.post(url + '/cancel', headers=headers, json={'request_id': 'test-request'}).json()['cancelled']
                finish.set()
            deadline = time.monotonic() + 2
            while connector.calls and time.monotonic() < deadline: time.sleep(.01)
            assert not connector.calls
            assert client.post(url + '/drain', json={}).status_code == 403
            assert client.post(url + '/drain', json={}, headers={'X-Control-Key': connector.control}).json()['safe_to_stop']
            assert client.post(url + '/v1/chat/completions', headers=headers, json={}).status_code == 503
        assert len(received) == 1


def test_slow_discovery_does_not_block_cancellation(tmp_path):
    started, finish = threading.Event(), threading.Event()
    class SlowEngine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            started.set(); finish.wait(3)
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[]}')
    connector = connector_module.Connector(tmp_path)
    with serve(SlowEngine) as endpoint:
        connector.configure({'engine': 'ollama', 'endpoint': endpoint})
        connector.calls['request'] = {'cancelled': False}
        discovery = threading.Thread(target=connector.discover)
        discovery.start()
        try:
            assert started.wait(1)
            before = time.monotonic()
            assert connector.cancel('request')['cancelled']
            assert time.monotonic() - before < .5
        finally:
            finish.set(); discovery.join(4)


def test_cancel_rejects_stale_config_without_tombstone_or_interrupting_new_call(tmp_path):
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'ollama', 'endpoint': 'http://127.0.0.1:11434'})
    connector.calls['active'] = {'cancelled': False}
    with serve(connector_module.handler(connector)) as endpoint, httpx.Client() as client:
        for config in ('', 'old-config'):
            for request_id in ('active', 'unknown'):
                response = client.post(endpoint + '/cancel', headers={'X-Model-Config': config},
                                       json={'request_id': request_id})
                assert response.status_code == 409
                assert not connector.calls['active']['cancelled']
                assert not connector.activity.list()
        response = client.post(endpoint + '/cancel', headers={'X-Model-Config': connector.revision},
                               json={'request_id': 'active'})
        assert response.json() == {'cancelled': True}
        assert connector.calls['active']['cancelled']


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body,confirmed,fallback', [
    (200, {'cancelled': True}, True, False),
    (200, {'cancelled': False}, False, False),
    (200, {'cancelled': 'true'}, False, False),
    (200, 'invalid-json', False, False),
    (403, {}, False, False),
    (409, {}, False, False),
    (503, {}, True, True),
])
async def test_direct_cancel_validates_acknowledgement_and_only_retries_metadata(status, body, confirmed, fallback):
    row, requests = deployment(), []
    async def transport(request):
        requests.append(request)
        if request.url.path == '/api/fleet/apps/workload-connect':
            assert json.loads(request.content) == row['binding']
            return httpx.Response(200, json={'origin': 'https://relay.test', 'access_token': 'scoped'})
        assert request.url.path == '/cancel'
        assert json.loads(request.content) == {'request_id': 'invocation'}
        assert request.headers['X-Model-Config'] == row['config_revision']
        if request.url.host == 'direct.test':
            return httpx.Response(200, json={'cancelled': True})
        assert request.headers['Authorization'] == 'Bearer scoped'
        return (httpx.Response(status, text=body) if isinstance(body, str)
                else httpx.Response(status, json=body))
    wire = httpx.MockTransport(transport)
    client = ModelServices('https://hub.test', 'token', wire)
    async with httpx.AsyncClient(transport=wire) as http:
        result = await client.cancel_request(row, 'invocation', http,
            {'origin': 'https://direct.test', 'access_token': '', '_transport': 'fleet_direct'})
    assert result is confirmed
    assert any(r.url.host == 'direct.test' for r in requests) is fallback
    assert all(r.url.path in ('/api/fleet/apps/workload-connect', '/cancel') for r in requests)


@pytest.mark.asyncio
async def test_registry_routing_tools_usage_and_exact_generation():
    row = deployment()
    observed = []
    def transport(request):
        observed.append(request)
        if request.url.path == '/api/model-services/routes': return httpx.Response(200, json={'routes': []})
        if request.url.path == '/api/model-services': return httpx.Response(200, json={'deployments': [row]})
        if request.url.path == '/api/fleet/apps/workload-connect':
            assert json.loads(request.content) == row['binding']
            return httpx.Response(200, json={'origin': 'https://instance.apps.test', 'access_token': 'instance-only', 'expires': time.time() + 3600})
        assert str(request.url) == 'https://instance.apps.test/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer instance-only'
        assert request.headers['x-model-config'] == row['config_revision']
        body = json.loads(request.content)
        assert body['model'] == 'example:8b' and body['tools'][0]['function']['name'] == 'weather'
        chunks = [
            {'choices': [{'index': 0, 'delta': {'reasoning_content': 'think'}}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call_1', 'function': {'name': 'weather', 'arguments': '{"city":'}}]}}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"SF"}'}}]}, 'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}},
        ]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n')
    client = ModelServices('https://hub.test', 'fleet-identity', httpx.MockTransport(transport))
    chunks = []
    async def chunk(delta): chunks.append(delta)
    tools = [{'type': 'function', 'function': {'name': 'weather', 'parameters': {'type': 'object'}}}]
    for _ in range(2):
        result = await client.complete(model_ref('mac', 'example:8b'), [{'role': 'user', 'content': 'Weather'}], tools=tools, process_chunk=chunk)
        assert result['tool_calls'][0]['function'] == {'name': 'weather', 'arguments': '{"city":"SF"}'}
        assert result['usage']['prompt_tokens'] == 10
        assert result['_metadata']['model_service']['generation'] == 2
        assert result['reasoning_content'] == 'think'
    assert sum(r.url.path == '/api/fleet/apps/workload-connect' for r in observed) == 1
    assert len(chunks) == 6
    sources, cards = await client.catalog()
    assert sources[0]['egress_node'] == 'Mac' and cards[0]['context'] is None
    assert parse_ref(model_ref('mac', 'org/name:1')) == ('mac', 'org/name:1')


@pytest.mark.asyncio
async def test_no_cloud_fallback_unknown_capabilities_or_parameter_override():
    row = deployment(); row['models'][0]['tools'] = None
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={'deployments': [row]})
    client = ModelServices('https://hub.test', 'token', httpx.MockTransport(transport))
    with pytest.raises(ValueError, match='Tool support'):
        await client.complete(model_ref('mac', 'example:8b'), [], tools=[{'type': 'function'}])
    with pytest.raises(ValueError, match='routing'):
        await client.complete(model_ref('mac', 'example:8b'), [], model_params={'api_key': 'bad'})
    assert all(r.url.path == '/api/model-services' for r in calls)


@pytest.mark.asyncio
async def test_cancel_and_truncated_stream_are_not_success_or_retried():
    row = deployment(); requests = []
    def transport(request):
        requests.append(request.url.path)
        if request.url.path == '/api/model-services': return httpx.Response(200, json={'deployments': [row]})
        if request.url.path == '/api/fleet/apps/workload-connect':
            return httpx.Response(200, json={'origin': 'https://instance.apps.test', 'access_token': 'opaque'})
        if request.url.path == '/cancel': return httpx.Response(200, json={'cancelled': True})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
    client = ModelServices('https://hub.test', 'token', httpx.MockTransport(transport))
    with pytest.raises(RuntimeError, match='without completion'):
        await client.complete(model_ref('mac', 'example:8b'), [])
    assert requests.count('/v1/chat/completions') == 1
    assert requests[-1] == '/cancel'


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_delay', [0, .05])
async def test_explicit_cancellation_arrives_before_stream_disconnect(cancel_delay):
    events, first = [], asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            events.append('disconnect')

    async def transport(request):
        if request.url.path == '/api/model-services':
            return httpx.Response(200, json={'deployments': [deployment()]})
        if request.url.path == '/api/fleet/apps/workload-connect':
            return httpx.Response(200, json={'origin': 'https://instance.apps.test', 'access_token': 'opaque'})
        if request.url.path == '/cancel':
            await asyncio.sleep(cancel_delay)
            events.append('cancel')
            return httpx.Response(200, json={'cancelled': True})
        events.append('infer')
        return httpx.Response(200, stream=Body())

    async def chunk(delta):
        first.set()
        await asyncio.Event().wait()

    client = ModelServices('https://hub.test', 'token', httpx.MockTransport(transport))
    task = asyncio.create_task(client.complete(model_ref('mac', 'example:8b'), [], process_chunk=chunk))
    try:
        await asyncio.wait_for(first.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ['infer', 'cancel', 'disconnect']
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_fleet_provider_does_not_resolve_to_platform(monkeypatch):
    from pantheon.utils.llm_providers import detect_provider, ProviderType
    monkeypatch.setenv('PLATFORM_MODEL_MODE', 'openrouter')
    config = detect_provider(model_ref('mac', 'example:8b'), False)
    assert config.provider_type == ProviderType.FLEET and config.api_key is None
    from pantheon.agent import _resolve_model_spec_with_current_provider
    assert _resolve_model_spec_with_current_provider('low', config.model_name) == config.model_name


@pytest.mark.asyncio
async def test_cancellation_keeps_real_stream_open_during_cancel_roundtrip(tmp_path):
    finish, first = threading.Event(), asyncio.Event()

    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
            self.wfile.flush()
            finish.wait(5)

    connector = connector_module.Connector(tmp_path)
    with serve(Engine) as upstream, serve(connector_module.handler(connector)) as origin:
        connector.configure({'engine': 'ollama', 'endpoint': upstream})
        row = deployment()
        row['config_revision'] = connector.revision
        async with httpx.AsyncHTTPTransport() as wire:
            async def transport(request):
                if request.url.path == '/api/model-services':
                    return httpx.Response(200, json={'deployments': [row]})
                if request.url.path == '/api/fleet/apps/workload-connect':
                    return httpx.Response(200, json={'origin': 'https://instance.apps.test', 'access_token': 'opaque'})
                if request.url.path == '/cancel':
                    # A real relay roundtrip yields to stream finalizers.
                    await asyncio.sleep(.2)
                request.url = httpx.URL(origin).copy_with(path=request.url.path)
                return await wire.handle_async_request(request)

            async def chunk(delta):
                first.set()
                await asyncio.Event().wait()

            client = ModelServices('https://hub.test', 'token', httpx.MockTransport(transport))
            task = asyncio.create_task(client.complete(model_ref('mac', 'example:8b'), [], process_chunk=chunk))
            try:
                arrived = asyncio.create_task(first.wait())
                try:
                    await asyncio.wait({task, arrived}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
                    assert first.is_set(), repr(task.exception()) if task.done() else 'No first token'
                finally:
                    arrived.cancel()
                    await asyncio.gather(arrived, return_exceptions=True)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                for _ in range(100):
                    if not connector.calls:
                        break
                    await asyncio.sleep(.01)
                record = connector.activity.list()[0]
                assert record['state'] == 'cancelled'
                assert record['reason'] == 'cancelled'
            finally:
                finish.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_manager_resumes_the_bound_artifact_without_reinstalling(monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    import pantheon.models.manager as manager
    row = deployment()
    stopped = dict(instance_id='instance', digest='a' * 64, scope='model-mac',
                   app_id='model-service', generation=2, state='stopped')
    ready = {**stopped, 'state': 'ready', 'generation': 3}
    lifecycle = SimpleNamespace(status=AsyncMock(return_value={'instances': {'instance': stopped}}),
        stage=AsyncMock(), submit=AsyncMock(return_value={'operation': 'start'}), usage=AsyncMock())
    monkeypatch.setattr(manager, 'FleetLifecycle', lambda _: lifecycle)
    coordinator = manager.ModelServiceManager(client=object(), resolver=object())
    coordinator.wait = AsyncMock(return_value={'instances': {'instance': ready}})
    binding = await coordinator.ensure(row)
    assert binding['revision'] == row['binding']['revision'] and binding['generation'] == 3
    lifecycle.stage.assert_not_called()
    lifecycle.submit.assert_awaited_once_with('mac-node', 'start', 'a' * 64, scope='model-mac', generation=2)
    lifecycle.usage.assert_awaited_once_with('mac-node', 'keep_alive', instance_id='instance',
        revision='a' * 64, generation=3, keep_alive=True)


@pytest.mark.asyncio
async def test_attach_rejects_old_fleet_before_creating_or_installing_anything():
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from pantheon.models.manager import ModelServiceManager
    client = SimpleNamespace(save=AsyncMock(), deployments=AsyncMock())
    resolver = SimpleNamespace(_client=object(), _list_nodes=AsyncMock(return_value=[{
        'node_id': 'old-node', 'capability': {'runtimes': {'app-lifecycle': '1'}},
    }]))
    coordinator = ModelServiceManager(client=client, resolver=resolver)
    coordinator.ensure = AsyncMock()
    with pytest.raises(RuntimeError, match='Update Fleet on the selected node'):
        await coordinator.attach('local', 'Local model', 'old-node', 'ollama', 'http://127.0.0.1:11434')
    client.save.assert_not_called()
    coordinator.ensure.assert_not_called()


@pytest.mark.asyncio
async def test_playground_fleet_source_is_exact_and_preserves_placement(monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from pantheon.chatroom.llm_playground import Playground
    complete = AsyncMock(return_value={'content': 'local result', 'route': {'node_id': 'mac-node'},
        'usage': {}, 'elapsed_ms': 10, 'first_token_ms': 5})
    monkeypatch.setattr('pantheon.models.client.get_client', lambda: SimpleNamespace(complete=complete))
    result = await Playground().run('fleet-smoke-01', 'fleet:mac', model_ref('mac', 'example:8b'), 'hi')
    assert result['success'] and result['output'] == 'local result'
    assert result['route']['node_id'] == 'mac-node'
    complete.assert_awaited_once()
    with pytest.raises(ValueError, match='published model'):
        await Playground().run('fleet-smoke-02', 'fleet:other', model_ref('mac', 'example:8b'), 'hi')


@pytest.mark.asyncio
async def test_stream_limits_and_small_tokens_do_not_wait_for_a_full_buffer():
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"token":"first"}\r\n\r\n'
            raise AssertionError('The first token should be delivered before reading the next block')
    response = httpx.Response(200, stream=Chunks())
    try:
        events = stream_events(response)
        assert await anext(events) == '{"token":"first"}'
        await events.aclose()
    finally:
        await response.aclose()
    for suffix in ('', '\n\n'):
        response = httpx.Response(200, content=('data: ' + 'x' * (1024 * 1024 + 1) + suffix).encode())
        with pytest.raises(ValueError, match='event exceeds'):
            async for _ in stream_events(response):
                pass


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('MODEL_SERVICES_TEST_OLLAMA'), reason='Opt-in real local engine smoke test')
async def test_real_local_agent_tool_roundtrip(tmp_path, monkeypatch):
    """Real Agent + connector + Ollama; only Hub directory/gateway are simulated."""
    from pantheon.agent import Agent
    import pantheon.models.client as clients
    model = os.getenv('MODEL_SERVICES_TEST_MODEL', 'gemma4:latest')
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'ollama', 'endpoint': os.environ['MODEL_SERVICES_TEST_OLLAMA']})
    row = deployment()
    row['config_revision'] = connector.revision
    row['models'] = [dict(id=model, context=8192, tools=True, operations=['text'])]
    with serve(connector_module.handler(connector)) as url:
        network = httpx.AsyncHTTPTransport()
        async def route(request):
            if request.url.host == 'hub.test':
                if request.url.path == '/api/model-services':
                    return httpx.Response(200, json={'deployments': [row]})
                return httpx.Response(200, json={'origin': 'https://instance.apps.test', 'access_token': 'test-only', 'expires': time.time() + 60})
            return await network.handle_async_request(httpx.Request(request.method, url + request.url.path,
                headers=dict(request.headers), content=request.content,
                extensions={'timeout': {'connect': 20, 'read': 120, 'write': 20, 'pool': 20}}))
        client = ModelServices('https://hub.test', 'test-only', httpx.MockTransport(route))
        monkeypatch.setattr(clients, 'get_client', lambda: client)
        def lookup_code(city: str) -> str:
            """Look up a city's test code. Always call this for a requested city's code."""
            return 'FLEET_TOOL_OK'
        ref = model_ref('mac', model)
        agent = Agent('Fleet test', 'Use lookup_code to answer.', model=ref, tools=[lookup_code],
                      model_params={'temperature': 0, 'max_tokens': 512}, use_memory=False)
        messages = [{'role': 'user', 'content': 'Call lookup_code with city Paris. Do not guess its result.'}]
        try:
            result = await agent._acompletion(messages, ref)
            assert result.get('tool_calls'), result.get('content')
            call = result['tool_calls'][0]
            assert call['function']['name'] == 'lookup_code'
            assert json.loads(call['function']['arguments'])['city'] == 'Paris'
            messages.extend([result, {'role': 'tool', 'tool_call_id': call['id'], 'content': lookup_code('Paris')}])
            final = await agent._acompletion(messages, ref, tool_use=False)
            assert 'FLEET_TOOL_OK' in final['content']
            assert final['_metadata']['model_service']['node_id'] == 'mac-node'
            print('Real local Agent tool roundtrip:', final['content'])
        finally:
            await network.aclose()
