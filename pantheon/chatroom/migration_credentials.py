"""Explicit, local conversion of backed-up model API keys to Fleet references.

This runs in the owner-side migrator, never inside the Agent release. It uses
Fleet's existing endpoint-bound vault and sends keys only over stdin. It does
not discover process environment, transmit keys to Hub, or create model engines.
OAuth, dotenv/global fallback semantics and non-model secrets need other
converters and remain blockers rather than silently changing their meaning.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .data_fence import MigrationFence, _open
from .migration_backup import _encoded, _read_json, verify_backup
from pantheon.settings import strip_jsonc_comments
from pantheon.utils.model_selector import PROVIDER_API_KEYS
from pantheon.utils.llm_providers import get_provider_base_env
from pantheon.utils.provider_registry import get_provider_config


def _endpoint(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError('Supply an explicit model API endpoint')
    parts = urlsplit(value)
    if (not parts.hostname or parts.username or parts.password or '?' in value or '#' in value
            or parts.scheme not in ('http', 'https')
            or parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Model credentials require HTTPS or a local endpoint')
    parts.port
    value = value.rstrip('/')
    return value if parts.path.rstrip('/') else value + '/v1'


class LocalModelCredentialVault:
    """A specifically selected local Fleet vault; never a management RPC."""
    def __init__(self, executable, *, state_dir, owner, node_id):
        if (not Path(executable).is_absolute() or not Path(state_dir).is_absolute()
                or not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner)
                or not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', node_id)):
            raise ValueError('Supply the local Fleet executable, state root and exact owner/node')
        self.executable, self.state_dir = Path(executable), Path(state_dir)
        self.owner, self.node_id = owner, node_id
        self._check_node()

    def _check_node(self):
        fd = _open(self.state_dir / 'node_id', os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            value = stream.read(257)
        if value.strip() != self.node_id.encode():
            raise ValueError('Credential vault belongs to another Fleet node')

    def ensure(self, ref, endpoint, key):
        self._check_node()
        if not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref):
            raise ValueError('Invalid model credential reference')
        if not isinstance(key, str) or not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError('Invalid model API credential')
        endpoint = _endpoint(endpoint)
        try:
            result = subprocess.run([str(self.executable), 'credentials', 'ensure',
                '--state-dir', str(self.state_dir), '--fleet', self.owner,
                '--name', ref.removeprefix('node-secret://'), '--endpoint', endpoint, '--stdin'],
                input=key.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Could not provision the local Fleet model credential') from None
        if result.returncode:
            raise ValueError('Fleet credential is unavailable or conflicts; it was not replaced')


class ModelCredentialConversion:
    """A private conversion plan pinned to an archive and migration fence.

Each binding names provider, source settings.json, credential alias, endpoint
and node-secret reference. Pairing an absent default endpoint is an explicit
owner input; the converter never guesses an SDK default. Nonempty source base
URLs must match it. Only one source per provider is accepted until per-project
provider configuration can be represented in the target launch contract.
"""
    def __init__(self, snapshot, *, digest, fence, bindings, vault):
        if not isinstance(fence, MigrationFence) or not isinstance(vault, LocalModelCredentialVault):
            raise ValueError('Supply a live migration fence and local Fleet credential vault')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        manifest = _read_json(Path(snapshot) / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('Credential backup does not belong to this migration')
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= 20:
            raise ValueError('Supply explicit model credential bindings')
        sources = {item['source']: item for item in manifest['files'] if item['category'] == 'opaque-configuration'}
        allowed = {str(Path(manifest['spec'][name]) / 'settings.json') for name in ('global_config', 'project_config')}
        self._digest, self._fence, self._vault = digest, deepcopy(fence.identity), vault
        self._entries, self._keys = [], {}
        providers, credentials, origins = {}, {}, []
        refs = set()
        from .migration_import import _snapshot_bytes
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {'provider', 'source', 'alias', 'endpoint', 'ref'}:
                raise ValueError('Invalid model credential conversion binding')
            provider, source, alias, ref = (binding[key] for key in ('provider', 'source', 'alias', 'ref'))
            if (not isinstance(provider, str) or not PROVIDER_API_KEYS.get(provider)
                    or provider in providers or not isinstance(source, str) or source not in allowed or source not in sources
                    or not isinstance(alias, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', alias)
                    or alias in credentials or alias in ('allocator', 'model_services')
                    or not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref)
                    or ref in refs):
                raise ValueError('Model credential binding has an ambiguous source, provider or target')
            raw = _snapshot_bytes(Path(snapshot), sources[source], 1024 * 1024)
            try:
                value = json.loads(strip_jsonc_comments(raw.decode()))
                keys = value['api_keys']
                if not isinstance(keys, dict):
                    raise ValueError
                key_name = PROVIDER_API_KEYS[provider]
                key = keys[key_name]
                if not isinstance(key, str) or not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key):
                    raise ValueError
                endpoint = _endpoint(binding['endpoint'])
                base_name = get_provider_base_env(provider, get_provider_config(provider))
                base = keys.get(base_name)
                if base not in (None, '') and _endpoint(base) != endpoint:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ValueError('Source model credential or endpoint does not match its binding') from None
            handled = self._keys.setdefault(source, {})
            handled[key_name] = key
            if base not in (None, ''):
                handled[base_name] = base
            self._entries.append((ref, endpoint, key))
            refs.add(ref)
            providers[provider] = alias
            credentials[alias] = {'ref': ref, 'endpoint': endpoint}
            origins.append({'source': source, 'fields': sorted([key_name] + ([base_name] if base not in (None, '') else [])),
                            'provider': provider, 'alias': alias})
        self._descriptor = {'protocol': 1, 'owner': vault.owner, 'node_id': vault.node_id,
            'models': {'providers': providers, 'model_services': 'model_services'},
            'credentials': credentials, 'sources': origins}

    def describe(self):
        """Only prepared aliases, endpoint-paired refs and source field names."""
        return deepcopy(self._descriptor)

    def assert_matches(self, digest, fence):
        if digest != self._digest or fence.identity != self._fence:
            raise ValueError('Model credential conversion belongs to another migration')

    def consume(self, source, keys):
        # Any nonempty field not explicitly handled still blocks import. This
        # includes LLM_API_* fallbacks, non-model API keys and shadowed providers.
        expected = self._keys.get(source, {})
        if not isinstance(keys, dict) or {key: value for key, value in keys.items() if value not in ('', None)} != expected:
            raise ValueError('Legacy credentials still need explicit conversion')

    def provision(self):
        for ref, endpoint, key in self._entries:
            self._vault.ensure(ref, endpoint, key)
