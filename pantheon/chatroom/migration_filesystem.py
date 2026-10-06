"""Passive filesystem metadata for private legacy archives, never App grants."""
import os
from pathlib import Path
import stat

from .data_fence import CONTROL_FILES


def capture_directories(spec, *, limit):
    """Record empty directories/modes under captured roots without following links."""
    from .migration import legacy_source_roots, PLATFORM_DATA
    roots = legacy_source_roots(spec)
    configs = {Path(spec[key]).resolve() for key in ('global_config', 'project_config')}
    configs.update((Path(p['path']) / '.pantheon').resolve() for p in spec['projects'])
    pending, found = list(roots), {}
    while pending:
        path = pending.pop()
        if str(path) in found or not path.exists():
            continue
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            continue
        if len(found) >= limit:
            raise ValueError('Migration filesystem metadata exceeds its entry limit')
        found[str(path)] = dict(source=str(path), source_mode=stat.S_IMODE(info.st_mode))
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name in CONTROL_FILES or (path in configs and entry.name in PLATFORM_DATA):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return [found[key] for key in sorted(found)]


def validate_filesystem(manifest, *, limit):
    """Validate optional metadata; old byte-only snapshots remain verifiable."""
    version = manifest.get('filesystem_metadata')
    if version is None:
        if 'directories' in manifest or any('source_mode' in row for row in manifest['files']):
            raise ValueError('Filesystem metadata requires its declared format')
        return
    if type(version) is not int or version != 1:
        raise ValueError('Unsupported migration filesystem metadata')
    directories = manifest.get('directories')
    if not isinstance(directories, list) or len(directories) + len(manifest['files']) > limit:
        raise ValueError('Invalid migration directory metadata')
    seen = set()
    for row in [*directories, *manifest['files']]:
        if not isinstance(row, dict):
            raise ValueError('Invalid migration filesystem entry')
        path, mode = row.get('source'), row.get('source_mode')
        if (not isinstance(path, str) or not Path(path).is_absolute() or '..' in Path(path).parts
                or path in seen or type(mode) is not int or not 0 <= mode <= 0o7777):
            raise ValueError('Invalid migration source identity or permission metadata')
        seen.add(path)
