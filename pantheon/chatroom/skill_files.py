"""Bounded authoring access to the Agent's own skill bundles.

Paths are relative to explicit configuration scopes. Workspace files, OS home,
credentials and other Apps are not resources of this interface.
"""
import base64
from hashlib import sha256
import os
from pathlib import Path
import tempfile

from pantheon.factory.scope_move import _check_path, _check_tree, _remove, move_template_scope
from pantheon.platform.registry_lock import registry_lock

CHUNK = 48 * 1024
MAX_EDIT = 64 * 1024
PAGE = 128


class AgentSkillFiles:
    def __init__(self, settings):
        self.settings = settings
        self.roots = {'project': Path(settings.skills_dir).absolute(),
                      'global': Path(settings.global_skills_dir).absolute()}
        self.lock = Path(settings.pantheon_dir) / '.template-scopes.lock'

    def _path(self, scope, path, *, root_allowed=False):
        if scope not in self.roots or not isinstance(path, str):
            raise ValueError('Choose an Agent skill scope and relative path')
        if ('\\' in path or '\0' in path or path.startswith('/')
                or any(part in ('.', '..') for part in path.split('/'))):
            raise ValueError('Skill paths must stay inside their configured scope')
        root = self.roots[scope]
        result = root / path
        if result == root:
            if not root_allowed or root.is_symlink():
                raise ValueError('Select a skill resource, not its scope root')
        else:
            _check_path(root, result)
        return result

    @staticmethod
    def _revision(path):
        stat = path.stat()
        return sha256(str((stat.st_dev, stat.st_ino, stat.st_size,
                           stat.st_mtime_ns, stat.st_ctime_ns)).encode()).hexdigest()

    def call(self, operation, scope, path='', *, content=None, revision=None, offset=0,
             target_scope=None, overwrite=False):
        if operation == 'move':
            source = self._path(scope, path)
            return move_template_scope(self.settings, 'skills', str(source), target_scope, overwrite)
        if operation not in ('list', 'stat', 'read', 'write', 'mkdir', 'delete'):
            raise ValueError('Unsupported Agent skill operation')
        if type(offset) is not int or offset < 0:
            raise ValueError('Invalid skill offset')
        with registry_lock(self.lock):
            selected = self._path(scope, path, root_allowed=operation == 'list')
            if operation == 'list':
                if not selected.exists():
                    return {'files': [], 'next_offset': None}
                entries = sorted(selected.iterdir(), key=lambda p: p.name)
                rows = []
                for item in entries[offset:offset+PAGE]:
                    if item.name.startswith('.scope-move-') or item.name.startswith('.skill-edit-'):
                        continue
                    _check_path(self.roots[scope], item)
                    info = item.stat()
                    if not (item.is_file() or item.is_dir()):
                        continue
                    rows.append({'name': item.name, 'type': 'directory' if item.is_dir() else 'file',
                                 'size': info.st_size, 'last_modified': info.st_mtime})
                return {'files': rows, 'next_offset': offset+PAGE if offset+PAGE < len(entries) else None}
            if operation in ('stat', 'read'):
                if not selected.is_file():
                    raise ValueError('Skill file does not exist')
                current = self._revision(selected)
                if revision is not None and revision != current:
                    raise ValueError('Skill changed while reading. Reopen it before continuing.')
                if operation == 'stat':
                    return {'revision': current, 'size': selected.stat().st_size}
                with selected.open('rb') as stream:
                    stream.seek(offset)
                    data = stream.read(CHUNK)
                if self._revision(selected) != current:
                    raise ValueError('Skill changed while reading. Reopen it before continuing.')
                return {'revision': current, 'data': base64.b64encode(data).decode('ascii'),
                        'next_offset': offset+len(data), 'eof': offset+len(data) >= selected.stat().st_size}
            if operation == 'write':
                if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_EDIT:
                    raise ValueError('Skill text edits must be UTF-8 text no larger than 64 KiB')
                if selected.exists() and not selected.is_file():
                    raise ValueError('Select a skill file')
                current = self._revision(selected) if selected.exists() else None
                if revision != current:
                    return {'success': False, 'conflict': True,
                            'message': 'Skill changed in another view. Reopen before saving.'}
                selected.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.skill-edit-', dir=selected.parent)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(content.encode('utf-8'))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, selected)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                return {'success': True, 'revision': self._revision(selected)}
            if operation == 'mkdir':
                selected.mkdir(parents=True, exist_ok=True)
                return {'success': True}
            _check_tree(selected)
            _remove(selected)
            return {'success': True}
