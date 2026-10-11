"""Owner-side dependency binding for logical resources in a running App.

The generic owner API takes exact installed bindings. A scoped capability can
pin that policy for a consumer, exposing only logical owner/operation IDs and
approved aliases. It is a composition boundary, not an HTTP authentication layer;
do not expose the owner object or Fleet credentials to App/template code.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pantheon.apps.dependency_assembly import (
    AssemblyError, DependencyAuthorizationError, DependencyStarter, IDENT, NAME, RPC, DIGEST, _compatible, _copy,
    _identity, _matches, _methods, _binding_phase,
)
from pantheon.apps.resource_sessions import ResourceSessionOwner, LIVE, _instance
from pantheon.utils.registry_lock import registry_lock


def _digest(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _binding_policy(bindings):
    bindings = _copy(bindings)
    if not isinstance(bindings, dict) or not 1 <= len(bindings) <= 16:
        raise AssemblyError('Select explicit dependency bindings')
    for alias, binding in bindings.items():
        if (not _matches(NAME, alias) or not isinstance(binding, dict)
                or not {'app_id', 'provider', 'methods'} <= binding.keys()
                or binding.keys() - {'app_id', 'provider', 'methods', 'resource'}
                or not _matches(NAME, binding['app_id']) or not isinstance(binding['methods'], dict)):
            raise AssemblyError('Invalid live dependency policy')
        _identity(binding['provider'], provider=True)
        if 'resource' in binding:
            resource = binding['resource']
            if (not isinstance(resource, dict) or set(resource) != {'kind', 'arguments'}
                    or not _matches(IDENT, resource['kind']) or not isinstance(resource['arguments'], dict)
                    or not resource['arguments'] or not resource['arguments'].keys() <= binding['methods'].keys()):
                raise AssemblyError('Invalid resource-session binding')
            for method, argument in resource['arguments'].items():
                rule = binding['methods'][method]
                if (not _matches(RPC, argument) or not isinstance(rule, dict)
                        or not isinstance(rule.get('bound'), dict) or not isinstance(rule.get('arguments'), list)
                        or argument in rule['bound'] or argument in rule['arguments']):
                    raise AssemblyError('Resource arguments must be supplied only by the owner')
    return bindings


class LiveDependencyOwner(DependencyStarter):
    """Platform-owned journals and leases; never an Agent execution dependency.

    Revisions use separate binding operation IDs but a stable logical owner and
    dependency alias reuse their original provider session. Retiring a logical
    owner requires draining all its revisions before explicit session release.
    This coordinator does not infer that retirement from a closed GUI or caller.
    """
    def __init__(self, lifecycle, root: Path, sessions: ResourceSessionOwner, authority=None):
        super().__init__(lifecycle, root, authority)
        self.sessions = sessions

    async def _live(self, consumer, owner=None):
        state = await self.sessions._state(consumer, owner)
        instance = state['instances'].get(consumer['instance_id'])
        _instance(instance)
        if (instance['digest'] != consumer['revision'] or instance['generation'] != consumer['generation']
                or instance['state'] not in LIVE - {'draining', 'stop_blocked'}):
            raise AssemblyError('Dependency binding requires the exact live consumer')
        return state

    async def _plan(self, consumer, owner_ref, operation_id, bindings):
        state = await self._live(consumer)
        installed = await self.lifecycle.manifest(consumer['node_id'], consumer['revision'])
        manifest = installed['manifest']
        if manifest.get('apiVersion') != 2 or not isinstance(manifest.get('dependencies'), dict):
            raise AssemblyError('Live bindings require declared App dependencies')
        requests, sessions = {}, {}
        for alias, binding in bindings.items():
            dependency = manifest['dependencies'].get(binding['app_id'])
            _binding_phase(dependency)
            provider = binding['provider']
            provided = (await self.lifecycle.manifest(provider['node_id'], provider['revision']))['manifest']
            if (provided.get('apiVersion') != 2 or provided.get('id') != binding['app_id']
                    or not _compatible(provided.get('version'), dependency.get('range', '*'))):
                raise AssemblyError('Provider does not match the consumer dependency declaration')
            methods = _copy(binding['methods'])
            resource = binding.get('resource')
            if resource:
                for method, argument in resource['arguments'].items():
                    methods[method]['bound'][argument] = '<pending-resource-session>'
                # Acquisition is independently journaled with a stable logical
                # owner key, not a config-revision key. A changed provider/kind
                # conflicts with that original resource, never silently replaces it.
                sessions[alias] = dict(consumer=consumer, operation_id='resource-' + _digest(consumer, owner_ref, alias),
                    owner_ref=_digest(state['owner'], consumer, owner_ref), provider=provider,
                    app_id=binding['app_id'], kind=resource['kind'], preparation_id='')
            requests[alias] = dict(operation_id='live-' + _digest(consumer, operation_id, alias),
                consumer=consumer, provider=provider, app_id=binding['app_id'],
                methods=_methods(dependency, provided, methods), ttl_seconds=900, timeout_seconds=60)
        return {'owner': state['owner'], 'requests': requests, 'sessions': sessions}

    async def bind(self, *, consumer, owner_ref, operation_id, bindings):
        consumer = _copy(consumer)
        path = self._owner_path(consumer, owner_ref)
        # Serialize every revision of one logical owner, including retirement.
        # The durable marker prevents revival after this process restarts.
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                raise AssemblyError('This logical dependency owner is retiring or retired')
            return await self._bind(consumer=consumer, owner_ref=owner_ref,
                                    operation_id=operation_id, bindings=bindings)

    def _owner_path(self, consumer, owner_ref):
        _identity(consumer)
        if not _matches(IDENT, owner_ref):
            raise AssemblyError('Use the original logical owner identity')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        directory = self.root / 'retirements'
        directory.mkdir(mode=0o700, exist_ok=True)
        self._private(directory, directory=True)
        return directory / (_digest(consumer, owner_ref) + '.json')

    def _read_private(self, path):
        self._private(path)
        with path.open('rb') as file:
            raw = file.read(256 * 1024 + 1)
        try:
            if len(raw) > 256 * 1024:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError):
            raise AssemblyError('Invalid logical-owner journal; explicit recovery required') from None

    async def _bind(self, *, consumer, owner_ref, operation_id, bindings):
        consumer, bindings = _copy(consumer), _binding_policy(bindings)
        _identity(consumer)
        if not _matches(IDENT, owner_ref) or not _matches(NAME, operation_id):
            raise AssemblyError('Use stable logical owner and binding operation IDs')
        recipe = dict(consumer=consumer, owner_ref=owner_ref, operation_id=operation_id,
                      bindings=bindings, preparation_id='')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        path = self.root / (_digest(consumer, operation_id) + '.json')
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                self._private(path)
                with path.open('rb') as file:
                    raw = file.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise AssemblyError('Invalid live dependency record')
                try:
                    record = json.loads(raw)
                    if (record['protocol'] != 1 or record['mode'] != 'live'
                            or record['phase'] not in {'binding', 'bound'} or record['recipe'] != recipe):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise AssemblyError('Binding operation belongs to another recipe or needs recovery') from None
            else:
                if sum(1 for _ in self.root.glob('*.json')) >= 4096:
                    raise AssemblyError('Live dependency journal is full')
                plan = await self._plan(consumer, owner_ref, operation_id, bindings)
                record = dict(protocol=1, mode='live', recipe=recipe, plan=plan,
                              phase='binding', renewals={})
                await self._checkpoint(path, record)
            plan = record['plan']
            await self._live(consumer, plan['owner'])
            # Acquire/observe all sessions before issuing grants. An unknown
            # acquire uses the same durable lease ID on explicit retry.
            for alias, session_recipe in plan['sessions'].items():
                resource = await self.sessions.acquire(**session_recipe)
                receipt = resource['receipt']
                if receipt is None or receipt['state'] != 'active':
                    raise AssemblyError('Original resource session is unavailable; explicit recovery required')
                for method, argument in bindings[alias]['resource']['arguments'].items():
                    current = plan['requests'][alias]['methods'][method]['bound'][argument]
                    if current not in ('<pending-resource-session>', receipt['session_id']):
                        raise AssemblyError('Original resource session identity changed')
                    plan['requests'][alias]['methods'][method]['bound'][argument] = receipt['session_id']
                await self._checkpoint(path, record)
            delivered = {}
            for alias, request in plan['requests'].items():
                # Replaying issuance recovers the same bearer/current expiry,
                # including after delivery or renewal acknowledgment was lost.
                grant = self._grant(await self.authority.issue(request), request, plan['owner'])
                previous = record['renewals'].get(alias)
                if previous and (previous['grant_id'] != grant['grant_id']
                                 or previous.get('state') in ('expired', 'revoked')):
                    raise AssemblyError('Original binding cannot be replaced or revived')
                record['renewals'][alias] = {k: v for k, v in grant.items()
                                             if k in {'grant_id', 'consumer', 'provider', 'expires'}}
                await self._checkpoint(path, record)
                delivered[alias] = grant
            await self._live(consumer, plan['owner'])
            record['phase'] = 'bound'
            await self._checkpoint(path, record)
            # Credentials are private RPC output only, never a public inventory
            # result or durable owner receipt. The gateway retains their secrets.
            return {'protocol': 1, 'owner_ref': owner_ref, 'operation_id': operation_id,
                    'consumer': consumer, 'bindings': delivered}

    async def retire(self, *, consumer, owner_ref):
        """Fence all revisions, revoke admission, then release owned sessions.

        The consumer must drain its accepted Runs before requesting retirement.
        Provider release may still report pending work; such a receipt stays
        retiring. Shared providers are never stopped. A terminal lost/expired
        receipt is reported as such, not as confirmed resource cleanup.
        """
        consumer = _copy(consumer)
        path = self._owner_path(consumer, owner_ref)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                result = self._read_private(path)
                if (set(result) != {'protocol', 'consumer', 'owner_ref', 'state', 'resources'}
                        or type(result['protocol']) is not int or result['protocol'] != 1
                        or result['consumer'] != consumer or result['owner_ref'] != owner_ref
                        or result['state'] not in {'retiring', 'retired'}
                        or not isinstance(result['resources'], dict) or len(result['resources']) > 4096
                        or any(not isinstance(k, str) or not isinstance(v, str)
                               or v not in {'active', 'closing', 'unknown', 'unallocated', 'released', 'expired', 'lost', 'failed'}
                               for k, v in result['resources'].items())
                        or result['state'] == 'retired' and any(v in {'active', 'closing', 'unknown'}
                                                              for v in result['resources'].values())):
                    raise AssemblyError('Invalid logical-owner retirement')
                if result['state'] == 'retired':
                    return result
            else:
                if sum(1 for _ in path.parent.glob('*.json')) >= 4096:
                    raise AssemblyError('Logical-owner retirement journal is full')
                result = dict(protocol=1, consumer=consumer, owner_ref=owner_ref,
                              state='retiring', resources={})
                # Persist before the first external mutation, even for an owner
                # whose acquisition has not yet started or was never delivered.
                await self._checkpoint(path, result)

            records = []
            for binding_path in sorted(self.root.glob('*.json')):
                record = self._read_private(binding_path)
                recipe = record.get('recipe', {})
                if recipe.get('consumer') != consumer or recipe.get('owner_ref') != owner_ref:
                    continue
                with registry_lock(binding_path.with_suffix('.lock'), timeout=0):
                    record = self._read_private(binding_path)
                    self._validate_retirement_binding(binding_path, record, consumer, owner_ref)
                    record['phase'] = 'retiring'
                    await self._checkpoint(binding_path, record)
                    records.append(binding_path)

            sessions = {}
            # Revoke every revision before releasing any resource. A failed or
            # lost acknowledgement leaves the owner fenced for explicit retry
            # and maintenance; never infer success from a transport timeout.
            for binding_path in records:
                with registry_lock(binding_path.with_suffix('.lock'), timeout=0):
                    record = self._read_private(binding_path)
                    plan = record['plan']
                    sessions.update({r['operation_id']: r for r in plan['sessions'].values()})
                    # Grants cannot have been issued until every session ID was
                    # durably installed in the plan. Partial acquisition has no
                    # grant to recover, but still owns any acquired resources.
                    resolved = not any(value == '<pending-resource-session>'
                        for request in plan['requests'].values()
                        for rule in request['methods'].values() for value in rule['bound'].values())
                    for alias, request in plan['requests'].items():
                        if alias in record.get('terminal_issuance', []):
                            continue
                        receipt = record['renewals'].get(alias)
                        if receipt is None and resolved:
                            try:
                                grant = self._grant(await self.authority.issue(request), request, plan['owner'])
                            except DependencyAuthorizationError as exc:
                                if exc.status != 410:
                                    raise
                                # The original issuance operation is terminal;
                                # it cannot be replaced under this stable ID.
                                record.setdefault('terminal_issuance', []).append(alias)
                                await self._checkpoint(binding_path, record)
                                continue
                            receipt = record['renewals'][alias] = {k: v for k, v in grant.items()
                                if k in {'grant_id', 'consumer', 'provider', 'expires'}}
                            await self._checkpoint(binding_path, record)
                        if receipt is not None and receipt.get('state') not in {'revoked', 'expired'}:
                            await self.authority.revoke(receipt['grant_id'])
                            receipt['state'] = 'revoked'
                            await self._checkpoint(binding_path, record)
            for operation, recipe in sessions.items():
                try:
                    released = await self.sessions.release(consumer=consumer, operation_id=operation)
                except FileNotFoundError:
                    # acquire journals before contacting the provider. Holding
                    # the logical-owner lock proves it cannot now begin.
                    result['resources'][operation] = 'unallocated'
                else:
                    receipt = released.get('receipt')
                    result['resources'][operation] = (
                        'lost' if released.get('reason') == 'provider_unavailable' else
                        receipt['state'] if receipt else 'unknown')
                await self._checkpoint(path, result)
            if all(state in {'unallocated', 'released', 'expired', 'lost', 'failed'}
                   for state in result['resources'].values()):
                result['state'] = 'retired'
                await self._checkpoint(path, result)
            return _copy(result)

    def _validate_retirement_binding(self, path, record, consumer, owner_ref):
        try:
            recipe, plan = record['recipe'], record['plan']
            if (record['protocol'] != 1 or record['mode'] != 'live'
                    or record['phase'] not in {'binding', 'bound', 'retiring'}
                    or recipe['consumer'] != consumer or recipe['owner_ref'] != owner_ref
                    or path.stem != _digest(consumer, recipe['operation_id'])
                    or not _matches(IDENT, plan['owner'])
                    or not isinstance(record['renewals'], dict)):
                raise ValueError
            terminal = record.get('terminal_issuance', [])
            if (not isinstance(terminal, list) or any(not isinstance(alias, str) for alias in terminal)
                    or len(set(terminal)) != len(terminal) or not set(terminal) <= plan['requests'].keys()):
                raise ValueError
            for alias, request in plan['requests'].items():
                if (request['consumer'] != consumer or request['operation_id'] !=
                        'live-' + _digest(consumer, recipe['operation_id'], alias)):
                    raise ValueError
                _identity(request['provider'], provider=True)
            for alias, session in plan['sessions'].items():
                if (session['consumer'] != consumer or session['operation_id'] !=
                        'resource-' + _digest(consumer, owner_ref, alias)
                        or session['owner_ref'] != _digest(plan['owner'], consumer, owner_ref)):
                    raise ValueError
            for alias, receipt in record['renewals'].items():
                if (receipt['consumer'] != {**consumer, 'fleet_id': plan['owner']}
                        or receipt['provider'] != {**plan['requests'][alias]['provider'], 'fleet_id': plan['owner']}
                        or not _matches(DIGEST, receipt['grant_id'])
                        or receipt.get('state') not in {None, 'active', 'revoked', 'expired'}):
                    raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AssemblyError('Invalid dependency record; cannot retire its resources') from None

    async def reconcile_once(self):
        totals = await super().reconcile_once()
        totals['retired'] = 0
        for path in sorted((self.root / 'retirements').glob('*.json')):
            try:
                record = self._read_private(path)
                if path != self._owner_path(record['consumer'], record['owner_ref']):
                    raise AssemblyError('Invalid retirement identity')
                if record['state'] == 'retired':
                    continue
                value = await self.retire(consumer=record['consumer'], owner_ref=record['owner_ref'])
                totals['retired' if value['state'] == 'retired' else 'deferred'] += 1
            except Exception:
                totals['deferred'] += 1
        return totals


class ScopedDependencyBindings:
    """Trusted composition capability with fixed placement/permission policy.

    A remote facade must authenticate and inject this capability's consumer.
    This local wrapper deliberately accepts no credentials, URL, provider,
    workspace path, methods or bound arguments from its caller.
    """
    def __init__(self, owner: LiveDependencyOwner, *, consumer, bindings, instances=None):
        from pantheon.apps.late_refs import has_late
        self._owner = owner
        # Late references (a Fleet deployment's Apps) resolve at each request
        # to the instances running then; the policy is otherwise unchanged.
        self._late = has_late(consumer) or has_late(bindings)
        if self._late and instances is None:
            raise AssemblyError('Late dependency references need the deployment status')
        self._instances = instances
        # A newly provisioned consumer may have no approved tools yet. Its
        # policy can start, but every allocation request still fails closed.
        self._consumer = _copy(consumer)
        self._raw_bindings = _copy(bindings)
        if self._late:
            self._bindings = dict(self._raw_bindings)
        else:
            self._bindings = {} if isinstance(bindings, dict) and not bindings else _binding_policy(bindings)
            _identity(self._consumer)

    async def _resolved(self, aliases=None):
        if not self._late:
            return self._consumer, self._bindings
        consumer = await self._instances.resolve(self._consumer)
        _identity(consumer)
        raw = self._raw_bindings if aliases is None else {k: self._raw_bindings[k] for k in aliases}
        return consumer, ({} if not raw else _binding_policy(await self._instances.resolve(raw)))

    async def bind(self, *, owner_ref, operation_id, aliases):
        if (not isinstance(aliases, list) or not aliases or len(aliases) > 16
                or not all(isinstance(name, str) for name in aliases)
                or len(set(aliases)) != len(aliases) or not set(aliases) <= self._bindings.keys()):
            raise AssemblyError('Select only the approved dependency aliases')
        consumer, bindings = await self._resolved(sorted(aliases))
        return await self._owner.bind(consumer=consumer, owner_ref=owner_ref, operation_id=operation_id,
                                      bindings={key: bindings[key] for key in sorted(aliases)})

    async def retire(self, *, owner_ref):
        consumer, _ = await self._resolved([])
        return await self._owner.retire(consumer=consumer, owner_ref=owner_ref)
