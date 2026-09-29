"""Bounded file staging over the attended SSH session; executes no App code."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

q = json.loads(sys.argv[1])
base = os.path.expandvars(q['root'])
if not os.path.isabs(base) or '$' in base:
    raise ValueError('App directory must expand to an absolute shared path')
root = Path(base)
root.mkdir(parents=True, exist_ok=True, mode=0o700)
if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
    raise ValueError('App directory must be private and owned by this user')
if not re.fullmatch(r'[a-f0-9]{64}', q['digest']):
    raise ValueError('Invalid asset digest')
if q['suffix'] not in ('.bin', '.tar', '.json'):
    raise ValueError('Invalid asset type')
folder = root / 'assets'
folder.mkdir(mode=0o700, exist_ok=True)
if folder.is_symlink() or folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
    raise ValueError('Unsafe asset directory')
target = folder / (q['digest'] + q['suffix'])
if target.is_symlink():
    raise ValueError('Asset cannot be a symbolic link')
if target.exists():
    if target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077:
        raise ValueError('Unsafe cached asset')
    with target.open('rb') as cached:
        data = cached.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != q['digest']:
        raise ValueError('Cached asset checksum differs')
elif q['write']:
    data = sys.stdin.buffer.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != q['digest']:
        raise ValueError('Incomplete or oversized asset')
    temporary = folder / ('.upload-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700 if q['suffix'] == '.bin' else 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
print(json.dumps({'exists': target.exists(), 'path': str(target.resolve()), 'root': str(root.resolve())}))
