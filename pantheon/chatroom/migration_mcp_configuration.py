"""Private, backed-up MCP launch configuration to an ordinary prepared App.

The old gateway supplies effective launch coordinates and tool contracts. The
owner selects target coordinates explicitly; credentials reuse Fleet's vault.
This builds/provisions a candidate. It does not admit an Agent data import or
claim that source executables/assets exist on a different target node.
"""
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re

from .migration_backup import (_encoded, _hash_file, _private_dir, _private_file,
                               _read_json, verify_backup)
from .migration_mcp_handoff import _environment, NAME
from pantheon.apps.builtin.mcp.scoped import endpoint
from pantheon.platform.mcp_package import validate_migration_contract, build_migration_package

LIMIT = 2 * 1024 * 1024


def _coordinates(command, cwd):
    return (isinstance(command, list) and 1 <= len(command) <= 128
        and all(isinstance(arg, str) and len(arg) <= 16384 and '\0' not in arg for arg in command)
        and Path(command[0]).is_absolute() and isinstance(cwd, str)
        and '\0' not in cwd and Path(cwd).is_absolute())


def _endpoint(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return endpoint(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Choose a valid MCP HTTP endpoint without inline credentials') from None


def _validate(document):
    if (not isinstance(document, dict)
            or set(document) != {'protocol', 'kind', 'project_config', 'global_config', 'overrides', 'servers', 'contract'}
            or type(document['protocol']) is not int or document['protocol'] != 1
            or document['kind'] != 'mcp-runtime-configuration'
            or any(not isinstance(document[k], str) or not Path(document[k]).is_absolute()
                   for k in ('project_config', 'global_config'))):
        raise ValueError('Invalid private MCP runtime configuration')
    validate_migration_contract(document['contract'])
    servers = document['servers']
    if (not isinstance(servers, dict) or set(servers) != {s['server'] for s in document['contract']['exports'].values()}
            or not isinstance(document['overrides'], dict)
            or set(document['overrides']) != {str(Path(document[k])/'mcp.json') for k in ('project_config', 'global_config')}
            or any(v is not None and (not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v))
                   for v in document['overrides'].values())):
        raise ValueError('MCP configuration does not match its captured tool contract')
    for name, row in servers.items():
        if not NAME.fullmatch(name) or not isinstance(row, dict):
            raise ValueError('Invalid captured MCP server')
        if row.get('transport') == 'stdio':
            env = row.get('environment')
            if (set(row) != {'transport', 'command', 'cwd', 'environment'}
                    or not _coordinates(row['command'], row['cwd']) or not isinstance(env, dict)
                    or set(env) != {'declarations', 'values'} or not _environment(env['declarations'])
                    or not _environment(env['values'], absent=True)
                    or not env['declarations'].keys() <= env['values'].keys()):
                raise ValueError('MCP launch coordinates/environment were not captured')
        elif row.get('transport') == 'http':
            if set(row) != {'transport', 'url'}:
                raise ValueError('Invalid captured MCP HTTP server')
            _endpoint(row['url'])
        else:
            raise ValueError('Unsupported captured MCP transport')
    if len(_encoded(document)) > LIMIT:
        raise ValueError('MCP runtime configuration exceeds its private limit')
    return document


def _overrides(settings):
    result = {}
    for root in (settings.pantheon_dir, settings.user_home):
        path = root.resolve()/'mcp.json'
        result[str(path)] = _hash_file(path, max_bytes=1024*1024)['sha256'] if path.exists() or path.is_symlink() else None
    return result


async def export_mcp_configuration(settings, manager, *, operation_id, servers, providers):
    """Capture effective servers under the gateway lock; return a private path.

    servers maps every selected source server to extra inherited env names.
    Every declared stdio variable is included automatically; HTTP entries use [].
    No launch value, credential, endpoint or credential hash is returned by RPC.
    """
    from pantheon.settings import Settings
    from .migration_mcp_tools import capture_mcp_tools
    if (not isinstance(settings, Settings) or not isinstance(operation_id, str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', operation_id)
            or manager._config_path is None
            or Path(manager._config_path).resolve() != (settings.pantheon_dir/'mcp.json').resolve()
            or not isinstance(servers, dict)):
        raise ValueError('Supply the original gateway and a stable capture operation')
    before = _overrides(settings)
    contract = await capture_mcp_tools(manager, providers=providers)
    if set(servers) != {spec['server'] for spec in contract['exports'].values()}:
        raise ValueError('Capture every server used by the selected tool contract')
    from fastmcp.client.transports import StdioTransport
    from .migration_mcp_handoff import ENV
    rows = {}
    for name, extra in servers.items():
        if (not isinstance(extra, list) or len(extra) > 64
                or any(not isinstance(k, str) or not ENV.fullmatch(k) for k in extra)
                or len(extra) != len(set(extra))):
            raise ValueError('Select explicit inherited MCP environment fields')
        instance = manager.instances[name]
        if instance.config.type == 'stdio':
            transport = instance.stdio_transport
            if not isinstance(transport, StdioTransport) or not isinstance(transport.env, dict):
                raise ValueError('Original MCP launch environment is unavailable')
            declarations = deepcopy(instance.config.env)
            names = set(declarations) | set(extra)
            rows[name] = {'transport': 'stdio', 'command': [transport.command, *transport.args],
                'cwd': str(transport.cwd) if transport.cwd is not None else None,
                'environment': {'declarations': declarations,
                    'values': {key: transport.env.get(key) for key in sorted(names)}}}
        else:
            if extra:
                raise ValueError('HTTP MCP servers have no captured stdio environment')
            rows[name] = {'transport': 'http', 'url': instance.config.uri}
    document = _validate({'protocol': 1, 'kind': 'mcp-runtime-configuration',
        'project_config': str(settings.pantheon_dir.resolve()), 'global_config': str(settings.user_home.resolve()),
        'overrides': before, 'servers': rows, 'contract': contract})
    if before != _overrides(settings):
        raise ValueError('MCP override configuration changed during capture')
    from .data_fence import _open, _sync_directory
    directory = _private_dir(settings.user_home.resolve()/'fleet-node/agent-migration/handoffs')
    path = directory/(operation_id+'-mcp-configuration.json')
    if path.exists() or path.is_symlink():
        _private_file(path)
        if _read_json(path) != document:
            raise ValueError('This MCP capture operation already holds another configuration')
    else:
        fd = _open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(_encoded(document)); stream.flush(); os.fsync(stream.fileno())
        _sync_directory(directory)
    return {'protocol': 1, 'source': str(path), 'requires_writer_fence': True}


def read_mcp_configuration(snapshot, manifest):
    source = manifest['spec'].get('mcp_configuration_file')
    if (source is None or manifest['inventory'].get('mcp_configuration') != {'source': source, 'exists': True}):
        raise ValueError('Missing backed-up MCP runtime configuration')
    files = {item['source']: item for item in manifest['files']}
    item = files.get(source)
    if item is None or item['category'] != 'opaque-configuration':
        raise ValueError('Missing private MCP configuration blob')
    from .migration_import import _snapshot_bytes
    import json
    try:
        value = _validate(json.loads(_snapshot_bytes(snapshot, item, LIMIT)))
        if any(value[k] != str(Path(manifest['spec'][k]).resolve()) for k in ('project_config', 'global_config')):
            raise ValueError
        for path, digest in value['overrides'].items():
            actual = files.get(path)
            if ((digest is None and actual is not None) or (digest is not None and
                    (actual is None or actual['category'] != 'opaque-configuration' or actual['sha256'] != digest))):
                raise ValueError
        return source, value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('MCP configuration does not match the captured source snapshot') from None


class MCPConfigurationConversion:
    """Reviewed launch relocation and existing vault conversion for a candidate.

    targets maps each stdio server to {command, cwd}, or each HTTP server to
    {url}. Explicit target coordinates allow cross-node relocation without
    treating the source host's filesystem or localhost as universally reachable.
    """
    def __init__(self, snapshot, *, digest, fence, vault, targets, environments):
        from .data_fence import MigrationFence
        from pantheon.models.credentials import LocalModelCredentialVault
        if not isinstance(fence, MigrationFence) or not isinstance(vault, LocalModelCredentialVault):
            raise ValueError('Supply the live migration fence and target Fleet vault')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        self._snapshot, self._digest, self._fence = Path(snapshot), digest, fence
        manifest = _read_json(self._snapshot/'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('MCP configuration belongs to another migration')
        _, document = read_mcp_configuration(self._snapshot, manifest)
        sources = document['servers']
        stdio = {name for name, row in sources.items() if row['transport'] == 'stdio'}
        if (not isinstance(targets, dict) or set(targets) != set(sources)
                or not isinstance(environments, dict) or set(environments) != stdio):
            raise ValueError('Review target coordinates and every stdio environment')
        self._environment = None
        if stdio:
            from .migration_mcp_credentials import MCPEnvironmentConversion
            self._environment = MCPEnvironmentConversion(snapshot, digest=digest, fence=fence, vault=vault,
                                                         servers=environments)
        env = self._environment.describe() if self._environment else {'servers': {}, 'credentials': {}}
        prepared = {}
        for name, target in targets.items():
            if not isinstance(target, dict):
                raise ValueError('Invalid target MCP configuration')
            if name in stdio:
                if set(target) != {'command', 'cwd'} or not _coordinates(target['command'], target['cwd']):
                    raise ValueError('Choose an absolute target MCP command and working directory')
                prepared[name] = {'transport': 'stdio', **deepcopy(target), **env['servers'][name]}
            else:
                if set(target) != {'url'}:
                    raise ValueError('Choose the target MCP HTTP endpoint')
                _endpoint(target['url'])
                prepared[name] = {'transport': 'http', **deepcopy(target)}
        self._identity = deepcopy(fence.identity)
        self._descriptor = {'protocol': 1, 'owner': vault.owner, 'node_id': vault.node_id,
            'values': {'mcp': {'protocol': 1, 'servers': prepared}}, 'credentials': env['credentials'],
            'contract': document['contract']}
        if len(_encoded(self._descriptor)) > LIMIT:
            raise ValueError('MCP candidate configuration exceeds its limit')

    def describe(self):
        return deepcopy(self._descriptor)

    def assert_current(self):
        self._fence.assert_owned()
        verify_backup(self._snapshot, digest=self._digest)
        manifest = _read_json(self._snapshot/'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != self._digest or manifest['fence'] != self._identity:
            raise ValueError('MCP candidate source snapshot changed')
        from .migration_import import _unchanged_sources
        _unchanged_sources(manifest)

    def provision(self):
        self.assert_current()
        if self._environment is not None:
            self._environment.provision()

    def build(self, destination, platform, *, transport=None):
        self.assert_current()
        return build_migration_package(destination, platform, contract=self._descriptor['contract'],
            credential_slots=list(self._descriptor['credentials']), transport=transport)
