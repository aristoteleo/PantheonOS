"""Managed, prepared-config host for the owner-side dependency service.

Only bind_dependencies is registered with the ordinary App HTTP host. Its
gateway grant MUST bind policy_id. Owner credentials are deliberately confined
to this trusted platform component; they are never Agent App configuration.
"""
import asyncio
import json
import ssl
from pathlib import Path
from collections.abc import Mapping

from pantheon.apps.dependency_assembly import AssemblyError, DependencyAuthority
from pantheon.apps.dependency_binding_service import DependencyBindingService
from pantheon.apps.live_dependencies import LiveDependencyOwner
from pantheon.apps.resource_sessions import ResourceSessionOwner
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.platform.dependency_control import OwnerDependencyLifecycle, owner_endpoint
from pantheon.platform.registry_lock import registry_lock


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class DependencyBindingHost:
    def __init__(self, *, configuration, data_dir, tls_context=None):
        try:
            spec = _plain(configuration.values['dependency_binding'])
            if (configuration.component != 'backend' or not configuration.owner
                    or set(configuration.values) != {'dependency_binding'}
                    or set(configuration.credentials) != {'hub', 'controller'}
                    or not {'protocol', 'policies'} <= set(spec)
                    or set(spec) - {'protocol', 'policies', 'trust_roots_pem'}
                    or type(spec['protocol']) is not int or spec['protocol'] != 1):
                raise ValueError
            if 'trust_roots_pem' in spec:
                pem = spec['trust_roots_pem']
                if not isinstance(pem, str) or not pem or len(pem) > 16384 or tls_context is not None:
                    raise ValueError
                tls_context = ssl.create_default_context(cadata=pem)
            owner_endpoint(configuration.credentials['hub'])
            self.lifecycle = OwnerDependencyLifecycle(owner=configuration.owner,
                credential=configuration.credentials['controller'], tls_context=tls_context)
            self.root = Path(data_dir) / 'dependency-owner'
            sessions = ResourceSessionOwner(self.lifecycle, self.root / 'sessions')
            self.owner = LiveDependencyOwner(self.lifecycle, self.root / 'bindings', sessions,
                DependencyAuthority(credential=configuration.credentials['hub'], tls_context=tls_context))
            self.service = DependencyBindingService(self.owner, policies=spec['policies'])
        except (KeyError, ValueError, TypeError, AttributeError, ssl.SSLError):
            raise AssemblyError('Invalid dependency owner configuration') from None
        self.sessions = sessions
        self.identity = {'owner': configuration.owner, 'instance_id': configuration.instance_id}
        self._lock = None
        self._maintenance = []
        self._active = set()
        self._accepting = False
        self._closed = False
        self.maintenance_status = {}

    async def start(self):
        if self._accepting or self._closed or self._lock is not None:
            raise AssemblyError('Dependency owner cannot start twice')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.owner._private(self.root, directory=True)
        lock = registry_lock(self.root / 'owner.lock', timeout=0)
        try:
            lock.__enter__()
        except TimeoutError:
            raise AssemblyError('Dependency owner data already has a writer') from None
        self._lock = lock
        try:
            identity = self.root / 'owner.json'
            if identity.exists():
                self.owner._private(identity)
                if json.loads(identity.read_text()) != self.identity:
                    raise AssemblyError('Dependency owner data belongs to another deployment')
            else:
                await self.owner._checkpoint(identity, self.identity)
            await self.lifecycle.connect()
            self._accepting = True
            for name, coordinator in (('bindings', self.owner), ('sessions', self.sessions)):
                self._maintenance.append(asyncio.create_task(self._maintain(name, coordinator)))
        except BaseException:
            await self.close()
            raise

    async def _maintain(self, name, coordinator):
        while True:
            try:
                self.maintenance_status[name] = await coordinator.reconcile_once()
            except Exception:
                self.maintenance_status[name] = {'deferred': 1}
            await asyncio.sleep(30)

    async def bind_dependencies(self, *, policy_id, owner_ref, operation_id, aliases):
        if not self._accepting:
            raise AssemblyError('Dependency owner is not accepting allocations')
        task = asyncio.current_task()
        self._active.add(task)
        try:
            return await self.service.bind_dependencies(policy_id=policy_id, owner_ref=owner_ref,
                                                       operation_id=operation_id, aliases=aliases)
        finally:
            self._active.discard(task)

    async def close(self):
        self._accepting = False
        self._closed = True
        # Drain admitted operations before releasing the connection/writer. A
        # caller timeout does not prove that a remote mutation was cancelled.
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)
        for task in self._maintenance:
            task.cancel()
        await asyncio.gather(*self._maintenance, return_exceptions=True)
        self._maintenance.clear()
        try:
            await self.lifecycle.close()
        finally:
            if self._lock is not None:
                self._lock.__exit__(None, None, None)
                self._lock = None
        # A planned owner restart does not revoke live consumer grants. Their
        # durable receipts are reconciled by the replacement within their TTL.


async def register(ctx):
    # The ordinary host enforces the Runner's per-generation RPC credential.
    # Loopback alone is insufficient for a service holding owner authority.
    ctx.require_rpc_token = True
    host = DependencyBindingHost(configuration=load_runtime_configuration(required=True),
                                 data_dir=ctx.state_dir)
    await host.start()
    ctx.method(host.bind_dependencies)
    ctx.concurrent_methods.add('bind_dependencies')
    ctx.on_cleanup(host.close)
