"""Durable, bounded advancement of a set of configured ordinary Apps.

Artifacts are content-addressed and must already be staged on their target nodes
(the Store/package transfer path owns code delivery). This coordinator installs,
prepares all instances, resolves exact-generation references, then binds/starts
providers before consumers. It never discovers an alternate node, silently stops
an existing App, changes a failed operation ID, or returns private configuration.
"""
import hashlib
import json
from pathlib import Path

from pantheon.apps.dependency_assembly import (
    AssemblyError, DIGEST, IDENT, NAME, DEPLOYMENT_BYTES, _copy, _identity, _matches,
)
from pantheon.apps.lifecycle import ConfigurationBusy
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.utils.registry_lock import registry_lock


def _references(value):
    if isinstance(value, dict):
        if '$app' in value:
            if (not _matches(NAME, value['$app'])
                    or set(value) not in ({'$app'}, {'$app', 'component', 'port'})
                    or len(value) > 1 and (value['component'] != 'backend' or value['port'] != 'http')):
                raise AssemblyError('Invalid prepared App reference')
            return {value['$app']}
        return set().union(*(_references(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_references(item) for item in value)) if value else set()
    return set()


def _resolve(value, prepared):
    if isinstance(value, dict):
        if '$app' in value:
            identity = prepared[value['$app']]
            return {**identity, 'generation': identity['generation'] + 1,
                    **{key: val for key, val in value.items() if key != '$app'}}
        return {key: _resolve(item, prepared) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, prepared) for item in value]
    return value


def deployment_recipe(owner, operation_id, apps):
    recipe = _copy(dict(owner=owner, operation_id=operation_id, apps=apps), DEPLOYMENT_BYTES)
    if (not _matches(IDENT, owner) or not _matches(NAME, operation_id)
            or not isinstance(recipe['apps'], dict) or not 1 <= len(apps) <= 16):
        raise AssemblyError('Supply a bounded deployment with a stable owner and operation ID')
    apps = recipe['apps']
    edges, instances = {}, set()
    for name, app in apps.items():
        if (not _matches(NAME, name) or not isinstance(app, dict) or set(app) != {
                'node_id', 'revision', 'scope', 'generation', 'components', 'bindings'}
                or not _matches(IDENT, app['node_id']) or not _matches(DIGEST, app['revision'])
                or not _matches(IDENT, app['scope']) or type(app['generation']) is not int
                or not 0 <= app['generation'] < 2**63-3
                or not isinstance(app['components'], dict)
                or not isinstance(app['bindings'], dict) or len(app['bindings']) > 16):
            raise AssemblyError('Invalid exact App deployment target')
        identity = (app['node_id'], app['revision'], app['scope'])
        if identity in instances:
            raise AssemblyError('Deployment names must refer to distinct App instances')
        instances.add(identity)
        # Only provider bindings introduce startup ordering. Configuration may
        # refer to the future consumer (e.g. a broker policy) without a cycle.
        edges[name] = _references(app['bindings'])
        if (_references(app['components']) | edges[name]) - apps.keys():
            raise AssemblyError('Prepared App reference is absent from the deployment')
    order = []
    while len(order) < len(apps):
        available = sorted(name for name, dependencies in edges.items()
                           if name not in order and dependencies <= set(order))
        if not available:
            raise AssemblyError('Startup dependency cycle; move logical resource allocation to runtime')
        order.extend(available)
    return recipe, order


class AppDeployment(OwnerJournal):
    """Advance the original intent using Fleet's idempotent operation ledger.

    Each advance returns when a node operation is pending. Caller polling resumes
    it; there is no detached task after platform shutdown or a hidden deployment
    retry loop. Explicitly repeat advance, without replacing the operation ID.
    """
    error_type = AssemblyError
    maximum_bytes = 2 * DEPLOYMENT_BYTES

    def __init__(self, starter, root):
        self.starter, self.lifecycle, self.root = starter, starter.lifecycle, Path(root)

    def _path(self, operation_id):
        if not _matches(NAME, operation_id):
            raise AssemblyError('Use the original deployment operation ID')
        return self.root / (operation_id + '.json')

    def _load(self, path):
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Invalid deployment checkpoint')
        record = json.loads(raw)
        recipe, order = deployment_recipe(**record['recipe'])
        if (record.get('protocol') != 1 or record.get('order') != order
                or not isinstance(record.get('prepared'), dict)
                or record['prepared'].keys() - recipe['apps'].keys()):
            raise AssemblyError('Invalid deployment checkpoint')
        for name, identity in record['prepared'].items():
            _identity(identity)
            app = recipe['apps'][name]
            if any(identity[key] != expected for key, expected in (
                    ('node_id', app['node_id']), ('revision', app['revision']),
                    ('generation', app['generation'] + 1))):
                raise AssemblyError('Deployment preparation changed its original identity')
        return record

    @staticmethod
    def _public(record):
        # No configuration, credential references, policy contents or grants.
        return _copy({key: record[key] for key in (
            'protocol', 'state', 'phase', 'app', 'prepared')} | {
            'operation_id': record['recipe']['operation_id'], 'observation': 'last-checkpoint'})

    def inspect(self, *, owner, operation_id):
        path = self._path(operation_id)
        self._private(self.root, directory=True)
        record = self._load(path)
        if record['recipe']['owner'] != owner:
            raise AssemblyError('Deployment belongs to another Fleet owner')
        return self._public(record)

    @staticmethod
    def operation_id(recipe, name, action):
        digest = hashlib.sha256(json.dumps([recipe['owner'], recipe['operation_id'], name, action],
                                           separators=(',', ':')).encode()).hexdigest()
        return 'deploy-' + digest

    async def _state(self, recipe, name):
        node = recipe['apps'][name]['node_id']
        state = await self.lifecycle.status(node)
        if (state.get('node_id') != node or state.get('owner') != recipe['owner']
                or state.get('dependency_config_protocol') != 1
                or not all(isinstance(state.get(key), dict) for key in ('operations', 'instances', 'installations'))):
            raise AssemblyError('Deployment target is unavailable, belongs to another owner or needs a Fleet update')
        return state

    async def _operation(self, recipe, name, action, generation):
        app = recipe['apps'][name]
        operation_id = self.operation_id(recipe, name, action)
        request = dict(protocol=1, operation_id=operation_id, action=action,
                       digest=app['revision'], scope=app['scope'], generation=generation)
        state = await self._state(recipe, name)
        operation = state['operations'].get(operation_id)
        if operation is None:
            # The complete recipe was checkpointed before any submission. Lost
            # replies can only resubmit this exact durable node operation ID.
            operation = await self.lifecycle.submit(app['node_id'], action, app['revision'],
                scope=app['scope'], generation=generation, operation_id=operation_id)
        if not isinstance(operation, dict) or operation.get('request') != request:
            raise AssemblyError('Deployment operation conflicts with the node ledger')
        if operation.get('state') in ('queued', 'running'):
            return False
        if operation.get('state') != 'succeeded':
            raise AssemblyError(f'App {name} {action} requires recovery; inspect its original Fleet operation')
        return True

    @staticmethod
    def _instance(state, app, preparation_id, identity=None, *, ready=False, prepared_only=False):
        if identity is None:
            matches = [(key, item) for key, item in state['instances'].items()
                       if item.get('digest') == app['revision'] and item.get('scope') == app['scope']]
            if len(matches) != 1:
                raise AssemblyError('Prepared App instance is missing or ambiguous')
            key, instance = matches[0]
            identity = dict(node_id=app['node_id'], instance_id=key, revision=app['revision'],
                            generation=app['generation'] + 1)
        else:
            instance = state['instances'].get(identity['instance_id'], {})
        _identity(identity)
        exact = instance.get('digest') == identity['revision'] and instance.get('scope') == app['scope']
        prepared = (instance.get('state') == 'prepared'
                    and instance.get('generation') == identity['generation']
                    and instance.get('start_preparation_id') == preparation_id)
        running = (instance.get('state') in {'starting', 'ready', 'recovered'}
                   and instance.get('generation') == identity['generation'] + 1)
        is_ready = (running and instance.get('state') in {'ready', 'recovered'}
                    and instance.get('ready_generation') == identity['generation'] + 1)
        usable = prepared if prepared_only else is_ready if ready else prepared or running
        if not exact or not usable:
            raise AssemblyError('Original App preparation was stopped, replaced or is no longer usable')
        return identity

    async def prepare(self, *, owner, operation_id, apps=None):
        """Reserve all identities without configuring, granting or starting Apps.

        Repeat until state is prepared, then explicitly advance to start. This
        is an owner-side boundary, not a distributed lock on App data. A started
        deployment cannot be converted back into a preparation.
        """
        return await self._advance(owner=owner, operation_id=operation_id, apps=apps, prepare_only=True)

    async def advance(self, *, owner, operation_id, apps=None):
        return await self._advance(owner=owner, operation_id=operation_id, apps=apps, prepare_only=False)

    async def _advance(self, *, owner, operation_id, apps, prepare_only):
        path = self._path(operation_id)
        proposed = deployment_recipe(owner, operation_id, apps)[0] if apps is not None else None
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                record = self._load(path)
                if record['recipe']['owner'] != owner or proposed is not None and proposed != record['recipe']:
                    raise AssemblyError('Deployment operation belongs to another immutable recipe')
            else:
                if proposed is None:
                    raise AssemblyError('Create a deployment recipe before resuming it')
                if sum(1 for _ in self.root.glob('*.json')) >= 1024:
                    raise AssemblyError('Deployment journal is full')
                _, order = deployment_recipe(**proposed)
                record = dict(protocol=1, recipe=proposed, order=order, prepared={},
                              state='pending', phase='installing', app=order[0])
                await self._checkpoint(path, record)
            recipe = record['recipe']
            if 'abort' in record:
                raise AssemblyError('Deployment is fenced for abort; resume its original abort operation')
            if prepare_only and record['phase'] in ('starting', 'ready'):
                raise AssemblyError('Deployment has entered startup; it cannot be prepared for data migration')

            async def progress(phase, name, state='pending'):
                record.update(phase=phase, app=name, state=state)
                await self._checkpoint(path, record)

            # Installation identity is the content digest. Existing installed
            # code is reused; installation hooks are not re-run on every open.
            for name in record['order']:
                app = recipe['apps'][name]
                state = await self._state(recipe, name)
                if state['installations'].get(app['revision'], {}).get('state') != 'installed':
                    if record['prepared']:
                        raise AssemblyError('An original installation disappeared; inspect Fleet recovery')
                    await progress('installing', name)
                    if not await self._operation(recipe, name, 'install', 0):
                        return self._public(record)
                    if (await self._state(recipe, name))['installations'].get(app['revision'], {}).get('state') != 'installed':
                        raise AssemblyError('Node did not retain the installed App revision')

            # Reserve identities for all Apps before resolving cross references.
            for name in record['order']:
                app = recipe['apps'][name]
                preparation = self.operation_id(recipe, name, 'prepare_start')
                if name not in record['prepared']:
                    await progress('preparing', name)
                    if not await self._operation(recipe, name, 'prepare_start', app['generation']):
                        return self._public(record)
                state = await self._state(recipe, name)
                if prepare_only and self.operation_id(recipe, name, 'start') in state['operations']:
                    raise AssemblyError('App has a start operation; preparation is no longer available')
                identity = self._instance(state, app, preparation,
                                          record['prepared'].get(name), prepared_only=prepare_only)
                if prepare_only and state['instances'][identity['instance_id']].get('resources'):
                    raise AssemblyError('Prepared App has running resources; inspect Fleet recovery')
                if name not in record['prepared']:
                    record['prepared'][name] = identity
                    await self._checkpoint(path, record)

            if prepare_only:
                await progress('prepared', '', 'prepared')
                return self._public(record)

            for name in record['order']:
                app = recipe['apps'][name]
                identity = record['prepared'][name]
                await progress('starting', name)
                try:
                    result = await self.starter.start(consumer=identity,
                        preparation_id=self.operation_id(recipe, name, 'prepare_start'),
                        operation_id=self.operation_id(recipe, name, 'start'),
                        bindings=_resolve(app['bindings'], record['prepared']),
                        components=_resolve(app['components'], record['prepared']))
                except ConfigurationBusy:
                    # The starter journal retains exact grants/configuration.
                    # Resume on the next bounded advance, without a new intent.
                    return self._public(record)
                operation = result['operation']
                if operation.get('state') in ('queued', 'running'):
                    return self._public(record)
                if operation.get('state') != 'succeeded':
                    raise AssemblyError(f'App {name} start requires recovery; inspect its original Fleet operation')
                self._instance(await self._state(recipe, name), app,
                               self.operation_id(recipe, name, 'prepare_start'), identity, ready=True)
            await progress('ready', '', 'ready')
            return self._public(record)
