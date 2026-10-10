"""Owner-side assembly of declared, exact-generation App dependencies.

No Agent imports and no provider discovery/fallback. The caller supplies already
installed providers and an already prepared consumer. A private durable attempt
preserves exact grants/configuration across lost replies; the same operation ID
never changes its recipe. Owner maintenance renews only the issued grants for
that generation. Session creation and cross-replica coordination are separate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from pantheon.utils.registry_lock import registry_lock
from pantheon.apps.owner_journal import OwnerJournal, OwnerJournalError


class AssemblyError(OwnerJournalError):
    """Safe to show: never contains upstream response bodies or credentials."""


class DependencyAuthorizationError(AssemblyError):
    def __init__(self, status):
        super().__init__('Dependency authorization unavailable; inspect its owner and instance state')
        self.status = status


NAME = r'[a-z0-9][a-z0-9_-]{0,79}'
IDENT = r'[A-Za-z0-9_-]{1,100}'
RPC = r'[A-Za-z][A-Za-z0-9_]{0,127}'
ARGUMENT = r'[A-Za-z_][A-Za-z0-9_]{0,127}'
DIGEST = r'[a-f0-9]{64}'
# Configuration carries complete tool schemas, whereas grants carry only
# method/argument rules. Keep their bounds separate (grant RPCs remain 64 KiB).
CONFIGURATION_BYTES = 128 * 1024
DEPLOYMENT_BYTES = 512 * 1024


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
            # App schema.Interface defaults omitted versions to 1. Assembly
            # reads the immutable raw manifest, not a Pydantic-normalized copy.
            key = (interface['name'], interface.get('version', 1))
            if key in interfaces or type(key[1]) is not int or key[1] < 1:
                raise ValueError
            interfaces[key] = interface['tools']
        for tool in provides.get('tools', []):
            if tool['name'] in tools:
                raise ValueError
            params = tool.get('params', [])
            names = [p['name'] for p in params]
            if len(set(names)) != len(names) or not all(_matches(ARGUMENT, n) for n in names):
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


def _binding_phase(dependency):
    if (not isinstance(dependency, dict) or dependency.keys() - {'range', 'uses', 'binding'}
            or dependency.get('binding', 'startup') not in ('startup', 'runtime')):
        raise AssemblyError('Dependency must declare a supported binding phase and interface contract')
    return dependency.get('binding', 'startup')


async def compile_contract(installed, bindings, components, provider_manifest):
    """Validate immutable configuration/dependency declarations without reserving Apps.

    The caller resolves explicit provider references. This same contract is used
    by read-only deployment review and the authoritative prepared-start path.
    No credentials are read and no grants or lifecycle operations are issued.
    """
    if not isinstance(bindings, dict) or len(bindings) > 16:
        raise AssemblyError('Supply explicit dependency bindings')
    manifest, definition = installed['manifest'], installed['definition']
    if manifest.get('apiVersion') != 2:
        raise AssemblyError('Dependency assembly requires an App manifest v2')
    dependencies = manifest.get('dependencies', {})
    if not isinstance(dependencies, dict):
        raise AssemblyError('Invalid App dependency declarations')
    startup = {name for name, dep in dependencies.items() if _binding_phase(dep) == 'startup'}
    declared = {c['name']: c.get('configuration') for c in definition['components'] if c.get('configuration')}
    configs = _copy(components, CONFIGURATION_BYTES)
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
    rules, seen = {}, set()
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
        artifact = await provider_manifest(provider)
        provided = artifact['manifest']
        dependency = dependencies[app_id]
        if _binding_phase(dependency) != 'startup':
            raise AssemblyError('Runtime dependency must be allocated under its live owner policy')
        if (provided.get('id') != app_id or provided.get('apiVersion') != 2
                or not _compatible(provided.get('version'), dependency.get('range', '*'))):
            raise AssemblyError('Provider version does not match the installed consumer declaration')
        rules[alias] = _methods(dependency, provided, binding['methods'])
        seen.add(app_id)
    if seen != startup:
        raise AssemblyError('Every startup dependency requires an explicit binding')
    for component, declaration in declared.items():
        provided = set(configs[component].get('credentials', {})) | {
            alias for alias, binding in bindings.items() if binding['component'] == component}
        if any(field.get('required') and key not in provided
               for key, field in declaration.get('credentials', {}).items()):
            raise AssemblyError('Missing required App credential input')
    return {'components': configs, 'methods': rules}


async def compile_assembly(lifecycle, consumer, preparation_id, bindings, components):
    _identity(consumer)
    if not _matches(NAME, preparation_id) or not isinstance(bindings, dict) or len(bindings) > 16:
        raise AssemblyError('Use a prepared consumer with explicit dependency bindings')
    state = await lifecycle.status(consumer['node_id'])
    in_ = state.get('instances', {}).get(consumer['instance_id'], {})
    if (in_.get('digest') != consumer['revision'] or in_.get('generation') != consumer['generation']
            or in_.get('state') != 'prepared' or in_.get('start_preparation_id') != preparation_id
            or not _matches(IDENT, state.get('owner')) or state.get('node_id') != consumer['node_id']
            or state.get('dependency_config_protocol') != 1):
        raise AssemblyError('Consumer is not the exact prepared start or Fleet needs an update')
    installed = await lifecycle.manifest(consumer['node_id'], consumer['revision'])

    async def provider_manifest(provider):
        _identity(provider, provider=True)
        return await lifecycle.manifest(provider['node_id'], provider['revision'])

    contract = await compile_contract(installed, bindings, components, provider_manifest)
    requests = {}
    for alias, binding in bindings.items():
        operation = hashlib.sha256(json.dumps(
            [consumer, preparation_id, alias], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        requests[alias] = {
            'operation_id': 'binding-' + operation,
            'consumer': {**consumer, 'generation': consumer['generation'] + 1},
            'preparation_id': preparation_id, 'provider': binding['provider'], 'app_id': binding['app_id'],
            'methods': contract['methods'][alias], 'ttl_seconds': 900, 'timeout_seconds': 60,
        }
    return {'owner': state['owner'], 'scope': in_['scope'], 'requests': requests,
            'components': contract['components']}


class DependencyAuthority:
    """Only the owner coordinator holds the issuer credential, never its consumer."""
    def __init__(self, *, credential=None, tls_context=None, rpc_origin=None):
        # Omitted credentials retain the legacy platform composition. Explicit
        # prepared Apps never fall back to a process-wide key or endpoint.
        self._credential = credential
        self._tls_context = tls_context
        if rpc_origin is not None:
            match = re.fullmatch(r'https://127\.0\.0\.1:([1-9][0-9]{0,4})', rpc_origin) if isinstance(rpc_origin, str) else None
            if (not match or int(match[1]) > 65535 or credential is None
                    or credential.endpoint != rpc_origin or tls_context is None):
                raise AssemblyError('Local RPC requires an explicit loopback issuer and private TLS trust')
        self.rpc_origin = rpc_origin

    async def issue(self, body):
        return await self._request('POST', '', body)

    async def renew(self, grant_id, ttl_seconds=900):
        if not _matches(DIGEST, grant_id) or type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 900:
            raise AssemblyError('Invalid dependency renewal')
        return await self._request('PATCH', '/' + grant_id, {'ttl_seconds': ttl_seconds})

    async def revoke(self, grant_id):
        if not _matches(DIGEST, grant_id):
            raise AssemblyError('Invalid dependency revocation')
        await self._request('DELETE', '/' + grant_id, None)

    async def _request(self, method, suffix, body):
        import httpx
        if self._credential is None:
            hub, token = os.getenv('PANTHEON_HUB_URL', '').rstrip('/'), os.getenv('FLEET_KEY', '')
        else:
            hub, token = self._credential.endpoint.rstrip('/'), self._credential.key
        parts = urlsplit(hub)
        if not token or parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise AssemblyError('Connect the platform to HTTPS Hub and Fleet before binding dependencies')
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False,
                                         verify=self._tls_context or True) as client:
                async with client.stream(method, hub + '/api/fleet/apps/dependency-grants' + suffix, json=body,
                                         headers={'Authorization': 'Bearer ' + token}) as response:
                    if method == 'DELETE' and response.status_code == 204:
                        return None
                    if response.status_code != 200:
                        raise DependencyAuthorizationError(response.status_code)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 64 * 1024:
                            raise AssemblyError('Invalid dependency authority response')
                    return json.loads(data)
        except (httpx.HTTPError, ValueError):
            raise AssemblyError('Dependency authority unavailable; retry the same attempt') from None


def _grant(value, request, owner, *, rpc_origin=None):
    try:
        expected_consumer = {**request['consumer'], 'fleet_id': owner}
        expected_provider = {**request['provider'], 'fleet_id': owner}
        endpoint = urlsplit(value['endpoint'])
        provider = request['provider']
        prefix = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
        bound_origin = endpoint.hostname and endpoint.hostname.startswith(prefix + '.') and endpoint.port is None
        if rpc_origin is not None:
            match = re.fullmatch(r'https://127\.0\.0\.1:([1-9][0-9]{0,4})', rpc_origin)
            if not match or int(match[1]) > 65535:
                raise ValueError
            bound_origin = bound_origin or value['endpoint'] == rpc_origin + '/rpc'
        if (set(value) != {'endpoint', 'access_token', 'grant_id', 'expires', 'consumer', 'provider'}
                or value['consumer'] != expected_consumer or value['provider'] != expected_provider
                or not _matches(DIGEST, value['access_token'])
                or value['grant_id'] != hashlib.sha256(value['access_token'].encode()).hexdigest()
                or type(value['expires']) is not int or not time.time() < value['expires'] <= time.time() + 900
                or endpoint.scheme != 'https' or not bound_origin
                or endpoint.path != '/rpc' or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password
                or '?' in value['endpoint'] or '#' in value['endpoint']):
            raise ValueError
        return _copy(value)
    except (TypeError, KeyError, AttributeError, ValueError):
        raise AssemblyError('Invalid or expired dependency credential; inspect its original binding before retrying') from None


class DependencyStarter(OwnerJournal):
    """Single-platform-owner journal. Not a cross-replica storage lock.

    The root must be a private local directory. Windows consumers are supported;
    the current POSIX platform coordinator does not provision Windows ACLs.
    """
    error_type = AssemblyError
    maximum_bytes = DEPLOYMENT_BYTES
    def __init__(self, lifecycle, root: Path, authority=None):
        self.lifecycle, self.root = lifecycle, Path(root)
        self.authority = authority or DependencyAuthority()

    def _grant(self, value, request, owner):
        origin = self.authority.rpc_origin if isinstance(self.authority, DependencyAuthority) else None
        return _grant(value, request, owner, rpc_origin=origin)

    async def start(self, *, consumer, preparation_id, operation_id, bindings, components):
        # Snapshot every caller-owned input before the first await.
        recipe = _copy(dict(consumer=consumer, preparation_id=preparation_id,
                            operation_id=operation_id, bindings=bindings, components=components), DEPLOYMENT_BYTES)
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
                    raw = file.read(self.maximum_bytes + 1)
                if len(raw) > self.maximum_bytes:
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
                    record['grants'][alias] = self._grant(grant, grant_request, plan['owner'])
                    await self._checkpoint(path, record)
                else:
                    self._grant(record['grants'][alias], grant_request, plan['owner'])
            config = _copy(plan['components'], CONFIGURATION_BYTES)
            for alias, grant in record['grants'].items():
                config[bindings[alias]['component']]['dependencies'][alias] = grant
            # compile_contract verified the immutable manifest: an empty map is
            # valid only when no component declares configuration. Such Apps
            # still consume the exact prepared identity through the normal start.
            if config:
                await self.lifecycle.configure(consumer['node_id'], instance_id=consumer['instance_id'],
                    revision=consumer['revision'], generation=consumer['generation'], preparation_id=preparation_id,
                    components=config)
            record['phase'] = 'submitting'
            record['renewals'] = {alias: {key: value for key, value in grant.items()
                                         if key in {'grant_id', 'expires', 'consumer', 'provider'}}
                                  for alias, grant in record['grants'].items()}
            # Node configuration is now immutable and durable. Retain only the
            # exact public start intent, not expired bearer keys, for retries.
            record['grants'] = {}
            await self._checkpoint(path, record)
            operation = await self.lifecycle.submit(consumer['node_id'], 'start', consumer['revision'],
                scope=plan['scope'], generation=consumer['generation'], operation_id=operation_id,
                start_preparation_id=preparation_id)
            return {'operation': operation, 'dependencies': len(bindings)}

    async def reconcile_once(self):
        """Maintain configured generations, without replaying a start or a tool.

        Called periodically by the platform owner, independently of GUI windows
        and Agent execution. A read failure is deferred; only an authoritative
        terminal/replaced consumer is revoked. An expired grant is never reissued.
        This journal uses the same per-attempt local lock as initial assembly.
        """
        totals = dict(renewed=0, revoked=0, expired=0, deferred=0, invalid=0)
        if not self.root.exists():
            return totals
        self._private(self.root, directory=True)
        for path in sorted(self.root.glob('*.json')):
            try:
                with registry_lock(path.with_suffix('.lock'), timeout=0):
                    self._private(path)
                    with path.open('rb') as file:
                        raw = file.read(256 * 1024 + 1)
                    if len(raw) > 256 * 1024:
                        raise AssemblyError('Invalid dependency maintenance record')
                    record = json.loads(raw)
                    if record.get('protocol') != 1:
                        continue
                    live = record.get('mode') == 'live'
                    if record.get('phase') not in ({'binding', 'bound'} if live else {'submitting'}):
                        continue
                    renewals = record.get('renewals', {})
                    if not renewals or all(g.get('state') in ('expired', 'revoked') for g in renewals.values()):
                        continue
                    await self._maintain_record(path, record, totals)
            except TimeoutError:
                totals['deferred'] += 1  # Another owner operation holds this attempt.
            except Exception:
                # Do not publish private paths, config values or transport text.
                totals['invalid'] += 1
        return totals

    async def _maintain_record(self, path, record, totals):
        recipe, plan, receipts = record['recipe'], record['plan'], record['renewals']
        consumer = recipe['consumer']
        _identity(consumer)
        live = record.get('mode') == 'live'
        if record.get('mode') not in (None, 'live') or live and recipe['preparation_id']:
            raise AssemblyError('Invalid dependency maintenance mode')
        expected = {**consumer, 'generation': consumer['generation'] + (0 if live else 1), 'fleet_id': plan['owner']}
        # Validate public receipts against the immutable start plan before using
        # grant IDs for owner-level operations. Never accept a substituted owner.
        for alias, receipt in receipts.items():
            provider = {**plan['requests'][alias]['provider'], 'fleet_id': plan['owner']}
            if (receipt.get('consumer') != expected or receipt.get('provider') != provider
                    or not _matches(DIGEST, receipt.get('grant_id')) or type(receipt.get('expires')) is not int
                    or receipt.get('state') not in (None, 'active', 'expired', 'revoked')):
                raise AssemblyError('Invalid dependency maintenance receipt')
        try:
            state = await self.lifecycle.status(consumer['node_id'])
            if state.get('owner') != plan['owner'] or state.get('node_id') != consumer['node_id'] or not isinstance(state.get('instances'), dict):
                raise AssemblyError('Dependency state belongs to another owner or node')
        except Exception:
            totals['deferred'] += 1
            return
        instance = state['instances'].get(consumer['instance_id'])
        if instance is not None and (not isinstance(instance, dict)
                or not _matches(DIGEST, instance.get('digest'))
                or type(instance.get('generation')) is not int or instance['generation'] <= 0
                or not isinstance(instance.get('state'), str) or not instance['state']):
            totals['deferred'] += 1
            return
        waiting = (not live and instance is not None and instance.get('digest') == consumer['revision']
                   and instance.get('generation') == consumer['generation'] and instance.get('state') == 'prepared'
                   and instance.get('start_preparation_id') == recipe['preparation_id'])
        if instance is not None and not waiting and instance['generation'] < expected['generation']:
            # An older or rolled-back inventory cannot prove this generation
            # stopped. Authority checks still prevent use of a dead consumer.
            totals['deferred'] += 1
            return
        exact = (instance is not None and instance.get('digest') == expected['revision']
                 and instance.get('generation') == expected['generation'])
        terminal = not waiting and (not exact or instance.get('state') in ('stopped', 'failed', 'removed'))
        changed = False
        for receipt in receipts.values():
            if receipt.get('state') in ('expired', 'revoked'):
                continue
            if terminal:
                try:
                    await self.authority.revoke(receipt['grant_id'])
                except Exception:
                    totals['deferred'] += 1
                    continue
                receipt['state'] = 'revoked'
                totals['revoked'] += 1
                changed = True
            elif receipt['expires'] <= time.time() + (0 if waiting else 300):
                try:
                    # The stored expiry can be stale after a lost renewal ack.
                    # Ask the authority about the same grant; it cannot revive
                    # an actually expired one, and we never mint a replacement.
                    value = await self.authority.renew(receipt['grant_id'], ttl_seconds=900)
                    if (not isinstance(value, dict) or set(value) != {'grant_id', 'expires', 'consumer', 'provider'}
                            or any(value[key] != receipt[key] for key in ('grant_id', 'consumer', 'provider'))
                            or type(value['expires']) is not int
                            or not max(receipt['expires'], time.time() + 30) <= value['expires'] <= time.time() + 900):
                        raise AssemblyError('Invalid dependency renewal response')
                except DependencyAuthorizationError as exc:
                    if exc.status == 410:
                        receipt['state'] = 'expired'
                        totals['expired'] += 1
                        changed = True
                    else:
                        totals['deferred'] += 1
                    continue
                except Exception:
                    totals['deferred'] += 1
                    continue
                receipt['expires'], receipt['state'] = value['expires'], 'active'
                totals['renewed'] += 1
                changed = True
        if changed:
            await self._checkpoint(path, record)
