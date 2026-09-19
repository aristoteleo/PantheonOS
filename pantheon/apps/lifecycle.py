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


def build_artifact(directory: Path, platform: str | None = None) -> tuple[bytes, str]:
    """Deterministic package. Refuse links and omit mutable/cache/git content."""
    root = directory.resolve(strict=True)
    if not (root / 'fleet.json').is_file():
        raise ValueError('This App has no fleet.json execution declaration')
    manifest_path = next((root / name for name in ('app.json', 'atrium.json') if (root / name).is_file()), None)
    if manifest_path is None:
        raise ValueError('App manifest is missing')
    manifest = json.loads(manifest_path.read_text())
    definition_path = root / 'fleet.json'
    variants = manifest.get('execution', {}).get('platform_manifests', {})
    if variants:
        name = variants.get(platform)
        if not name or not re.fullmatch(r'fleet\.(linux|darwin|windows)-(amd64|arm64)\.json', name):
            raise ValueError(f'This App has no native package for node platform {platform!r}')
        definition_path = root / name
        if definition_path.is_symlink():
            raise ValueError('App artifacts cannot contain symbolic links')
    definition_bytes = definition_path.read_bytes()
    definition = json.loads(definition_bytes)
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
            replacement = definition_bytes if relative.as_posix() == 'fleet.json' else None
            size = len(replacement) if replacement is not None else path.stat().st_size
            if out.tell() + size + 10240 > MAX_ARTIFACT:
                raise ValueError('App code package exceeds 32 MiB; use pinned images for large dependencies')
            entry = tarfile.TarInfo(relative.as_posix())
            entry.size = size
            entry.mode = 0o500 if path.stat().st_mode & 0o111 else 0o400
            if replacement is not None:
                archive.addfile(entry, io.BytesIO(replacement))
            else:
                with path.open('rb') as stream:
                    archive.addfile(entry, stream)
    payload = out.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


class FleetLifecycle:
    def __init__(self, resolver):
        self.resolver = resolver
        self._platforms = {}

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
        self._platforms[node_id] = f"{node.get('os')}-{node.get('arch')}"
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
        client = await self._client(node_id)
        def package():
            from pantheon.apps.portable import execution_package
            platform = self._platforms.get(node_id)
            workspace = getattr(self.resolver, '_workdir', None) if node_id == getattr(self.resolver, '_node', None) else None
            with execution_package(directory, platform, workspace=workspace) as root:
                return build_artifact(root, platform)
        payload, digest = await asyncio.to_thread(package)
        snapshot = await client.lifecycle(node_id, 'status')
        if snapshot.get('error'):
            raise RuntimeError(snapshot['error'])
        if snapshot.get('installations', {}).get(digest, {}).get('state') == 'installed':
            return digest
        # Reuse the authenticated connection across chunks; this is code only,
        # never a bulk document transfer. All chunks are offset/idempotent.
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

    async def usage(self, node_id: str, method: str, *, instance_id: str,
                    revision: str, generation: int, lease_id: str = '',
                    release: bool = False, keep_alive: bool = False):
        if method not in {'lease', 'keep_alive'}:
            raise ValueError('Unsupported App usage operation')
        return await self._request(node_id, method, instance_id=instance_id,
            revision=revision, generation=generation, lease_id=lease_id,
            release=release, keep_alive=keep_alive)
