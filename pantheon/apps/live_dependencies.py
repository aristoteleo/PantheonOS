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
    AssemblyError, DependencyStarter, IDENT, NAME, RPC, _compatible, _copy,
    _grant, _identity, _matches, _methods,
)
from pantheon.apps.resource_sessions import ResourceSessionOwner, LIVE, _instance
from pantheon.platform.registry_lock import registry_lock


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
            provider = binding['provider']
            provided = (await self.lifecycle.manifest(provider['node_id'], provider['revision']))['manifest']
            if (not isinstance(dependency, dict) or dependency.keys() - {'range', 'uses'}
                    or provided.get('apiVersion') != 2 or provided.get('id') != binding['app_id']
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
                grant = _grant(await self.authority.issue(request), request, plan['owner'])
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


class ScopedDependencyBindings:
    """Trusted composition capability with fixed placement/permission policy.

    A remote facade must authenticate and inject this capability's consumer.
    This local wrapper deliberately accepts no credentials, URL, provider,
    workspace path, methods or bound arguments from its caller.
    """
    def __init__(self, owner: LiveDependencyOwner, *, consumer, bindings):
        self._owner = owner
        self._consumer, self._bindings = _copy(consumer), _binding_policy(bindings)
        _identity(self._consumer)

    async def bind(self, *, owner_ref, operation_id, aliases):
        if (not isinstance(aliases, list) or not aliases or len(aliases) > 16
                or not all(isinstance(name, str) for name in aliases)
                or len(set(aliases)) != len(aliases) or not set(aliases) <= self._bindings.keys()):
            raise AssemblyError('Select only the approved dependency aliases')
        return await self._owner.bind(consumer=self._consumer, owner_ref=owner_ref, operation_id=operation_id,
                                      bindings={key: self._bindings[key] for key in sorted(aliases)})
