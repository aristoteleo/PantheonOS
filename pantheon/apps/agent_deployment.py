"""Agent installation preset expressed as a generic App deployment recipe.

This is owner-side composition, not part of the Agent runtime. It emits the
existing fleet_app_deploy input; it does not start processes, discover nodes,
read login credentials or introduce another lifecycle coordinator.
"""
import argparse
import json
import os
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError, _copy
from pantheon.apps.deployment import deployment_recipe
from pantheon.apps.agent_defaults import dependency_defaults


def compose_deployment(*, owner, operation_id, targets, agent, tools, models,
                       credentials, extra_bindings=None, provider_apps=None):
    """Compose exact candidate targets, explicit policies and node-vault refs.

    targets names: agent, allocator, model-access; each supplies node_id,
    revision, scope, generation. Existing tool/model providers are pinned in
    their policies; they are not restarted or substituted by this preset.
    Additional Agent GUI/plugin startup grants can be supplied in extra_bindings.
    provider_apps can install ordinary dependencies in the same prepared recipe;
    policies reference their exact upcoming generations using $app references.
    """
    targets, agent, tools, models, credentials, extra_bindings = _copy([
        targets, agent, tools, models, credentials, {} if extra_bindings is None else extra_bindings])
    names = {'agent', 'allocator', 'model-access'}
    provider_apps = _copy({} if provider_apps is None else provider_apps)
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
    apps.update(provider_apps)
    recipe, _ = deployment_recipe(owner, operation_id, apps)
    return recipe


async def compose_selected_deployment(client, *, spec, fleet_tiers, allow_wake=False):
    """Build a reviewable ordinary preset from owner-selected directory refs.

    The authenticated owner client supplies the existing Model Service directory.
    Nothing is installed, woken or saved. The resulting recipe goes through the
    same owner approval, persistence and generic deployment path as other Apps.
    """
    from pantheon.models.dependency_plan import plan_dependency
    from pantheon.utils.model_selector import QUALITY_TAGS

    spec, fleet_tiers = _copy([spec, fleet_tiers])
    required = {'owner', 'operation_id', 'targets', 'agent', 'tools', 'credentials'}
    if (not isinstance(spec, dict) or not required <= spec.keys()
            or spec.keys() - required - {'extra_bindings', 'provider_apps'}
            or not isinstance(fleet_tiers, dict) or 'normal' not in fleet_tiers
            or fleet_tiers.keys() - QUALITY_TAGS
            or any(not isinstance(ref, str) for ref in fleet_tiers.values())
            or type(allow_wake) is not bool):
        raise AssemblyError('Supply Agent targets and selected Fleet quality tiers, without a hand-written model policy')
    # Validate the complete composition before reading the directory. Preserve
    # explicitly configured compatibility providers; do not infer budget state.
    compose_deployment(**spec, models={'deployments': {}, 'routes': {}, 'allow_wake': allow_wake})
    config = spec['agent']['models']
    if 'fleet_tiers' in config and config['fleet_tiers'] != fleet_tiers:
        raise AssemblyError('Selected models conflict with the existing Agent quality tiers')
    plan = await plan_dependency(client, references=list(dict.fromkeys(fleet_tiers.values())),
                                 allow_wake=allow_wake)
    for model in plan['selected']:
        if ('text' not in model['operations'] or model['capabilities']['tools'] is not True
                or type(model['context']) is not int or model['context'] <= 0):
            raise AssemblyError('Agent models require published text, tool support and a positive context length')
    config['fleet_tiers'] = fleet_tiers
    recipe = compose_deployment(**spec, models=plan['policy'])
    return {'recipe': recipe, 'model_selection': plan}


async def update_selected_deployment(client, *, recipe, operation_id, fleet_tiers, allow_wake=False):
    """Edit model selection without dropping an existing preset's other grants.

    Only canonical ordinary Agent compositions can round-trip here. Prepared
    model-provider startup and custom deployment graphs keep their original
    editor; they must not be silently reduced to a three-App preset.
    """
    recipe = _copy(recipe)
    try:
        if set(recipe) != {'owner', 'operation_id', 'apps'} or operation_id == recipe['operation_id']:
            raise ValueError
        apps = recipe['apps']
        core = {'agent', 'allocator', 'model-access'}
        target_keys = {'node_id', 'revision', 'scope', 'generation'}
        backend = apps['agent']['components']['backend']
        tools = apps['allocator']['components']['backend']['values']['dependency_binding']['policies']['agent']['bindings']
        models = apps['model-access']['components']['backend']['values']['model_services']['policies']['agent']
        if set(models) != {'consumer', 'deployments', 'routes', 'allow_wake'}:
            raise ValueError
        spec = dict(owner=recipe['owner'], operation_id=recipe['operation_id'],
            targets={name: {key: apps[name][key] for key in target_keys} for name in core},
            agent=backend['values']['agent'], tools=tools,
            credentials={name: apps[name]['components']['backend']['credentials'] for name in core},
            extra_bindings={key: value for key, value in apps['agent']['bindings'].items()
                            if key not in {'allocator', 'model_services'}},
            provider_apps={key: value for key, value in apps.items() if key not in core})
        existing = {key: models[key] for key in ('deployments', 'routes', 'allow_wake')}
        if compose_deployment(**spec, models=existing) != recipe:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise AssemblyError('Use a canonical Agent preset and a new operation ID; custom startup graphs require their original editor') from None
    spec['operation_id'] = operation_id
    # Replacement is explicit in this edit operation; the read-only composer
    # still rejects conflicting defaults when creating a new configuration.
    spec['agent']['models'].pop('fleet_tiers', None)
    return await compose_selected_deployment(client, spec=spec, fleet_tiers=fleet_tiers, allow_wake=allow_wake)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    recipe = compose_deployment(**json.loads(args.input.read_text()))
    # Prepared recipes carry private paths and credential references. Never
    # overwrite another deployment intent or emit them into terminal history.
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(recipe, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
