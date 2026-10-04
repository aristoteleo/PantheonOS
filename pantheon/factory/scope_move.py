"""Move authored resources between the two scopes supplied by an Agent owner.

No process home lookup. The lock coordinates cooperating local writers, not
independent replicas; release cutover and crash recovery are separate concerns.
"""
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from pantheon.platform.registry_lock import registry_lock


def _check_path(root, path):
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Select a resource inside its configured scope, not the scope root')
    current = root
    for part in ('', *path.relative_to(root).parts):
        if part:
            current = current / part
        if current.is_symlink():
            raise ValueError('Scope changes do not follow symbolic links')
    # Catch linked ancestors above the scope root as well. The explicitly
    # configured parent may itself be a canonical filesystem location.
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Resource is outside its configured scope')


def _check_tree(path):
    if path.is_symlink():
        raise ValueError('Scope changes do not follow symbolic links')
    if path.is_file():
        return
    if not path.is_dir():
        raise ValueError('Only regular files and directories can change scope')
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(parent) / name
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                raise ValueError('Scope changes require regular files without symbolic links')


def _remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def move_template_scope(settings, kind, source_path, target_scope, overwrite=False, inspect_team=None):
    """Preserve legacy scope names, but bind both roots to the supplied settings."""
    if kind not in ('agents', 'teams', 'skills') or target_scope not in ('project', 'global'):
        raise ValueError('Choose agents, teams or skills and a project or global scope')
    if not isinstance(source_path, str) or not source_path or '\0' in source_path:
        raise ValueError('Supply a resource path')
    if type(overwrite) is not bool:
        raise ValueError('Overwrite must be a boolean')
    source = Path(source_path)
    if '..' in source.parts:
        raise ValueError('Scope paths must not contain parent traversal')
    roots = {scope: Path(getattr(settings, prefix + kind + '_dir')).absolute()
             for scope, prefix in [('project', ''), ('global', 'global_')]}
    first, second = roots['project'].resolve(), roots['global'].resolve()
    if first.is_relative_to(second) or second.is_relative_to(first):
        raise ValueError('Configured scopes must be separate, non-overlapping directories')
    with registry_lock(Path(settings.pantheon_dir) / '.template-scopes.lock'):
        if source.is_absolute():
            candidates = [source]
        else:
            relative = source
            for prefix in (Path('.pantheon') / kind, Path(kind), Path('.pantheon')):
                if relative.is_relative_to(prefix):
                    relative = relative.relative_to(prefix)
                    break
            candidates = [root / relative for root in roots.values()]
        selected = None
        for candidate in candidates:
            for scope, root in roots.items():
                if candidate.is_relative_to(root):
                    _check_path(root, candidate)
                    if candidate.exists():
                        selected = scope, root, candidate
                        break
            if selected:
                break
        if not selected:
            raise ValueError('Resource was not found in this Agent\'s configured scopes')
        scope, root, source = selected
        # Selecting SKILL.md or a support file moves the containing bundle.
        if kind == 'skills' and source.is_file():
            source = source.parent
        _check_path(root, source)
        _check_tree(source)
        if kind != 'skills' and not source.is_file():
            raise ValueError('Select one agent or team definition')
        warnings = []
        if kind == 'teams' and inspect_team is not None:
            warnings.extend(inspect_team(source))
        if scope == target_scope:
            return {'success': True, 'message': 'Resource is already in the selected scope'}
        destination = roots[target_scope] / source.relative_to(root)
        _check_path(roots[target_scope], destination)
        if destination.exists():
            if not overwrite:
                return {'success': False, 'conflict': True,
                        'message': 'A resource with this name already exists in the target scope.'}
            _check_tree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.scope-move-', dir=destination.parent))
        payload, backup = staging / 'payload', staging / 'previous'
        removed_source = source.with_name('.scope-move-' + uuid4().hex)
        published = False
        try:
            if source.is_dir():
                shutil.copytree(source, payload)
            else:
                shutil.copy2(source, payload)
            if destination.exists():
                os.replace(destination, backup)
            # Renaming within the source filesystem is reversible if publish
            # fails; never recursively delete the source before publication.
            os.replace(source, removed_source)
            os.replace(payload, destination)
            published = True
        except Exception:
            # If recovery itself fails, retain staging and the renamed source.
            # Do not remove the only recoverable copy in a finally block.
            if removed_source.exists():
                os.replace(removed_source, source)
            if backup.exists():
                os.replace(backup, destination)
            _remove(staging)
            raise
        if published:
            for path in (removed_source, staging):
                try:
                    _remove(path)
                except OSError:
                    warnings.append('The move succeeded, but a temporary backup could not be removed.')
            return {'success': True, 'message': f'Moved to {target_scope}', 'warnings': warnings}
