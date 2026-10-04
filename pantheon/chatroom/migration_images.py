"""Relocate captured ImageStore assets, never discover files from message text."""
from hashlib import sha256
import json
import os
from pathlib import Path


def image_destination(root):
    # Keep equal chat IDs/hash filenames from different legacy stores separate.
    return 'configuration/.pantheon/images/_imported/' + sha256(str(root).encode()).hexdigest()


def image_mapping(manifest, files, target):
    spec = manifest['spec']
    roots = {Path(spec[key]) for key in ('project_config', 'global_config')}
    roots.update(Path(project['path']) / '.pantheon' for project in spec['projects'])
    # Only declared roots may be canonicalized. Message references below are
    # purely lexical, so they cannot cause filesystem reads or symlink traversal.
    aliases = {str(root / 'images'): str(root.resolve() / 'images') for root in roots}
    aliases.update({value: value for value in list(aliases.values())})
    paths = {item['source']: 'file://' + str(target / destination)
             for destination, item in files.items() if item['category'] == 'image-store'}
    return {'roots': aliases, 'paths': paths}


def rewrite_message_images(message, mapping):
    """Change only typed image URLs; text, tool arguments and external URLs stay."""
    changed = False
    for field in ('content', '_llm_content'):
        blocks = message.get(field)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get('type') != 'image_url':
                continue
            image = block.get('image_url')
            value = image.get('url') if isinstance(image, dict) else image
            if not isinstance(value, str):
                continue
            raw = value.removeprefix('file://')
            if not Path(raw).is_absolute():
                continue
            # Check lexical ownership before normalization so traversal out of a
            # declared store cannot turn into an unrelated workspace reference.
            root = next((r for r in mapping['roots'] if Path(raw).is_relative_to(r)), None)
            if root is None:
                continue
            normalized = Path(os.path.normpath(raw))
            if not normalized.is_relative_to(root):
                raise ValueError('Legacy image reference escapes its declared image store')
            canonical = str(Path(mapping['roots'][root]) / normalized.relative_to(root))
            replacement = mapping['paths'].get(canonical)
            if replacement is None:
                raise ValueError('Legacy image reference is missing from the captured image store')
            if isinstance(image, dict):
                image['url'] = replacement
            else:
                block['image_url'] = replacement
            changed = changed or value != replacement
    return changed


def converted_message_lines(snapshot, item, mapping):
    """Bound memory to one JSONL message, verifying the entire source blob."""
    from .migration_backup import _open, _encoded
    digest, size = sha256(), 0
    fd = _open(snapshot / item['blob'], os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        while line := stream.readline(16 * 1024 * 1024 + 1):
            size += len(line)
            if len(line) > 16 * 1024 * 1024 or size > item['size']:
                raise ValueError('Legacy message exceeds its conversion limit')
            digest.update(line)
            if line.strip():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('Invalid legacy message')
                if rewrite_message_images(message, mapping):
                    line = _encoded(message) + b'\n'
            yield line
    if size != item['size'] or digest.hexdigest() != item['sha256']:
        raise ValueError('Backup content changed during image conversion')
