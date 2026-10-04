"""Owner-side validation of the legacy launcher's backed-up dotenv file.

No process environment is inspected or changed. The optional private runtime
handoff is handled separately; platform-budget/OAuth conversion remains pending.
"""
from io import StringIO
import json
from pathlib import Path

from dotenv.parser import parse_stream
from dotenv.variables import parse_variables, Variable
from pantheon.settings import strip_jsonc_comments


def environment_source(spec):
    # Settings resolves even a user-level env_file against the selected launch
    # work_dir, not against ~/.pantheon or the selected .pantheon directory.
    value = spec.get('environment_file')
    if value is None:
        value = str(Path(spec['project_config']).parent / '.env')
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError('The migration environment file must be an explicit absolute path')
    path = Path(value)
    if path.is_symlink():
        raise ValueError('The migration environment file must not be a symlink')
    return path.resolve()


def read_environment(snapshot, manifest):
    """Return settings sources and dotenv values from the verified archive.

    The inventory deliberately never parses secrets. Once private backup exists,
    verify its selected env_file against user/project settings before consuming it.
    Unresolved interpolation is not borrowed from the migrator's environment.
    """
    from .migration_import import _snapshot_bytes
    spec = manifest['spec']
    blobs = {item['source']: item for item in manifest['files']}
    settings, env_file = {}, '.env'
    for name in ('global_config', 'project_config'):
        source = str(Path(spec[name]) / 'settings.json')
        if source not in blobs:
            continue
        try:
            value = json.loads(strip_jsonc_comments(_snapshot_bytes(snapshot, blobs[source], 1024 * 1024).decode()))
            if not isinstance(value, dict) or not isinstance(value.get('api_keys', {}), dict):
                raise ValueError
            if 'env_file' in value:
                env_file = value['env_file']
            settings[source] = value
        except (ValueError, UnicodeError):
            raise ValueError('Invalid legacy model settings document') from None
    if not isinstance(env_file, str) or not env_file:
        raise ValueError('Legacy env_file must name a file relative to the launch directory or an absolute file')
    declared = (Path(spec['project_config']).parent / env_file).resolve()
    expected = environment_source(spec)
    inventory = manifest['inventory'].get('environment')
    if (declared != expected or not isinstance(inventory, dict)
            or inventory.get('source') != str(expected) or type(inventory.get('exists')) is not bool):
        raise ValueError('Back up the effective env_file explicitly before importing legacy settings')
    if not inventory['exists']:
        if str(expected) in blobs:
            raise ValueError('Inconsistent environment backup')
        return settings, str(expected), {}
    if str(expected) in settings:
        raise ValueError('The legacy environment file cannot also be its settings document')
    item = blobs.get(str(expected))
    if item is None or item['category'] != 'opaque-configuration':
        raise ValueError('Missing private environment backup')
    values = {}
    try:
        raw = _snapshot_bytes(snapshot, item, 1024 * 1024).decode('utf-8')
        for entry in parse_stream(StringIO(raw)):
            if entry.error:
                raise ValueError
            if entry.key is None:
                continue
            if entry.value is None:
                values[entry.key] = None
                continue
            atoms = list(parse_variables(entry.value))
            if any(isinstance(atom, Variable) and atom.name not in values and atom.default is None for atom in atoms):
                raise ValueError
            values[entry.key] = ''.join(atom.resolve(values) for atom in atoms)
    except (ValueError, UnicodeError):
        # Parser errors can carry the raw line; never propagate secret text.
        raise ValueError('Legacy environment is malformed or requires an external interpolation value') from None
    # Even an empty non-model variable can change behavior (presence tests,
    # PATH, etc.). It must not disappear merely because it isn't a secret.
    from pantheon.settings import LEGACY_API_KEY_ENV_MAP
    from pantheon.utils.model_selector import PROVIDER_API_KEYS
    from pantheon.utils.llm_providers import get_provider_base_env
    from pantheon.utils.provider_registry import get_provider_config
    model_fields = {name for name in PROVIDER_API_KEYS.values() if name}
    model_fields.update(get_provider_base_env(provider, get_provider_config(provider))
                        for provider, key in PROVIDER_API_KEYS.items() if key)
    model_fields.update(LEGACY_API_KEY_ENV_MAP.values())
    if any(key not in model_fields and value is not None for key, value in values.items()):
        raise ValueError('Legacy environment contains fields requiring explicit scope conversion')
    return settings, str(expected), values
