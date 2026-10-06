"""Review a complete provider/consumer startup without provisioning anything."""
from pantheon.apps.dependency_assembly import _copy
from pantheon.apps.deployment_preview import preview_deployment
from .bootstrap import ModelServiceBootstrap, recipe, resolve_models


async def preview_bootstrap(lifecycle, **spec):
    spec = recipe(**spec)
    providers = {name: item['app'] for name, item in spec['model_apps'].items()}
    first = await preview_deployment(lifecycle, owner=spec['owner'],
        operation_id=ModelServiceBootstrap.child_id(spec, 'providers'), apps=providers)
    # Keep provider and consumer deployment limits and operation identities
    # exactly as startup uses them. No combined graph with a new operation ID.
    planned = {name: dict(node_id=app['node_id'], revision=app['revision'],
        instance_id='review-' + name, generation=app['generation'] + 2,
        component='backend', port='http') for name, app in providers.items()}
    second = await preview_deployment(lifecycle, owner=spec['owner'],
        operation_id=ModelServiceBootstrap.child_id(spec, 'consumers'),
        apps=resolve_models(spec['apps'], planned), _planned_providers=tuple(planned.values()))
    return _copy(dict(protocol=1, operation_id=spec['operation_id'], state='reviewed',
        observation='read-only-snapshot', order=first['order'] + second['order'],
        apps=first['apps'] + second['apps']))
