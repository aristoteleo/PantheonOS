"""Owner registration of an already started, ordinary Model Service App.

No lifecycle/configuration mutation or implicit model selection. Directory CAS
makes a lost acknowledgement retryable without overwriting owner changes. Live
bindings and configuration revisions remain fenced by the existing data plane.
"""
import re

from pantheon.apps.lifecycle import FleetLifecycle
from .managed import module


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
            or type(binding['generation']) is not int or binding['generation'] < 1
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
        if (not isinstance(model, dict) or set(model) - {'id', 'context_limit'}
                or not isinstance(model.get('id'), str) or not 0 < len(model['id']) <= 200
                or model['id'] in selected):
            raise ValueError('Select unique model ids and optional context limits')
        limit = model.get('context_limit')
        if limit is not None and (type(limit) is not int or limit < 512):
            raise ValueError('A context limit is at least 512 tokens')
        selected[model['id']] = limit
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
        published = []
        for model_id, limit in selected.items():
            reported = catalog[model_id].get('reported') or {}
            # This helper publishes Agent/chat models. Other modalities retain
            # their original explicit Model Services publication workflow.
            if reported.get('operations') not in (None, ['text'], ['embedding']):
                raise ValueError('Use Model Services publication for non-chat models')
            published.append(manager.chat_entry(model_id, reported,
                compute='provider' if config['engine'] == 'api' else 'node', context_limit=limit))
        candidate = dict(deployment_id=deployment_id, name=name, node_id=binding['node_id'],
                         node_name=node.get('name', binding['node_id']), engine=config['engine'], mode='attached',
                         state='ready', binding=dict(binding), config_revision=expected, models=published, revision=0)
        existing = next((row for row in await manager.client.deployments() if row['deployment_id'] == deployment_id), None)
        await verify_configuration()
        await verify_instance()
        if existing is not None:
            if not same_registration(existing, candidate):
                raise ValueError('This registration differs from the directory; use explicit Model Services management')
            return existing
        # Never retry a write blindly: a timeout is uncertain. A caller retry
        # reads the current directory and must match it exactly before succeeding.
        return await manager.client.save(candidate)
