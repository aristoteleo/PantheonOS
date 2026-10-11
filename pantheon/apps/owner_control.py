"""Explicit owner connection used by the managed dependency allocator.

This is platform infrastructure, not an Agent tool or a consumer SDK. No node
discovery, default-node placement, process environment or development fallback.
The controller join must return the Fleet owner from the prepared configuration.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from pantheon.apps.client import AppClient
from pantheon.apps.dependency_assembly import AssemblyError, IDENT, _matches
from pantheon.apps.lifecycle import FleetLifecycle


def owner_endpoint(credential):
    try:
        parts = urlsplit(credential.endpoint)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or not credential.key):
            raise ValueError
        return credential.endpoint.rstrip('/')
    except (ValueError, AttributeError):
        raise AssemblyError('Supply an explicit HTTPS owner credential') from None


class OwnerDependencyLifecycle(FleetLifecycle):
    """Read exact instances and control resource sessions over owner NATS.

    FleetLifecycle still validates manifest and resource-session responses. The
    node checks the provider generation and its owner membership. This transport
    does not install/start Apps and is not exposed as an RPC method itself.
    """
    def __init__(self, *, owner, credential, tls_context=None):
        super().__init__(None)
        if not _matches(IDENT, owner):
            raise AssemblyError('Supply the prepared Fleet owner identity')
        self.owner, self.credential = owner, credential
        self.endpoint = owner_endpoint(credential)
        self.tls_context = tls_context
        self._connection = None
        self._credentials_path = None
        self._connection_lock = asyncio.Lock()
        self._closed = False

    async def _disconnect(self):
        connection, self._connection = self._connection, None
        try:
            if connection is not None:
                await connection.close()
        finally:
            if self._credentials_path is not None:
                self._credentials_path.unlink(missing_ok=True)
                self._credentials_path = None

    async def connect(self):
        import httpx
        import nats
        async with self._connection_lock:
            if self._closed:
                raise AssemblyError('Dependency owner connection is closed')
            if self._connection is not None and self._connection.is_connected:
                return AppClient(self._connection, self.owner)
            await self._disconnect()
            try:
                async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False,
                                             verify=self.tls_context or True) as client:
                    async with client.stream('POST', self.endpoint + '/join',
                                             json={'key': self.credential.key}) as response:
                        if response.status_code != 200:
                            raise ValueError
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 64 * 1024:
                                raise ValueError
                        joined = json.loads(raw)
                address = urlsplit(joined['nats_url'])
                if (joined['fleet_id'] != self.owner or not joined.get('creds')
                        or not isinstance(joined['creds'], str)
                        or address.scheme not in {'nats', 'tls', 'ws', 'wss'}
                        or not address.hostname or address.username or address.password
                        or address.query or address.fragment):
                    raise ValueError
                fd, name = tempfile.mkstemp(prefix='pantheon-dependency-owner-', suffix='.creds')
                self._credentials_path = Path(name)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(joined['creds'])
                # Retain the connection before awaiting so failed/cancelled
                # authentication also closes its socket and background tasks.
                self._connection = nats.NATS()
                await self._connection.connect(
                    servers=[joined['nats_url']], user_credentials=name,
                    inbox_prefix=b'_INBOX_' + self.owner.encode(),
                    name='pantheon-dependency-owner', connect_timeout=5,
                    allow_reconnect=False, tls=self.tls_context)
                return AppClient(self._connection, self.owner)
            except BaseException as exc:
                await self._disconnect()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise AssemblyError('Dependency owner could not join its configured Fleet') from None

    async def _request(self, node_id, method, **data):
        if not _matches(IDENT, node_id) or method not in {'status', 'app_manifest', 'invoke'}:
            raise AssemblyError('Unsupported dependency owner operation')
        if method == 'invoke' and data.get('payload', {}).get('method') not in {
                'resource_session_acquire', 'resource_session_get',
                'resource_session_renew', 'resource_session_release'}:
            raise AssemblyError('Only resource-session control is allowed')
        client = await self.connect()
        try:
            result = await client.lifecycle(node_id, method, **data)
            if not isinstance(result, dict) or result.get('error'):
                raise ValueError
            return result
        except Exception:
            # Never replay an outcome-unknown mutation under a new operation.
            raise AssemblyError('Dependency node did not acknowledge the original operation') from None

    async def close(self):
        async with self._connection_lock:
            self._closed = True
            await self._disconnect()


class OwnerCredentialLifecycle(OwnerDependencyLifecycle):
    """Owner onboarding transport, separate from the allocator's operation set.

    Reuse authenticated owner join/cleanup; send only status and encrypted vault
    delivery. This object is never supplied to an Agent or App dependency.
    """
    async def _request(self, node_id, method, **data):
        if not _matches(IDENT, node_id) or method not in {'status', 'credential_prepare', 'credential_ensure'}:
            raise AssemblyError('Unsupported owner credential operation')
        client = await self.connect()
        try:
            result = await client.lifecycle(node_id, method, **data)
            if not isinstance(result, dict) or result.get('error'):
                raise ValueError
            return result
        except Exception:
            raise AssemblyError('Credential node did not acknowledge delivery; no replacement was requested') from None
