"""Private environment evidence from the original legacy MCP gateway.

Capture the transport's saved launch environment, not today's Agent or
migrator environment. This is a selected environment handoff, not a complete
MCP server/tool publication or a claim that a child is still alive.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

from pantheon.settings import Settings
from .migration_backup import _encoded, _private_dir, _private_file, _read_json


ENV = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,127}\Z')
MAX_BYTES = 1024 * 1024


def _environment(value, *, absent=False):
    return (isinstance(value, dict) and len(value) <= 64
        and all(isinstance(k, str) and ENV.fullmatch(k)
            and ((v is None and absent) or (isinstance(v, str) and len(v) <= 16384 and '\0' not in v))
            for k, v in value.items()))


def _servers(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        raise ValueError('Select the original MCP stdio servers for environment capture')
    for name, row in value.items():
        if (not isinstance(name, str) or not NAME.fullmatch(name)
                or not isinstance(row, dict) or set(row) != {'declarations', 'values'}
                or not _environment(row['declarations']) or not _environment(row['values'], absent=True)
                or not row['declarations'].keys() <= row['values'].keys()):
            raise ValueError('Invalid MCP environment handoff')
    return value


def export_mcp_handoff(settings, manager, *, operation_id, servers):
    """Write selected launch env privately; return no key, endpoint or value hash.

    `servers` maps original names to extra inherited env names to capture. Every
    declared variable is captured automatically, including explicit absence.
    The caller must hold the legacy manager's lock. No server is started here.
    """
    from pantheon.apps.builtin.mcp.manager import MCPManager
    from fastmcp.client.transports import StdioTransport
    if (not isinstance(settings, Settings) or not isinstance(manager, MCPManager)
            or not isinstance(operation_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', operation_id)
            or not isinstance(servers, dict) or not 1 <= len(servers) <= 64
            or manager._config_path is None
            or Path(manager._config_path).resolve() != (settings.pantheon_dir / 'mcp.json').resolve()):
        raise ValueError('Supply the original MCP gateway and a stable capture operation')
    rows = {}
    for name, extra in servers.items():
        if (not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(extra, list)
                or len(extra) > 64 or any(not isinstance(k, str) or not ENV.fullmatch(k) for k in extra)
                or len(extra) != len(set(extra))):
            raise ValueError('Select explicit inherited environment names for each MCP server')
        instance = manager.instances.get(name)
        if (instance is None or instance.config.type != 'stdio' or instance.status != 'running'
                or not isinstance(instance.stdio_transport, StdioTransport)
                or not isinstance(instance.stdio_transport.env, dict)):
            raise ValueError('The original MCP stdio launch environment is unavailable')
        declarations = deepcopy(instance.config.env)
        if not _environment(declarations):
            raise ValueError('Invalid MCP environment declarations')
        names = set(declarations) | set(extra)
        environment = instance.stdio_transport.env
        rows[name] = {'declarations': declarations,
                      'values': {key: environment.get(key) for key in sorted(names)}}
    _servers(rows)
    document = {'protocol': 1, 'kind': 'mcp-launch-environment',
        'project_config': str(settings.pantheon_dir.resolve()),
        'global_config': str(settings.user_home.resolve()), 'servers': rows}
    raw = _encoded(document)
    if len(raw) > MAX_BYTES:
        raise ValueError('MCP environment handoff exceeds its private file limit')
    from .data_fence import _open, _sync_directory
    directory = _private_dir(settings.user_home.resolve() / 'fleet-node/agent-migration/handoffs')
    path = directory / (operation_id + '-mcp.json')
    if path.exists() or path.is_symlink():
        _private_file(path)
        if _read_json(path) != document:
            raise ValueError('This MCP handoff already captured another environment; use a new operation')
    else:
        fd = _open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        _sync_directory(directory)
    return {'protocol': 1, 'source': str(path), 'project_config': document['project_config'],
            'global_config': document['global_config'], 'requires_writer_fence': True}


def read_mcp_handoff(snapshot, manifest):
    """Read the captured values only from the caller's verified private backup."""
    source = manifest['spec'].get('mcp_environment_file')
    inventory = manifest['inventory'].get('mcp_environment')
    if source is None:
        if inventory is not None:
            raise ValueError('Unexpected MCP environment handoff')
        return None, None
    if inventory != {'source': source, 'exists': True}:
        raise ValueError('MCP environment handoff does not match this backup')
    item = next((item for item in manifest['files'] if item['source'] == source), None)
    if item is None or item['category'] != 'opaque-configuration':
        raise ValueError('Missing private MCP environment handoff backup')
    from .migration_import import _snapshot_bytes
    try:
        value = json.loads(_snapshot_bytes(snapshot, item, MAX_BYTES))
        if (not isinstance(value, dict) or set(value) != {'protocol', 'kind', 'project_config', 'global_config', 'servers'}
                or type(value['protocol']) is not int or value['protocol'] != 1
                or value['kind'] != 'mcp-launch-environment'
                or any(value[key] != str(Path(manifest['spec'][key]).resolve()) for key in ('project_config', 'global_config'))):
            raise ValueError
        return source, _servers(value['servers'])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('MCP environment handoff is invalid or belongs to different settings') from None
