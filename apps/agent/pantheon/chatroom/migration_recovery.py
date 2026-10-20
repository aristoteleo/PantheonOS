"""Recover a captured subtree into a new private directory, without execution.

This is owner-side recovery, not Agent admission or a grant to a Files/Shell App.
It never overwrites the original tree. External/cyclic links and privileged modes
require explicit conversion instead of silently changing or activating them.
"""
from collections import deque
from hashlib import sha256
import os
from pathlib import Path

from .migration_backup import (_atomic_json, _hash_file, _open, _private_dir,
                               _read_json, _sync_directory, verify_backup)


def _link_parts(value, source):
    path = Path(value)
    if path.is_absolute():
        if not path.is_relative_to(source):
            raise ValueError('Workspace recovery needs an explicit external-link conversion')
        return True, path.relative_to(source).parts
    return False, path.parts


def _validate_links(links, files, source):
    # Evaluate '..' after expanding each encountered link. Merely normalizing
    # the target string would miss e.g. inside-link/../outside-root escapes.
    for identity, value in links.items():
        absolute, parts = _link_parts(value, source)
        stack = [] if absolute else list(identity[:-1])
        pending, followed = deque(parts), 0
        while pending:
            part = pending.popleft()
            if part in ('', '.'):
                continue
            if part == '..':
                if not stack:
                    raise ValueError('Workspace link escapes its recovered tree')
                stack.pop()
                continue
            candidate = (*stack, part)
            if candidate in links:
                followed += 1
                if followed > 40:
                    raise ValueError('Cyclic or excessive workspace links need explicit conversion')
                absolute, replacement = _link_parts(links[candidate], source)
                if absolute:
                    stack = []
                pending.extendleft(reversed(replacement))
            else:
                if candidate in files and pending:
                    raise ValueError('Workspace link traverses a regular file')
                stack.append(part)


def recover_tree(snapshot, *, digest, source, directory):
    """Restore one captured subtree to directory/tree; never adopt an existing target.

    Failed writes remain private under `pending` with an incomplete receipt. The
    caller may inspect them or retry to a fresh destination; they are never ready
    App data. A completed receipt proves byte/structure recovery, not portability
    of interpreter shebangs, native libraries or references in arbitrary file text.
    """
    if os.name != 'posix':
        raise ValueError('Workspace permission recovery currently requires a POSIX node')
    verify_backup(snapshot, digest=digest)
    snapshot = Path(snapshot)
    manifest = _read_json(snapshot / 'manifest.json')
    if manifest.get('filesystem_metadata') != 1:
        raise ValueError('Workspace recovery requires a backup with filesystem metadata')
    source = Path(source)
    if not source.is_absolute() or '..' in source.parts:
        raise ValueError('Select an absolute captured source directory')
    directories = {Path(row['source']).relative_to(source): row for row in manifest['directories']
                   if Path(row['source']).is_relative_to(source)}
    if Path('.') not in directories:
        raise ValueError('Workspace source directory is absent from the backup')
    files = {Path(row['source']).relative_to(source): row for row in manifest['files']
             if Path(row['source']).is_relative_to(source)}
    for path, row in [*directories.items(), *files.items()]:
        if row['source_mode'] & 0o7000:
            raise ValueError('Privileged filesystem modes need explicit recovery conversion')
        if path != Path('.') and path.parent not in directories:
            raise ValueError('Workspace archive is missing a parent directory')
    links = {}
    for path, row in files.items():
        if row.get('source_kind') == 'symlink':
            with os.fdopen(_open(snapshot / row['blob'], os.O_RDONLY), 'rb') as stream:
                raw = stream.read(64 * 1024 + 1)
            if len(raw) != row['size'] or sha256(raw).hexdigest() != row['sha256'] or b'\0' in raw or not raw:
                raise ValueError('Invalid captured workspace link')
            links[path.parts] = os.fsdecode(raw)
    regular = {path.parts for path in files if path.parts not in links}
    _validate_links(links, regular, source)

    destination = Path(directory)
    if not destination.is_absolute() or '..' in destination.parts or destination.is_symlink():
        raise ValueError('Recovery needs a new absolute private destination')
    destination = destination.resolve()
    # Do not consult old files, which may no longer exist. All original source
    # roots are represented by directory entries; exclude ancestors and children.
    for row in manifest['directories']:
        original = Path(row['source'])
        if destination.is_relative_to(original) or original.is_relative_to(destination):
            raise ValueError('Recovery must be outside all captured source trees')
    if destination.is_relative_to(snapshot) or snapshot.is_relative_to(destination):
        raise ValueError('Recovery must be outside its immutable backup')
    _private_dir(destination.parent, create=False)
    destination.mkdir(mode=0o700)  # Exclusive reservation; never replace another tree.
    _sync_directory(destination.parent)
    intent = dict(protocol=1, kind='agent-workspace-recovery', backup=digest,
                  source=str(source), phase='copying', files=len(files), directories=len(directories))
    _atomic_json(destination / 'recovery.json', intent)
    pending = destination / 'pending'
    pending.mkdir(mode=0o700)
    try:
        ordered = sorted(directories, key=lambda path: (len(path.parts), str(path)))
        for path in ordered:
            if path != Path('.'):
                (pending / path).mkdir(mode=0o700)
        # Write every regular file before constructing links. No write can go
        # through a link supplied by the archive.
        for path, row in files.items():
            if path.parts in links:
                continue
            with os.fdopen(_open(pending / path, os.O_CREAT | os.O_EXCL | os.O_WRONLY), 'wb') as output:
                actual = _hash_file(snapshot / row['blob'], max_bytes=row['size'], copy_to=output)
                if actual != {key: row[key] for key in ('size', 'sha256')}:
                    raise ValueError('Workspace blob changed during recovery')
                os.fchmod(output.fileno(), row['source_mode'])
                output.flush(); os.fsync(output.fileno())
        for identity, value in links.items():
            path = Path(*identity)
            if Path(value).is_absolute():
                # Preserve path components, including '..' after symlinks.
                value = os.path.relpath(source, (source / path).parent) + '/' + str(Path(value).relative_to(source))
            os.symlink(value, pending / path)
        for path in reversed(ordered):
            fd = os.open(pending / path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchmod(fd, directories[path]['source_mode'])
                os.fsync(fd)
            finally:
                os.close(fd)
        pending.rename(destination / 'tree')
        _sync_directory(destination)
        receipt = {**intent, 'phase': 'complete', 'tree': str(destination / 'tree')}
        _atomic_json(destination / 'recovery.json', receipt)
        return receipt
    except BaseException:
        _atomic_json(destination / 'recovery.json', {**intent, 'phase': 'incomplete'})
        raise
