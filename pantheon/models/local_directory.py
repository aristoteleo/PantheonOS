"""Profile-owned publications for prepared local Model Service Connectors.

This is directory storage, not an engine or inference implementation. The
original prepared registration verifies the live Connector before publishing.
Consumers read through ModelServiceControl; only the local owner can write.
Attached and managed publications and ordinary model aliases use the existing
Hub wire contract and lifecycle validation. Group journals and automatic idle
wake require their separate local coordinators; they are not attached records.
No network endpoint, API key, raw engine config or global settings is stored.
"""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat

from pantheon.apps.owner_journal import OwnerJournal
from pantheon.utils.registry_lock import registry_lock
from .errors import ControlError
from pydantic import ValidationError
from pantheon.model_contracts.deployments import Deployment, normalize, validate_create, validate_update
from pantheon.model_contracts.errors import DirectoryError


ID = r'[a-z0-9][a-z0-9_-]{0,63}'
IDENT = r'[A-Za-z0-9_-]{1,100}'
OPERATIONS = {'text', 'embedding', 'rerank', 'speech', 'transcription', 'image', 'video'}
CAPABILITIES = {'tools', 'vision', 'reasoning', 'structured_output'}
MAX_REVISION = 9007199254740991


def _need(condition):
    if not condition:
        raise ControlError(400, 'Invalid local model directory record')


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _text(value, maximum, minimum=1):
    return isinstance(value, str) and minimum <= len(value) <= maximum


def _integer(value, minimum=0, maximum=MAX_REVISION):
    return type(value) is int and minimum <= value <= maximum


def _fields(value, required, optional=()):
    _need(isinstance(value, dict) and set(required) <= value.keys()
          and not value.keys() - set(required) - set(optional))


def _model(value):
    _fields(value, {'id'}, {'name', 'operations', 'context', 'context_limit', 'compute', *CAPABILITIES})
    model = dict(name='', operations=['text'], context=None, context_limit=None,
                 compute='unknown', **{key: None for key in CAPABILITIES}) | deepcopy(value)
    _need(_text(model['id'], 200) and _text(model['name'], 200, 0))
    _need(isinstance(model['operations'], list) and len(model['operations']) <= 7
          and all(isinstance(op, str) and op in OPERATIONS for op in model['operations']))
    _need(all(model[key] is None or type(model[key]) is bool for key in CAPABILITIES))
    _need(model['context'] is None or _integer(model['context'], 1))
    _need(model['context_limit'] is None or _integer(model['context_limit'], 512))
    _need(model['compute'] in ('node', 'provider', 'unknown'))
    return model


def _deployment(value):
    # Use the same schema and lifecycle invariants as Hub. Local serialization
    # stays strict and bounded; no endpoint, credential or unknown field is admitted.
    try:
        body = Deployment.model_validate(value, strict=True)
        row = {**normalize(body), 'revision': body.revision}
    except ValidationError:
        # Pydantic diagnostics include rejected input (possibly a credential).
        raise ControlError(400, 'Invalid local model directory record') from None
    except DirectoryError as exc:
        raise ControlError(exc.status, exc.detail) from None
    _need(not re.fullmatch(r'group-[a-f0-9]{24}', row['deployment_id']))
    _need(_integer(row['revision']))
    _need(row['config_revision'] == '' or _matches(r'[a-f0-9]{64}', row['config_revision']))
    for key in ('binding', 'engine_binding'):
        if row[key] is not None:
            _need(_integer(row[key]['generation'], 1))
    row['models'] = [_model(model) for model in row['models']]
    _need(len({model['id'] for model in row['models']}) == len(row['models']))
    try:
        size = len(json.dumps(row, allow_nan=False))
    except (ValueError, TypeError):
        raise ControlError(400, 'Invalid local model directory record') from None
    if size > 512 * 1024:
        raise ControlError(413)
    return row


def _requirements(value):
    _fields(value, set(), {'operation', 'tools', 'vision', 'structured_output', 'context'})
    result = dict(operation='text', tools=False, vision=False, structured_output=False, context=0) | value
    _need(isinstance(result['operation'], str) and result['operation'] in OPERATIONS
          and _integer(result['context'], 0, 1048576)
          and all(type(result[key]) is bool for key in ('tools', 'vision', 'structured_output')))
    return result


def _route(value):
    _fields(value, {'route_id', 'name', 'candidates', 'allowed_nodes'},
            {'allowed_compute', 'allowed_billing', 'transport', 'fallback', 'selection', 'requires', 'revision'})
    row = dict(allowed_compute=['node'], allowed_billing=['local'], transport='relay_allowed',
               fallback='none', selection='ordered', requires={}, revision=0) | deepcopy(value)
    _need(_matches(ID, row['route_id']) and _text(row['name'], 120) and _integer(row['revision']))
    _need(isinstance(row['candidates'], list) and 1 <= len(row['candidates']) <= 16)
    for candidate in row['candidates']:
        _fields(candidate, {'deployment_id', 'model_id'})
        _need(_matches(ID, candidate['deployment_id']) and _text(candidate['model_id'], 200))
    _need(len({(c['deployment_id'], c['model_id']) for c in row['candidates']}) == len(row['candidates']))
    _need(isinstance(row['allowed_nodes'], list) and 1 <= len(row['allowed_nodes']) <= 16
          and all(_text(node, 100) for node in row['allowed_nodes']))
    for field, values in (('allowed_compute', ('node', 'provider', 'unknown')),
                          ('allowed_billing', ('local', 'provider', 'unknown'))):
        _need(isinstance(row[field], list) and 1 <= len(row[field]) <= 3
              and all(item in values for item in row[field]))
    _need(row['transport'] in ('relay_allowed', 'direct_only') and row['fallback'] in ('none', 'preflight')
          and row['selection'] in ('ordered', 'ready_first'))
    row['requires'] = _requirements(row['requires'])
    return row


def _resolve(route, deployments, request):
    request = _requirements(request)
    candidates, excluded = [], []
    for c in route['candidates'] if route['fallback'] == 'preflight' else route['candidates'][:1]:
        row = deployments.get(c['deployment_id'])
        model = next((m for m in (row or {}).get('models', []) if m['id'] == c['model_id']), None)
        reason = ''
        compute = 'provider' if row and row['engine'] == 'api' else (model or {}).get('compute', 'unknown')
        billing = {'node': 'local', 'provider': 'provider'}.get(compute, 'unknown')
        if not row or not model:
            reason = 'model_not_published'
        elif row['node_id'] not in route['allowed_nodes']:
            reason = 'node_not_allowed'
        elif compute not in route['allowed_compute'] or billing not in route['allowed_billing']:
            reason = 'location_or_billing_not_allowed'
        elif row['state'] != 'ready' or not row['binding']:
            reason = 'service_not_ready'
        elif route['requires']['operation'] != request['operation'] or request['operation'] not in model['operations']:
            reason = 'operation_not_supported'
        else:
            for key in ('tools', 'vision', 'structured_output'):
                if (route['requires'][key] or request[key]) and model[key] is not True:
                    reason = key + '_unconfirmed'
                    break
            if not reason and max(route['requires']['context'], request['context']) > (model['context'] or 0):
                reason = 'context_unconfirmed_or_insufficient'
        if reason:
            excluded.append({**c, 'reason': reason})
        else:
            candidates.append({'deployment': {**row, 'models': [model]}, 'model': model,
                               'compute': compute, 'billing': billing})
    return {'route': route, 'candidates': candidates, 'excluded': excluded,
            'transport': 'fleet_direct' if route['transport'] == 'direct_only' else 'fleet_relay',
            'resolved': bool(candidates)}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate directory field')
        value[key] = item
    return value


class LocalModelDirectory(OwnerJournal):
    """Atomic local publication journal, owner-pinned across process restarts.

    Writers share a stable file lock and compare revisions inside that lock.
    Readers see a whole atomic snapshot and never create a missing directory.
    Tombstones prevent a deleted/recreated alias from reusing an old revision.
    This coordinates one local filesystem, not replicas of a cloud volume.
    """
    maximum_bytes = 4 * 1024 * 1024

    def __init__(self, root, *, owner, read_only=False):
        _need(_matches(IDENT, owner) and type(read_only) is bool)
        super().__init__(Path(root))
        _need(self.root.is_absolute())
        self.owner, self.read_only = owner, read_only

    def _load(self):
        self._private(self.root, directory=True)
        path = self.root / 'directory.json'
        # A missing snapshot after creation is an error, never an empty catalog.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                  and not info.st_mode & 0o077 and info.st_size <= self.maximum_bytes)
            raw = stream.read(self.maximum_bytes + 1)
        _need(len(raw) <= self.maximum_bytes)
        record = json.loads(raw, object_pairs_hook=_unique)
        _fields(record, {'protocol', 'owner', 'deployments', 'routes'})
        _need(type(record['protocol']) is int and record['protocol'] == 1 and record['owner'] == self.owner)
        for kind, validate, key in (('deployments', _deployment, 'deployment_id'), ('routes', _route, 'route_id')):
            items = record[kind]
            _need(isinstance(items, dict))
            for identity, entry in items.items():
                _fields(entry, {'revision', 'value'})
                _need(_matches(ID, identity) and _integer(entry['revision'], 1))
                if entry['value'] is not None:
                    value = validate(entry['value'])
                    _need(value[key] == identity and value['revision'] == entry['revision'])
                    entry['value'] = value
        return record

    async def initialize(self):
        if self.read_only:
            raise ControlError(403)
        await self._run(self._initialize)

    def _initialize(self):
        created = False
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
        except FileExistsError:
            pass
        self._private(self.root, directory=True)
        with registry_lock(self.root / 'directory.lock'):
            if not (self.root / 'directory.json').is_symlink() and not (self.root / 'directory.json').exists():
                if not created:
                    raise ControlError(503, 'Local model directory is missing; restore its existing snapshot')
                self._write(self.root / 'directory.json', dict(protocol=1, owner=self.owner, deployments={}, routes={}))
            self._load()

    async def _run(self, call, *args):
        # Cancellation must not leave a write racing a restart/retry.
        task = asyncio.create_task(asyncio.to_thread(call, *args))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            # Retrieve an error too; the caller still has an uncertain outcome.
            try:
                task.result()
            finally:
                raise asyncio.CancelledError
        return task.result()

    async def deployments(self):
        return (await self.hub_request('GET', '/api/model-services'))['deployments']

    async def routes(self):
        return (await self.hub_request('GET', '/api/model-services/routes'))['routes']

    async def deployment(self, deployment_id):
        _need(_matches(ID, deployment_id))
        row = next((r for r in await self.deployments() if r['deployment_id'] == deployment_id), None)
        if row is None:
            raise ControlError(404)
        return row

    async def save(self, row):
        row = _deployment(row)
        return await self.hub_request('PUT', '/api/model-services/' + row['deployment_id'], row)

    async def remove(self, deployment_id, revision):
        _need(_matches(ID, deployment_id))
        return await self.hub_request('DELETE', '/api/model-services/' + deployment_id, {'revision': revision})

    async def hub_request(self, method, path, data=None):
        # Freeze caller-owned values before another thread examines them.
        data = deepcopy(data)
        write = method in ('PUT', 'DELETE')
        if write and self.read_only:
            raise ControlError(403)
        return await self._run(self._request, method, path, data, write)

    def _request(self, method, path, data, write):
        if not write:
            return self._dispatch(self._load(), method, path, data)
        self._private(self.root, directory=True)
        with registry_lock(self.root / 'directory.lock'):
            record = self._load()
            result = self._dispatch(record, method, path, data)
            self._write(self.root / 'directory.json', record)
            return result

    def _dispatch(self, record, method, path, data):
        rows = {k: v['value'] for k, v in record['deployments'].items() if v['value'] is not None}
        routes = {k: v['value'] for k, v in record['routes'].items() if v['value'] is not None}
        if method == 'GET' and data is None:
            if path == '/api/model-services': return {'deployments': list(rows.values())}
            if path == '/api/model-services/routes': return {'routes': list(routes.values())}
        resolved = re.fullmatch('/api/model-services/routes/(' + ID + ')/resolve', path)
        if resolved and method == 'POST':
            route = routes.get(resolved[1])
            if route is None: raise ControlError(404)
            return _resolve(route, rows, data)
        match = re.fullmatch('/api/model-services/(routes/)?(' + ID + ')', path)
        if not match or method not in ('PUT', 'DELETE'):
            raise ControlError(403)
        kind, key, maximum = ('routes', 'route_id', 128) if match[1] else ('deployments', 'deployment_id', 64)
        identity, table = match[2], record[kind]
        previous = table.get(identity)
        existing = previous and previous['value']
        if method == 'PUT':
            value = _route(data) if kind == 'routes' else _deployment(data)
            _need(value[key] == identity)
            if (value['revision'] == 0 and existing is not None
                    or value['revision'] != 0 and (existing is None or value['revision'] != previous['revision'])):
                raise ControlError(409)
            if existing is None and sum(entry['value'] is not None for entry in table.values()) >= maximum:
                raise ControlError(409)
            if kind == 'routes':
                for candidate in value['candidates']:
                    row = rows.get(candidate['deployment_id'])
                    _need(row is not None and any(m['id'] == candidate['model_id'] for m in row['models']))
            else:
                try:
                    body = Deployment.model_validate(value, strict=True)
                    if existing is None:
                        validate_create(body)
                    else:
                        validate_update(existing, body, normalize(body))
                except DirectoryError as exc:
                    raise ControlError(exc.status, exc.detail) from None
            revision = previous['revision'] + 1 if previous else 1
            _need(_integer(revision, 1))
            value['revision'] = revision
            table[identity] = {'revision': revision, 'value': value}
            return value
        _fields(data, {'revision'})
        _need(_integer(data['revision'], 1))
        if existing is None or previous['revision'] != data['revision']:
            raise ControlError(409)
        if kind == 'deployments':
            if (existing['state'] not in ('stopped', 'draft')
                    or any(existing.get(k) for k in ('recovery', 'connector_update', 'engine_update', 'operation_stop'))
                    or any(
                    any(c['deployment_id'] == identity for c in route['candidates']) for route in routes.values())):
                raise ControlError(409)
        table[identity] = {'revision': previous['revision'], 'value': None}
        return {'deleted': True} if kind == 'routes' else {'removed': identity}
