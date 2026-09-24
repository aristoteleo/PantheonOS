"""Stable model identity for group packages; node-specific timestamps stay local."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def descriptor(record):
    if (not isinstance(record, dict) or not {'sha256', 'files', 'config', 'weights_bytes',
            'tensor_parallel_weights'} <= set(record)
            or not isinstance(record['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', record['sha256'])
            or type(record['weights_bytes']) is not int or not 0 < record['weights_bytes'] <= 1 << 40
            or not isinstance(record['config'], dict) or record['config'].get('auto_map')
            or not isinstance(record['files'], list) or not 1 <= len(record['files']) <= 4096):
        raise ValueError('Use a verified non-executable model snapshot receipt')
    weights = record['tensor_parallel_weights']
    if (not isinstance(weights, dict) or set(weights) - {'2', '4', '8'}
            or any(type(n) is not int or not 0 < n <= record['weights_bytes'] for n in weights.values())):
        raise ValueError('Use the prepared tensor parallel weight estimates')
    files, names, total = [], set(), 0
    for item in record['files']:
        if (not isinstance(item, dict) or not {'path', 'size', 'sha256'} <= set(item)
                or not isinstance(item['path'], str)
                or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}', item['path'])
                or item['path'] == 'snapshot.json'
                or not item['path'].endswith(('.json', '.safetensors', '.txt', '.model', '.tiktoken'))
                or item['path'].casefold() in names
                or type(item['size']) is not int or not 0 < item['size'] <= 1 << 40
                or not isinstance(item['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
            raise ValueError('Invalid immutable model file receipt')
        names.add(item['path'].casefold())
        total += item['size']
        files.append({key: item[key] for key in ('path', 'size', 'sha256')})
    if (total > 1 << 40 or 'config.json' not in names
            or not names.intersection({'tokenizer.json', 'tokenizer.model', 'tokenizer.tiktoken'})
            or sum(f['size'] for f in files if f['path'].endswith('.safetensors')) != record['weights_bytes']):
        raise ValueError('Incomplete model files or inconsistent weights size')
    value = dict(sha256=record['sha256'], config=deepcopy(record['config']),
        weights_bytes=record['weights_bytes'], tensor_parallel_weights=deepcopy(weights),
        files=sorted(files, key=lambda f: f['path']))
    if len(json.dumps(value, sort_keys=True, allow_nan=False).encode()) > 2 << 20:
        raise ValueError('Model receipt exceeds its metadata bound')
    return value


def check_record(record, expected):
    if descriptor(expected) != expected or descriptor(record) != expected:
        raise ValueError('Node model receipt differs from the original group package')


def check_files(root, record):
    """Reuse verified cache metadata; hash only files whose timestamps changed.

    Image layers may round timestamps. Confirming their pinned bytes is safe;
    neither redownloading weights nor rewriting an immutable receipt is needed.
    """
    descriptor(record)  # Validate bounded root-level paths before file access.
    for item in record['files']:
        path = Path(root) / item['path']
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or path.is_symlink() or before.st_size != item['size']:
                raise ValueError('Original group model file changed')
            if type(item.get('mtime_ns')) is int and item['mtime_ns'] == before.st_mtime_ns:
                continue
            sha = hashlib.sha256()
            while chunk := source.read(8 << 20):
                sha.update(chunk)
            after = os.fstat(source.fileno())
            if (sha.hexdigest() != item['sha256'] or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    != (before.st_size, before.st_mtime_ns, before.st_ctime_ns)):
                raise ValueError('Original group model file failed content verification')
