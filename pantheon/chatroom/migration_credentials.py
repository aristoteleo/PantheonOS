"""Explicit, local conversion of backed-up model API keys to Fleet references.

This runs in the owner-side migrator, never inside the Agent release. It uses
Fleet's existing endpoint-bound vault and sends keys only over stdin. It does
not discover process environment, transmit keys to Hub, or create model engines.
OAuth, global fallback semantics and non-model secrets need other
converters and remain blockers rather than silently changing their meaning.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from .data_fence import MigrationFence
from pantheon.models.credentials import LocalModelCredentialVault, model_credential_endpoint as _endpoint
from .migration_backup import _encoded, _read_json, verify_backup
from pantheon.utils.model_selector import PROVIDER_API_KEYS
from pantheon.utils.llm_providers import get_provider_base_env
from pantheon.utils.provider_registry import get_provider_config
from pantheon.settings import LEGACY_API_KEY_ENV_MAP


class ModelCredentialConversion:
    """A private conversion plan pinned to an archive and migration fence.

    Each binding names provider, the effective key's source file, alias, endpoint
    and node-secret reference. Pairing an absent default endpoint is an explicit
    owner input; the converter never guesses an SDK default. Nonempty source base
    URLs must match it. User/project settings and the selected launch dotenv (or
    the legacy runtime's explicit environment handoff) retain Settings precedence;
    overwritten values remain in the private
    backup, not in the App. Other projects' scopes still require separate mapping.
    Optional platform_budget pairs the captured runtime state with the confirmed
    browser choice and existing Hub provisioning receipt. It requires a matching
    ModelSelectionConversion; no force-proxy key is delivered to the Agent.
    """
    def __init__(self, snapshot, *, digest, fence, bindings, vault, platform_budget=None):
        if not isinstance(fence, MigrationFence) or not isinstance(vault, LocalModelCredentialVault):
            raise ValueError('Supply a live migration fence and local Fleet credential vault')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        manifest = _read_json(Path(snapshot) / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('Credential backup does not belong to this migration')
        if not isinstance(bindings, list) or not (0 if platform_budget is not None else 1) <= len(bindings) <= 20:
            raise ValueError('Supply explicit model credential bindings')
        from .migration_environment import read_environment
        settings, env_source, environment = read_environment(Path(snapshot), manifest)
        source_keys = {source: value.get('api_keys', {}) for source, value in settings.items()}
        source_keys[env_source] = environment
        from .migration_handoff import read_handoff
        runtime_source, runtime_environment = read_handoff(Path(snapshot), manifest)
        if runtime_source is not None:
            source_keys[runtime_source] = runtime_environment
        # get_api_key uses truthy dotenv values before the merged settings.
        # A project null masks a user's key; an empty dotenv value does not.
        merged, merged_origins = {}, {}
        for source, value in settings.items():
            for name, key in value.get('api_keys', {}).items():
                merged[name], merged_origins[name] = key, source
        # The live snapshot is the whole effective env view, not another dotenv
        # overlay: explicit absent/empty fields cannot resurrect stale file keys.
        active_env = runtime_environment if runtime_source is not None else environment
        active_source = runtime_source or env_source
        effective, origins_by_key = {}, {}
        for name in merged.keys() | active_env.keys() | set(LEGACY_API_KEY_ENV_MAP):
            alias = LEGACY_API_KEY_ENV_MAP.get(name)
            for values, origins in ((active_env, None), (merged, merged_origins)):
                found = next((key for key in (name, alias) if key and values.get(key)), None)
                if found:
                    effective[name] = values[found]
                    origins_by_key[name] = active_source if origins is None else origins[found]
                    break
        self._digest, self._fence, self._vault = digest, deepcopy(fence.identity), vault
        self._entries, self._keys = [], {}
        providers, credentials, origins = {}, {}, []
        refs = set()
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {'provider', 'source', 'alias', 'endpoint', 'ref'}:
                raise ValueError('Invalid model credential conversion binding')
            provider, source, alias, ref = (binding[key] for key in ('provider', 'source', 'alias', 'ref'))
            if (not isinstance(provider, str) or not PROVIDER_API_KEYS.get(provider)
                    or provider in providers or not isinstance(source, str) or source not in source_keys
                    or not isinstance(alias, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', alias)
                    or alias in credentials or alias in ('allocator', 'model_services')
                    or not isinstance(ref, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', ref)
                    or ref in refs):
                raise ValueError('Model credential binding has an ambiguous source, provider or target')
            try:
                key_name = PROVIDER_API_KEYS[provider]
                key = effective[key_name]
                if (origins_by_key[key_name] != source or not isinstance(key, str)
                        or not 0 < len(key) <= 8192 or any(not 33 <= ord(c) <= 126 for c in key)):
                    raise ValueError
                endpoint = _endpoint(binding['endpoint'])
                base_name = get_provider_base_env(provider, get_provider_config(provider))
                base = effective.get(base_name)
                if base not in (None, '') and _endpoint(base) != endpoint:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ValueError('Source model credential or endpoint does not match its binding') from None
            for origin, keys in source_keys.items():
                names = (key_name, base_name, LEGACY_API_KEY_ENV_MAP.get(key_name), LEGACY_API_KEY_ENV_MAP.get(base_name))
                fields = [name for name in names if name and keys.get(name) not in (None, '')]
                if not fields:
                    continue
                handled = self._keys.setdefault(origin, {})
                handled.update({name: keys[name] for name in fields})
                origins.append({'source': origin, 'fields': sorted(fields),
                                'provider': provider, 'alias': alias})
            self._entries.append((ref, endpoint, key))
            refs.add(ref)
            providers[provider] = alias
            credentials[alias] = {'ref': ref, 'endpoint': endpoint}
        self._descriptor = {'protocol': 1, 'owner': vault.owner, 'node_id': vault.node_id,
            'models': {'providers': providers, 'model_services': 'model_services'},
            'credentials': credentials, 'sources': origins}
        if platform_budget is not None:
            from .migration_budget import plan_budget, BUDGET_FIELDS
            budget, entry = plan_budget(platform_budget, source=runtime_source,
                                       environment=runtime_environment, vault=vault)
            if entry is not None:
                if entry[0] in refs:
                    raise ValueError('Budget and provider credentials require distinct references')
                self._entries.append(entry)
            self._descriptor['platform_budget'] = budget
            for origin in (env_source, runtime_source):
                fields = {name: value for name, value in source_keys[origin].items()
                          if name in BUDGET_FIELDS and value not in (None, '')}
                self._keys.setdefault(origin, {}).update(fields)

    def assert_selection(self, selection):
        budget = self._descriptor.get('platform_budget')
        if budget is not None and (selection is None or selection.audit().get('budget_choice') != budget['choice']):
            raise ValueError('Budget migration requires Model Service selections with the same confirmed budget choice')
        if budget is not None and budget['choice']['enabled']:
            review = selection.audit().get('budget_review')
            if review is None or review['provisioning'] != budget['provisioning']:
                raise ValueError('Review all model selections against the provisioned budget Connector before importing')

    def describe(self):
        """Only prepared aliases, endpoint-paired refs and source field names."""
        return deepcopy(self._descriptor)

    def assert_matches(self, digest, fence):
        if digest != self._digest or fence.identity != self._fence:
            raise ValueError('Model credential conversion belongs to another migration')

    def consume(self, source, keys):
        # Any nonempty field not explicitly handled still blocks import. This
        # includes LLM_API_* fallbacks, non-model keys and unbound providers.
        expected = self._keys.get(source, {})
        if not isinstance(keys, dict) or {key: value for key, value in keys.items() if value not in ('', None)} != expected:
            raise ValueError('Legacy credentials still need explicit conversion')

    def provision(self):
        for ref, endpoint, key in self._entries:
            self._vault.ensure(ref, endpoint, key)
