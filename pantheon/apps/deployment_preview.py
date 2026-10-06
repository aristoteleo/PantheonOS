"""Read-only review of exact installed App targets before owner deployment.

This inspects declarations and observed generations, not code readiness, secret
contents or future health. No reservation, configuration, grant or journal write
occurs. The normal deployment/start paths still validate again at mutation time.
"""
import asyncio

from pantheon.apps.dependency_assembly import AssemblyError, _copy, _identity, compile_contract
from pantheon.apps.deployment import AppDeployment, deployment_recipe


async def preview_deployment(lifecycle, *, owner, operation_id, apps, _planned_providers=()):
    recipe, order = deployment_recipe(owner, operation_id, apps)
    states, artifacts = {}, {}

    async def state(node):
        if node not in states:
            value = await lifecycle.status(node)
            if (not isinstance(value, dict) or value.get('owner') != owner or value.get('node_id') != node
                    or type(value.get('dependency_config_protocol')) is not int
                    or value['dependency_config_protocol'] != 1
                    or not all(isinstance(value.get(key), dict) for key in ('operations', 'instances', 'installations'))):
                raise AssemblyError('Target is unavailable, belongs to another owner or needs a Fleet update')
            states[node] = value
        return states[node]

    async def artifact(node, revision):
        if (await state(node))['installations'].get(revision, {}).get('state') != 'installed':
            raise AssemblyError('Install the exact App release on its selected node before reviewing it')
        if (node, revision) not in artifacts:
            value = await lifecycle.manifest(node, revision)
            if (not isinstance(value, dict) or value.get('protocol') != 1 or value.get('revision') != revision
                    or not isinstance(value.get('manifest'), dict) or not isinstance(value.get('definition'), dict)):
                raise AssemblyError('Selected App release declarations are unavailable')
            artifacts[node, revision] = value
        return artifacts[node, revision]

    async def provider_manifest(provider):
        if isinstance(provider, dict) and '$app' in provider:
            target = recipe['apps'][provider['$app']]
            if set(provider) != {'$app', 'component', 'port'} or provider['component'] != 'backend' or provider['port'] != 'http':
                raise AssemblyError('Use an explicit backend/http provider reference')
            return await artifact(target['node_id'], target['revision'])
        _identity(provider, provider=True)
        # Internal multi-phase review only: the preceding provider phase has
        # checked these exact installed targets. They are plans, not ready
        # instances, and are never returned as usable bindings or grants.
        if provider in _planned_providers:
            return await artifact(provider['node_id'], provider['revision'])
        observed = await state(provider['node_id'])
        instance = observed['instances'].get(provider['instance_id'], {})
        if (instance.get('digest') != provider['revision'] or instance.get('generation') != provider['generation']
                or instance.get('ready_generation') != provider['generation']
                or instance.get('state') not in {'ready', 'recovered'}):
            raise AssemblyError('An external dependency is not the selected ready generation')
        return await artifact(provider['node_id'], provider['revision'])

    results = []
    try:
        async with asyncio.timeout(30):
            for name in order:
                app = recipe['apps'][name]
                try:
                    observed = await state(app['node_id'])
                    if any(AppDeployment.operation_id(recipe, name, action) in observed['operations']
                           for action in ('install', 'prepare_start', 'start')):
                        raise AssemblyError('This operation has already been submitted; inspect its existing deployment')
                    matches = [item for item in observed['instances'].values()
                               if item.get('digest') == app['revision'] and item.get('scope') == app['scope']]
                    if (len(matches) > 1 or not matches and app['generation'] != 0
                            or matches and (matches[0].get('state') != 'stopped'
                                            or type(matches[0].get('generation')) is not int
                                            or matches[0]['generation'] != app['generation'])):
                        raise AssemblyError('The target scope is in use or its generation changed; prepare an explicit cutover')
                    installed = await artifact(app['node_id'], app['revision'])
                    await compile_contract(installed, app['bindings'], app['components'], provider_manifest)
                    manifest = installed['manifest']
                    results.append({'name': name, 'node_id': app['node_id'], 'revision': app['revision'],
                        'app_id': manifest['id'], 'version': manifest['version'], 'scope': app['scope'],
                        'generation': app['generation'], 'action': 'prepare_start',
                        'dependencies': sorted(app['bindings']), 'components': sorted(app['components'])})
                except AssemblyError as exc:
                    raise AssemblyError(f'App {name}: {exc}') from None
    except AssemblyError:
        raise
    except Exception:
        raise AssemblyError('Deployment review is unavailable; no App was installed, reserved or started') from None
    return _copy({'protocol': 1, 'operation_id': operation_id, 'state': 'reviewed',
                  'observation': 'read-only-snapshot', 'order': order, 'apps': results})
