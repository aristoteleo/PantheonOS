from copy import deepcopy
import json
import time

import httpx
import pytest

from pantheon.models.client import ModelServices, ControlError
from pantheon.models import idle
from pantheon.models.routing import select
from test_model_services import deployment


def row(name='mac'):
    r = deployment()
    r.update(deployment_id=name, node_id=name+'-node', mode='managed',
        engine_binding=dict(node_id=name+'-node', instance_id=name+'-engine', revision='e'*64,
                            generation=1, component='backend', port='http'),
        engine_idle=dict(idle_seconds=60, policy_revision=1, phase='enabled'))
    r['binding'].update(node_id=name+'-node', instance_id=name+'-connector')
    return r


def snapshot(r, state):
    return dict(id=r['deployment_id'], revision=1, enabled=True, state=state,
        connector={k: r['binding'][k] for k in ('instance_id', 'revision', 'generation')},
        engine={**{k: r['engine_binding'][k] for k in ('instance_id', 'revision')},
                'generation': 3 if state == 'active' else 2},
        config_revision='d'*64 if state == 'active' else 'c'*64, idle_seconds=60, cycle=1, wake_requested=state == 'waking')


@pytest.mark.asyncio
@pytest.mark.parametrize('alias', [False, True])
async def test_wake_before_binding_only_selected_service_and_no_prompt_on_control(monkeypatch, alias):
    rows = {n: row(n) for n in ('mac', 'other')}
    phases = {n: 'sleeping' for n in rows}
    actions, inferences, grants = [], [], []
    async def tick(_): pass
    monkeypatch.setattr(idle.asyncio, 'sleep', tick)
    def transport(request):
        path, body = request.url.path, json.loads(request.content or 'null')
        if path.endswith('/resolve'):
            return httpx.Response(200, json={'route': {'route_id': 'choice', 'revision': 1, 'transport': 'relay_allowed',
                'selection': 'ordered', 'fallback': 'preflight'}, 'transport': 'fleet_relay',
                'candidates': [{'deployment': deepcopy(r), 'model': r['models'][0], 'compute': 'node', 'billing': 'local'}
                               for r in rows.values()]})
        if path == '/api/model-services': return httpx.Response(200, json={'deployments': list(rows.values())})
        if path.endswith('/engine-idle'):
            name = path.split('/')[-2]
            actions.append((name, body['action']))
            assert set(body) == {'action', 'revision'} and 'secret prompt' not in request.content.decode()
            assert body['revision'] == rows[name]['revision']
            if body['action'] == 'wake':
                phases[name] = 'waking' if phases[name] == 'sleeping' else 'active'
                if phases[name] == 'active':
                    rows[name]['engine_binding']['generation'] = 3
                    rows[name]['config_revision'] = 'd'*64
                    rows[name]['revision'] += 1
            elif phases[name] == 'waking': phases[name] = 'active'
            return httpx.Response(200, json={'deployment': deepcopy(rows[name]), 'idle': snapshot(rows[name], phases[name])})
        if path.endswith('/workload-connect'):
            assert phases['mac'] == 'active' and rows['mac']['config_revision'] == 'd'*64
            assert body == rows['mac']['binding']
            grants.append(body)
            return httpx.Response(200, json={'origin': 'https://mac.test', 'access_token': 'grant', 'expires': time.time()+60})
        assert path == '/v1/chat/completions', path
        assert request.headers['x-model-config'] == 'd'*64
        assert body['messages'][0]['content'] == 'secret prompt'
        inferences.append(body)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    result = await client.complete('fleet-route://choice' if alias else 'fleet-model://mac/example:8b',
                                   [{'role': 'user', 'content': 'secret prompt'}])
    assert result['content'] == 'ok' and len(grants) == len(inferences) == 1
    assert all(name == 'mac' for name, action in actions if action == 'wake')
    assert phases['other'] == 'sleeping'
    assert rows['mac']['engine_binding']['generation'] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stop', 'node', 'publication', 'pending', 'inference'])
async def test_wake_failure_never_sends_or_replays_inference(failure):
    r = row(); paths = []
    def transport(request):
        path = request.url.path; paths.append(path)
        if path == '/api/model-services':
            if failure == 'pending': r['engine_idle']['phase'] = 'registering'
            return httpx.Response(200, json={'deployments': [r]})
        if path.endswith('/engine-idle'):
            if failure == 'stop': return httpx.Response(409, json={'detail': 'Stopped during wake'})
            current = deepcopy(r); current.update(config_revision='d'*64, revision=2)
            current['engine_binding']['generation'] = 3
            if failure == 'node': current['node_id'] = 'another-node'
            if failure == 'publication': current['models'] = []
            return httpx.Response(200, json={'deployment': current, 'idle': snapshot(r, 'active')})
        if path.endswith('/workload-connect'):
            return httpx.Response(200, json={'origin': 'https://mac.test', 'access_token': 'grant', 'expires': time.time()+60})
        if path == '/cancel': return httpx.Response(200, json={'cancelled': True})
        assert path == '/v1/chat/completions'
        return httpx.Response(503)
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    with pytest.raises((ValueError, RuntimeError, ControlError)):
        await client.complete('fleet-model://mac/example:8b', [])
    assert paths.count('/v1/chat/completions') == int(failure == 'inference')
    assert sum(p.endswith('/engine-idle') for p in paths) == int(failure != 'pending')


@pytest.mark.asyncio
async def test_ready_first_keeps_sleeping_candidate_dormant():
    cold, warm = row('cold'), row('warm')
    grants, actions = [], []
    def transport(request):
        path, body = request.url.path, json.loads(request.content or 'null')
        if path.endswith('/resolve'):
            return httpx.Response(200, json={'route': {'route_id': 'choice', 'revision': 1, 'transport': 'relay_allowed',
                'selection': 'ready_first', 'fallback': 'preflight'}, 'transport': 'fleet_relay',
                'candidates': [{'deployment': r, 'model': r['models'][0], 'compute': 'node', 'billing': 'local'}
                               for r in (cold, warm)]})
        if path.endswith('/engine-idle'):
            actions.append(body['action'])
            assert body['action'] == 'status'
            r = cold if '/cold/' in path else warm
            info = snapshot(r, 'sleeping' if r is cold else 'active')
            if r is warm: info.update(engine={k: warm['engine_binding'][k] for k in ('instance_id', 'revision', 'generation')}, config_revision=warm['config_revision'])
            return httpx.Response(200, json={'deployment': r, 'idle': info})
        if path.endswith('/workload-connect'):
            assert body['node_id'] == warm['node_id']
            grants.append(body)
            return httpx.Response(200, json={'origin': 'https://warm.test', 'access_token': 'grant', 'expires': time.time()+60})
        assert path == '/route-state'
        return httpx.Response(200, json={'protocol': 1, 'ready': True, 'config_revision': warm['config_revision'],
            'active_calls': 0, 'capacity': 4, 'models': [{'id': 'example:8b', 'loaded': True}]})
    client = ModelServices('https://hub.test', 'owner', httpx.MockTransport(transport))
    chosen, _, _, evidence = await select(client, 'fleet-route://choice', {'operation': 'text'})
    assert chosen['deployment_id'] == 'warm' and evidence['preflight']['model_loaded']
    assert actions == ['status', 'status'] and len(grants) == 1
