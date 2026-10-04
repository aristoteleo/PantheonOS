"""Private, resumable backups of explicitly fenced legacy Agent sources.

The snapshot is data, never an App release artifact. Settings/credential-bearing
files are opaque backup bytes and cannot be implicitly imported as App config.
Source data is not changed. Cooperative local fencing does not exclude older
binaries or independent replicas; deployment-level exclusion is still required.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from pantheon.platform.registry_lock import registry_lock
from .data_fence import MigrationFence, _open, _sync_directory
from .migration import inspect_legacy, legacy_source_roots, PLATFORM_DATA, _stamp

MAX_MANIFEST = 64 * 1024 * 1024
MAX_FILES = 100000
DEFAULT_MAX_BYTES = 20 * 1024 ** 3


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _private_dir(path, *, create=True):
    if path.is_symlink():
        raise ValueError('Migration backup directories must not be symlinks')
    if create:
        missing, current = [], path
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            _sync_directory(directory.parent)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or (os.name == 'posix' and
            (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
        raise ValueError('Migration backup directories must be owner-private')
    return path


def _private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name == 'posix' and
            (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
        raise ValueError('Migration backup files must be owner-private regular files')


def _read_json(path):
    _private_file(path)
    fd = _open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Migration backup manifest is too large')
    return json.loads(raw)


def _atomic_json(path, value):
    raw = _encoded(value)
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Migration backup manifest is too large')
    temporary = path.with_name('.' + path.name + '.partial')
    if temporary.exists() or temporary.is_symlink():
        _private_file(temporary)
        temporary.unlink()
    fd = _open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


def _hash_file(path, *, max_bytes, copy_to=None):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise ValueError('Migration requires bounded regular source files')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    digest, size = sha256(), 0
    with os.fdopen(fd, 'rb') as source:
        if _stamp(os.fstat(source.fileno())) != _stamp(info):
            raise ValueError('Migration source changed while opening')
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError('Migration backup exceeds byte limit')
            digest.update(chunk)
            if copy_to is not None:
                copy_to.write(chunk)
        if _stamp(os.fstat(source.fileno())) != _stamp(info):
            raise ValueError('Migration source changed while reading')
    if _stamp(path.lstat()) != _stamp(info):
        raise ValueError('Migration source changed while reading')
    return dict(size=size, sha256=digest.hexdigest())


def _plan(spec, *, max_bytes):
    report = inspect_legacy(**spec)
    files = {item['source']: dict(item) for item in report['files']}
    total = sum(item['size'] for item in files.values())
    # Keep opaque originals of settings and unclassified configuration so a
    # future conversion cannot discard the only copy. They remain unmapped and
    # the inventory issues remain unresolved. Never follow an external symlink.
    for issue in report['issues']:
        if issue['code'] in ('non_regular_file', 'invalid_configuration_root', 'invalid_conversation_store'):
            raise ValueError('Migration backup requires regular source trees; inspect the dry-run issues')
        if issue['code'] not in ('configuration_requires_explicit_conversion', 'unclassified_source'):
            continue
        pending = [Path(issue['source'])]
        while pending:
            path = pending.pop()
            if path.is_symlink():
                raise ValueError('Migration backup does not follow source symlinks')
            if path.is_dir():
                pending.extend(sorted(path.iterdir(), reverse=True))
                continue
            key = str(path)
            if key not in files:
                if len(files) >= MAX_FILES:
                    raise ValueError('Migration backup exceeds file limit')
                metadata = _hash_file(path, max_bytes=max_bytes - total)
                total += metadata['size']
                files[key] = dict(source=key, target=None, category='opaque-configuration', **metadata)
    if total > max_bytes:
        raise ValueError('Migration backup exceeds byte limit')
    entries = [dict(item, blob=f'{index:06d}.bin')
               for index, item in enumerate(sorted(files.values(), key=lambda item: item['source']))]
    return dict(inventory=report, files=entries, total_bytes=total)


def _destination(spec, directory):
    if not isinstance(directory, (str, Path)) or not Path(directory).is_absolute():
        raise ValueError('Migration backup destination must be absolute')
    path = Path(directory)
    if path.is_symlink():
        raise ValueError('Migration backup destination must not be a symlink')
    path = path.resolve()
    from .migration_environment import environment_source
    if environment_source(spec).is_relative_to(path):
        raise ValueError('Migration destination must not contain the source environment file')
    from .migration_handoff import handoff_source
    handoff = handoff_source(spec)
    if handoff is not None and handoff.is_relative_to(path):
        raise ValueError('Migration destination must not contain the model environment handoff')
    config_roots = {Path(spec[key]).resolve() for key in ('global_config', 'project_config')}
    config_roots.update(Path(project['path']).resolve() / '.pantheon' for project in spec['projects'])
    for root in legacy_source_roots(spec):
        if path == root or root.is_relative_to(path):
            raise ValueError('Migration backup must not contain a source root')
        if path.is_relative_to(root):
            relative = path.relative_to(root)
            # Fleet's private data directory normally lives inside ~/.pantheon.
            # Only allow subtrees already excluded from the source inventory.
            if root not in config_roots or relative.parts[0] not in PLATFORM_DATA or len(relative.parts) < 2:
                raise ValueError('Migration backup must be outside inventoried source data')
    return path


def _verify_snapshot(directory, manifest):
    expected_names = {'manifest.json', *(item['blob'] for item in manifest['files'])}
    if set(p.name for p in directory.iterdir()) != expected_names:
        raise ValueError('Migration snapshot has missing or unexpected files')
    for index, item in enumerate(manifest['files']):
        if item['blob'] != f'{index:06d}.bin':
            raise ValueError('Invalid migration snapshot blob identity')
        path = directory / item['blob']
        _private_file(path)
        metadata = _hash_file(path, max_bytes=item['size'])
        if metadata != {key: item[key] for key in ('size', 'sha256')}:
            raise ValueError('Migration snapshot checksum mismatch')


def verify_backup(directory, *, digest):
    """Verify a published snapshot without opening or requiring its old sources.

    The caller retains digest in its migration journal, outside this archive.
    Nothing from this archive is executed or resolved as an import destination.
    """
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Expected migration snapshot digest is required')
    root = Path(directory)
    if not root.is_absolute():
        raise ValueError('Migration snapshot path must be absolute')
    _private_dir(root, create=False)
    manifest = _read_json(root / 'manifest.json')
    if (not isinstance(manifest, dict) or manifest.get('protocol') != 1
            or manifest.get('kind') != 'agent-legacy-backup'
            or sha256(_encoded(manifest)).hexdigest() != digest):
        raise ValueError('Migration snapshot manifest does not match its expected digest')
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError('Invalid migration snapshot files')
    for index, item in enumerate(files):
        if (not isinstance(item, dict) or item.get('blob') != f'{index:06d}.bin'
                or type(item.get('size')) is not int or item['size'] < 0
                or not isinstance(item.get('sha256'), str) or len(item['sha256']) != 64):
            raise ValueError('Invalid migration snapshot file metadata')
    if sum(item['size'] for item in files) != manifest.get('total_bytes'):
        raise ValueError('Migration snapshot size mismatch')
    _verify_snapshot(root, manifest)
    return dict(directory=str(root), sha256=digest, files=len(files),
                bytes=manifest['total_bytes'], ready_to_import=False)


def backup_legacy(spec, *, fence, directory, max_bytes=DEFAULT_MAX_BYTES):
    """Publish or resume one private immutable source snapshot.

    Requires the live matching fence, not an 'I stopped the writer' boolean.
    Completed backup verification never rewrites its manifest or source files.
    Interruptions leave the source fenced and incomplete bytes under .pending;
    retry reuses verified blobs and discards only its known partial blob files.
    """
    if not isinstance(fence, MigrationFence):
        raise ValueError('A live legacy migration fence is required')
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('Migration backup byte limit must be positive')
    fence.assert_owned()
    if list(fence.roots) != legacy_source_roots(spec):
        raise ValueError('Migration fence does not cover the exact source specification')
    root = _private_dir(_destination(spec, directory))
    # Serialize retries sharing an archive, independent of their source roots.
    # Pre-create the existing generic lock privately, then use its protocol.
    lock = root / 'writer.lock'
    fd = _open(lock, os.O_CREAT | os.O_RDWR); os.close(fd)
    _private_file(lock)
    with registry_lock(lock, timeout=0):
        plan = _plan(spec, max_bytes=max_bytes)
        manifest = dict(protocol=1, kind='agent-legacy-backup', fence=fence.identity,
                        spec=spec, **plan)
        fingerprint = sha256(_encoded(manifest)).hexdigest()
        intent = dict(protocol=1, sha256=fingerprint, fence=fence.identity)
        intent_path = root / 'intent.json'
        if intent_path.exists() or intent_path.is_symlink():
            if _read_json(intent_path) != intent:
                raise ValueError('Migration backup belongs to different source bytes or intent; use a new destination')
        else:
            if set(p.name for p in root.iterdir()) - {'writer.lock', '.intent.json.partial'}:
                raise ValueError('Migration backup destination is not empty')
            _atomic_json(intent_path, intent)
        snapshot = root / 'snapshot'
        if snapshot.exists() or snapshot.is_symlink():
            _private_dir(snapshot)
            if _read_json(snapshot / 'manifest.json') != manifest:
                raise ValueError('Migration snapshot manifest mismatch')
            _verify_snapshot(snapshot, manifest)
            fence.assert_owned()
            return dict(directory=str(snapshot), sha256=fingerprint, files=len(plan['files']),
                        bytes=plan['total_bytes'], ready_to_import=False)
        pending = _private_dir(root / '.pending')
        expected = {'manifest.json', '.manifest.json.partial',
                    *(item['blob'] for item in plan['files']),
                    *('.' + item['blob'] + '.partial' for item in plan['files'])}
        if set(p.name for p in pending.iterdir()) - expected:
            raise ValueError('Unrecognized files in incomplete migration backup')
        for item in plan['files']:
            fence.assert_owned()
            path = pending / item['blob']
            checksum = {key: item[key] for key in ('size', 'sha256')}
            if path.exists() or path.is_symlink():
                _private_file(path)
                if _hash_file(path, max_bytes=item['size']) != checksum:
                    raise ValueError('Incomplete migration backup contains a corrupt completed blob')
                continue
            partial = path.with_name('.' + path.name + '.partial')
            if partial.exists() or partial.is_symlink():
                _private_file(partial)
                partial.unlink()
            fd = _open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'wb') as output:
                metadata = _hash_file(Path(item['source']), max_bytes=item['size'], copy_to=output)
                output.flush(); os.fsync(output.fileno())
            if metadata != checksum:
                raise ValueError('Migration source no longer matches its inventory')
            os.replace(partial, path)
            _sync_directory(pending)
        # Detect additions/removals and opaque configuration changes as well as
        # edits of known histories. Do not bless an inconsistent archive.
        if _plan(spec, max_bytes=max_bytes) != plan:
            raise ValueError('Migration sources changed during backup')
        fence.assert_owned()
        _atomic_json(pending / 'manifest.json', manifest)
        _verify_snapshot(pending, manifest)
        pending.rename(snapshot)
        _sync_directory(root)
        return dict(directory=str(snapshot), sha256=fingerprint, files=len(plan['files']),
                    bytes=plan['total_bytes'], ready_to_import=False)
