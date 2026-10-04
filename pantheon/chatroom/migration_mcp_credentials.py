"""Move explicitly reviewed MCP environment keys into the existing Fleet vault.

Owner-side and snapshot-bound. This converts declared user/project MCP env
overrides, optionally with a private launch-environment handoff from the original
gateway. It does not convert the whole gateway/tool menu. The migrator never
reads its own process environment. No Agent data is admitted or MCP launched.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from pantheon.models.credentials import LocalModelCredentialVault, model_credential_endpoint
from pantheon.settings import strip_jsonc_comments
from .data_fence import MigrationFence
from .migration_backup import _encoded, _read_json, verify_backup


ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
ALIAS = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')


def _overrides(snapshot, manifest):
    from .migration_import import _snapshot_bytes
    files = {item['source']: item for item in manifest['files']}
    result, origins, kinds = {}, {}, {}
    for root in ('global_config', 'project_config'):
        source = str(Path(manifest['spec'][root]) / 'mcp.json')
        item = files.get(source)
        if item is None:
            continue
        if item['category'] != 'opaque-configuration':
            raise ValueError('MCP configuration is missing from its private backup')
        try:
            value = json.loads(strip_jsonc_comments(_snapshot_bytes(snapshot, item, 1024 * 1024).decode()))
            if not isinstance(value, dict) or not isinstance(value.get('servers', {}), dict):
                raise ValueError
            for name, server in value.get('servers', {}).items():
                if not isinstance(server, dict):
                    raise ValueError
                # A missing type may have come from factory defaults that this
                # archive does not capture. Do not guess the old transport.
                kinds[name] = server.get('type', kinds.get(name))
                if 'env' not in server:
                    continue
                env = server['env']
                if (not isinstance(env, dict) or len(env) > 64
                        or any(not ENV_NAME.fullmatch(key) or not isinstance(val, str) or '\0' in val
                               for key, val in env.items())):
                    raise ValueError
                # Match Settings' recursive user -> project merge. An empty
                # project env object does not remove the user's earlier fields.
                result.setdefault(name, {}).update(env)
                origins.setdefault(name, {}).update({key: source for key in env})
        except (ValueError, TypeError, UnicodeError):
            raise ValueError('MCP environment declarations require explicit conversion') from None
    return result, origins, kinds


class MCPEnvironmentConversion:
    """Review every declared env field of each selected server as literal/secret.

    servers = {name: {literals: [env_name], credentials: {env_name: {
        source: absolute_mcp_json, alias: slot, ref: node-secret://..., endpoint: ...
    }}}}. A key source must be the effective override, not an overwritten value.
    Returned prepared env values contain only reviewed literals and credential
    references. Keys are passed privately to Fleet's existing `credentials ensure`.
    """
    def __init__(self, snapshot, *, digest, fence, vault, servers):
        if not isinstance(fence, MigrationFence) or not isinstance(vault, LocalModelCredentialVault):
            raise ValueError('Supply the live migration fence and selected local Fleet vault')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        snapshot = Path(snapshot)
        manifest = _read_json(snapshot / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('MCP credential snapshot belongs to another migration')
        if not isinstance(servers, dict) or not 1 <= len(servers) <= 64:
            raise ValueError('Select the MCP servers whose declared environment is being converted')
        envs, origins, kinds = _overrides(snapshot, manifest)
        from .migration_mcp_handoff import read_mcp_handoff
        runtime_source, captured = read_mcp_handoff(snapshot, manifest)
        if captured is not None:
            for name in servers:
                row = captured.get(name)
                if (row is None or (name in kinds and kinds[name] not in (None, 'stdio'))
                        or any(key not in row['declarations'] or row['declarations'][key] != value
                               for key, value in envs.get(name, {}).items())):
                    raise ValueError('MCP runtime capture and backed-up declarations disagree')
                # The original transport, including captured factory declarations
                # and explicitly selected inherited fields, is authoritative.
                envs[name] = row['values']
                origins[name] = {key: runtime_source for key in row['values']}
                kinds[name] = 'stdio'
        prepared, credentials, sources, entries, refs = {}, {}, [], [], set()
        for name, selection in servers.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', name)
                    or name not in envs or kinds[name] != 'stdio' or not isinstance(selection, dict)
                    or set(selection) != {'literals', 'credentials'}
                    or not isinstance(selection['literals'], list)
                    or not isinstance(selection['credentials'], dict)):
                raise ValueError('Supply explicit MCP environment selections')
            literal, secret = selection['literals'], selection['credentials']
            if (any(not isinstance(v, str) or not ENV_NAME.fullmatch(v) for v in literal)
                    or len(set(literal)) != len(literal) or set(literal) & secret.keys()
                    or set(literal) | secret.keys() != envs[name].keys()):
                raise ValueError('Classify every declared MCP environment field exactly once')
            if any(value is None for value in envs[name].values()):
                raise ValueError('An absent MCP environment variable needs an explicit omission policy')
            if captured is None and any(value.startswith('${') and value.endswith('}') for value in envs[name].values()):
                raise ValueError('MCP environment references require a captured runtime handoff')
            target = {'env': {key: envs[name][key] for key in literal}, 'env_credentials': {}}
            for variable, binding in secret.items():
                if (not isinstance(binding, dict) or set(binding) != {'source', 'alias', 'ref', 'endpoint'}
                        or binding['source'] != origins[name][variable]
                        or not isinstance(binding['alias'], str) or not ALIAS.fullmatch(binding['alias'])
                        or binding['alias'] in credentials
                        or not isinstance(binding['ref'], str) or not binding['ref'].startswith('node-secret://')
                        or not ALIAS.fullmatch(binding['ref'].removeprefix('node-secret://'))
                        or binding['ref'] in refs):
                    raise ValueError('Invalid MCP credential source or target binding')
                try:
                    endpoint = model_credential_endpoint(binding['endpoint'])
                except (ValueError, TypeError, AttributeError):
                    raise ValueError('Invalid MCP credential endpoint') from None
                key = envs[name][variable]
                if not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
                    raise ValueError('MCP credential is not a supported API key')
                alias, ref = binding['alias'], binding['ref']
                credentials[alias] = {'ref': ref, 'endpoint': endpoint}
                target['env_credentials'][variable] = {'credential': alias, 'endpoint': endpoint}
                sources.append({'source': binding['source'], 'server': name, 'variable': variable, 'alias': alias})
                entries.append((ref, endpoint, key))
                refs.add(ref)
            prepared[name] = target
        if len(entries) > 16:
            raise ValueError('Select at most 16 MCP API credentials for this App')
        self._descriptor = {'protocol': 1, 'owner': vault.owner, 'node_id': vault.node_id,
                            'servers': prepared, 'credentials': credentials, 'sources': sources}
        if len(_encoded(self._descriptor)) > 64 * 1024:
            raise ValueError('MCP environment conversion exceeds its prepared configuration limit')
        self._snapshot, self._digest = snapshot, digest
        self._fence, self._identity = fence, deepcopy(fence.identity)
        self._vault, self._entries = vault, entries

    def describe(self):
        return deepcopy(self._descriptor)

    def assert_matches(self, digest, fence):
        fence.assert_owned()
        if self._digest != digest or self._identity != fence.identity:
            raise ValueError('MCP credentials belong to another migration')

    def provision(self):
        from .migration_import import _unchanged_sources
        self.assert_matches(self._digest, self._fence)
        verify_backup(self._snapshot, digest=self._digest)
        manifest = _read_json(self._snapshot / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != self._digest or manifest['fence'] != self._identity:
            raise ValueError('MCP credential snapshot changed after review')
        _unchanged_sources(manifest)
        for ref, endpoint, key in self._entries:
            self._fence.assert_owned()
            # Existing values must match; an interrupted conversion can resume
            # without rotating another App's credential or deleting its data.
            self._vault.ensure(ref, endpoint, key)
        self._fence.assert_owned()
