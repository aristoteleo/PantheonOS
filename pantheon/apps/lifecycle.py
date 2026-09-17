"""Versioned Fleet lifecycle coordinator; execution always belongs to a node.

Artifacts contain a fleet.json declaration and immutable App code. This module
does not run install hooks locally, copy local interpreters, or fall back to a
different node when the requested node cannot run an App.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import tarfile
import uuid
from pathlib import Path

PROTOCOL = 1
MAX_ARTIFACT = 32 * 1024 * 1024
CHUNK_SIZE = 192 * 1024


def build_artifact(directory: Path) -> tuple[bytes, str]:
    """Deterministic package. Refuse links and omit mutable/cache/git content."""
    root = directory.resolve(strict=True)
    if not (root / 'fleet.json').is_file():
        raise ValueError('This App has no fleet.json execution declaration')
    definition = json.loads((root / 'fleet.json').read_text())
    manifest_path = next((root / name for name in ('app.json', 'atrium.json') if (root / name).is_file()), None)
    if manifest_path is None:
        raise ValueError('App manifest is missing')
    manifest = json.loads(manifest_path.read_text())
    if definition.get('protocol') != PROTOCOL or definition.get('app_id') != manifest.get('id') or definition.get('version') != manifest.get('version'):
        raise ValueError('Execution declaration must match the App identity and version')
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if any(part in {'.git', '__pycache__', 'node_modules', '.venv'} or part.startswith('.env')
                   for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f'App artifacts cannot contain symbolic links: {relative}')
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f'App artifacts cannot contain special files: {relative}')
            size = path.stat().st_size
            if out.tell() + size + 10240 > MAX_ARTIFACT:
                raise ValueError('App code package exceeds 32 MiB; use pinned images for large dependencies')
            entry = tarfile.TarInfo(relative.as_posix())
            entry.size = size
            entry.mode = 0o500 if path.stat().st_mode & 0o111 else 0o400
            with path.open('rb') as stream:
                archive.addfile(entry, stream)
    payload = out.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


class FleetLifecycle:
    def __init__(self, resolver):
        self.resolver = resolver

    async def _client(self, node_id):
        from pantheon.apps.builtin.fleet.inventory import node_inventory
        if not re.fullmatch(r'[A-Za-z0-9_-]+', node_id or ''):
            raise ValueError('A concrete Fleet node is required')
        await self.resolver._ensure_client()
        inventory = node_inventory(await self.resolver._list_nodes(max_age=0))
        node = next((node for node in inventory['nodes'] if node['node_id'] == node_id), None)
        if not node:
            raise ValueError('Node is not in this user’s Fleet')
        if node['status'] not in ('online', 'busy'):
            raise RuntimeError('Node is offline; lifecycle outcome is unknown until it reconnects')
        if node.get('runtimes', {}).get('app-lifecycle') != '1':
            raise RuntimeError('Upgrade Fleet on this node to enable managed App lifecycle v1')
        return self.resolver._client

    async def _request(self, node_id: str, method: str, **data):
        client = await self._client(node_id)
        result = await client.lifecycle(node_id, method, **data)
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    async def status(self, node_id: str):
        return await self._request(node_id, 'status')

    async def stage(self, node_id: str, directory: Path):
        payload, digest = await asyncio.to_thread(build_artifact, directory)
        # Reuse the authenticated connection across chunks; this is code only,
        # never a bulk document transfer. All chunks are offset/idempotent.
        client = await self._client(node_id)
        for offset in range(0, len(payload), CHUNK_SIZE):
            result = await client.lifecycle(node_id, 'stage', digest=digest, offset=offset,
                data=base64.b64encode(payload[offset:offset + CHUNK_SIZE]).decode())
            if result.get('error'):
                raise RuntimeError(result['error'])
        return digest

    async def submit(self, node_id: str, action: str, digest: str, *, scope='app',
                     generation=0, operation_id: str | None = None):
        if action not in {'install', 'uninstall', 'start', 'stop', 'reconcile'}:
            raise ValueError('Unsupported lifecycle operation')
        result = await self._request(node_id, 'submit', request={
            'protocol': PROTOCOL, 'operation_id': operation_id or uuid.uuid4().hex,
            'action': action, 'digest': digest, 'scope': scope, 'generation': generation,
        })
        return result['operation']
