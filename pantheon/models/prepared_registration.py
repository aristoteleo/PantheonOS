"""Owner registration of an already started, ordinary Model Service App.

No lifecycle/configuration mutation or implicit model selection. Directory CAS
makes a lost acknowledgement retryable without overwriting owner changes. Live
bindings and configuration revisions remain fenced by the existing data plane.
"""
import re
from copy import deepcopy

from pantheon.apps.lifecycle import FleetLifecycle
from .managed import module
from .local_directory import OPERATIONS, _deployment
from .errors import ControlError


def inputs(name, binding, configuration, models):
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError('Supply a model service name of at most 120 characters')
    if not isinstance(binding, dict) or set(binding) != {
            'node_id', 'instance_id', 'revision', 'generation', 'component', 'port'}:
        raise ValueError('Supply the exact prepared Model Service binding')
    for field in ('node_id', 'instance_id'):
        if not isinstance(binding[field], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', binding[field]):
            raise ValueError('Invalid prepared Model Service identity')
    if (not isinstance(binding['revision'], str) or not re.fullmatch(r'[a-f0-9]{64}', binding['revision'])
            or type(binding['generation']) is not int or not 1 <= binding['generation'] < 2**63-1
            or binding['component'] != 'backend' or binding['port'] != 'http'):
        raise ValueError('Supply the exact prepared Model Service binding')
    if (not isinstance(configuration, dict) or set(configuration) - {'engine', 'endpoint', 'secret_ref'}
            or not all(isinstance(v, str) for v in configuration.values())):
        raise ValueError('Prepared registration accepts only engine, endpoint and a node credential reference')
    config = module('server').validate_config(configuration)
    if not isinstance(models, list) or len(models) > 1000:
        raise ValueError('Select at most 1000 models')
    selected = {}
    for model in models:
        if (not isinstance(model, dict) or set(model) - {'id', 'context_limit', 'operations'}
                or not isinstance(model.get('id'), str) or not 0 < len(model['id']) <= 200
                or model['id'] in selected):
            raise ValueError('Select unique model ids and optional context limits or operations')
        limit = model.get('context_limit')
        if limit is not None and (type(limit) is not int or limit < 512):
            raise ValueError('A context limit is at least 512 tokens')
        operations = model.get('operations')
        if 'operations' in model and (not isinstance(operations, list) or not operations
                or len(operations) > len(OPERATIONS)
                or any(not isinstance(op, str) or op not in OPERATIONS for op in operations)
                or len(set(operations)) != len(operations)):
            raise ValueError('Select explicit supported model operations')
        selected[model['id']] = {'context_limit': limit, 'operations': deepcopy(operations)}
    return config, selected


def same_registration(existing, candidate):
    if any(existing.get(k) for k in ('managed', 'engine_binding', 'engine_idle',
                                    'recovery', 'connector_update', 'engine_update', 'operation_stop')):
        return False
    if any(existing.get(k, 'attached' if k == 'mode' else None) != candidate[k]
           for k in ('deployment_id', 'name', 'node_id', 'engine', 'mode', 'state', 'binding', 'config_revision')):
        return False
    # Hub fills optional capability fields with null. Order is not an identity.
    def entries(rows):
        return sorted(({k: v for k, v in row.items() if v is not None} for row in rows), key=lambda m: m['id'])
    return entries(existing.get('models', [])) == entries(candidate['models'])


async def register(manager, deployment_id, name, binding, configuration, models):
    config, selected = inputs(name, binding, configuration, models)
    async with manager.lock(deployment_id):
        candidate, verify = await inspected_registration(manager, deployment_id, name, binding, config, selected)
        existing = next((row for row in await manager.client.deployments() if row['deployment_id'] == deployment_id), None)
        await verify()
        if existing is not None:
            if not same_registration(existing, candidate):
                raise ValueError('This registration differs from the directory; use explicit Model Services management')
            return existing
        # Never retry a write blindly: a timeout is uncertain. A caller retry
        # reads the current directory and must match it exactly before succeeding.
        return await manager.client.save(candidate)


async def inspected_registration(manager, deployment_id, name, binding, config, selected):
    """Read a live Connector; callers hold its management lock and own directory CAS."""
    node = await manager.node(binding['node_id'])
    if config.get('secret_ref') and (node.get('capability') or {}).get('runtimes', {}).get('model-credentials') != '1':
        raise ValueError('Update Fleet on this node to use named model credentials')
    lifecycle = FleetLifecycle(manager.resolver)

    async def verify_instance():
        instance, stopped = manager.bound_instance(await lifecycle.status(binding['node_id']), binding,
                                                   'model-' + deployment_id)
        if stopped or instance['state'] != 'ready':
            raise ValueError('The prepared connector is not ready; inspect it in Fleet')

    await verify_instance()
    expected = (await manager.rpc(binding, 'preview_configuration', {'config': config}))['config_revision']
    if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('The connector returned an invalid configuration revision')

    async def verify_configuration():
        status = await manager.rpc(binding, 'status')
        if (status.get('config_revision') != expected or status.get('accepting') is not True
                or status.get('active_model_operations') != 0):
            raise ValueError('The connector configuration or admission changed; inspect Model Services')
        activity = await manager.rpc(binding, 'activity')
        if activity.get('accepting') is not True:
            raise ValueError('The connector is draining or requires recovery')

    await verify_configuration()
    discovered = await manager.rpc(binding, 'discover')
    if discovered.get('config_revision') != expected:
        raise ValueError('The connector configuration changed during discovery')
    catalog = {m['id']: m for m in discovered['models']}
    if any(model_id not in catalog for model_id in selected):
        raise ValueError('Select only models returned by this connector')
    suggestions = {}
    if config['engine'] == 'api':
        # As when publishing from Model Services: an API that states nothing gets
        # the labelled OpenRouter catalog entry; the service's own report wins.
        from .model_metadata import suggest
        unknown = [m for m in selected if 'context' not in (catalog[m].get('reported') or {})]
        suggestions = await suggest(unknown) if unknown else {}
    published = []
    for model_id, selection in selected.items():
        reported = catalog[model_id].get('reported') or {}
        operations = selection['operations']
        reported_ops = reported.get('operations')
        if operations is not None and reported_ops is not None and not set(operations) <= set(reported_ops):
            raise ValueError('Selected operations contradict the connector discovery')
        operations = operations if operations is not None else reported_ops or ['text']
        compute = 'provider' if config['engine'] == 'api' else 'node'
        if 'text' in operations or operations == ['embedding']:
            entry = manager.chat_entry(model_id, reported, suggestions.get(model_id), compute=compute,
                                       context_limit=selection['context_limit'])
            entry['operations'] = operations
        else:
            if selection['context_limit'] is not None:
                raise ValueError('Context limits apply to text or embedding models')
            entry = dict(id=model_id, name=model_id, operations=operations, compute=compute)
        published.append(entry)
    candidate = dict(deployment_id=deployment_id, name=name, node_id=binding['node_id'],
                     node_name=node.get('name', binding['node_id']), engine=config['engine'], mode='attached',
                     state='ready', binding=dict(binding), config_revision=expected, models=published, revision=0)
    # Reuse the attached-directory contract, including engine/modality support.
    # Publication is explicit selection, never inference or a capability probe.
    try:
        _deployment(candidate)
    except ControlError:
        raise ValueError('Model publication is incompatible with the selected engine') from None
    async def verify():
        await verify_configuration()
        await verify_instance()
    return candidate, verify


def rebind_inputs(previous, binding, configuration):
    """Validate a clean restart intent before lifecycle or directory writes."""
    if (not isinstance(previous, dict)
            or not {'deployment_id', 'name', 'node_id', 'binding', 'models', 'config_revision'} <= previous.keys()
            or not isinstance(previous.get('deployment_id'), str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', previous['deployment_id'])
            or not isinstance(previous.get('config_revision'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', previous['config_revision'])
            or previous.get('state') != 'stopped'
            or type(previous.get('revision')) is not int or previous['revision'] < 1
            or previous.get('mode', 'attached') != 'attached'
            or any(previous.get(k) is not None for k in ('managed', 'engine_binding', 'engine_idle',
                'recovery', 'connector_update', 'engine_update', 'operation_stop', 'last_operation_stop'))
            or not isinstance(previous.get('binding'), dict)
            or not isinstance(previous.get('models'), list)
            or any(not isinstance(m, dict) or 'id' not in m for m in previous['models'])):
        raise ValueError('Rebinding requires the exact stopped attached-model publication')
    old = previous['binding']
    selected = [{'id': m['id'], 'context_limit': m.get('context_limit'),
                 'operations': m.get('operations', ['text'])} for m in previous['models']]
    inputs(previous['name'], old, configuration, selected)
    config, selected = inputs(previous['name'], binding, configuration, selected)
    if (type(old.get('generation')) is not int
            or binding != {**old, 'generation': old['generation'] + 2}
            or previous['node_id'] != binding['node_id']):
        raise ValueError('Rebind only the same stopped Connector after one preparation and start')
    if (previous.get('engine') != config['engine']
            or previous['config_revision'] != module('server').configuration_revision(config)):
        raise ValueError('Restart changed model configuration; review it in Model Services')
    return config, selected


async def rebind(manager, previous, binding, configuration):
    """Publish a clean prepared restart, retaining the owner's exact model choices.

    The caller retains the stopped publication as its immutable restart intent.
    No process is started, no configuration is changed, and no new models or
    capabilities are silently accepted. Crash recovery and upgrades use their
    own management flows, not this clean-stop transition.
    """
    previous, binding, configuration = deepcopy((previous, binding, configuration))
    config, selected = rebind_inputs(previous, binding, configuration)
    deployment_id = previous['deployment_id']
    async with manager.lock(deployment_id):
        candidate, verify = await inspected_registration(
            manager, deployment_id, previous['name'], binding, config, selected)
        desired = {**previous, 'state': 'ready', 'binding': binding}
        if not same_registration(desired, candidate):
            raise ValueError('Restart changed model configuration or publication; review it in Model Services')
        current = await manager.client.deployment(deployment_id)
        await verify()
        if current == {**desired, 'revision': previous['revision'] + 1}:
            return current  # The exact directory write committed but its reply was lost.
        if current != previous:
            raise ValueError('The stopped publication changed; inspect the original restart')
        return await manager.client.save(desired)
