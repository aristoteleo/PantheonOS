"""Validation of a composed App recipe: bounded, named Apps on exact targets
whose provider bindings form an order (no startup cycles). Composition only;
the Fleet controller deploys Apps (docs/fleet-orchestration.md).
"""
from pantheonos.apps.dependency_assembly import (
    AssemblyError, DIGEST, IDENT, NAME, DEPLOYMENT_BYTES, _copy, _matches,
)


def _references(value):
    if isinstance(value, dict):
        if '$app' in value:
            if (not _matches(NAME, value['$app'])
                    or set(value) not in ({'$app'}, {'$app', 'component', 'port'})
                    or len(value) > 1 and (value['component'] != 'backend' or value['port'] != 'http')):
                raise AssemblyError('Invalid prepared App reference')
            return {value['$app']}
        return set().union(*(_references(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_references(item) for item in value)) if value else set()
    return set()


def deployment_recipe(owner, operation_id, apps):
    recipe = _copy(dict(owner=owner, operation_id=operation_id, apps=apps), DEPLOYMENT_BYTES)
    if (not _matches(IDENT, owner) or not _matches(NAME, operation_id)
            or not isinstance(recipe['apps'], dict) or not 1 <= len(apps) <= 16):
        raise AssemblyError('Supply a bounded deployment with a stable owner and operation ID')
    apps = recipe['apps']
    edges, instances = {}, set()
    for name, app in apps.items():
        if (not _matches(NAME, name) or not isinstance(app, dict) or set(app) != {
                'node_id', 'revision', 'scope', 'generation', 'components', 'bindings'}
                or not _matches(IDENT, app['node_id']) or not _matches(DIGEST, app['revision'])
                or not _matches(IDENT, app['scope']) or type(app['generation']) is not int
                or not 0 <= app['generation'] < 2**63-3
                or not isinstance(app['components'], dict)
                or not isinstance(app['bindings'], dict) or len(app['bindings']) > 16):
            raise AssemblyError('Invalid exact App deployment target')
        identity = (app['node_id'], app['revision'], app['scope'])
        if identity in instances:
            raise AssemblyError('Deployment names must refer to distinct App instances')
        instances.add(identity)
        # Only provider bindings introduce startup ordering. Configuration may
        # refer to the future consumer (e.g. a broker policy) without a cycle.
        edges[name] = _references(app['bindings'])
        if (_references(app['components']) | edges[name]) - apps.keys():
            raise AssemblyError('Prepared App reference is absent from the deployment')
    order = []
    while len(order) < len(apps):
        available = sorted(name for name, dependencies in edges.items()
                           if name not in order and dependencies <= set(order))
        if not available:
            raise AssemblyError('Startup dependency cycle; move logical resource allocation to runtime')
        order.extend(available)
    return recipe, order
