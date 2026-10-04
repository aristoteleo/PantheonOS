"""Read-only selection of existing publications for an ordinary App dependency.

This builds the policy accepted by ModelServiceControl, not another model
directory or router. Authorization is connector-wide; the returned review
explicitly lists that scope. No grants, engine wakes or inference are issued.
"""
import asyncio

from pantheon.apps.dependency_assembly import AssemblyError, _copy, _identity
from .client import model_ref, parse_ref
from .routing import parse_route_ref, summary


async def plan_dependency(client, *, references, allow_wake=False):
    if (not isinstance(references, list) or not 1 <= len(references) <= 128
            or any(not isinstance(ref, str) or len(ref) > 1024 for ref in references)
            or len(set(references)) != len(references) or type(allow_wake) is not bool):
        raise AssemblyError('Choose distinct model references and an explicit wake policy')
    parsed = []
    try:
        for ref in references:
            if ref.startswith('fleet-route://'):
                parsed.append(('route', parse_route_ref(ref), None))
            else:
                deployment, model = parse_ref(ref)
                if ref != model_ref(deployment, model):
                    raise ValueError
                parsed.append(('model', deployment, model))
    except ValueError:
        raise AssemblyError('Choose canonical Fleet model or route references') from None

    try:
        async with asyncio.timeout(30):
            rows = _copy(await client.deployments(), limit=4 * 1024 * 1024)
            routes = (_copy(await client.routes(), limit=1024 * 1024)
                      if any(kind == 'route' for kind, _, _ in parsed) else [])
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AssemblyError('Model directory unavailable; reconnect and refresh before composing a preset') from None

    policy = {'deployments': {}, 'routes': {}, 'allow_wake': allow_wake}
    selected, grants = [], {}
    try:
        by_id = {row['deployment_id']: row for row in rows}
        by_route = {route['route_id']: route for route in routes}
        if len(by_id) != len(rows) or len(by_route) != len(routes):
            raise ValueError

        def include(deployment, model):
            if not isinstance(model, str) or not 1 <= len(model) <= 200:
                raise ValueError
            parse_ref(model_ref(deployment, model))
            row = by_id[deployment]
            binding = row['binding']
            _identity(binding, provider=True)
            if binding['node_id'] != row['node_id']:
                raise ValueError
            models = {item['id']: item for item in row['models']}
            if len(models) != len(row['models']):
                raise ValueError
            spec = models[model]
            policy['deployments'][deployment] = binding
            grants[deployment] = {'deployment_id': deployment, 'binding': binding,
                'scope': 'connector',
                'published_models': [model_ref(deployment, key) for key in sorted(models)]}
            return row, spec

        for ref, (kind, identity, model) in zip(references, parsed):
            if kind == 'model':
                row, spec = include(identity, model)
                if row['state'] != 'ready':
                    raise ValueError
                name = spec.get('name') or spec['id']
            else:
                route = by_route[identity]
                if (type(route['revision']) is not int or route['revision'] < 1
                        or not 1 <= len(route['candidates']) <= 16):
                    raise ValueError
                # Include the whole declared route, never silently trim a
                # fallback or substitute a different publication/generation.
                candidates = [include(c['deployment_id'], c['model_id'])[0]
                              for c in route['candidates']]
                if not any(row['state'] == 'ready' for row in candidates):
                    raise ValueError
                policy['routes'][identity] = route['revision']
                spec, name = summary(route, by_id), route['name']
            if (not isinstance(name, str) or not isinstance(spec['operations'], list)
                    or any(not isinstance(op, str) for op in spec['operations'])):
                raise ValueError
            selected.append({'reference': ref, 'name': name,
                'operations': spec['operations'], 'context': spec.get('context'),
                'capabilities': {key: spec.get(key) for key in
                                 ('tools', 'vision', 'reasoning', 'structured_output')}})
        if len(policy['deployments']) > 64:
            raise ValueError
    except (KeyError, TypeError, ValueError, AssemblyError):
        raise AssemblyError('Selected model or route is unavailable or has an invalid binding; refresh Model Services') from None
    return _copy({'protocol': 1, 'policy': policy, 'selected': selected,
                  'authorization': list(grants.values())}, limit=4 * 1024 * 1024)
