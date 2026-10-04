"""Prepared filesystem service. The legacy combined toolset remains available.

This package advertises filesystem operations only; model-assisted inspection and
image generation belong to explicitly bound model/image Apps, not an embedded
Agent. A workspace is a default path, not an OS sandbox: owner grants must bound
paths or deploy this service under an appropriately restricted OS identity.
"""
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from .file_manager import FileManagerToolSet

METHODS = frozenset(('read_file', 'write_file', 'update_file', 'glob', 'grep', 'apply_patch',
    'view_file_outline', 'list_files', 'stat_path', 'get_cwd', 'manage_path',
    'create_directory', 'delete_path', 'move_file'))


def create_service(config):
    if (not isinstance(config, Mapping) or set(config) - {'workspace', 'limits'}
            or not isinstance(config.get('workspace'), str) or not Path(config['workspace']).is_absolute()):
        raise ValueError('Files needs an explicit absolute workspace')
    workspace = Path(config['workspace'])
    if not workspace.is_dir():
        raise ValueError('Files workspace must exist on its node')
    limits = config.get('limits', {})
    ceilings = dict(max_file_read_chars=8*1024*1024, max_file_read_lines=100000, max_glob_results=100000)
    defaults = dict(max_file_read_chars=50000, max_file_read_lines=800, max_glob_results=1000)
    if (not isinstance(limits, Mapping) or limits.keys() - ceilings.keys()
            or any(type(value) is not int or not 1 <= value <= ceilings[key] for key, value in limits.items())):
        raise ValueError('Invalid Files response limits')
    service = FileManagerToolSet('file_manager', workspace,
        file_settings=SimpleNamespace(**(defaults | dict(limits))), template_fallback=False)
    service.functions = {name: value for name, value in service.functions.items() if name in METHODS}
    if service.functions.keys() != METHODS:
        raise ValueError('Files package methods differ from the source toolset')
    return service


async def register(ctx):
    configuration = load_runtime_configuration(required=True)
    service = create_service(configuration.values.get('files'))
    await register_toolset(ctx, service)
