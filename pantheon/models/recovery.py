"""Explicit recovery of pinned model instances after a runner/node restart.

Hub intent survives the Agent. A deterministic start operation fences lost
acknowledgements; a later unrelated generation is never adopted. Recovery uses
installed artifacts and node-local configuration, never installation/download.
"""
import uuid

from pantheon.apps.lifecycle import FleetLifecycle


def exact_instance(state, binding, scope):
    current = state['instances'].get(binding['instance_id'])
    if (not current or current['digest'] != binding['revision'] or current['scope'] != scope
            or current['app_id'] != 'model-service'):
        raise ValueError('Model service identity changed; inspect its exact Fleet binding')
    return current


def stopped(instance):
    return instance['state'] == 'stopped' and not instance.get('resources') and not instance.get('reservations')


async def restore(manager, row, key, scope):
    lifecycle = FleetLifecycle(manager.resolver)
    source = row['recovery'][key]
    node = row['node_id']
    operation_id = 'recover-' + row['recovery']['operation_id'] + '-' + key
    state = await lifecycle.status(node)
    current = exact_instance(state, source, scope)
    previous = state.get('operations', {}).get(operation_id)
    if previous:
        request = previous['request']
        if (request['action'] != 'start' or request['scope'] != scope or request['digest'] != source['revision']
                or request['generation'] not in {source['generation'], source['generation'] + 1}):
            raise ValueError('Recovery operation identity changed; inspect it in Fleet')
        if previous['state'] in {'queued', 'running'}:
            state = await manager.wait(node, previous)
            current = exact_instance(state, source, scope)
        if current['generation'] != request['generation'] + 1:
            raise ValueError('Recovery start did not leave its expected generation; inspect it in Fleet')
    else:
        allowed = {source['generation'], source['generation'] + 1} if stopped(current) else {source['generation']}
        if current['generation'] not in allowed:
            raise ValueError('Model service generation changed; recovery did not adopt another process')
    state = await manager.wait(node, await lifecycle.submit(node, 'recover', source['revision'],
        scope=scope, generation=current['generation']))
    current = exact_instance(state, source, scope)
    if stopped(current):
        if previous:
            raise ValueError('The recovery process exited again; inspect its logs in Fleet before starting another recovery')
        # Only this single recorded start may advance a dead source generation.
        state = await manager.wait(node, await lifecycle.submit(node, 'start', source['revision'],
            scope=scope, generation=current['generation'], operation_id=operation_id))
        current = exact_instance(state, source, scope)
    if current['state'] != 'ready':
        raise ValueError('The owned process is not ready; inspect it in Fleet')
    binding = {**source, 'generation': current['generation']}
    await lifecycle.usage(node, 'keep_alive', **{k: binding[k] for k in
        ('instance_id', 'revision', 'generation')}, keep_alive=True)
    return binding


async def recover(manager, deployment_id):
    async with manager.lock(deployment_id):
        return await recover_locked(manager, await manager.client.deployment(deployment_id))


async def recover_locked(manager, row):
    deployment_id = row["deployment_id"]
    if row.get('operation_stop'):
        raise ValueError('Finish stopping this operation before recovering the service')
    if row.get('connector_update') or row.get('engine_update') or row['state'] not in {'ready', 'recovering'}:
        raise ValueError('Finish setup, stopping or the connector update before recovering this service')
    node = await manager.node(row['node_id'], managed=row.get('mode') == 'managed')
    if node.get('capability', {}).get('runtimes', {}).get('app-recovery') != '1':
        raise ValueError('Update Fleet on this node to verify and recover owned processes')
    from .idle_management import cancel, reset_fence, recovery_revisions
    row = await cancel(manager, row)
    lifecycle = FleetLifecycle(manager.resolver)
    if not row.get('recovery'):
        if not row.get('binding') or (row.get('mode') == 'managed' and not row.get('engine_binding')):
            raise ValueError('Complete service setup before recovery')
        state = await lifecycle.status(row['node_id'])
        definition = state.get('installations', {}).get(row['binding']['revision'], {}).get('definition', {})
        # These protocol methods first ship in connector 0.1.2. Check the
        # immutable manifest before committing an intent or starting a process.
        version = definition.get('version', '')
        parts = version.split('.')
        if len(parts) != 3 or not all(p.isdigit() for p in parts) or tuple(map(int, parts)) < (0, 1, 2):
            raise ValueError('Update the connector before using service recovery')
        manager.bound_instance(state, row['binding'], 'model-' + deployment_id)
        if row.get('engine_binding'):
            manager.bound_instance(state, row['engine_binding'], 'engine-' + deployment_id)
        row.update(state='recovering', recovery={'operation_id': uuid.uuid4().hex,
            'binding': dict(row['binding']), 'engine_binding': row.get('engine_binding')})
        row = await manager.client.save(row)
    binding = await restore(manager, row, 'binding', 'model-' + deployment_id)
    status = await manager.rpc(binding, 'status')
    if status.get('recovery_protocol') != 1:
        raise ValueError('This connector lacks the required recovery protocol')
    await manager.drain_binding(binding)
    if row.get('engine_idle'):
        status = await manager.rpc(binding, 'status')
    await reset_fence(manager, binding, status)
    idle_revisions = recovery_revisions(row, await lifecycle.status(row['node_id'])) if row.get('engine_idle') else set()
    engine_binding = row.get('engine_binding')
    expected_revision = row['config_revision']
    if row.get('mode') == 'managed':
        engine_binding = await restore(manager, row, 'engine_binding', 'engine-' + deployment_id)
        configuration = await manager.managed_configuration(row, engine_binding)
        target = (await manager.rpc(binding, 'preview_configuration', configuration))['config_revision']
        # An acknowledged or lost configure is safe to resume only when the
        # current configuration is the original or this exact desired value.
        if status['config_revision'] not in {expected_revision, target, *idle_revisions}:
            raise ValueError('Connector configuration changed outside recovery; it was not overwritten')
        if status['config_revision'] != target:
            result = await manager.rpc(binding, 'configure', {
                **configuration, 'expected_revision': status['config_revision']})
            if result['config_revision'] != target:
                raise ValueError('Recovered engine configuration did not match its preview')
        expected_revision = target
    elif status['config_revision'] != expected_revision:
        raise ValueError('Attached endpoint configuration changed; recovery did not overwrite it')
    # Verify upstream availability before readmitting inference. An attached
    # daemon/API is external: recovery never starts or stops that process.
    discovered = await manager.rpc(binding, 'discover')
    if discovered['config_revision'] != expected_revision:
        raise ValueError('Configuration changed while validating recovery')
    for candidate, scope in [(binding, 'model-' + deployment_id),
                              (engine_binding, 'engine-' + deployment_id)]:
        if candidate:
            state = await lifecycle.status(row['node_id'])
            instance, dead = manager.bound_instance(state, candidate, scope)
            if dead or instance['state'] != 'ready':
                raise ValueError('An owned process changed during recovery; retry after inspecting Fleet')
    await manager.rpc(binding, 'resume', {'config_revision': expected_revision})
    row.update(binding=binding, engine_binding=engine_binding, state='ready', recovery=None,
               config_revision=expected_revision)
    return await manager.client.save(row)
