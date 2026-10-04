"""Prepared filesystem service. The legacy combined toolset remains available.

This package advertises filesystem operations only; model-assisted inspection and
image generation belong to explicitly bound model/image Apps, not an embedded
Agent. A workspace is a default path, not an OS sandbox: owner grants must bound
paths or deploy this service under an appropriately restricted OS identity.
"""
from collections.abc import Mapping
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from pantheon.toolset import tool
from .file_manager import FileManagerToolSet

METHODS = frozenset(('read_file', 'write_file', 'update_file', 'glob', 'grep', 'apply_patch',
    'view_file_outline', 'list_files', 'stat_path', 'get_cwd', 'manage_path',
    'create_directory', 'delete_path', 'move_file', 'fetch_image_base64'))


class ManagedFiles(FileManagerToolSet):
    """Preview files on this provider, without an ambient Agent image store."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._preview_slots = asyncio.Semaphore(2)

    @tool(exclude=True)
    async def fetch_image_base64(self, image_path: str, max_size: int = 1568) -> dict:
        """Return a bounded image preview from this Files workspace.

        Args:
            image_path: Image path inside the configured workspace.
            max_size: Raster preview's longest edge, from 1 to 4096 pixels.
        """
        if not isinstance(image_path, str) or not image_path or type(max_size) is not int or not 1 <= max_size <= 4096:
            return {'success': False, 'error': 'Supply an image path and a preview size from 1 to 4096'}

        def encode():
            from pantheon.utils.vision import get_image_base64
            path = self._resolve_path(image_path).resolve()
            root = self.path.resolve()
            if not path.is_relative_to(root):
                raise ValueError('Image is outside the configured Files workspace')
            formats = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}
            if not path.is_file() or path.suffix.lower() not in formats:
                raise ValueError('Image is missing or has an unsupported format')
            limit = 10 * 1024 * 1024
            if path.suffix.lower() in {'.gif', '.svg'}:
                with path.open('rb') as stream:
                    raw = stream.read(limit + 1)
                if not raw or len(raw) > limit:
                    raise ValueError('Image is empty or exceeds the preview byte limit')
                mime = 'gif' if path.suffix.lower() == '.gif' else 'svg+xml'
                return f'data:image/{mime};base64,' + base64.b64encode(raw).decode('ascii')
            return get_image_base64(str(path), max_size=max_size, max_bytes=limit, max_pixels=40_000_000)

        async with self._preview_slots:
            # Keep the admission slot until the worker finishes, even if its
            # request is cancelled, so disconnected viewers cannot pile up decodes.
            pending = asyncio.create_task(asyncio.to_thread(encode))
            try:
                uri = await asyncio.shield(pending)
                return {'success': True, 'image_path': image_path, 'data_uri': uri}
            except asyncio.CancelledError:
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not pending.cancelled():
                    pending.exception()
                raise
            except Exception:
                return {'success': False, 'error': 'Image preview unavailable; check its path, format and size in this Files workspace'}


def create_service(config):
    if (not isinstance(config, Mapping) or set(config) - {'workspace', 'limits'}
            or not isinstance(config.get('workspace'), str) or not Path(config['workspace']).is_absolute()):
        raise ValueError('Files needs an explicit absolute workspace')
    workspace = Path(config['workspace'])
    if not workspace.is_dir():
        raise ValueError('Files workspace must exist on its node')
    limits = config.get('limits', {})
    ceilings = dict(max_file_read_chars=8*1024*1024, max_file_read_lines=100000, max_glob_results=100000)
    defaults = dict(max_file_read_chars=50000, max_file_read_lines=800, max_glob_results=1000)
    if (not isinstance(limits, Mapping) or limits.keys() - ceilings.keys()
            or any(type(value) is not int or not 1 <= value <= ceilings[key] for key, value in limits.items())):
        raise ValueError('Invalid Files response limits')
    service = ManagedFiles('file_manager', workspace,
        file_settings=SimpleNamespace(**(defaults | dict(limits))), template_fallback=False)
    service.functions = {name: value for name, value in service.functions.items() if name in METHODS}
    if service.functions.keys() != METHODS:
        raise ValueError('Files package methods differ from the source toolset')
    return service


async def register(ctx):
    configuration = load_runtime_configuration(required=True)
    service = create_service(configuration.values.get('files'))
    await register_toolset(ctx, service)
