"""Private model-environment handoff from the legacy owner runtime.

Only the legacy runtime exports its actual Settings environment. The migrator
reads this explicitly selected, backed-up file, never its own process environment.
This is configuration evidence, not a writer fence or a ready-to-cut-over claim.
"""
import json
import os
from pathlib import Path
import re
import stat

from pantheon.settings import Settings, LEGACY_API_KEY_ENV_MAP
from pantheon.utils.model_selector import PROVIDER_API_KEYS
from pantheon.utils.llm_providers import get_provider_base_env
from pantheon.utils.provider_registry import get_provider_config


def environment_fields():
    fields = {name for name in PROVIDER_API_KEYS.values() if name}
    fields.update(get_provider_base_env(provider, get_provider_config(provider))
                  for provider, key in PROVIDER_API_KEYS.items() if key)
    fields.update(LEGACY_API_KEY_ENV_MAP.values())
    # Capture these so an active fallback/budget cannot silently become BYOK.
    # Budget conversion is explicit; fallback/local-engine fields still block it.
    fields.update(('LLM_API_BASE', 'LLM_API_KEY', 'LLM_FORCE_PROXY', 'PLATFORM_MODEL_MODE',
                   'PANTHEON_PLATFORM_PROXY_BASE', 'PANTHEON_PLATFORM_PROXY_KEY', 'OLLAMA_API_BASE'))
    return fields


def export_model_handoff(settings, *, operation_id):
    if (not isinstance(settings, Settings) or not isinstance(operation_id, str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', operation_id)):
        raise ValueError('Supply the live legacy settings and a stable handoff operation')
    # Ensure loading has finished, then copy the exact environment view once.
    # In particular, a key removed after dotenv loading must stay absent.
    settings._ensure_loaded()
    environment = dict(os.environ if settings._environment is None else settings._environment)
    values = {name: environment.get(name) for name in sorted(environment_fields())}
    if any(value is not None and (not isinstance(value, str) or len(value) > 16384) for value in values.values()):
        raise ValueError('Legacy model environment exceeds the handoff limits')
    document = {'protocol': 1, 'kind': 'agent-model-environment',
        'project_config': str(settings.pantheon_dir.resolve()),
        'global_config': str(settings.user_home.resolve()), 'values': values}
    from .migration_backup import _encoded, _private_dir, _private_file, _read_json
    from .data_fence import _open, _sync_directory
    directory = _private_dir(settings.user_home.resolve() / 'fleet-node/agent-migration/handoffs')
    path = directory / (operation_id + '.json')
    raw = _encoded(document)
    if len(raw) > 1024 * 1024:
        raise ValueError('Legacy model environment exceeds the handoff limits')
    if path.exists() or path.is_symlink():
        _private_file(path)
        if _read_json(path) != document:
            raise ValueError('This handoff operation already captured different settings; use a new operation')
    else:
        fd = _open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        _sync_directory(directory)
    # No value or hash of a secret appears in the owner RPC response.
    return {'protocol': 1, 'source': str(path), 'project_config': document['project_config'],
            'global_config': document['global_config'], 'requires_writer_fence': True}


def handoff_source(spec):
    value = spec.get('model_environment_file')
    if value is None:
        return None
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError('Model environment handoff must name an absolute private file')
    path = Path(value)
    if path.is_symlink():
        raise ValueError('Model environment handoff cannot be a symlink')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name == 'posix'
            and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
        raise ValueError('Model environment handoff must be an owner-private regular file')
    return path.resolve()


def read_handoff(snapshot, manifest):
    """Read only verified backup bytes, including explicit absent env fields."""
    source = manifest['spec'].get('model_environment_file')
    if source is None:
        if 'model_environment' in manifest['inventory']:
            raise ValueError('Unexpected runtime model handoff in backup')
        return None, None
    inventory = manifest['inventory'].get('model_environment')
    if not isinstance(inventory, dict) or inventory != {'source': source, 'exists': True}:
        raise ValueError('Model environment handoff does not match this backup')
    item = next((item for item in manifest['files'] if item['source'] == source), None)
    if item is None or item['category'] != 'opaque-configuration':
        raise ValueError('Missing private model environment handoff backup')
    from .migration_import import _snapshot_bytes
    try:
        value = json.loads(_snapshot_bytes(snapshot, item, 1024 * 1024))
        if (not isinstance(value, dict) or set(value) != {'protocol', 'kind', 'project_config', 'global_config', 'values'}
                or type(value['protocol']) is not int or value['protocol'] != 1
                or value['kind'] != 'agent-model-environment'
                or any(value[key] != str(Path(manifest['spec'][key]).resolve()) for key in ('project_config', 'global_config'))
                or not isinstance(value['values'], dict) or set(value['values']) != environment_fields()
                or any(v is not None and (not isinstance(v, str) or len(v) > 16384) for v in value['values'].values())):
            raise ValueError
        return source, value['values']
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('Model environment handoff is invalid or belongs to different settings') from None
