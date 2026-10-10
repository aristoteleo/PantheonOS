"""Private, revision-checked Agent settings, applied only at backend restart.

This edits runtime preferences, not deployment grants, paths or credentials.
Unmanaged legacy fields are preserved on disk and never sent to the GUI.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from pantheon.utils.registry_lock import registry_lock
from pantheon.settings import strip_jsonc_comments


EDITABLE = frozenset({
    'models', 'image_gen_model', 'context_compression', 'think_system',
    'task_system', 'delegation', 'memory_system', 'learning_system', 'vision',
    'llm_retry', 'default_template_auto_update',
})
MAX_BYTES = 64 * 1024


class AgentSettingsDocument:
    def __init__(self, settings):
        # Freeze the current runtime before any GUI saves a pending revision.
        self._effective = {k: deepcopy(v) for k, v in settings.settings.items() if k in EDITABLE}
        self._path = Path(settings.pantheon_dir) / settings.SETTINGS_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = self._path.with_suffix('.lock')
        with registry_lock(self._lock):
            _, self._active_revision = self._read()

    def _read(self):
        if self._path.is_symlink():
            raise ValueError('Agent settings must not be a symlink')
        raw = self._path.read_bytes() if self._path.exists() else b'{}'
        if len(raw) > MAX_BYTES:
            raise ValueError('Agent settings exceed the document size limit')
        value = json.loads(strip_jsonc_comments(raw.decode('utf-8')))
        if not isinstance(value, dict):
            raise ValueError('Agent settings must be a JSON object')
        return value, sha256(raw).hexdigest()

    def _describe(self, value, revision):
        return {'protocol': 1, 'revision': revision, 'active_revision': self._active_revision,
                'restart_required': revision != self._active_revision,
                'overrides': {k: v for k, v in value.items() if k in EDITABLE},
                'effective': deepcopy(self._effective), 'editable_sections': sorted(EDITABLE)}

    def read(self):
        with registry_lock(self._lock):
            value, revision = self._read()
            return self._describe(value, revision)

    def save(self, expected_revision, overrides):
        if (not isinstance(expected_revision, str) or not isinstance(overrides, dict)
                or set(overrides) - EDITABLE):
            raise ValueError('Only declared Agent preference sections can be edited')
        # JSON roundtrip also detaches caller data; disallow non-finite values.
        serialized = json.dumps(overrides, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Agent settings exceed the document size limit')
        overrides = json.loads(serialized)
        for key, value in overrides.items():
            default = self._effective.get(key)
            if default is not None and type(value) is not type(default):
                raise ValueError(f'Invalid type for Agent setting: {key}')
        with registry_lock(self._lock):
            previous, revision = self._read()
            if expected_revision != revision:
                return {'success': False, 'conflict': True,
                        'message': 'Settings changed in another view. Reload before saving.'}
            value = {k: v for k, v in previous.items() if k not in EDITABLE}
            value.update(overrides)
            raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
            if len(raw) > MAX_BYTES:
                raise ValueError('Agent settings exceed the document size limit')
            fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=self._path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return {'success': True, **self._describe(value, sha256(raw).hexdigest())}
