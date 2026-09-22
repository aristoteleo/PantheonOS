"""Drain and replace an owned engine, resuming only a durably pinned target.

Engine binaries are immutable; model files live in the stable engine scope's
cache. The connector stays alive, retaining credentials, models and activity.
No request, download or start is replayed after an uncertain acknowledgement.
"""
import time
import uuid
import hashlib

from pantheon.apps.lifecycle import FleetLifecycle
from . import managed
from .recovery import exact_instance, stopped


async def upgrade(manager, deployment_id, recipe_id):
    async with manager.lock(deployment_id):
        row = await manager.client.deployment(deployment_id)
        if (row.get('mode') != 'managed' or not row.get('engine_binding')
                or row.get('recovery') or row.get('connector_update')):
            raise ValueError('An owned engine with no pending recovery or connector update is required')
        pending = row.get('engine_update')
        if not pending and row['state'] != 'ready':
            raise ValueError('Start and verify this service before updating its engine')
        if pending and recipe_id != pending['target_config']['recipe_id']:
            raise ValueError('Resume the pinned engine update before selecting another version')
        node = await manager.node(row['node_id'], managed=True)
        cap = node['capability']
        lifecycle = FleetLifecycle(manager.resolver)
        scope = 'engine-' + deployment_id
        state = await lifecycle.status(row['node_id'])
        connector, dead = manager.bound_instance(state, row['binding'], 'model-' + deployment_id)
        if dead or connector['state'] != 'ready':
            raise ValueError('The exact connector must be ready before resuming an engine update')
        status = await manager.rpc(row['binding'], 'status')
        if status.get('recovery_protocol') != 1:
            raise ValueError('Update the connector before updating its engine')
        if not pending:
            original, dead = manager.bound_instance(state, row['engine_binding'], scope)
            if dead or original['state'] != 'ready' or status['config_revision'] != row['config_revision']:
                raise ValueError('Verify service recovery before updating the engine')
            target = cap['os'] + '-' + cap['arch']
            config = managed.validate({**row['managed'], 'recipe_id': recipe_id}, target)
            recipe = managed.engines().recipe(recipe_id, target=target)
            if recipe['engine'] != row['engine']:
                raise ValueError('An engine update cannot change the model engine family')
            catalog = await manager.rpc(row['binding'], 'engines_catalog')
            selected = next((r for r in catalog['recipes'] if r['id'] == recipe_id), None)
            if not selected:
                raise ValueError('Update the connector to include the selected engine recipe')
            if selected.get('unavailable_reason') or (recipe.get('runtime') != 'container' and not selected.get('prepared')):
                raise ValueError('Prepare the selected engine version before updating; the current engine is unchanged')
            with managed.package(config, target) as directory:
                digest = await lifecycle.stage(row['node_id'], directory)
            if digest == row['engine_binding']['revision']:
                return row
            # Install records the immutable package, without creating a running
            # instance or reserving memory. Pin Fleet protocol 1's deterministic
            # owner/node/digest/scope identity before submitting its first start.
            state = await lifecycle.status(row['node_id'])
            current = next((i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == scope), None)
            if current and (current['generation'] != 0 or not stopped(current)):
                raise ValueError('This engine revision already ran; inspect it in Fleet before updating')
            state = await manager.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'install', digest, scope=scope))
            if state.get('protocol') != 1 or not state.get('owner') or state.get('node_id') != row['node_id']:
                raise ValueError('Fleet did not provide this node’s owned instance identity')
            target_id = hashlib.sha256('\0'.join((state['owner'], row['node_id'], digest, scope)).encode()).hexdigest()[:32]
            current = state['instances'].get(target_id)
            if current and (current['app_id'] != 'model-service' or current['generation'] != 0 or not stopped(current)):
                raise ValueError('The prepared engine target is not an unused owned instance')
            pending = dict(operation_id=uuid.uuid4().hex, source=dict(row['engine_binding']),
                connector=dict(row['binding']), target_revision=digest, target_instance_id=target_id,
                target_config=config, started_at=time.time(), phase='draining')
            row.update(engine_update=pending, state='stopping')
            row = await manager.client.save(row)

        async def phase(value):
            nonlocal row
            if row['engine_update']['phase'] != value:
                row['engine_update']['phase'] = value
                row = await manager.client.save(row)

        # A resumed operation uses only saved identity, not the current Agent's
        # recipe catalog or newly generated wrapper code.
        source = pending['source']
        target_binding = dict(node_id=row['node_id'], instance_id=pending['target_instance_id'],
            revision=pending['target_revision'], generation=1, component='backend', port='http')
        await manager.drain_binding(row['binding'])
        await phase('stopping')
        state = await lifecycle.status(row['node_id'])
        original, dead = manager.bound_instance(state, source, scope)
        if not dead:
            state = await manager.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'stop',
                source['revision'], scope=scope, generation=source['generation'],
                operation_id='engine-stop-' + pending['operation_id']))
            original, dead = manager.bound_instance(state, source, scope)
        if not dead:
            raise ValueError('The previous engine has not released its processes and reservations')
        await phase('starting')
        current = state['instances'].get(target_binding['instance_id'])
        if current:
            exact_instance(state, target_binding, scope)
        operation_id = 'engine-start-' + pending['operation_id']
        previous = state.get('operations', {}).get(operation_id)
        if previous:
            request = previous['request']
            if any(request.get(k) != v for k, v in dict(action='start', scope=scope,
                    digest=target_binding['revision'], generation=0).items()):
                raise ValueError('Engine update operation identity changed')
            state = await manager.wait(row['node_id'], previous)
        else:
            if current and (current['generation'] != 0 or not stopped(current)):
                raise ValueError('The target was started outside this engine update')
            state = await manager.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'start',
                target_binding['revision'], scope=scope, generation=0, operation_id=operation_id))
        current = exact_instance(state, target_binding, scope)
        if current['generation'] != 1 or current['state'] != 'ready':
            raise ValueError('The pinned engine did not leave its expected ready generation; inspect Fleet')
        await lifecycle.usage(row['node_id'], 'keep_alive', **{k: target_binding[k] for k in
            ('instance_id', 'revision', 'generation')}, keep_alive=True)
        await phase('verifying')
        configuration = await manager.managed_configuration({**row, 'managed': pending['target_config']}, target_binding)
        target_revision = (await manager.rpc(row['binding'], 'preview_configuration', configuration))['config_revision']
        status = await manager.rpc(row['binding'], 'status')
        if status['config_revision'] not in {row['config_revision'], target_revision}:
            raise ValueError('Connector configuration changed outside this engine update; it was not overwritten')
        if status['config_revision'] != target_revision:
            result = await manager.rpc(row['binding'], 'configure', {
                **configuration, 'expected_revision': status['config_revision']})
            if result['config_revision'] != target_revision:
                raise ValueError('Updated engine configuration does not match its preview')
        discovered = await manager.rpc(row['binding'], 'discover')
        if (discovered['config_revision'] != target_revision
                or not {m['id'] for m in row['models']} <= {m['id'] for m in discovered['models']}):
            raise ValueError('The updated engine did not retain the published models; inspect Fleet before resuming')
        state = await lifecycle.status(row['node_id'])
        for binding, owned_scope in [(row['binding'], 'model-' + deployment_id), (target_binding, scope)]:
            current, dead = manager.bound_instance(state, binding, owned_scope)
            if dead or current['state'] != 'ready':
                raise ValueError('An owned process changed during engine verification')
        await manager.rpc(row['binding'], 'resume', {'config_revision': target_revision})
        row.update(managed=pending['target_config'], engine_binding=target_binding,
            config_revision=target_revision, engine_update=None, state='ready')
        return await manager.client.save(row)
