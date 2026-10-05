"""Drain an exact completed deployment through ordinary Fleet stop operations.

The owner supplies the original deployment and an immutable stop intent. Nodes
run their existing before_stop hooks; this coordinator never kills processes,
uninstalls code, deletes data, or infers completion from an observation timeout.
Partial startup and distributed fencing require their own recovery workflows.
"""
import hashlib
import json
from pathlib import Path

from .dependency_assembly import AssemblyError, IDENT, NAME, _copy, _matches
from .deployment import _references
from .owner_journal import OwnerJournal
from pantheon.platform.registry_lock import registry_lock


def intent(owner, operation_id, source_operation_id, apps):
    value = _copy(dict(owner=owner, operation_id=operation_id,
                       source_operation_id=source_operation_id, apps=apps))
    if (not _matches(IDENT, owner) or not _matches(NAME, operation_id)
            or not _matches(NAME, source_operation_id) or operation_id == source_operation_id
            or not isinstance(apps, list) or not 1 <= len(apps) <= 16
            or any(not _matches(NAME, name) for name in apps) or len(set(apps)) != len(apps)):
        raise AssemblyError('Select distinct deployed Apps and a new stable stop operation ID')
    value['apps'] = sorted(apps)
    return value


class AppDeploymentStop(OwnerJournal):
    error_type = AssemblyError

    def __init__(self, deployment, root):
        self.deployment, self.root = deployment, Path(root)

    def _path(self, operation_id):
        if not _matches(NAME, operation_id):
            raise AssemblyError('Use the original deployment stop operation ID')
        return self.root / (operation_id + '.json')

    def _load(self, path):
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Invalid deployment stop checkpoint')
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('protocol') != 1
                or set(value) != {'protocol', 'intent', 'source_hash', 'order', 'stopped', 'state', 'app'}):
            raise AssemblyError('Invalid deployment stop checkpoint')
        spec = intent(**value['intent'])
        order, stopped = value['order'], value['stopped']
        if (spec != value['intent'] or not _matches(r'[a-f0-9]{64}', value['source_hash'])
                or not isinstance(order, list) or any(not _matches(NAME, name) for name in order)
                or sorted(order) != spec['apps'] or not isinstance(stopped, list)
                or stopped != order[:len(stopped)] or len(stopped) > len(order)
                or value['state'] not in ('pending', 'stopped')
                or value['app'] not in ('', *order)
                or value['state'] == 'stopped' and (stopped != order or value['app'])):
            raise AssemblyError('Invalid deployment stop checkpoint')
        return value

    @staticmethod
    def _public(record):
        return dict(protocol=1, operation_id=record['intent']['operation_id'],
                    state=record['state'], app=record['app'], stopped=list(record['stopped']),
                    observation='last-checkpoint')

    def inspect(self, *, owner, operation_id):
        self._private(self.root, directory=True)
        record = self._load(self._path(operation_id))
        if record['intent']['owner'] != owner:
            raise AssemblyError('Deployment stop belongs to another Fleet owner')
        return self._public(record)

    def _source(self, spec):
        self.deployment._private(self.deployment.root, directory=True)
        path = self.deployment._path(spec['source_operation_id'])
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            source = self.deployment._load(path)
        recipe = source['recipe']
        if recipe['owner'] != spec['owner']:
            raise AssemblyError('Deployment belongs to another Fleet owner')
        if (source.get('state') != 'ready' or source.get('phase') != 'ready'
                or set(source['prepared']) != set(recipe['apps'])):
            raise AssemblyError('Recover the original startup before stopping its deployment')
        selected = set(spec['apps'])
        if not selected <= recipe['apps'].keys():
            raise AssemblyError('Stop selections must belong to the original deployment')
        for name in recipe['apps'].keys() - selected:
            if _references(recipe['apps'][name]) & selected:
                raise AssemblyError('Include every deployed consumer of the selected Apps')
        order = [name for name in reversed(source['order']) if name in selected]
        digest = hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest()
        return source, order, digest

    def _request(self, spec, source, name):
        app = source['recipe']['apps'][name]
        return dict(protocol=1, operation_id=self.deployment.operation_id(spec, name, 'stop'),
                    action='stop', digest=app['revision'], scope=app['scope'],
                    generation=source['prepared'][name]['generation'] + 1)

    async def _observe(self, spec, source, name):
        app, identity = source['recipe']['apps'][name], source['prepared'][name]
        state = await self.deployment._state(source['recipe'], name)
        request = self._request(spec, source, name)
        operation = state['operations'].get(request['operation_id'])
        if operation is not None:
            if not isinstance(operation, dict) or operation.get('request') != request:
                raise AssemblyError('Stop operation conflicts with the original node ledger')
            if operation.get('state') not in ('queued', 'running', 'succeeded'):
                raise AssemblyError(f'App {name} stop requires recovery; inspect its original Fleet operation')
        instance = state['instances'].get(identity['instance_id'], {})
        generation = request['generation']
        if (instance.get('digest') != app['revision'] or instance.get('scope') != app['scope']
                or type(instance.get('generation')) is not int):
            raise AssemblyError('Original deployment instance was removed or replaced')
        stopped = (instance.get('state') == 'stopped' and instance['generation'] == generation + 1
                   and not instance.get('resources') and not instance.get('reservations'))
        if not stopped and (instance['generation'] != generation
                or instance.get('state') not in ('ready', 'recovered', 'degraded', 'failed', 'stop_blocked', 'draining')
                or instance.get('state') == 'draining' and operation is None):
            raise AssemblyError('Original deployment generation is no longer safe to stop')
        if operation and operation['state'] == 'succeeded' and not stopped:
            raise AssemblyError('Node has not retained the acknowledged stopped generation')
        return stopped, operation

    async def advance(self, *, owner, operation_id, source_operation_id=None, apps=None):
        proposed = intent(owner, operation_id, source_operation_id, apps) if (
            source_operation_id is not None or apps is not None) else None
        path = self._path(operation_id)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                record = self._load(path)
                if (record['intent']['owner'] != owner
                        or proposed is not None and proposed != record['intent']):
                    raise AssemblyError('Stop operation belongs to another immutable intent')
            else:
                if proposed is None or sum(1 for _ in self.root.glob('*.json')) >= 1024:
                    raise AssemblyError('Supply a stop intent and available journal capacity')
                source, order, digest = self._source(proposed)
                record = dict(protocol=1, intent=proposed, source_hash=digest, order=order,
                              stopped=[], state='pending', app='')
                await self._checkpoint(path, record)
            spec = record['intent']
            source, order, digest = self._source(spec)
            if order != record['order'] or digest != record['source_hash']:
                raise AssemblyError('Original deployment changed; inspect the stop intent')
            # Check the whole selected set before stopping any one App. A stale
            # generation elsewhere must not trigger partial teardown.
            for name in order:
                stopped, operation = await self._observe(spec, source, name)
                if name in record['stopped'] and not stopped:
                    raise AssemblyError('A stopped deployment App has been replaced')
            for name in order:
                stopped, operation = await self._observe(spec, source, name)
                if not stopped or operation and operation['state'] in ('queued', 'running'):
                    record.update(state='pending', app=name)
                    await self._checkpoint(path, record)
                    if operation is None:
                        request = self._request(spec, source, name)
                        app = source['recipe']['apps'][name]
                        # The intent and deterministic ID are durable before
                        # sending. Lost acknowledgements observe this same ID.
                        await self.deployment.lifecycle.submit(app['node_id'], 'stop', app['revision'],
                            scope=app['scope'], generation=request['generation'],
                            operation_id=request['operation_id'])
                    return self._public(record)
                if name not in record['stopped']:
                    record['stopped'].append(name)
                    await self._checkpoint(path, record)
            record.update(state='stopped', app='')
            await self._checkpoint(path, record)
            return self._public(record)
