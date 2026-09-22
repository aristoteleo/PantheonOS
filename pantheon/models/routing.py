"""Choose once before submission; failed/uncertain generations are never replayed."""
import asyncio
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

    async def probe(index, candidate):
        row, spec = candidate['deployment'], candidate['model']
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
                models = state['models']
                model = next((m for m in models if m['id'] == spec['id']), None)
                if (state['protocol'] != 1 or state['config_revision'] != row['config_revision']
                        or state['ready'] is not True or not model or type(active) is not int
                        or type(capacity) is not int or not 0 <= active <= capacity <= 64
                        or type(queued) is not int or type(queue_capacity) is not int
                        or not 0 <= queued <= queue_capacity <= 256
                        or (active == capacity and queued >= queue_capacity)):
                    return None
                rank = ((0 if model.get('loaded') is True else 1), (active + queued) / capacity, index)
                return rank, candidate, grant
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError):
            return None

    tasks = [asyncio.create_task(probe(i, c)) for i, c in enumerate(candidates)]
    winner = None
    try:
        if plan['route']['selection'] == 'ordered':
            # Probe concurrently, but don't make a healthy preferred node wait
            # for unrelated offline alternatives. Lower priority never wins
            # merely because its metadata response arrived first.
            for task in tasks:
                winner = await task
                if winner is not None:
                    break
        else:
            available = [p for p in await asyncio.gather(*tasks) if p is not None]
            winner = min(available, key=lambda p: p[0]) if available else None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if winner is None:
        raise RuntimeError('No authorized candidate is reachable with free capacity. No inference was submitted.')
    _, candidate, grant = winner
    return candidate['deployment'], candidate['model'], grant, {
        'alias': ref, 'alias_revision': plan['route']['revision'],
        'selection': plan['route']['selection'], 'transport': plan['transport'],
        'compute_location': candidate['compute'], 'billing_account': candidate['billing']}
