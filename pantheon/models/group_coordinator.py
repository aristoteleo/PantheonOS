"""Prepare-all / concurrently-start coordinator for explicit Fleet groups.

This is a lifecycle primitive, not model topology or an inference publisher.
There are no timers which free possibly owned resources and no automatic replay
of uncertain submissions. Every advance is bounded; callers schedule observation.
"""
import asyncio
import hashlib
import uuid

from .group_journal import GroupConflict


def observation(state, *, clean=False, stop_generation=None):
    return dict(state=state, clean=clean, stop_generation=stop_generation)


def inspect_member(owner, member, snapshot):
    target = member['target']
    node, digest, scope, base = (target[k] for k in ('node_id', 'digest', 'scope', 'generation'))
    if snapshot.get('protocol') != 1 or snapshot.get('owner') != owner or snapshot.get('node_id') != node:
        return observation('conflict')
    identity = hashlib.sha256('\0'.join((owner, node, digest, scope)).encode()).hexdigest()[:32]
    instances = snapshot.get('instances') or {}
    current = instances.get(identity)
    if current and (current.get('instance_id') != identity or current.get('digest') != digest or current.get('scope') != scope):
        return observation('conflict')
    # A different version running under this scope cannot be adopted or stopped.
    if any(i.get('scope') == scope and key != identity and
           (i.get('state') != 'stopped' or i.get('resources') or i.get('reservations'))
           for key, i in instances.items()):
        return observation('conflict')
    operations = snapshot.get('operations') or {}
    owned = {member[k]['request']['operation_id'] for k in ('prepare', 'start', 'stop') if member[k]}
    if any(op.get('request', {}).get('scope') == scope and key not in owned and
           op.get('state') in {'queued', 'running'} for key, op in operations.items()):
        return observation('conflict')
    empty = not current or (current.get('state') == 'stopped' and
                           not current.get('resources') and not current.get('reservations'))
    generation = current.get('generation') if current else 0
    if not member['prepare']['sent']:
        if empty and generation == base:
            return observation('unsubmitted', clean=True)
        return observation('conflict')
    key = 'stop' if member['stop'] else 'start' if member['start']['sent'] else 'prepare'
    request = member[key]['request']
    op = operations.get(request['operation_id'])
    if op is None:
        return observation('unknown')  # Includes a crash between journal commit and RPC.
    actual = op.get('request', {})
    if (any(actual.get(k) != v for k, v in request.items()) or actual.get('if_idle')
            or actual.get('data_source') or (key != 'start' and actual.get('start_preparation_id'))):
        return observation('conflict')
    if op.get('state') in {'queued', 'running'}:
        return observation('pending')
    if op.get('state') not in {'succeeded', 'failed', 'unknown'}:
        return observation('unknown')
    if key == 'stop':
        if empty and generation == request['generation'] + 1:
            return observation('released', clean=True)
        return observation('unknown' if op['state'] == 'unknown' else 'conflict')
    if (current and generation == base + 1 and current.get('state') == 'prepared'
            and current.get('start_preparation_id') == member['prepare']['request']['operation_id']
            and not current.get('resources') and current.get('reservations')):
        return observation('prepared' if key == 'prepare' and op['state'] != 'failed' else 'failed',
                           stop_generation=generation)
    if key == 'prepare':
        if empty and generation == base and op['state'] == 'failed':
            return observation('failed', clean=True)
        if empty and generation == base + 2:
            # Fleet may explicitly cancel the preparation. Its incremented
            # generation fences any late commit carrying the old token.
            return observation('released', clean=True)
        return observation('unknown' if op['state'] == 'unknown' else 'conflict')
    if current and generation == base + 2:
        if (op['state'] == 'succeeded' and current.get('state') == 'ready'
                and current.get('ready_generation') == generation):
            return observation('ready', stop_generation=generation)
        # The exact recorded start owns only this generation, even after a
        # Runner restart marks the operation/instance unknown. Normal Fleet stop
        # still enforces hooks/checkpoints; the coordinator never force-kills.
        return observation('failed', clean=empty, stop_generation=None if empty else generation)
    if empty and generation == base + 3:
        return observation('released', clean=True)
    return observation('conflict')


class GroupCoordinator:
    def __init__(self, journal, lifecycle, *, rpc_timeout=15):
        self.journal, self.lifecycle, self.rpc_timeout = journal, lifecycle, rpc_timeout

    def stop(self, group_id):
        """Persist stop intent. A racing sender's claimed RPC remains uncertain."""
        row = self.journal.load(group_id)
        if row['phase'] in {'aborting', 'stopped'}:
            return row
        row['phase'] = 'aborting'
        return self.journal.save(row)

    async def _observe(self, owner, member):
        try:
            state = await asyncio.wait_for(self.lifecycle.status(member['target']['node_id']), self.rpc_timeout)
        except Exception:
            return observation('unknown')
        return inspect_member(owner, member, state)

    async def _send(self, node, request):
        args = {k: v for k, v in request.items() if k != 'protocol'}
        try:
            # A timeout or even an error reply can follow a successful durable
            # submission. Only a subsequent exact operation snapshot is evidence.
            await asyncio.wait_for(self.lifecycle.submit(node, **args), self.rpc_timeout)
        except Exception:
            pass

    async def advance(self, group_id):
        row = self.journal.load(group_id)
        if row['phase'] == 'stopped':
            return row
        observed = await asyncio.gather(*(self._observe(row['owner'], m) for m in row['members']))
        for member, result in zip(row['members'], observed):
            member['observation'] = result
        states = {o['state'] for o in observed}
        if row['phase'] != 'aborting' and states & {'failed', 'released', 'conflict'}:
            row['phase'] = 'aborting'
        if row['phase'] == 'preparing' and states == {'prepared'}:
            # Durable barrier: starts happen only on a later observation after
            # every rank's reservation has been confirmed.
            row['phase'] = 'committing'
            try:
                return self.journal.save(row)
            except GroupConflict:
                return self.journal.load(group_id)
        if row['phase'] in {'committing', 'ready'}:
            row['phase'] = 'ready' if states == {'ready'} else 'committing'
        if row['phase'] == 'aborting' and all(o['clean'] for o in observed):
            row['phase'] = 'stopped'
        sends = []
        for member, result in zip(row['members'], observed):
            action = None
            if row['phase'] == 'preparing' and result['state'] == 'unsubmitted':
                action = member['prepare']
            elif row['phase'] == 'committing' and result['state'] == 'prepared' and 'unknown' not in states:
                action = member['start']
            elif (row['phase'] == 'aborting' and result['stop_generation'] is not None
                  and member['stop'] is None):
                target = member['target']
                action = member['stop'] = dict(sent=False, request=dict(protocol=1, action='stop',
                    digest=target['digest'], scope=target['scope'], generation=result['stop_generation'],
                    operation_id=uuid.uuid4().hex))
            if action and not action['sent']:
                action['sent'] = True
                sends.append((member['target']['node_id'], action['request']))
        try:
            row = self.journal.save(row)
        except GroupConflict:
            # Another worker claimed these RPCs or recorded a stop while we read
            # Fleet. Never send from this stale view.
            return self.journal.load(group_id)
        await asyncio.gather(*(self._send(node, request) for node, request in sends))
        return row
