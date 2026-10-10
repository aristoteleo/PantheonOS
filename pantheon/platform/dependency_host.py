"""Managed, prepared-config host for the owner-side dependency service.

Only bounded allocation/retirement is registered with the ordinary App HTTP host. Its
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
from pantheon.utils.registry_lock import registry_lock


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
                    or set(spec) - {'protocol', 'policies', 'trust_roots_pem', 'rpc_origin'}
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
            # Written by the Runner when it carries an instance's data into a
            # new revision (clone_data / import_data).
            self._carried = (Path(data_dir) / '.fleet-data-source.json').is_file()
            sessions = ResourceSessionOwner(self.lifecycle, self.root / 'sessions')
            self.owner = LiveDependencyOwner(self.lifecycle, self.root / 'bindings', sessions,
                DependencyAuthority(credential=configuration.credentials['hub'], tls_context=tls_context,
                                    rpc_origin=spec.get('rpc_origin')))
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
        self._close_lock = asyncio.Lock()
        self._reconcile_on_close = False
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
                recorded = json.loads(identity.read_text())
                if recorded != self.identity:
                    # Data the Runner carried into this new revision (it wrote
                    # the receipt) is this owner's; earlier grants were bound to
                    # the old instance and reconciliation revokes them.
                    if recorded.get('owner') != self.identity['owner'] or not self._carried:
                        raise AssemblyError('Dependency owner data belongs to another deployment')
                    await self.owner._checkpoint(identity, self.identity)
            else:
                await self.owner._checkpoint(identity, self.identity)
            await self.lifecycle.connect()
            self._reconcile_on_close = True
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
        async with self._close_lock:
            await self._close()

    async def _close(self):
        # Drain admitted operations before releasing the connection/writer. A
        # caller timeout does not prove that a remote mutation was cancelled.
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)
        for task in self._maintenance:
            task.cancel()
        await asyncio.gather(*self._maintenance, return_exceptions=True)
        self._maintenance.clear()
        if self._reconcile_on_close:
            # The stop coordinator drains consumers before this App. Reconcile
            # their durable receipts while we still hold the writer and owner
            # connection, rather than waiting for the next 30-second sweep.
            # An allocator-only restart must preserve healthy consumers: use
            # authoritative generation/state checks, not blanket retirement.
            incomplete = []
            for name, coordinator in (('bindings', self.owner), ('sessions', self.sessions)):
                try:
                    status = await coordinator.reconcile_once()
                except Exception:
                    status = {'deferred': 1}
                self.maintenance_status[name] = status
                if status.get('deferred', 0) or status.get('invalid', 0):
                    incomplete.append(name)
            if incomplete:
                # The ordinary App drain endpoint reports failure and permits
                # retry. Keep authority and exclusive ownership until the
                # original journaled operations can be observed/reconciled.
                raise AssemblyError('Dependency owner shutdown requires recovery: ' + ', '.join(incomplete))
            self._reconcile_on_close = False
        try:
            await self.lifecycle.close()
        finally:
            if self._lock is not None:
                self._lock.__exit__(None, None, None)
                self._lock = None
        # Live consumer receipts remain for the replacement within their TTL.

    async def retire_dependencies(self, *, policy_id, owner_ref):
        if not self._accepting:
            raise AssemblyError('Dependency owner is not accepting retirement requests')
        task = asyncio.current_task()
        self._active.add(task)
        try:
            return await self.service.retire_dependencies(policy_id=policy_id, owner_ref=owner_ref)
        finally:
            self._active.discard(task)


async def register(ctx):
    # The ordinary host enforces the Runner's per-generation RPC credential.
    # Loopback alone is insufficient for a service holding owner authority.
    ctx.require_rpc_token = True
    host = DependencyBindingHost(configuration=load_runtime_configuration(required=True),
                                 data_dir=ctx.state_dir)
    await host.start()
    ctx.method(host.bind_dependencies)
    ctx.method(host.retire_dependencies)
    ctx.concurrent_methods.update({'bind_dependencies', 'retire_dependencies'})
    # Runner must observe completed reconciliation before stopping the process.
    # A failed drain leaves the same host/journals available for a retry.
    ctx.before_stop = host.close
    ctx.on_cleanup(host.close)
