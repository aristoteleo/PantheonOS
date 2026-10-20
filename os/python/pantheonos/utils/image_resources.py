"""Resolve model image inputs using explicit storage and Files capabilities.

No node discovery or ambient filesystem fallback. App-owned image bytes stay in
the supplied image store; other paths belong to the supplied Files provider.
"""
import asyncio
import base64
import copy
from pathlib import Path, PureWindowsPath

from .owned_io import run_owned_io


def is_file_image(value):
    return isinstance(value, str) and (value.startswith(('file://', '/'))
                                       or PureWindowsPath(value).is_absolute())


class BoundImageResolver:
    def __init__(self, *, image_root=None, files=None):
        self.root = Path(image_root).absolute() if image_root is not None else None
        self.files = files
        self._slots = asyncio.Semaphore(2)

    async def __call__(self, reference):
        path = reference.removeprefix('file://')
        candidate = Path(path)
        # Classify by the declared store first. A missing or escaping store
        # image must never fall through to another provider's same-named file.
        owned = self.root is not None and candidate.is_absolute() and candidate.is_relative_to(self.root)
        async with self._slots:
            try:
                if owned:
                    def read():
                        from .vision import get_image_base64
                        resolved = candidate.resolve()
                        if not resolved.is_relative_to(self.root.resolve()):
                            raise ValueError('Image escapes its owned store')
                        return get_image_base64(str(resolved), max_bytes=10 * 1024 * 1024,
                                                max_pixels=40_000_000)
                    uri = await run_owned_io(read)
                else:
                    if self.files is None:
                        raise ValueError('No Files capability')
                    result = await self.files.call_tool('fetch_image_base64', {'image_path': path, 'max_size': 1568})
                    if not isinstance(result, dict) or result.get('success') is not True:
                        raise ValueError('Files did not return an image')
                    uri = result.get('data_uri')
                # No returned file/HTTP URL may send the provider adapter back
                # to the Agent host or to a second, ungranted network endpoint.
                if not isinstance(uri, str) or len(uri) > 14 * 1024 * 1024:
                    raise ValueError('Invalid image size')
                header, encoded = uri.split(',', 1)
                if not header.startswith('data:image/') or not header.endswith(';base64') or not encoded:
                    raise ValueError('Invalid image response')
                base64.b64decode(encoded, validate=True)
                return uri
            except asyncio.CancelledError:
                raise
            except Exception:
                source = 'Agent image store' if owned else 'bound Files service'
                raise ValueError(f'Image attachment unavailable in the {source}; check its path and access') from None


async def expand_bound_images(messages, resolver):
    """Expand one model request without changing stored history or sharing cache.

    Resolves user, tool and ephemeral images alike. Repeated references in this
    request share bytes, but later calls recheck their capability and contents.
    """
    result = copy.deepcopy(messages)
    resolved = {}
    for message in result:
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'image_url':
                continue
            image = block.get('image_url')
            reference = image.get('url') if isinstance(image, dict) else image
            if not is_file_image(reference):
                continue
            if reference not in resolved:
                resolved[reference] = await resolver(reference)
            if isinstance(image, dict):
                image['url'] = resolved[reference]
            else:
                block['image_url'] = resolved[reference]
    return result
