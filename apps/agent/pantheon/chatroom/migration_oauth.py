"""Owner-reviewed import of legacy OAuth into the existing scoped compatibility path.

No token exchange, global credential discovery or implicit Model Service routing.
Private token bytes remain outside receipts and the distributable Agent package.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from .data_fence import MigrationFence
from .migration_backup import _encoded, _read_json, verify_backup

PROVIDERS = {'codex': 'codex.json', 'gemini-cli': 'gemini_cli.json'}


class OAuthConfigurationConversion:
    def __init__(self, snapshot, *, digest, fence, owner, node_id, providers):
        if not isinstance(fence, MigrationFence):
            raise ValueError('OAuth conversion requires the live legacy source fence')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        manifest = _read_json(Path(snapshot) / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('OAuth snapshot belongs to another migration')
        if (not isinstance(providers, list) or not providers or len(providers) > 2
                or any(not isinstance(p, str) or p not in PROVIDERS for p in providers)
                or len(set(providers)) != len(providers)
                or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', v)
                       for v in (owner, node_id))):
            raise ValueError('Select explicit OAuth providers and Agent placement')
        source = Path(manifest['spec']['global_config']).resolve() / 'oauth'
        if str(source.parent) not in fence.identity['roots']:
            raise ValueError('OAuth source must be covered by the migration fence')
        from .migration_import import _snapshot_bytes
        self._files, self._consumed = [], set()
        rows = {row['source']: row for row in manifest['files']}
        for provider in providers:
            path = str(source / PROVIDERS[provider])
            item = rows.get(path)
            if item is None or item['category'] != 'opaque-configuration' or item.get('kind', 'file') != 'file':
                raise ValueError('Selected OAuth provider has no captured regular credential record')
            raw = _snapshot_bytes(Path(snapshot), item, 2 * 1024 * 1024)
            try:
                record = json.loads(raw)
                tokens = record['tokens']
                if (not isinstance(record, dict) or not isinstance(tokens, dict)
                        or not tokens or record.keys() - {'tokens', 'provider', 'last_refresh'}
                        or record.get('provider') != ('codex' if provider == 'codex' else 'gemini_cli')
                        or 'last_refresh' in record and (not isinstance(record['last_refresh'], str)
                                                        or len(record['last_refresh']) > 256)
                        or not isinstance(tokens.get('access_token'), str) or not tokens['access_token']
                        or not isinstance(tokens.get('refresh_token'), str) or not tokens['refresh_token']):
                    raise ValueError
                allowed = ({'access_token', 'refresh_token', 'id_token', 'account_id', 'organization_id', 'project_id'}
                           if provider == 'codex' else
                           {'access_token', 'refresh_token', 'expires_at', 'email', 'project_id'})
                if tokens.keys() - allowed:
                    raise ValueError
                for key, value in tokens.items():
                    if key == 'expires_at':
                        if type(value) not in (int, float) or not 0 < value < 10**12:
                            raise ValueError
                    elif value is not None and (not isinstance(value, str) or len(value) > 128 * 1024):
                        raise ValueError
            except (ValueError, TypeError, KeyError):
                raise ValueError('Captured OAuth record needs explicit format recovery') from None
            self._files.append({**item, 'target': 'oauth/' + provider + '.json'})
            self._consumed.add(path)
            lock = rows.get(path + '.lock')
            if lock is not None:
                if (lock['category'] != 'opaque-configuration' or lock.get('kind', 'file') != 'file'
                        or _snapshot_bytes(Path(snapshot), lock, 1) != b'\0'):
                    raise ValueError('Captured OAuth lock is invalid')
                self._consumed.add(path + '.lock')
        self._snapshot = Path(snapshot)
        self._guard = fence
        self._digest, self._fence = digest, deepcopy(fence.identity)
        self._bindings = dict(protocol=1, owner=owner, node_id=node_id,
                              mode='agent-scoped-oauth', providers=sorted(providers))

    def assert_matches(self, digest, fence):
        fence.assert_owned()
        if digest != self._digest or fence.identity != self._fence:
            raise ValueError('OAuth conversion belongs to another backup or destination')

    def describe(self):
        return deepcopy(self._bindings)

    def consumes(self, source):
        return source in self._consumed

    def provision(self, root):
        self._guard.assert_owned()
        if str(Path(root).resolve()) != self._fence['target']:
            raise ValueError('OAuth credentials belong to the reserved Agent destination')
        from .data_transition import transition_state
        state = transition_state(root)
        if (state is None or state['phase'] != 'importing' or state['backup'] != self._digest
                or state['fence'] != self._fence['sha256']
                or state.get('oauth_bindings') != sha256(_encoded(self._bindings)).hexdigest()):
            raise ValueError('OAuth delivery requires the matching unstartable import destination')
        # Reuse the importer's private, bounded, exact-byte copy and retry rules.
        # Startup remains barred until the outer importer commits admission.
        from .migration_import import _copy
        for item in self._files:
            _copy(self._snapshot, Path(root), item)
