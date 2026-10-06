"""Owner-reviewed retention of captured execution trees on their source node.

This does not move or execute environments, create grants or provision providers.
Their ordinary prepared profiles still pin and validate each live generation.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from pantheon.apps.dependency_assembly import _identity, _matches, IDENT
from .data_fence import MigrationFence
from .migration_backup import _encoded, _read_json, verify_backup


class RetainedWorkspaceConversion:
    def __init__(self, snapshot, *, digest, fence, owner, source_node_id, roots, providers):
        if not isinstance(fence, MigrationFence):
            raise ValueError('Retained workspaces require a live migration fence')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        manifest = _read_json(Path(snapshot) / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('Retained workspaces belong to another captured migration')
        if (not _matches(IDENT, owner) or not _matches(IDENT, source_node_id)
                or not isinstance(roots, list) or not 1 <= len(roots) <= 128
                or not isinstance(providers, dict) or set(providers) != {'file_manager', 'shell'}):
            raise ValueError('Declare retained roots and both ordinary Files/Shell providers')
        pins = {}
        for name, entry in providers.items():
            if (not isinstance(entry, dict) or set(entry) != {'alias', 'provider'}
                    or not _matches(IDENT, entry['alias'])):
                raise ValueError('Retained workspace profiles need exact provider aliases')
            provider = deepcopy(entry['provider'])
            _identity(provider, provider=True)
            if provider['node_id'] != source_node_id:
                raise ValueError('Retained Files/Shell providers must run on the original source node')
            # Generation changes on a clean provider restart. The ordinary
            # provisioner still checks the full current profile against grants.
            pins[name] = {'alias': entry['alias'], 'provider': {
                key: value for key, value in provider.items() if key != 'generation'}}
        if manifest.get('filesystem_metadata') != 1:
            raise ValueError('Retained roots require captured filesystem metadata')
        captured = {row['source'] for row in manifest['directories']}
        spec = manifest['spec']
        configs = {Path(spec[key]).resolve() for key in ('global_config', 'project_config')}
        configs.update(Path(p['path']) / '.pantheon' for p in spec['projects'])
        selected = []
        for raw in roots:
            if not isinstance(raw, str) or raw not in captured:
                raise ValueError('Select exact captured directories for retained workspaces')
            root = Path(raw)
            if str(root) != raw or '..' in root.parts:
                raise ValueError('Retained workspace paths must be canonical')
            allowed = any(root.is_relative_to(config / 'workspaces') or (
                root.is_relative_to(config / 'brain') and len(root.relative_to(config / 'brain').parts) >= 2)
                for config in configs)
            if not allowed or any(root.is_relative_to(p) or p.is_relative_to(root) for p in selected):
                raise ValueError('Retain disjoint workspace or per-task execution subtrees only')
            selected.append(root)
        self._roots = tuple(sorted(selected))
        for item in manifest['files']:
            if self.consumes(item['source']) and (
                    Path(item['source']).name == 'task_state.json'
                    or item['category'] not in {'configuration', 'opaque-configuration', 'opaque-symlink'}):
                raise ValueError('Retained execution roots cannot hide Agent state or other owned data')
        self._digest, self._identity = digest, deepcopy(fence.identity)
        self._bindings = dict(protocol=1, owner=owner, source_node_id=source_node_id,
            roots=[str(p) for p in self._roots], providers=pins)
        if len(_encoded(self._bindings)) > 64 * 1024:
            raise ValueError('Retained workspace bindings exceed the admission limit')

    def consumes(self, source):
        return any(Path(source).is_relative_to(root) for root in self._roots)

    def resolves(self, issue):
        return issue['code'] in {'unclassified_source', 'non_regular_file'} and self.consumes(issue['source'])

    def assert_matches(self, digest, fence):
        fence.assert_owned()
        if digest != self._digest or fence.identity != self._identity:
            raise ValueError('Retained workspace conversion belongs to another migration')

    def describe(self):
        return deepcopy(self._bindings)
