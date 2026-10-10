"""Generic owner-managed provider sessions, independent of Agent execution.

Each durable acquisition intent pins a logical owner and two App generations.
Only owner-authenticated platform APIs call this coordinator. Session receipts
are not grants. An App consumer cannot create/renew permissions through them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from pantheon.apps.dependency_assembly import AssemblyError, IDENT, NAME, _copy, _identity, _matches, _methods
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.utils.registry_lock import registry_lock


TERMINAL = {'released', 'expired', 'lost', 'failed'}
LIVE = {'starting', 'ready', 'draining', 'stop_blocked', 'recovered'}


class ResourceStateUnavailable(AssemblyError):
    pass


def _instance(value):
    if (not isinstance(value, dict) or not _matches(r'[a-f0-9]{64}', value.get('digest'))
            or type(value.get('generation')) is not int or value['generation'] <= 0
            or not isinstance(value.get('state'), str) or not value['state']):
        raise ResourceStateUnavailable('Resource-session instance state is incomplete')


def _recipe(value):
    value = _copy(value)
    if not isinstance(value, dict) or set(value) != {
            'consumer', 'preparation_id', 'operation_id', 'owner_ref', 'provider', 'app_id', 'kind'}:
        raise AssemblyError('Use a complete resource-session acquisition recipe')
    _identity(value['consumer'])
    _identity(value['provider'], provider=True)
    if (not _matches(NAME, value['operation_id']) or not _matches(IDENT, value['owner_ref'])
            or not _matches(NAME, value['app_id']) or not _matches(IDENT, value['kind'])
            or value['preparation_id'] and not _matches(NAME, value['preparation_id'])):
        raise AssemblyError('Invalid resource-session owner or operation identity')
    if not isinstance(value['preparation_id'], str):
        raise AssemblyError('Invalid prepared consumer identity')
    return value


def _key(consumer, operation_id):
    _identity(consumer)
    if not _matches(NAME, operation_id):
        raise AssemblyError('Use the original resource-session operation ID')
    return hashlib.sha256((consumer['node_id'] + '\0' + operation_id).encode()).hexdigest()


def _lease_id(owner, key):
    return hashlib.sha256(('resource-session\0' + owner + '\0' + key).encode()).hexdigest()


def _receipt(value, record):
    recipe = record['recipe']
    if (not isinstance(value, dict) or set(value) != {'owner_ref', 'lease_id', 'kind', 'session_id', 'state', 'expires'}
            or value['owner_ref'] != recipe['owner_ref'] or value['lease_id'] != record['lease_id']
            or value['kind'] != recipe['kind'] or value['state'] not in TERMINAL | {'active', 'closing'}
            or type(value['expires']) is not int or value['expires'] < 0
            or not isinstance(value['session_id'], str) or len(value['session_id']) > 256
            or value['state'] == 'active' and not value['session_id']):
        raise AssemblyError('Provider returned an invalid resource-session receipt')
    previous = record.get('receipt')
    if previous and previous['session_id'] and previous['session_id'] != value['session_id']:
        raise AssemblyError('Provider attempted to replace the original resource session')
    if previous and previous['state'] in TERMINAL and value != previous:
        raise AssemblyError('Provider attempted to revive a terminal resource session')
    return _copy(value)


class ResourceSessionOwner(OwnerJournal):
    """Single local platform owner; cross-replica fencing is still required."""
    error_type = AssemblyError

    def __init__(self, lifecycle, root: Path):
        super().__init__(root)
        self.lifecycle = lifecycle

    def _root(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)

    def _read(self, path):
        self._private(path)
        with path.open('rb') as file:
            raw = file.read(256 * 1024 + 1)
        try:
            if len(raw) > 256 * 1024:
                raise ValueError
            record = json.loads(raw)
            recipe = _recipe(record['recipe'])
            key = _key(recipe['consumer'], recipe['operation_id'])
            if (record['protocol'] != 1 or type(record['protocol']) is not int
                    or not _matches(IDENT, record['owner']) or path.stem != key
                    or record['lease_id'] != _lease_id(record['owner'], key)
                    or record['phase'] not in {'acquiring', 'active', 'releasing', 'terminal'}):
                raise ValueError
            if record.get('receipt') is not None:
                _receipt(record['receipt'], record)
            return record
        except (ValueError, TypeError, KeyError, RecursionError):
            raise AssemblyError('Resource-session journal is invalid; recover it before retrying') from None

    async def _state(self, identity, owner=None):
        state = await self.lifecycle.status(identity['node_id'])
        if (not isinstance(state, dict) or state.get('node_id') != identity['node_id']
                or not _matches(IDENT, state.get('owner')) or not isinstance(state.get('instances'), dict)
                or owner is not None and state['owner'] != owner):
            raise ResourceStateUnavailable('Resource-session state belongs to another owner or node')
        return state

    @staticmethod
    def _consumer(record, state):
        recipe, identity = record['recipe'], record['recipe']['consumer']
        instance = state['instances'].get(identity['instance_id'])
        if instance is None:
            return 'terminal'
        _instance(instance)
        if (recipe['preparation_id'] and instance.get('digest') == identity['revision']
                and instance.get('generation') == identity['generation'] - 1
                and instance.get('state') == 'prepared'
                and instance.get('start_preparation_id') == recipe['preparation_id']):
            return 'pending'
        if instance['generation'] < identity['generation']:
            # A stale/rolled-back inventory cannot prove this consumer ended.
            return 'unknown'
        if instance.get('digest') != identity['revision'] or instance.get('generation') != identity['generation']:
            return 'terminal'
        if instance.get('state') in {'stopped', 'removed'}:
            return 'terminal'
        return 'active' if instance.get('state') in LIVE else 'unknown'

    @staticmethod
    def _provider(record, state):
        recipe, identity = record['recipe'], record['recipe']['provider']
        instance = state['instances'].get(identity['instance_id'])
        if instance is None:
            return False
        _instance(instance)
        if instance['generation'] < identity['generation']:
            raise ResourceStateUnavailable('Resource-session provider generation is not current')
        if not _matches(NAME, instance.get('app_id')):
            raise ResourceStateUnavailable('Resource-session provider state is incomplete')
        return (instance.get('app_id') == recipe['app_id'] and instance.get('digest') == identity['revision']
                and instance.get('generation') == identity['generation'] and instance.get('state') not in {'stopped', 'removed'})

    async def _invoke(self, record, action):
        recipe = record['recipe']
        args = {'owner_ref': recipe['owner_ref'], 'lease_id': record['lease_id']}
        if action in {'acquire', 'renew'}:
            args['ttl_seconds'] = 900
        if action == 'acquire':
            args['kind'] = recipe['kind']
        value = await self.lifecycle.resource_session(recipe['app_id'], recipe['provider'], 'resource_session_' + action, args)
        receipt = _receipt(value, record)
        if receipt['state'] == 'active' and not time.time() - 30 <= receipt['expires'] <= time.time() + 930:
            raise AssemblyError('Provider returned an invalid resource-session lifetime')
        record['receipt'] = receipt
        if receipt['state'] in TERMINAL:
            record['phase'] = 'terminal'
        elif record['phase'] != 'releasing':
            record['phase'] = 'active'
        return receipt

    async def acquire(self, *, consumer, operation_id, owner_ref, provider, app_id, kind, preparation_id=''):
        recipe = _recipe(dict(consumer=consumer, preparation_id=preparation_id, operation_id=operation_id,
                              owner_ref=owner_ref, provider=provider, app_id=app_id, kind=kind))
        self._root()
        path = self.root / (_key(recipe['consumer'], operation_id) + '.json')
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                record = self._read(path)
                if record['recipe'] != recipe:
                    raise AssemblyError('Resource-session operation belongs to a different recipe')
            else:
                if sum(1 for _ in self.root.glob('*.json')) >= 4096:
                    raise AssemblyError('Resource-session journal is full; retain old intents during recovery')
                state = await self._state(recipe['consumer'])
                record = {'protocol': 1, 'recipe': recipe, 'owner': state['owner'],
                          'lease_id': _lease_id(state['owner'], path.stem), 'phase': 'acquiring', 'receipt': None}
                if self._consumer(record, state) not in {'pending', 'active'}:
                    raise AssemblyError('Resource session requires a live or exact prepared consumer')
                provided = await self.lifecycle.manifest(recipe['provider']['node_id'], recipe['provider']['revision'])
                manifest = provided['manifest']
                if manifest.get('id') != app_id or manifest.get('apiVersion') != 2:
                    raise AssemblyError('Resource session requires the declared provider App')
                methods = {name: {'arguments': ['owner_ref', 'lease_id'] +
                    (['kind', 'ttl_seconds'] if name.endswith('_acquire') else ['ttl_seconds'] if name.endswith('_renew') else []),
                    'bound': {}} for name in ('resource_session_acquire', 'resource_session_get', 'resource_session_renew', 'resource_session_release')}
                _methods({'uses': ['resource-session@1']}, manifest, methods)
                # Write intent before the first provider mutation. Cancellation
                # or lost reply must never generate a fresh acquisition ID.
                await self._checkpoint(path, record)
            if record['phase'] == 'terminal':
                return _copy(record)
            state = await self._state(recipe['consumer'], record['owner'])
            if self._consumer(record, state) not in {'pending', 'active'}:
                raise AssemblyError('Original consumer is unavailable; inspect or release its session')
            provider_state = await self._state(recipe['provider'], record['owner'])
            if not self._provider(record, provider_state):
                raise AssemblyError('Original provider is unavailable; no replacement session was created')
            if record['phase'] == 'releasing':
                raise AssemblyError('Resource session is being released')
            await self._invoke(record, 'acquire' if record['phase'] == 'acquiring' else 'get')
            await self._checkpoint(path, record)
            return _copy(record)

    async def release(self, *, consumer, operation_id):
        consumer = _copy(consumer)
        self._root()
        path = self.root / (_key(consumer, operation_id) + '.json')
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            record = self._read(path)
            if record['recipe']['consumer'] != consumer:
                raise AssemblyError('Use the original consumer identity to release its session')
            if record['phase'] == 'terminal':
                return _copy(record)
            record['phase'] = 'releasing'
            await self._checkpoint(path, record)
            await self._maintain(path, record, release=True)
            return _copy(record)

    async def inspect(self, *, consumer, operation_id):
        consumer = _copy(consumer)
        self._root()
        path = self.root / (_key(consumer, operation_id) + '.json')
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            record = self._read(path)
            if record['recipe']['consumer'] != consumer:
                raise AssemblyError('Use the original resource-session consumer identity')
            return _copy(record)

    async def _maintain(self, path, record, release=False):
        recipe = record['recipe']
        can_renew = False
        if not release and record['phase'] != 'releasing':
            state = await self._state(recipe['consumer'], record['owner'])
            consumer_state = self._consumer(record, state)
            can_renew = consumer_state == 'active'
            if consumer_state == 'unknown':
                return 'deferred'
            if consumer_state == 'terminal':
                record['phase'] = 'releasing'
                await self._checkpoint(path, record)
        provider = await self._state(recipe['provider'], record['owner'])
        if not self._provider(record, provider):
            # The binding was replaced/stopped. Never call its replacement and
            # never claim the previous remote resource has been cleaned up.
            record['phase'], record['reason'] = 'terminal', 'provider_unavailable'
            await self._checkpoint(path, record)
            return 'lost'
        if record['phase'] == 'releasing':
            await self._invoke(record, 'release')
            outcome = 'released' if record['phase'] == 'terminal' else 'deferred'
        else:
            # Query before renewal. A stale locally stored expiry after a lost
            # acknowledgement is not evidence that the provider lease expired.
            receipt = await self._invoke(record, 'get')
            outcome = 'terminal' if record['phase'] == 'terminal' else 'observed'
            if can_renew and receipt['state'] == 'active' and receipt['expires'] <= time.time() + 300:
                await self._invoke(record, 'renew')
                outcome = 'renewed' if record['phase'] == 'active' else 'terminal'
        await self._checkpoint(path, record)
        return outcome

    async def reconcile_once(self):
        totals = dict(observed=0, renewed=0, released=0, terminal=0, lost=0, deferred=0, invalid=0)
        if not self.root.exists():
            return totals
        self._private(self.root, directory=True)
        for path in sorted(self.root.glob('*.json')):
            try:
                with registry_lock(path.with_suffix('.lock'), timeout=0):
                    record = self._read(path)
                    if record['phase'] == 'terminal':
                        continue
                    totals[await self._maintain(path, record)] += 1
            except ResourceStateUnavailable:
                totals['deferred'] += 1
            except (AssemblyError, ValueError, TypeError, KeyError):
                totals['invalid'] += 1
            except Exception:
                totals['deferred'] += 1
        return totals
