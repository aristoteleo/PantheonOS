"""Explicitly stop an unfinished operation without losing its owned instances.

The stop plan is committed before drain/stop. Retries inspect the same revisions
and generations; no failed start, model download or external daemon is replayed.
If a replacement already started, retain that version for an explicit restart.
"""
from copy import deepcopy
import time
import uuid

from pantheon.apps.lifecycle import FleetLifecycle
from .recovery import exact_instance, stopped


PENDING = ('recovery', 'connector_update', 'engine_update')


def idle_operations(state, scopes):
    if any(o['state'] in {'queued', 'running'} and o['request']['scope'] in scopes
           for o in state.get('operations', {}).values()):
        raise RuntimeError('Fleet is still running this operation. Wait for it to finish before stopping it.')


def snapshot(state, binding, scope, role):
    current = exact_instance(state, binding, scope)
    allowed = {binding['generation']}
    if stopped(current):
        allowed.add(binding['generation'] + 1)
    if current['generation'] not in allowed:
        raise ValueError('The operation generation changed; no newer process was stopped')
    return dict(role=role, scope=scope, binding={**binding, 'generation': current['generation']})


def validate_plan(state, intent):
    scopes = {t['scope'] for t in intent['targets']}
    idle_operations(state, scopes)
    known = {t['binding']['instance_id'] for t in intent['targets']}
    for current in state['instances'].values():
        if current['scope'] in scopes and current['instance_id'] not in known and not stopped(current):
            raise ValueError('An unplanned instance appeared; inspect Fleet before stopping this operation')
    for target in intent['targets']:
        snapshot(state, target['binding'], target['scope'], target['role'])


def plan(row, state):
    kinds = [k for k in PENDING if row.get(k)]
    if len(kinds) != 1:
        raise ValueError('Select one unfinished recovery or update')
    kind = kinds[0]
    pending = row[kind]
    connector_scope, engine_scope = 'model-' + row['deployment_id'], 'engine-' + row['deployment_id']
    idle_operations(state, {connector_scope, engine_scope})
    targets = []
    for key, scope in [('binding', connector_scope), ('engine_binding', engine_scope)]:
        binding = deepcopy(row.get(key))
        if not binding:
            continue
        if kind == 'recovery':
            # Only the saved recovery start may account for a newer generation.
            source = pending[key]
            op = state.get('operations', {}).get('recover-' + pending['operation_id'] + '-' + key)
            if op:
                req = op['request']
                if (req['action'] != 'start' or req['scope'] != scope or req['digest'] != source['revision']
                        or req['generation'] not in {source['generation'], source['generation'] + 1}):
                    raise ValueError('Recovery start identity changed')
                current = exact_instance(state, source, scope)
                # A start can fail preflight without incrementing its generation.
                if current['generation'] == req['generation'] and stopped(current):
                    binding['generation'] = req['generation']
                else:
                    binding['generation'] = req['generation'] + 1
        targets.append(snapshot(state, binding, scope, key))
    if kind in {'connector_update', 'engine_update'}:
        scope = connector_scope if kind == 'connector_update' else engine_scope
        matches = [i for i in state['instances'].values()
                   if i['digest'] == pending['target_revision'] and i['scope'] == scope]
        if len(matches) > 1:
            raise ValueError('Replacement instance identity is ambiguous')
        if matches:
            current = matches[0]
            if current['app_id'] != 'model-service' or current['generation'] not in {0, 1}:
                raise ValueError('Replacement generation changed outside this operation')
            if kind == 'engine_update':
                if current['instance_id'] != pending['target_instance_id']:
                    raise ValueError('Replacement engine identity changed')
                op = state.get('operations', {}).get('engine-start-' + pending['operation_id'])
                if current['generation'] and (not op or any(op['request'].get(k) != v for k, v in
                        dict(action='start', scope=scope, digest=pending['target_revision'], generation=0).items())):
                    raise ValueError('Replacement engine was started outside this operation')
            else:
                original = exact_instance(state, pending['source'], scope)
                if not stopped(original) or current.get('data_source') != {
                        'digest': pending['source']['revision'], 'generation': original['generation']}:
                    raise ValueError('Replacement connector does not retain this source configuration')
            binding = dict(node_id=row['node_id'], instance_id=current['instance_id'],
                revision=pending['target_revision'], generation=current['generation'], component='backend', port='http')
            targets.append(snapshot(state, binding, scope, 'replacement'))
    # Stop every connector before stopping any owned engine.
    targets.sort(key=lambda t: t['scope'] != connector_scope)
    return dict(operation_id=uuid.uuid4().hex, kind=kind, started_at=time.time(), targets=targets)


async def stop_operation(manager, deployment_id, revision):
    async with manager.lock(deployment_id):
        row = await manager.client.deployment(deployment_id)
        if row['state'] == 'stopped' and row.get('last_operation_stop'):
            return row
        if row['revision'] != revision:
            raise ValueError('Service changed. Refresh before stopping this operation.')
        await manager.node(row['node_id'])
        lifecycle = FleetLifecycle(manager.resolver)
        state = await lifecycle.status(row['node_id'])
        if not row.get('operation_stop'):
            row['operation_stop'] = plan(row, state)
            row = await manager.client.save(row)
        intent = row['operation_stop']
        # Validate all targets before touching any process.
        validate_plan(state, intent)
        finished = {}
        for target in intent['targets']:
            binding, scope = target['binding'], target['scope']
            state = await lifecycle.status(row['node_id'])
            validate_plan(state, intent)
            current = exact_instance(state, binding, scope)
            if not stopped(current) and current['state'] != 'ready':
                # Confirm dead processes and release their reservations without
                # requiring an HTTP drain hook from a process that has exited.
                try:
                    await manager.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'recover',
                        binding['revision'], scope=scope, generation=binding['generation']))
                except RuntimeError:
                    # A live but unhealthy process still has to pass Fleet's
                    # normal stop hooks; a failed probe is not permission to kill.
                    pass
                state = await lifecycle.status(row['node_id'])
                # A timeout is not proof that recovery finished. Never submit a
                # stop while the previous operation is still queued or running.
                validate_plan(state, intent)
                current = exact_instance(state, binding, scope)
            if not stopped(current):
                if scope == 'model-' + deployment_id and current['state'] == 'ready':
                    await manager.drain_binding(binding)
                validate_plan(await lifecycle.status(row['node_id']), intent)
                state = await manager.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'stop',
                    binding['revision'], scope=scope, generation=binding['generation']))
                snapshot(state, binding, scope, target['role'])
                current = exact_instance(state, binding, scope)
                if not stopped(current):
                    raise RuntimeError('Fleet has not confirmed release of the operation resources')
            finished[target['role']] = {**binding, 'generation': current['generation']}
        # Recheck the complete plan at publication, including earlier stops.
        state = await lifecycle.status(row['node_id'])
        validate_plan(state, intent)
        for target in intent['targets']:
            current = exact_instance(state, finished[target['role']], target['scope'])
            if not stopped(current) or current['generation'] != finished[target['role']]['generation']:
                raise ValueError('An operation instance changed before completion')
        row.update(binding=finished['binding'], engine_binding=finished.get('engine_binding'))
        replacement = next((t for t in intent['targets'] if t['role'] == 'replacement'), None)
        if replacement and replacement['binding']['generation'] > 0:
            # Retain the explicitly chosen replacement; do not silently roll
            # back its version. Starting it is a separate user action.
            if intent['kind'] == 'engine_update':
                row.update(engine_binding=finished['replacement'], managed=row['engine_update']['target_config'])
            else:
                row['binding'] = finished['replacement']
        row.update(state='stopped', operation_stop=None,
                   last_operation_stop={**intent, 'completed_at': time.time()})
        for key in PENDING:
            row[key] = None
        return await manager.client.save(row)
