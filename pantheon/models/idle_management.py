"""Owner-only durable idle policy management; inference callers cannot use it."""
import asyncio
import time
import re

from pantheon.apps.lifecycle import FleetLifecycle
from .recovery import exact_instance, stopped


def binding(value):
    return {k: value[k] for k in ('instance_id', 'revision', 'generation')}


def registration(row, seconds):
    return dict(id=row['deployment_id'], revision=(row.get('engine_idle') or {}).get('policy_revision', 0),
                connector=binding(row['binding']), engine=binding(row['engine_binding']),
                config_revision=row['config_revision'], idle_seconds=seconds)


async def capability(manager, row):
    node = await manager.node(row['node_id'], managed=True)
    if node.get('capability', {}).get('runtimes', {}).get('model-engine-idle-management') != '1':
        raise ValueError('Update Fleet on this node before managing automatic engine idle')


def checked(row, policy):
    q = row['engine_idle']['registration']
    if (policy.get('registration') != q or policy.get('id') != q['id']
            or type(policy.get('revision')) is not int or policy['revision'] not in {q['revision']+1, q['revision']+2}
            or policy.get('idle_seconds') != q['idle_seconds'] or policy.get('connector') != q['connector']
            or any(policy.get('engine', {}).get(k) != q['engine'][k] for k in ('instance_id', 'revision'))
            or type(policy.get('engine', {}).get('generation')) is not int
            or policy['engine']['generation'] < q['engine']['generation']):
        raise ValueError('Node idle policy does not match this saved registration')
    if policy.get('enabled') is True and policy['revision'] != q['revision']+1:
        raise ValueError('Node idle policy revision changed')
    return policy


async def enable(manager, row, seconds):
    """Caller holds the deployment lock. Persist original request before RPC."""
    if (row.get('mode') != 'managed' or row['engine'] not in {'ollama', 'lmstudio'}
            or row.get('managed', {}).get('load_policy') not in {'on_demand', 'warm'}
            or seconds < row['managed']['keep_alive_seconds'] or row['state'] != 'ready'
            or not row.get('engine_binding') or not row.get('binding')
            or any(row.get(k) for k in ('recovery', 'engine_update', 'connector_update', 'operation_stop'))):
        raise ValueError('Choose a ready owned on-demand/warm engine and preserve its model TTL')
    await capability(manager, row)
    previous = row.get('engine_idle') or {}
    if previous.get('phase') in {'enabled', 'registering'}:
        if previous['idle_seconds'] != seconds:
            raise ValueError('Disable the current idle policy before changing its timeout')
    elif previous.get('phase') == 'disabling':
        raise ValueError('Finish disabling this idle policy before enabling it again')
    else:
        q = registration(row, seconds)
        row['engine_idle'] = dict(idle_seconds=seconds, policy_revision=q['revision'], phase='registering', registration=q)
        row = await manager.client.save(row)
    q = row['engine_idle']['registration']
    lifecycle = FleetLifecycle(manager.resolver)
    state = await lifecycle.status(row['node_id'])
    policy = (state.get('model_idle') or {}).get(row['deployment_id'])
    if not policy or policy.get('registration') != q:
        if row['engine_idle']['phase'] != 'registering':
            raise ValueError('The node lost this idle policy; inspect service recovery')
        if policy and (policy['revision'] != q['revision'] or policy.get('enabled') or policy['state'] != 'disabled'):
            raise ValueError('Another node idle policy superseded this registration')
        policy = await lifecycle.model_idle(row['node_id'], 'register', registration=q)
    policy = checked(row, policy)
    if policy.get('enabled') is not True or policy['state'] in {'disabled', 'recovery_required'}:
        raise ValueError('This idle registration was cancelled or needs recovery; it was not restarted')
    if row['engine_idle']['phase'] == 'enabled':
        return row
    row['engine_idle'].update(phase='enabled', policy_revision=policy['revision'])
    return await manager.client.save(row)


def settled_engine(row, policy, state):
    """Adopt only the policy's own generation or a provable final operation."""
    source = {**row['engine_binding'], **policy['engine']}
    scope = 'engine-' + row['deployment_id']
    current = exact_instance(state, source, scope)
    expected = source['generation']
    if current['generation'] == expected or (stopped(current) and current['generation'] == expected+1):
        return {**source, 'generation': current['generation']}
    # Disable can win while an already executing start/stop finishes. Its
    # immutable receipt is evidence of ownership, not permission to replay it.
    for action, key in [('start', 'start_operation'), ('stop', 'stop_operation')]:
        operation = state.get('operations', {}).get(policy.get(key))
        if not operation:
            continue
        request = operation['request']
        if (operation.get('model_idle_id') == row['deployment_id']
                and operation['state'] in {'succeeded', 'failed', 'unknown'}
                and all(request.get(k) == v for k, v in dict(action=action, scope=scope,
                    digest=source['revision'], generation=expected).items())
                and current['generation'] == expected+1):
            return {**source, 'generation': current['generation']}
    raise ValueError('Engine generation changed outside this idle operation; no newer process was adopted')


async def cancel(manager, row):
    """Revoke automatic work before Stop/recovery/update, without waking it."""
    current = row.get('engine_idle') or {}
    if not current or current['phase'] == 'disabled':
        return row
    await capability(manager, row)
    if current['phase'] != 'disabling':
        row['engine_idle']['phase'] = 'disabling'
        row = await manager.client.save(row)
    lifecycle = FleetLifecycle(manager.resolver)
    policy = checked(row, await lifecycle.model_idle(row['node_id'], 'cancel',
        registration=row['engine_idle']['registration']))
    if policy.get('enabled') is not False or policy['state'] != 'disabled':
        raise ValueError('The node has not acknowledged idle cancellation')
    deadline = time.monotonic()+30
    while True:
        state = await lifecycle.status(row['node_id'])
        observed = checked(row, (state.get('model_idle') or {}).get(row['deployment_id'], {}))
        if observed != policy:
            raise ValueError('Idle policy changed during cancellation; inspect this service')
        pending = [o for o in state.get('operations', {}).values() if o['state'] in {'queued', 'running'}
                   and o['request']['scope'] in {'engine-'+row['deployment_id'], 'model-'+row['deployment_id']}]
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('An owned lifecycle operation is still finishing. Resume cancellation after it finishes.')
        await asyncio.sleep(.5)
    row['engine_binding'] = settled_engine(row, policy, state)
    row['engine_idle'].update(phase='disabled', policy_revision=policy['revision'])
    return await manager.client.save(row)


async def reset_fence(manager, binding, status):
    """Explicit owner recovery only; automatic consumer wake must never reset."""
    idle = status.get('engine_idle') or {}
    if idle.get('phase') in {'fenced', 'resuming', 'stopped'}:
        # drain() must have persisted the stopped phase before reset. Repeat it
        # after a lost ACK; neither reset nor configure reopens admission.
        await manager.drain_binding(binding)
        latest = await manager.rpc(binding, 'status')
        idle = latest.get('engine_idle') or {}
        if idle.get('phase') != 'stopped' or not idle.get('suspend_id'):
            raise ValueError('Idle fence is not stopped; inspect recovery before reopening it')
        await manager.rpc(binding, 'idle_reset', dict(suspend_id=idle['suspend_id'], config_revision=latest['config_revision']))


def recovery_revisions(row, state):
    """A cancelled in-flight rebind can finish after its last Hub publication."""
    saved = row.get('engine_idle') or {}
    if saved.get('phase') != 'disabled':
        return set()
    policy = checked(row, (state.get('model_idle') or {}).get(row['deployment_id'], {}))
    if policy.get('enabled') is not False or policy['revision'] != saved['policy_revision']:
        raise ValueError('Idle cancellation changed before recovery')
    source = row.get('recovery') or row
    # An old cancelled policy cannot justify a later unrelated connector/engine.
    if (binding(source['binding']) != policy['connector']
            or any(source['engine_binding'][k] != policy['engine'][k] for k in ('instance_id', 'revision'))):
        return set()
    return {value for key in ('config_revision', 'resume_revision')
            if isinstance(value := policy.get(key), str) and re.fullmatch('[a-f0-9]{64}', value)}
