"""Validate deliverables on the filesystem that owns the workspace files."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path


async def output_metadata(path: str, context: dict, node_id: str | None = None) -> dict:
    root = context.get('project_root') or context.get('workdir') or os.getcwd()
    candidate = Path(path).expanduser()
    if node_id and not candidate.is_absolute():
        raise ValueError('An explicit node_id requires an absolute path on that node')
    if not candidate.is_absolute():
        candidate = Path(root) / candidate

    from pantheon.apps.resolver import get_shared_resolver
    from pantheon.internal.memory_system.file_routing import memory_path
    resolver = get_shared_resolver()
    # Durable memory is explicitly owned by the Agent. Ordinary deliverables
    # belong to the same FileManager instance used by the Agent's file tools.
    if node_id and resolver is None:
        raise RuntimeError('Cross-node output registration requires a connected Fleet')
    if resolver is not None and (node_id or memory_path(str(candidate), root) is None):
        from pantheon.apps.proxy import ToolsetProxy
        async with asyncio.timeout(30):
            service_id = await resolver.ensure_instance('file_manager', node_id=node_id)
            proxy = ToolsetProxy.from_toolset(service_id)
            if not node_id:
                proxy.bind_instance(resolver, 'file_manager')
            result = await proxy.invoke('stat_path', {'file_path': str(candidate)})
        if not isinstance(result, dict) or result.get('success') is not True:
            raise RuntimeError(result.get('error', 'File metadata unavailable') if isinstance(result, dict)
                               else 'Workspace file service did not return file metadata')
        owner = node_id or result.get('node_id')
        if not owner:
            raise RuntimeError('File backend did not identify its Fleet node; update the file backend before registering outputs')
        if node_id and result.get('node_id') and result['node_id'] != node_id:
            raise RuntimeError('File backend answered from a different Fleet node')
        result['source'] = {'node_id': owner, 'path': result['path'], 'service_id': service_id}
        return result

    absolute = candidate.resolve()
    base = Path(root).resolve()
    return {'success': True, 'exists': absolute.exists(), 'is_dir': absolute.is_dir(),
            'path': str(absolute), 'store_path': str(absolute.relative_to(base)) if absolute.is_relative_to(base) else str(absolute)}
