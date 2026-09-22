"""Choose once before submission; failed/uncertain generations are never replayed."""
import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx


def parse_route_ref(ref):
    parts = urlsplit(ref)
    if (parts.scheme != 'fleet-route' or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', parts.netloc)
            or parts.path or parts.query or parts.fragment):
        raise ValueError('Invalid Fleet route reference')
    return parts.netloc


def location(row, model):
    compute = ('node' if row.get('mode') == 'managed' else 'provider' if row['engine'] == 'api'
               else model.get('compute', 'unknown'))
    return compute, {'node': 'local', 'provider': 'provider'}.get(compute, 'unknown')


def summary(route, deployments):
    """Conservative catalog capabilities: every candidate must confirm support."""
    models = [next((m for m in deployments.get(c['deployment_id'], {}).get('models', [])
                    if m['id'] == c['model_id']), {}) for c in route['candidates']]
    return {'id': route['route_id'], 'name': route['name'], 'operations': [route['requires']['operation']],
            'context': min((m.get('context') or 0 for m in models), default=0) or None,
            **{k: True if models and all(m.get(k) is True for m in models) else None
               for k in ('tools', 'vision', 'structured_output', 'reasoning')}}


async def select(client, ref, requirements):
    route_id = parse_route_ref(ref)
    plan = await client.hub_request('POST', f'/api/model-services/routes/{route_id}/resolve', requirements)
    candidates = plan['candidates']
    if not candidates:
        reasons = ', '.join(sorted({r['reason'] for r in plan.get('excluded', [])}))
        raise ValueError('No allowed model satisfies this route: ' + (reasons or 'no candidates'))
    if plan['transport'] != 'fleet_relay' or plan['route']['transport'] != 'relay_allowed':
        raise ValueError('The required model transport is unavailable')
    if len(candidates) > 16:
        raise ValueError('Too many route candidates')
    if plan['route']['fallback'] == 'none':
        candidates = candidates[:1]

    async def service_state(row):
        try:
            async with asyncio.timeout(12):
                grant = await client.connect(row)
                async with httpx.AsyncClient(transport=client.transport, timeout=10, follow_redirects=False) as http:
                    response = await http.get(grant['origin'] + '/route-state', headers={
                        'Authorization': 'Bearer ' + grant['access_token'], 'X-Model-Config': row['config_revision']})
                    response.raise_for_status()
                    state = response.json()
                active, capacity = state['active_calls'], state['capacity']
                queued, queue_capacity = state.get('queued_calls', 0), state.get('queue_capacity', 0)
                if (state['protocol'] != 1 or state['config_revision'] != row['config_revision']
                        or state['ready'] is not True or type(active) is not int
                        or type(capacity) is not int or not 1 <= capacity <= 64 or not 0 <= active <= capacity
                        or type(queued) is not int or type(queue_capacity) is not int
                        or not 0 <= queued <= queue_capacity <= 256
                        or (active == capacity and queued >= queue_capacity)):
                    return None
                models = {}
                for model in state['models']:
                    models.setdefault(model['id'], model)
                return models, (active + queued) / capacity, grant
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError):
            return None

    # A route can publish several models from one connector. Read that exact
    # generation/configuration once per invocation, rather than racing multiple
    # grants and asking the engine for the same inventory for every model.
    states, tasks = {}, []

    async def probe(index, candidate, state_task):
        state = await asyncio.shield(state_task)
        if state is None:
            return None
        models, occupancy, grant = state
        model = models.get(candidate['model']['id'])
        if model is None:
            return None
        rank = (0 if model.get('loaded') is True else 1, occupancy, index)
        return rank, candidate, grant

    winner = None
    try:
        for i, candidate in enumerate(candidates):
            row = candidate['deployment']
            key = json.dumps({k: row[k] for k in
                              ('deployment_id', 'node_id', 'binding', 'config_revision')}, sort_keys=True)
            if key not in states:
                states[key] = asyncio.create_task(service_state(row))
            tasks.append(asyncio.create_task(probe(i, candidate, states[key])))
        if plan['route']['selection'] == 'ordered':
            # Probe concurrently, but don't make a healthy preferred node wait
            # for unrelated offline alternatives. Lower priority never wins
            # merely because its metadata response arrived first.
            for task in tasks:
                winner = await task
                if winner is not None:
                    break
        else:
            pending = {task: i for i, task in enumerate(tasks)}
            while pending:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    pending.pop(task)
                    result = task.result()
                    if result is not None and (winner is None or result[0] < winner[0]):
                        winner = result
                # Loaded + idle is the best possible state. Once no unresolved
                # earlier candidate can beat its tie-break position, waiting
                # for slow lower-priority nodes cannot change the selection.
                if winner is not None and (not pending or
                        winner[0] < min((0, 0, i) for i in pending.values())):
                    break
    finally:
        all_tasks = [*tasks, *states.values()]
        for task in all_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
    if winner is None:
        raise RuntimeError('No authorized candidate is reachable with free capacity. No inference was submitted.')
    _, candidate, grant = winner
    return candidate['deployment'], candidate['model'], grant, {
        'alias': ref, 'alias_revision': plan['route']['revision'],
        'selection': plan['route']['selection'], 'transport': plan['transport'],
        'compute_location': candidate['compute'], 'billing_account': candidate['billing']}
