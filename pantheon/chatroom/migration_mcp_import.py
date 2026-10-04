"""Admit captured MCP configuration using an explicitly pinned ordinary App.

Secrets remain in the fenced backup and provider node vault. The Agent receives
only tool schemas, default selection and exact provider identities. Import is
not evidence of a running provider: the normal allocator/gateway still owns
availability and authorization, and the consumer checks every delivered grant.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from pantheon.apps.agent_defaults import with_dependency_defaults
from pantheon.apps.dependency_assembly import _copy, _identity, _matches, IDENT
from .migration_backup import _encoded, _read_json


class MCPImportConversion:
    def __init__(self, configuration, candidate, *, provider, agent_node_id):
        from .migration_mcp_configuration import MCPConfigurationConversion, read_mcp_configuration
        if not isinstance(configuration, MCPConfigurationConversion):
            raise ValueError('Supply the captured MCP configuration conversion')
        configuration.assert_current()
        candidate = _copy(candidate)
        if configuration._prepared_candidates.get(sha256(_encoded(candidate)).hexdigest()) != candidate:
            raise ValueError('Use an unchanged candidate prepared by this MCP conversion')
        provider = _copy(provider)
        _identity(provider, provider=True)
        target = next(iter(candidate['provider_apps'].values()))
        if (not _matches(IDENT, agent_node_id)
                or provider['node_id'] != target['node_id']
                or provider['revision'] != target['revision']
                or provider['generation'] != target['generation'] + 2):
            raise ValueError('Select the reviewed MCP candidate and its prepared running generation')
        # Fleet's stable instance identity includes scope, so two installations
        # of the same artifact cannot exchange data/grants during migration.
        identity = '\0'.join((candidate['owner'], target['node_id'], target['revision'], target['scope']))
        if provider['instance_id'] != sha256(identity.encode()).hexdigest()[:32]:
            raise ValueError('MCP instance does not belong to the reviewed candidate scope')
        self._configuration, self._candidate = configuration, candidate
        manifest = _read_json(configuration._snapshot / 'manifest.json')
        source, document = read_mcp_configuration(configuration._snapshot, manifest)
        self._sources = {source} | {path for path, digest in document['overrides'].items() if digest is not None}
        # Uncaptured definitions must not disappear merely because they were
        # inactive during capture. They require their own explicit conversion.
        from .migration_import import _snapshot_bytes
        from pantheon.settings import strip_jsonc_comments
        for item in manifest['files']:
            if item['source'] in self._sources and item['source'] != source:
                value = json.loads(strip_jsonc_comments(_snapshot_bytes(configuration._snapshot, item).decode()))
                if (not isinstance(value, dict) or value.keys() - {'$schema', 'version', 'servers', 'auto_start', 'host', 'port', 'cache_ttl'}
                        or not isinstance(value.get('servers', {}), dict)
                        or value.get('servers', {}).keys() - document['servers'].keys()
                        or not isinstance(value.get('auto_start', []), list)
                        or any(not isinstance(name, str) or name not in document['servers'] for name in value.get('auto_start', []))):
                    raise ValueError('MCP definitions or gateway features remain outside the captured conversion')
        profiles = deepcopy(candidate['profiles']['mcp_servers'])
        for profile in profiles.values():
            profile['provider'] = deepcopy(provider)
        self._bindings = _copy({'protocol': 1, 'owner': candidate['owner'], 'node_id': agent_node_id,
                               'profiles': profiles, 'defaults': candidate['defaults']})

    def describe(self):
        return deepcopy(self._bindings)

    def assert_matches(self, digest, fence):
        self._configuration.assert_current()
        if digest != self._configuration._digest or fence.identity != self._configuration._identity:
            raise ValueError('MCP bindings belong to another migration')
        from pantheon.apps.lifecycle import build_artifact
        _, revision = build_artifact(Path(self._candidate['artifact']['directory']))
        if revision != self._candidate['artifact']['revision']:
            raise ValueError('Reviewed MCP artifact changed before import')

    def consumes(self, source):
        return source in self._sources

    def check_member(self, config):
        value = with_dependency_defaults(config, self._bindings['defaults'])
        names = set(value['mcp_servers'])
        names.update('mcp' if name == 'mcp' else name[4:] for name in value['toolsets']
                     if name == 'mcp' or name.startswith('mcp:'))
        if names - self._bindings['profiles'].keys():
            raise ValueError('Saved Agent requires an MCP provider absent from its reviewed migration')

    def provision(self):
        self._configuration.provision()
