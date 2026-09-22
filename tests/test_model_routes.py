import copy
import asyncio
import json
import time

import httpx
import pytest

from pantheon.models.client import ModelServices
from pantheon.models.routing import parse_route_ref, select, summary


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


@pytest.mark.asyncio
async def test_route_can_queue_and_ranks_actual_waiting_work():
    route = plan()
    submitted = []
    def transport(request):
        path = request.url.path
        if path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if path.endswith('/workload-connect'):
            node = json.loads(request.content)['node_id']
            return httpx.Response(200, json={'origin': f'https://{node}.test', 'access_token': 'grant', 'expires': time.time()+60})
        if path == '/route-state':
            return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
                'active_calls': 4, 'capacity': 4, 'queue_capacity': 32,
                'queued_calls': 9 if request.url.host == 'cold.test' else 1,
                'models': [{'id': 'model', 'loaded': True}]})
        submitted.append(request.url.host)
        return httpx.Response(200, text='data: [DONE]\n\n', headers={'X-Model-Queue-Ms': '120'})
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    result = await client.complete('fleet-route://private', [])
    assert submitted == ['warm.test']
    assert result['route']['queue_ms'] == 120
    assert result['route']['request_id']


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


@pytest.mark.asyncio
async def test_models_on_same_service_share_one_fresh_probe_per_invocation():
    route = plan()
    original = route['candidates'][0]
    route['candidates'] = [copy.deepcopy(original) for _ in range(16)]
    for i, candidate in enumerate(route['candidates']):
        candidate['model']['id'] = f'model-{i}'
    calls, loaded = [], 15

    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            return httpx.Response(200, json={'origin': 'https://node.test', 'access_token': 'grant', 'expires': time.time()+60})
        assert request.url.path == '/route-state'
        return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
            'active_calls': 0, 'capacity': 4, 'models': [
                {'id': f'model-{i}', 'loaded': i == loaded} for i in range(16)]})

    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    _, model, _, _ = await select(client, 'fleet-route://private', {})
    assert model['id'] == 'model-15'
    assert calls.count('/api/fleet/apps/workload-connect') == 1
    assert calls.count('/route-state') == 1
    loaded = 0
    _, model, _, _ = await select(client, 'fleet-route://private', {})
    assert model['id'] == 'model-0'
    assert calls.count('/route-state') == 2  # do not cache live load/queue state between calls


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['deployment_id', 'node_id', 'binding', 'config_revision'])
async def test_probe_sharing_never_crosses_service_identity(changed):
    route = plan()
    route['candidates'][1] = copy.deepcopy(route['candidates'][0])
    row = route['candidates'][1]['deployment']
    if changed == 'binding':
        row['binding']['generation'] += 1
    else:
        row[changed] = 'b'*64 if changed == 'config_revision' else 'other'
    probes = []

    def transport(request):
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            return httpx.Response(200, json={'origin': 'https://node.test', 'access_token': 'grant', 'expires': time.time()+60})
        probes.append(request.headers['x-model-config'])
        return httpx.Response(200, json={'protocol': 1, 'ready': True,
            'config_revision': request.headers['x-model-config'], 'active_calls': 1, 'capacity': 4,
            'models': [{'id': 'model', 'loaded': True}]})

    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    await select(client, 'fleet-route://private', {})
    assert len(probes) == 2


@pytest.mark.asyncio
async def test_ready_first_skips_slow_lower_priority_only_when_winner_cannot_be_beaten():
    route = plan()
    # A failed earlier candidate must also be accounted for before pruning.
    route['candidates'].insert(0, copy.deepcopy(route['candidates'][0]))
    first = route['candidates'][0]['deployment']
    first.update(deployment_id='offline', node_id='offline', binding={**first['binding'], 'node_id': 'offline'})
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def transport(request):
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            node = json.loads(request.content)['node_id']
            return httpx.Response(200, json={'origin': f'https://{node}.test', 'access_token': node, 'expires': time.time()+60})
        assert request.url.path == '/route-state'
        if request.url.host == 'warm.test':
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        await entered.wait()
        if request.url.host == 'offline.test':
            return httpx.Response(503)
        return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
            'active_calls': 0, 'capacity': 4, 'models': [{'id': 'model', 'loaded': True}]})

    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    row, _, _, _ = await asyncio.wait_for(select(client, 'fleet-route://private', {}), 1)
    assert row['node_id'] == 'cold'
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_ready_first_preserves_earlier_tie_break_despite_slower_probe():
    route = plan()
    later_returned, release = asyncio.Event(), asyncio.Event()

    async def transport(request):
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            node = json.loads(request.content)['node_id']
            return httpx.Response(200, json={'origin': f'https://{node}.test', 'access_token': node, 'expires': time.time()+60})
        if request.url.host == 'cold.test':
            await release.wait()
        else:
            later_returned.set()
        return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
            'active_calls': 0, 'capacity': 4, 'models': [{'id': 'model', 'loaded': True}]})

    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    task = asyncio.create_task(select(client, 'fleet-route://private', {}))
    try:
        await asyncio.wait_for(later_returned.wait(), 1)
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        row, _, _, _ = await asyncio.wait_for(task, 1)
        assert row['node_id'] == 'cold'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_zero_capacity_candidate_is_unavailable_even_with_queue_space():
    route = plan()

    def transport(request):
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            node = json.loads(request.content)['node_id']
            return httpx.Response(200, json={'origin': f'https://{node}.test', 'access_token': node, 'expires': time.time()+60})
        assert request.url.path == '/route-state'
        return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': 'a'*64,
            'active_calls': 0, 'capacity': 0 if request.url.host == 'cold.test' else 4,
            'queued_calls': 0, 'queue_capacity': 32, 'models': [{'id': 'model', 'loaded': True}]})

    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    row, _, _, _ = await select(client, 'fleet-route://private', {})
    assert row['node_id'] == 'warm'


@pytest.mark.asyncio
async def test_cancelled_route_releases_shared_probe_and_all_waiters():
    route = plan()
    route['candidates'] = [copy.deepcopy(route['candidates'][0]) for _ in range(16)]
    started, closed = asyncio.Event(), asyncio.Event()
    probes = 0

    async def transport(request):
        nonlocal probes
        if request.url.path.endswith('/resolve'):
            return httpx.Response(200, json=route)
        if request.url.path.endswith('/workload-connect'):
            return httpx.Response(200, json={'origin': 'https://node.test', 'access_token': 'grant', 'expires': time.time()+60})
        assert request.url.path == '/route-state'
        probes += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    before = asyncio.all_tasks()
    task = asyncio.create_task(select(client, 'fleet-route://private', {}))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert probes == 1 and closed.is_set()
    assert not asyncio.all_tasks() - before
