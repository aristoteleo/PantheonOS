"""Owner-side assembly of declared, exact-generation App dependencies.

No Agent imports and no provider discovery/fallback. The caller supplies already
installed providers and an already prepared consumer. A private durable attempt
preserves exact grants/configuration across lost replies; the same operation ID
never changes its recipe. This is initial binding, not session or grant renewal.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit

from pantheon.platform.registry_lock import registry_lock


class AssemblyError(RuntimeError):
    """Safe to show: never contains upstream response bodies or credentials."""


NAME = r'[a-z0-9][a-z0-9_-]{0,79}'
IDENT = r'[A-Za-z0-9_-]{1,100}'
RPC = r'[A-Za-z][A-Za-z0-9_]{0,127}'
DIGEST = r'[a-f0-9]{64}'


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _copy(value, limit=64 * 1024):
    try:
        raw = json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':'))
        if len(raw.encode()) > limit:
            raise ValueError
        return json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise AssemblyError('Invalid or oversized dependency assembly') from None


def _identity(value, provider=False):
    fields = {'node_id', 'instance_id', 'revision', 'generation'}
    if provider:
        fields |= {'component', 'port'}
    if (not isinstance(value, dict) or set(value) != fields
            or not all(_matches(IDENT, value.get(k)) for k in ('node_id', 'instance_id'))
            or not _matches(DIGEST, value.get('revision'))
            or type(value.get('generation')) is not int or not 0 < value['generation'] < 2**63-1
            or provider and (value['component'] != 'backend' or value['port'] != 'http')):
        raise AssemblyError('Use an exact App instance binding')


def _version(value):
    # Deliberately reject unsupported syntax rather than interpreting it as 0.0.0.
    if not _matches(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise AssemblyError('Dependency assembly requires a stable three-part version')
    return tuple(int(v) for v in value.split('.'))


def _compatible(version, constraint):
    actual = _version(version)
    if constraint == '*':
        return True
    if not isinstance(constraint, str):
        raise AssemblyError('Invalid dependency version range')
    prefix = next((p for p in ('>=', '^', '~') if constraint.startswith(p)), '')
    minimum = _version(constraint[len(prefix):])
    if not prefix:
        return actual == minimum
    if prefix == '>=':
        return actual >= minimum
    if prefix == '~':
        maximum = (minimum[0], minimum[1] + 1, 0)
    elif minimum[0]:
        maximum = (minimum[0] + 1, 0, 0)
    elif minimum[1]:
        maximum = (0, minimum[1] + 1, 0)
    else:
        maximum = (0, 0, minimum[2] + 1)
    return minimum <= actual < maximum


def _methods(dependency, provider, requested):
    """Only methods from declared interface versions and their exact parameters."""
    provides = provider.get('provides', {})
    interfaces, tools = {}, {}
    try:
        for interface in provides.get('interfaces', []):
            key = (interface['name'], interface['version'])
            if key in interfaces or type(key[1]) is not int or key[1] < 1:
                raise ValueError
            interfaces[key] = interface['tools']
        for tool in provides.get('tools', []):
            if tool['name'] in tools:
                raise ValueError
            params = tool.get('params', [])
            names = [p['name'] for p in params]
            if len(set(names)) != len(names) or not all(_matches(RPC, n) for n in names):
                raise ValueError
            tools[tool['name']] = params
        allowed = set()
        uses = dependency.get('uses', [])
        if not uses or not isinstance(uses, list):
            raise ValueError
        for use in uses:
            name, version = use.rsplit('@', 1)
            if not re.fullmatch(r'[1-9][0-9]*', version):
                raise ValueError
            allowed.update(interfaces[(name, int(version))])
        if not isinstance(requested, dict) or not 1 <= len(requested) <= 64 or not requested.keys() <= allowed:
            raise ValueError
        for method, rule in requested.items():
            if not _matches(RPC, method) or set(rule) != {'arguments', 'bound'}:
                raise ValueError
            params = tools[method]
            arguments, bound = rule['arguments'], rule['bound']
            if not isinstance(arguments, list) or not isinstance(bound, dict):
                raise ValueError
            selected = set(arguments) | bound.keys()
            if (len(set(arguments)) != len(arguments) or set(arguments) & bound.keys()
                    or not selected <= {p['name'] for p in params} or len(selected) > 64
                    or any(p.get('required', True) and p['name'] not in selected for p in params)):
                raise ValueError
        return _copy(requested)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AssemblyError('Provider does not satisfy the declared dependency interface or method arguments') from None


async def compile_assembly(lifecycle, consumer, preparation_id, bindings, components):
    _identity(consumer)
    if not _matches(NAME, preparation_id) or not isinstance(bindings, dict) or not 1 <= len(bindings) <= 16:
        raise AssemblyError('Use a prepared consumer with explicit dependency bindings')
    state = await lifecycle.status(consumer['node_id'])
    in_ = state.get('instances', {}).get(consumer['instance_id'], {})
    if (in_.get('digest') != consumer['revision'] or in_.get('generation') != consumer['generation']
            or in_.get('state') != 'prepared' or in_.get('start_preparation_id') != preparation_id
            or not _matches(IDENT, state.get('owner')) or state.get('node_id') != consumer['node_id']
            or state.get('dependency_config_protocol') != 1):
        raise AssemblyError('Consumer is not the exact prepared start or Fleet needs an update')
    installed = await lifecycle.manifest(consumer['node_id'], consumer['revision'])
    manifest, definition = installed['manifest'], installed['definition']
    if manifest.get('apiVersion') != 2:
        raise AssemblyError('Dependency assembly requires an App manifest v2')
    dependencies = manifest.get('dependencies', {})
    declared = {c['name']: c.get('configuration') for c in definition['components'] if c.get('configuration')}
    configs = _copy(components)
    if not isinstance(configs, dict) or set(configs) != set(declared):
        raise AssemblyError('Configure exactly the declared App components')
    for name, cfg in configs.items():
        if not isinstance(cfg, dict) or cfg.keys() - {'values', 'credentials'}:
            raise AssemblyError('Base configuration cannot contain dependency credentials')
        values, credentials = cfg.get('values', {}), cfg.get('credentials', {})
        decl = declared[name]
        if (not isinstance(values, dict) or not isinstance(credentials, dict)
                or values.keys() - decl.get('values', {}).keys()
                or credentials.keys() - decl.get('credentials', {}).keys()
                or any(field.get('required') and values.get(key) is None
                       for key, field in decl.get('values', {}).items())):
            raise AssemblyError('Missing or undeclared App configuration inputs')
        cfg['dependencies'] = {}
    requests, seen = {}, set()
    for alias, binding in bindings.items():
        if (not _matches(NAME, alias) or not isinstance(binding, dict)
                or set(binding) != {'app_id', 'component', 'provider', 'methods'}):
            raise AssemblyError('Invalid dependency binding')
        app_id, component = binding['app_id'], binding['component']
        if (app_id not in dependencies or component not in declared
                or alias not in declared[component].get('credentials', {})
                or alias in configs[component].get('credentials', {})):
            raise AssemblyError('Dependency alias is not a declared credential input')
        provider = binding['provider']
        _identity(provider, provider=True)
        artifact = await lifecycle.manifest(provider['node_id'], provider['revision'])
        provided = artifact['manifest']
        dependency = dependencies[app_id]
        if not isinstance(dependency, dict) or dependency.keys() - {'range', 'uses'}:
            raise AssemblyError('Dependency must declare an interface contract')
        if (provided.get('id') != app_id or provided.get('apiVersion') != 2
                or not _compatible(provided.get('version'), dependency.get('range', '*'))):
            raise AssemblyError('Provider version does not match the installed consumer declaration')
        requests[alias] = {
            'consumer': {**consumer, 'generation': consumer['generation'] + 1},
            'preparation_id': preparation_id, 'provider': provider, 'app_id': app_id,
            'methods': _methods(dependency, provided, binding['methods']),
            'ttl_seconds': 900, 'timeout_seconds': 60,
        }
        seen.add(app_id)
    if seen != dependencies.keys():
        raise AssemblyError('Every declared dependency requires an explicit binding')
    for component, declaration in declared.items():
        provided = set(configs[component].get('credentials', {})) | {
            alias for alias, binding in bindings.items() if binding['component'] == component}
        if any(field.get('required') and key not in provided
               for key, field in declaration.get('credentials', {}).items()):
            raise AssemblyError('Missing required App credential input')
    return {'owner': state['owner'], 'scope': in_['scope'], 'requests': requests, 'components': configs}


class DependencyAuthority:
    """Only the owner coordinator holds this Hub credential, never the App."""
    async def issue(self, body):
        import httpx
        hub, token = os.getenv('PANTHEON_HUB_URL', '').rstrip('/'), os.getenv('FLEET_KEY', '')
        parts = urlsplit(hub)
        if not token or parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise AssemblyError('Connect the platform to HTTPS Hub and Fleet before binding dependencies')
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
                async with client.stream('POST', hub + '/api/fleet/apps/dependency-grants', json=body,
                                         headers={'Authorization': 'Bearer ' + token}) as response:
                    if response.status_code != 200:
                        raise AssemblyError('Dependency authorization failed; check the selected provider and Hub/Fleet versions')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 64 * 1024:
                            raise AssemblyError('Invalid dependency authority response')
                    return json.loads(data)
        except (httpx.HTTPError, ValueError):
            raise AssemblyError('Dependency authority unavailable; retry the same attempt') from None


def _grant(value, request, owner):
    try:
        expected_consumer = {**request['consumer'], 'fleet_id': owner}
        expected_provider = {**request['provider'], 'fleet_id': owner}
        endpoint = urlsplit(value['endpoint'])
        provider = request['provider']
        prefix = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
        if (set(value) != {'endpoint', 'access_token', 'grant_id', 'expires', 'consumer', 'provider'}
                or value['consumer'] != expected_consumer or value['provider'] != expected_provider
                or not _matches(DIGEST, value['access_token'])
                or value['grant_id'] != hashlib.sha256(value['access_token'].encode()).hexdigest()
                or type(value['expires']) is not int or not time.time() < value['expires'] <= time.time() + 900
                or endpoint.scheme != 'https' or not endpoint.hostname or not endpoint.hostname.startswith(prefix + '.')
                or endpoint.path != '/rpc' or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password or endpoint.port):
            raise ValueError
        return _copy(value)
    except (TypeError, KeyError, AttributeError, ValueError):
        raise AssemblyError('Invalid or expired dependency credential; cancel preparation and create a new attempt') from None


class DependencyStarter:
    """Single-platform-owner journal. Not a cross-replica storage lock.

    The root must be a private local directory. Windows consumers are supported;
    the current POSIX platform coordinator does not provision Windows ACLs.
    """
    def __init__(self, lifecycle, root: Path, authority=None):
        self.lifecycle, self.root = lifecycle, Path(root)
        self.authority = authority or DependencyAuthority()

    def _private(self, path, directory=False):
        info = path.lstat()
        if (os.name != 'posix' or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))):
            raise AssemblyError('Dependency attempt storage must be private to the platform owner')

    def _write(self, path, value):
        raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        if len(raw) > 256 * 1024:
            raise AssemblyError('Dependency attempt exceeds storage limit')
        fd, name = tempfile.mkstemp(prefix=path.stem + '-', suffix='.tmp', dir=path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            tmp.unlink(missing_ok=True)

    async def _checkpoint(self, path, record):
        # Do not release the attempt lock while a cancelled observer's storage
        # thread is still publishing. A retry must see the durable checkpoint.
        task = asyncio.get_running_loop().run_in_executor(None, self._write, path, record)
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def start(self, *, consumer, preparation_id, operation_id, bindings, components):
        # Snapshot every caller-owned input before the first await.
        recipe = _copy(dict(consumer=consumer, preparation_id=preparation_id,
                            operation_id=operation_id, bindings=bindings, components=components))
        consumer, bindings, components = recipe['consumer'], recipe['bindings'], recipe['components']
        _identity(consumer)
        if not _matches(NAME, operation_id) or not _matches(NAME, preparation_id):
            raise AssemblyError('Use stable preparation and start operation IDs')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        # The node scopes operation IDs; include it to permit independent nodes.
        key = hashlib.sha256((consumer['node_id'] + '\0' + operation_id).encode()).hexdigest()
        path = self.root / (key + '.json')
        lockpath = self.root / (key + '.lock')
        # Immediate local lock acquisition never sleeps on the async RPC loop.
        with registry_lock(lockpath, timeout=0):
            if path.exists() or path.is_symlink():
                self._private(path)
                with path.open('rb') as file:
                    raw = file.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise AssemblyError('Dependency attempt record is invalid')
                record = json.loads(raw)
                if not isinstance(record, dict) or record.get('recipe') != recipe or record.get('protocol') != 1:
                    raise AssemblyError('Start operation already belongs to a different dependency recipe')
            else:
                plan = await compile_assembly(self.lifecycle, consumer, preparation_id, bindings, components)
                record = {'protocol': 1, 'recipe': recipe, 'plan': plan, 'grants': {}, 'phase': 'binding'}
                await self._checkpoint(path, record)
            plan = record['plan']
            request = dict(protocol=1, action='start', operation_id=operation_id, digest=consumer['revision'],
                           scope=plan['scope'], generation=consumer['generation'], start_preparation_id=preparation_id)
            state = await self.lifecycle.status(consumer['node_id'])
            if state.get('owner') != plan['owner']:
                raise AssemblyError('Dependency attempt belongs to another Fleet owner')
            operation = state.get('operations', {}).get(operation_id)
            if operation is not None:
                if operation.get('request') != request:
                    raise AssemblyError('Node operation ID belongs to another start request')
                return {'operation': operation, 'dependencies': len(bindings)}
            if record['phase'] == 'submitting':
                # The exact durable operation can be re-submitted. Never issue new
                # grants or configure a replacement generation after a lost ack.
                operation = await self.lifecycle.submit(consumer['node_id'], 'start', consumer['revision'],
                    scope=plan['scope'], generation=consumer['generation'], operation_id=operation_id,
                    start_preparation_id=preparation_id)
                return {'operation': operation, 'dependencies': len(bindings)}
            # Revalidate the exact manifests/consumer before issuing further grants.
            current = await compile_assembly(self.lifecycle, consumer, preparation_id, bindings, components)
            if current != plan:
                raise AssemblyError('Dependency start plan changed; create a new preparation')
            for alias, grant_request in plan['requests'].items():
                if alias not in record['grants']:
                    grant = await self.authority.issue(grant_request)
                    record['grants'][alias] = _grant(grant, grant_request, plan['owner'])
                    await self._checkpoint(path, record)
                else:
                    _grant(record['grants'][alias], grant_request, plan['owner'])
            config = _copy(plan['components'])
            for alias, grant in record['grants'].items():
                config[bindings[alias]['component']]['dependencies'][alias] = grant
            await self.lifecycle.configure(consumer['node_id'], instance_id=consumer['instance_id'],
                revision=consumer['revision'], generation=consumer['generation'], preparation_id=preparation_id,
                components=config)
            record['phase'] = 'submitting'
            # Node configuration is now immutable and durable. Retain only the
            # exact public start intent, not expired bearer keys, for retries.
            record['grants'] = {}
            await self._checkpoint(path, record)
            operation = await self.lifecycle.submit(consumer['node_id'], 'start', consumer['revision'],
                scope=plan['scope'], generation=consumer['generation'], operation_id=operation_id,
                start_preparation_id=preparation_id)
            return {'operation': operation, 'dependencies': len(bindings)}
