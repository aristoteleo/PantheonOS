"""Resolve image artifacts on their owning Fleet node, never by disk guessing."""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path, PureWindowsPath
from urllib.parse import quote, unquote

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def image_location(path: Path) -> dict:
    from pantheon.apps.builtin.fleet.local_node import local_node_id
    node = local_node_id()
    absolute = str(path.resolve())
    return {"path": absolute, "resolved_path": absolute, "node_id": node,
            "image_ref": f"pantheon-node:///{quote(node, safe='')}{quote(absolute, safe='/')}" if node else absolute}


async def resolve_image_sources(image_paths, node_id, resolve_local, temporary: Path):
    from pantheon.apps.builtin.fleet.local_node import local_node_id
    paths = [image_paths] if isinstance(image_paths, str) else image_paths
    if not isinstance(paths, list) or not 1 <= len(paths) <= 8:
        raise ValueError('Provide between 1 and 8 image paths')
    resolved, sources = [], []
    for index, raw in enumerate(paths):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Each image path must be a non-empty string')
        owner, path = node_id, raw
        if raw.startswith('pantheon-node:///'):
            parts = raw[len('pantheon-node:///'):].split('/', 1)
            if len(parts) != 2 or not parts[0]:
                raise ValueError('Invalid node image reference')
            owner, path = unquote(parts[0]), unquote('/' + parts[1])
            if node_id and node_id != owner:
                raise ValueError('node_id conflicts with the image reference')
        if owner and not (Path(path).is_absolute() or PureWindowsPath(path).is_absolute()):
            raise ValueError('A node image requires an absolute path on that node')
        if owner and owner != local_node_id():
            target = temporary / f'image-{index}'
            await _download_image(owner, path, target)
            uri_path = path if path.startswith('/') else '/' + path
            source = {"path": path, "node_id": owner,
                      "image_ref": f"pantheon-node:///{quote(owner, safe='')}{quote(uri_path, safe='/')}"}
        else:
            target = resolve_local(path)
            if not target.is_file():
                raise ValueError(f'Image file does not exist or is not a file: {path}. '
                                 'Use the exact image_ref or path returned by the screenshot tool; '
                                 'a requested filename is not proof it was saved.')
            if target.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError('Image exceeds the 20 MiB limit')
            source = image_location(target)
        resolved.append(str(target))
        sources.append(source)
    return resolved, sources


async def _download_image(node_id: str, path: str, target: Path):
    from pantheon.apps.resolver import get_shared_resolver
    from pantheon.apps.proxy import ToolsetProxy
    resolver = get_shared_resolver()
    if resolver is None:
        raise RuntimeError('A connected Fleet is required to read an image on another node')
    async with asyncio.timeout(60):
        # Exact-node resolution enforces membership, online state and shared roots.
        service = await resolver.ensure_instance('file_manager', node_id=node_id)
        proxy = ToolsetProxy.from_toolset(service)

        async def transfer(method, **args):
            result = await proxy.invoke('file_transfer', {'method': method, 'args': args})
            if not isinstance(result, dict) or result.get('success') is not True:
                raise RuntimeError(result.get('error', 'Image transfer failed') if isinstance(result, dict)
                                   else 'Invalid image transfer response')
            return result

        opened = await transfer('open_file_for_read', file_path=path)
        handle = opened['handle_id']
        try:
            size = opened['total_size']
            if not isinstance(size, int) or not 0 < size <= MAX_IMAGE_BYTES:
                raise ValueError('Image must be non-empty and at most 20 MiB')
            with target.open('wb') as out:
                offset = 0
                while offset < size:
                    count = min(48 * 1024, size - offset)
                    chunk = await transfer('read_chunk_at', handle_id=handle, offset=offset, size=count)
                    data = base64.b64decode(chunk['data'], validate=True)
                    if not data or len(data) > count:
                        raise ValueError('Incomplete or oversized image transfer chunk')
                    out.write(data)
                    offset += len(data)
        finally:
            # Bound cleanup, including cancelled/failed/oversized reads.
            try:
                await asyncio.wait_for(transfer('close_file', handle_id=handle), 5)
            except Exception:
                pass
