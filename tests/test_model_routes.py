import copy
import asyncio
import json
import time

import httpx
import pytest

from pantheon.models.client import ModelServices
from pantheon.models.routing import parse_route_ref, summary


def plan():
    def candidate(node):
        spec = {'id': 'model', 'operations': ['text'], 'tools': True, 'context': 8192}
        row = {'deployment_id': node, 'name': node, 'node_id': node, 'engine': 'ollama', 'mode': 'managed',
               'state': 'ready', 'config_revision': 'a'*64, 'models': [spec],
               'binding': {'node_id': node, 'instance_id': node, 'revision': 'b'*64, 'generation': 3}}
        return {'deployment': row, 'model': spec, 'compute': 'node', 'billing': 'local'}
    return {'route': {'route_id': 'private', 'name': 'Private', 'revision': 7, 'transport': 'relay_allowed',
                     'fallback': 'preflight', 'selection': 'ready_first'},
            'candidates': [candidate('cold'), candidate('warm')], 'transport': 'fleet_relay'}


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_alias_selects_loaded_once_and_never_replays_after_submission(failed):
    route = plan()
    submitted, probes = [], []
    def transport(request):
        path = request.url.path
        if path.endswith('/resolve'):
            assert json.loads(request.content)['tools'] is True
            return httpx.Response(200, json=copy.deepcopy(route))
        if path.endswith('/workload-connect'):
            body = json.loads(request.content)
            assert body['generation'] == 3
            return httpx.Response(200, json={'origin': f"https://{body['node_id']}.test", 'access_token': body['node_id'], 'expires': time.time()+60})
        if path == '/route-state':
            probes.append(request.url.host)
            return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
                'active_calls': 0, 'capacity': 4, 'models': [{'id': 'model', 'loaded': request.url.host == 'warm.test'}]})
        if path == '/cancel':
            return httpx.Response(200, json={'cancelled': True})
        assert path == '/v1/chat/completions'
        submitted.append(request.url.host)
        assert request.headers['authorization'] == 'Bearer warm'
        assert request.headers['x-model-config'] == 'a'*64
        route['candidates'][1]['deployment']['binding']['generation'] = 4
        return httpx.Response(503) if failed else httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    if failed:
        with pytest.raises(RuntimeError, match='No fallback'):
            await client.complete('fleet-route://private', [], tools=[{'type': 'function'}])
    else:
        result = await client.complete('fleet-route://private', [], tools=[{'type': 'function'}])
        assert result['content'] == 'ok'
        assert result['_metadata']['model_service']['generation'] == 3
        assert result['route']['alias_revision'] == 7
        assert result['route']['compute_location'] == 'node'
    assert set(probes) == {'cold.test', 'warm.test'}
    assert submitted == ['warm.test']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['none', 'offline', 'busy', 'changed', 'direct'])
async def test_route_preflight_fails_closed(mode):
    route = plan(); paths = []
    if mode == 'none': route['route']['fallback'] = 'none'
    if mode == 'direct': route['route']['transport'] = 'direct_only'
    def transport(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/resolve'): return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            return httpx.Response(200, json={'origin': 'https://node.test', 'access_token': 'grant', 'expires': time.time()+60})
        assert request.url.path == '/route-state'
        if mode in {'offline', 'none'}: return httpx.Response(503)
        return httpx.Response(200, json={'protocol': 1, 'ready': True,
            'config_revision': 'b'*64 if mode == 'changed' else 'a'*64, 'active_calls': 4, 'capacity': 4,
            'models': [{'id': 'model', 'loaded': True}]})
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    with pytest.raises((RuntimeError, ValueError)):
        await client.complete('fleet-route://private', [])
    assert '/v1/chat/completions' not in paths
    if mode == 'none': assert paths.count('/route-state') == 1
    if mode == 'direct': assert len(paths) == 1


def test_alias_identity_and_conservative_capabilities():
    for ref in ['fleet-route://a/path', 'fleet-route://a?key=x', 'fleet-route://a#b', 'fleet-route://UPPER', 'fleet-model://a/b']:
        with pytest.raises(ValueError): parse_route_ref(ref)
    route = {'route_id': 'a', 'name': 'Route', 'requires': {'operation': 'text'},
             'candidates': [{'deployment_id': 'a', 'model_id': 'x'}, {'deployment_id': 'b', 'model_id': 'x'}]}
    rows = {'a': {'models': [{'id': 'x', 'tools': True, 'context': 8192}]},
            'b': {'models': [{'id': 'x', 'tools': None, 'context': 4096}]}}
    assert summary(route, rows)['tools'] is None
    assert summary(route, rows)['context'] == 4096
    del rows['b']
    assert summary(route, rows)['context'] is None


@pytest.mark.asyncio
async def test_refreshing_deployments_preserves_active_alias_capabilities():
    routes = [{'route_id': 'private'}]
    def transport(request):
        return httpx.Response(200, json={'routes': routes} if request.url.path.endswith('/routes') else {'deployments': []})
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    client.metadata['fleet-route://private'] = {'context': 8192, 'tools': True}
    await client.deployments()
    await client.routes()
    assert client.metadata['fleet-route://private']['context'] == 8192
    routes.clear()
    await client.routes()
    assert 'fleet-route://private' not in client.metadata


@pytest.mark.asyncio
async def test_ordered_route_does_not_wait_for_slow_lower_priority_probe():
    route = plan(); route['route']['selection'] = 'ordered'
    pending = 0
    async def transport(request):
        nonlocal pending
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            node = json.loads(request.content)['node_id']
            return httpx.Response(200, json={'origin': f'https://{node}.test', 'access_token': node, 'expires': time.time()+60})
        if request.url.path == '/route-state':
            if request.url.host == 'warm.test':
                pending += 1
                try:
                    await asyncio.Event().wait()
                finally:
                    pending -= 1
            await asyncio.sleep(.01)
            return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
                'active_calls': 0, 'capacity': 4, 'models': [{'id': 'model', 'loaded': False}]})
        assert request.url.path == '/v1/chat/completions' and request.url.host == 'cold.test'
        return httpx.Response(200, text='data: [DONE]\n\n')
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    result = await asyncio.wait_for(client.complete('fleet-route://private', []), 1)
    assert result['route']['node_id'] == 'cold'
    assert pending == 0
