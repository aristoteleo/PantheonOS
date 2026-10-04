"""Explicit saved-member model selection conversion for an Agent migration.

Owner-side only. This does not infer equivalent models, inspect credentials,
publish models, mutate a route or make an inference call. The caller chooses
every destination reference against the intended Model Services deployment.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from pantheon.agent import _parse_thinking_suffix
from pantheon.factory.instances import _identifier
from pantheon.models.client import parse_ref, parse_route_ref
from pantheon.utils.model_selector import QUALITY_TAGS
from .data_fence import MigrationFence
from .migration_backup import _encoded, _read_json, verify_backup


class ModelSelectionConversion:
    def __init__(self, snapshot, *, digest, fence, owner, node_id, selections,
                 fleet_tiers, dependency='model_services'):
        if not isinstance(fence, MigrationFence):
            raise ValueError('Supply the live migration fence for model selection conversion')
        fence.assert_owned()
        verify_backup(snapshot, digest=digest)
        manifest = _read_json(Path(snapshot) / 'manifest.json')
        if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
            raise ValueError('Model selections do not belong to this migration')
        if (not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner)
                or not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', node_id)
                or not isinstance(dependency, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', dependency)
                or not isinstance(selections, list) or not 1 <= len(selections) <= 100000):
            raise ValueError('Supply exact Agent placement, dependency and saved-member selections')
        if not isinstance(fleet_tiers, dict) or set(fleet_tiers) != QUALITY_TAGS:
            raise ValueError('Explicitly map every quality tier to Model Services for new/default Agents')
        for reference in fleet_tiers.values():
            if not isinstance(reference, str) or len(reference) > 4096:
                raise ValueError('Invalid Model Service quality tier')
            if reference.startswith('fleet-model://'):
                parse_ref(reference)
            elif reference.startswith('fleet-route://'):
                parse_route_ref(reference)
            else:
                raise ValueError('Quality tiers require Model Service references')
            if _parse_thinking_suffix(reference)[1] is not None:
                raise ValueError('Keep reasoning effort on the Agent selection, not its quality tier')
        entries = {}
        for entry in selections:
            if (not isinstance(entry, dict) or set(entry) != {'conversation_id', 'config_id', 'source', 'target'}
                    or not _identifier(entry['conversation_id']) or not _identifier(entry['config_id'])):
                raise ValueError('Invalid saved-member model conversion')
            source, target = entry['source'], entry['target']
            if (type(source) is not type(target) or type(source) not in (str, list)
                    or isinstance(source, list) and (not source or len(source) != len(target) or len(source) > 128)):
                raise ValueError('Preserve the saved model selector shape and fallback order')
            for old, new in zip(source if isinstance(source, list) else [source],
                                target if isinstance(target, list) else [target]):
                if not all(isinstance(v, str) and 0 < len(v) <= 4096 and not any(ord(c) < 32 for c in v)
                           for v in (old, new)):
                    raise ValueError('Invalid saved model selector')
                _, old_effort = _parse_thinking_suffix(old)
                reference, new_effort = _parse_thinking_suffix(new)
                if old_effort != new_effort:
                    raise ValueError('Preserve the saved reasoning effort during model migration')
                if reference.startswith('fleet-model://'):
                    parse_ref(reference)
                elif reference.startswith('fleet-route://'):
                    parse_route_ref(reference)
                else:
                    raise ValueError('Map saved models to explicit Model Service model or route references')
            identity = (entry['conversation_id'], entry['config_id'])
            if identity in entries:
                raise ValueError('Duplicate saved-member model conversion')
            entries[identity] = deepcopy(entry)
        audit = {'protocol': 1, 'selections': [entries[key] for key in sorted(entries)]}
        raw = _encoded(audit)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Model selections exceed the conversion document limit')
        self._entries, self._audit = entries, audit
        self._digest, self._fence = digest, deepcopy(fence.identity)
        self._bindings = {'protocol': 1, 'owner': owner, 'node_id': node_id,
                          'models': {'model_services': dependency, 'fleet_tiers': deepcopy(fleet_tiers)}, 'credentials': {},
                          'selection_sha256': sha256(raw).hexdigest()}

    def assert_matches(self, digest, fence):
        fence.assert_owned()
        if digest != self._digest or fence.identity != self._fence:
            raise ValueError('Model selections belong to another backup or migration')

    def convert(self, conversation_id, config_id, source):
        entry = self._entries.get((conversation_id, config_id))
        if entry is None or entry['source'] != source or type(entry['source']) is not type(source):
            raise ValueError('Every saved member requires an exact source model selection mapping')
        return deepcopy(entry['target'])

    def require_members(self, identities):
        if set(identities) != set(self._entries):
            raise ValueError('Model conversion contains missing or unrelated saved members')

    def describe(self):
        return deepcopy(self._bindings)

    def audit(self):
        return deepcopy(self._audit)
