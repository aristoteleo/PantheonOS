"""Agent installation preset expressed as a generic App deployment recipe.

This is owner-side composition, not part of the Agent runtime. It emits the
recipe the General Team setup composes; it does not start processes, discover nodes,
read login credentials or introduce another lifecycle coordinator.
"""
import re
import ssl
from pathlib import Path

from pantheonos.appkit.dependency_assembly import AssemblyError, _copy, DEPLOYMENT_BYTES
from pantheonos.appkit.recipe import deployment_recipe
from pantheonos.appkit.agent_defaults import dependency_defaults


def compose_deployment(*, owner, operation_id, targets, agent, tools, models,
                       credentials, extra_bindings=None, provider_apps=None, local_transport=None):
    """Compose exact candidate targets, explicit policies and node-vault refs.

    targets names: agent, allocator, model-access; each supplies node_id,
    revision, scope, generation. Existing tool/model providers are pinned in
    their policies; they are not restarted or substituted by this preset.
    Additional Agent GUI/plugin startup grants can be supplied in extra_bindings.
    provider_apps can install ordinary dependencies in the same prepared recipe;
    policies reference their exact upcoming generations using $app references.
    local_transport explicitly pins the same-host profile's issuer, public CA
    and model directory. It carries no keys and never changes cloud defaults.
    """
    targets, agent, tools, models, credentials, extra_bindings = _copy([
        targets, agent, tools, models, credentials, {} if extra_bindings is None else extra_bindings], DEPLOYMENT_BYTES)
    names = {'agent', 'allocator', 'model-access'}
    provider_apps = _copy({} if provider_apps is None else provider_apps, DEPLOYMENT_BYTES)
    if not isinstance(provider_apps, dict) or provider_apps.keys() & names:
        raise AssemblyError('Additional providers cannot replace the Agent composition')
    if (not isinstance(targets, dict) or set(targets) != names
            or any(not isinstance(t, dict) or set(t) != {'node_id', 'revision', 'scope', 'generation'}
                   for t in targets.values())
            or not isinstance(agent, dict) or type(agent.get('protocol')) is not int or agent['protocol'] != 1
            or not isinstance(tools, dict) or not isinstance(models, dict)
            or set(models) != {'deployments', 'routes', 'allow_wake'}
            or not isinstance(credentials, dict) or set(credentials) != names
            or any(not isinstance(group, dict) for group in credentials.values())
            or not isinstance(extra_bindings, dict)
            or set(extra_bindings) & {'allocator', 'model_services'}):
        raise AssemblyError('Supply exact Agent targets, configuration and owner-approved policies')
    if (set(credentials['allocator']) != {'hub', 'controller'}
            or set(credentials['model-access']) != {'hub'}
            or not isinstance(credentials['agent'], dict)
            or set(credentials['agent']) & {'allocator', 'model_services'}):
        raise AssemblyError('Keep owner credentials on the allocation and model access Apps')
    for group in credentials.values():
        if not isinstance(group, dict):
            raise AssemblyError('Supply endpoint-bound node vault references')
        for value in group.values():
            if (not isinstance(value, dict) or set(value) != {'ref', 'endpoint'}
                    or not isinstance(value['ref'], str) or not value['ref'].startswith('node-secret://')
                    or not isinstance(value['endpoint'], str) or not value['endpoint'].startswith(('https://', 'http://'))):
                raise AssemblyError('Deployment inputs must use node vault references, not inline keys')
    dependencies = agent.get('dependencies')
    if (not isinstance(dependencies, dict) or not {'allocator', 'profiles'} <= dependencies.keys()
            or dependencies.keys() - {'allocator', 'profiles', 'defaults'}
            or dependencies['allocator'] != 'allocator'
            or not isinstance(dependencies['profiles'], dict)
            or set(dependencies['profiles']) != {'toolsets', 'mcp_servers'}):
        raise AssemblyError('Supply Agent profiles bound to its allocator dependency')
    try:
        dependency_defaults(dependencies.get('defaults', {'toolsets': [], 'mcp_servers': []}),
                            profiles=dependencies['profiles'])
    except ValueError as exc:
        raise AssemblyError(str(exc)) from None
    aliases = set()
    for profiles in dependencies['profiles'].values():
        if not isinstance(profiles, dict):
            raise AssemblyError('Invalid Agent dependency profiles')
        for profile in profiles.values():
            if not isinstance(profile, dict) or not isinstance(profile.get('alias'), str):
                raise AssemblyError('Invalid Agent dependency profile alias')
            aliases.add(profile['alias'])
    if aliases != set(tools):
        raise AssemblyError('Agent tool profiles and approved allocation aliases must match')
    model_config = agent.get('models')
    if (not isinstance(model_config, dict)
            or model_config.get('model_services', 'model_services') != 'model_services'):
        raise AssemblyError('Bind the Agent model catalog to its model_services dependency')
    model_config['model_services'] = 'model_services'
    local = _copy(local_transport)
    if local is not None:
        try:
            if not isinstance(local, dict) or set(local) != {'origin', 'trust_roots_pem', 'directory_root'}:
                raise ValueError
            origin, pem, directory = (local[key] for key in ('origin', 'trust_roots_pem', 'directory_root'))
            match = re.fullmatch(r'https://127\.0\.0\.1:([1-9][0-9]{0,4})', origin)
            if (not match or int(match[1]) > 65535
                    or not isinstance(pem, str) or not pem or len(pem) > 16384
                    or not isinstance(directory, str) or not Path(directory).is_absolute()
                    or len({target['node_id'] for target in targets.values()}) != 1
                    or any(value['endpoint'] != origin for name in ('allocator', 'model-access')
                           for value in credentials[name].values())
                    or 'trust_roots_pem' in agent and agent['trust_roots_pem'] != pem
                    or 'rpc_origin' in agent and agent['rpc_origin'] != origin):
                raise ValueError
            ssl.create_default_context(cadata=pem)
        except (ValueError, TypeError, ssl.SSLError):
            raise AssemblyError('Local Agent composition requires same-node targets, exact loopback owner endpoints, public TLS trust and an absolute model directory') from None
        agent['trust_roots_pem'] = pem
        agent['rpc_origin'] = origin

    def binding(app_id, provider, method, arguments):
        return {'app_id': app_id, 'component': 'backend',
                'provider': {'$app': provider, 'component': 'backend', 'port': 'http'},
                'methods': {method: {'arguments': arguments, 'bound': {'policy_id': 'agent'}}}}

    apps = {
        'agent': {**targets['agent'], 'components': {'backend': {
            'values': {'agent': agent}, 'credentials': credentials['agent']}},
            'bindings': {**extra_bindings,
                'allocator': binding('dependency-binding', 'allocator', 'bind_dependencies',
                                     ['owner_ref', 'operation_id', 'aliases']),
                'model_services': binding('model-services-control', 'model-access', 'model_services_control',
                                          ['operation', 'arguments'])}},
        'allocator': {**targets['allocator'], 'bindings': {}, 'components': {'backend': {
            'values': {'dependency_binding': {'protocol': 1, 'policies': {'agent': {
                'consumer': {'$app': 'agent'}, 'bindings': tools}}}},
            'credentials': credentials['allocator']}}},
        'model-access': {**targets['model-access'], 'bindings': {}, 'components': {'backend': {
            'values': {'model_services': {'protocol': 1, 'policies': {'agent': {
                'consumer': {'$app': 'agent'}, **models}}}},
            'credentials': credentials['model-access']}}},
    }
    apps['agent']['bindings']['allocator']['methods']['retire_dependencies'] = {
        'arguments': ['owner_ref'], 'bound': {'policy_id': 'agent'}}
    if local is not None:
        apps['allocator']['components']['backend']['values']['dependency_binding'].update(
            rpc_origin=local['origin'], trust_roots_pem=local['trust_roots_pem'])
        apps['model-access']['components']['backend']['values']['model_services'].update(
            http_origin=local['origin'], trust_roots_pem=local['trust_roots_pem'],
            directory_root=local['directory_root'])
    apps.update(provider_apps)
    recipe, _ = deployment_recipe(owner, operation_id, apps)
    return recipe
