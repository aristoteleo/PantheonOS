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


PLUGIN_MODEL_FIELDS = frozenset({
    ('context_compression', 'compression_model'),
    ('memory_system', 'selection_model'), ('memory_system', 'flush_model'), ('memory_system', 'dream_model'),
    ('learning_system', 'model'), ('learning_system', 'extract_model'),
})


def validate_budget_choice(choice, service_id):
    if (not isinstance(service_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', service_id)
            or not isinstance(choice, dict)
            or set(choice) != {'protocol', 'source', 'service_id', 'enabled'}
            or type(choice['protocol']) is not int or choice['protocol'] != 1
            or choice['source'] != 'legacy-local-browser' or choice['service_id'] != service_id
            or type(choice['enabled']) is not bool):
        raise ValueError('Supply the confirmed budget choice from the exact source Desktop service')
    return deepcopy(choice)


def _validate_pair(source, target):
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


class ModelSelectionConversion:
    def __init__(self, snapshot, *, digest, fence, owner, node_id, selections,
                 fleet_tiers, dependency='model_services', templates=None, settings=None,
                 budget_choice=None, source_service_id=None):
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
                or not isinstance(selections, list) or len(selections) > 100000):
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
            _validate_pair(source, target)
            identity = (entry['conversation_id'], entry['config_id'])
            if identity in entries:
                raise ValueError('Duplicate saved-member model conversion')
            entries[identity] = deepcopy(entry)
        audit = {'protocol': 1, 'selections': [entries[key] for key in sorted(entries)]}
        if budget_choice is not None or source_service_id is not None:
            # A browser-side preference is independent of the private server
            # backup. Retain its explicitly paired observation for review; never
            # infer budget enablement from a stored key or API endpoint.
            audit['budget_choice'] = validate_budget_choice(budget_choice, source_service_id)

        template_entries = {}
        if templates is not None:
            if not isinstance(templates, list) or len(templates) > 100000:
                raise ValueError('Invalid template model conversions')
            for entry in templates:
                if (not isinstance(entry, dict) or set(entry) != {'path', 'config_id', 'source', 'target'}
                        or not isinstance(entry['path'], str) or not Path(entry['path']).is_absolute()
                        or str(Path(entry['path'])) != entry['path'] or '..' in Path(entry['path']).parts
                        or not _identifier(entry['config_id'])
                        or not isinstance(entry['source'], str) or not isinstance(entry['target'], str)):
                    raise ValueError('Supply an exact template path, member ID and scalar model mapping')
                _validate_pair(entry['source'], entry['target'])
                identity = (entry['path'], entry['config_id'])
                if identity in template_entries:
                    raise ValueError('Duplicate template model conversion')
                template_entries[identity] = deepcopy(entry)
        if template_entries:
            audit['templates'] = [template_entries[key] for key in sorted(template_entries)]
        self._templates = template_entries
        setting_entries = {}
        if settings is not None:
            if not isinstance(settings, list) or len(settings) > 100000:
                raise ValueError('Invalid plugin model conversions')
            for entry in settings:
                if (not isinstance(entry, dict) or set(entry) != {'path', 'field', 'source', 'target'}
                        or not isinstance(entry['path'], str) or not Path(entry['path']).is_absolute()
                        or str(Path(entry['path'])) != entry['path'] or '..' in Path(entry['path']).parts
                        or not isinstance(entry['field'], list) or len(entry['field']) != 2
                        or not all(isinstance(key, str) for key in entry['field'])
                        or tuple(entry['field']) not in PLUGIN_MODEL_FIELDS
                        or not isinstance(entry['source'], str) or not isinstance(entry['target'], str)):
                    raise ValueError('Supply an exact settings path and supported plugin model field')
                _validate_pair(entry['source'], entry['target'])
                identity = (entry['path'], tuple(entry['field']))
                if identity in setting_entries:
                    raise ValueError('Duplicate plugin model conversion')
                setting_entries[identity] = deepcopy(entry)
        if setting_entries:
            audit['settings'] = [setting_entries[key] for key in sorted(setting_entries)]
        self._settings = setting_entries
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

    async def review_budget(self, client, provisioning, *, extra_references=None):
        """Read-only approval of existing publications, including every fallback."""
        references = list(self._bindings['models']['fleet_tiers'].values())
        for entries in (self._entries, self._templates, self._settings):
            for entry in entries.values():
                old = entry['source']
                if any(ref.startswith(('codex/', 'gemini-cli/'))
                       for ref in (old if isinstance(old, list) else [old])):
                    raise ValueError('OAuth model selections have separate billing and require explicit OAuth migration')
                target = entry['target']
                references.extend(target if isinstance(target, list) else [target])
        if extra_references is not None:
            if not isinstance(extra_references, list) or any(not isinstance(v, str) for v in extra_references):
                raise ValueError('Supply explicit existing Fleet model references for budget review')
            references.extend(extra_references)
        from .migration_budget import review_budget_models
        from pantheon.agent import _parse_thinking_suffix
        references = list(dict.fromkeys(_parse_thinking_suffix(ref)[0] for ref in references))
        review = await review_budget_models(client, provisioning, references)
        audit = {**self._audit, 'budget_review': review}
        raw = _encoded(audit)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Model selections exceed the conversion document limit')
        self._audit = audit
        self._bindings['selection_sha256'] = sha256(raw).hexdigest()
        return deepcopy(review)

    def _reviewed_target(self, target):
        review = self._audit.get('budget_review')
        if review is not None:
            from pantheon.agent import _parse_thinking_suffix
            allowed = {item['reference'] for item in review['model_selection']['selected']}
            if any(_parse_thinking_suffix(ref)[0] not in allowed
                   for ref in (target if isinstance(target, list) else [target])):
                raise ValueError('Model reference is missing from the budget publication review')
        return deepcopy(target)

    def convert(self, conversation_id, config_id, source):
        entry = self._entries.get((conversation_id, config_id))
        if entry is None or entry['source'] != source or type(entry['source']) is not type(source):
            raise ValueError('Every saved member requires an exact source model selection mapping')
        return self._reviewed_target(entry['target'])

    def require_members(self, identities):
        if set(identities) != set(self._entries):
            raise ValueError('Model conversion contains missing or unrelated saved members')

    def template_model(self, path, config_id, source):
        from pantheon.agent import _is_model_tag
        entry = self._templates.get((path, config_id))
        if entry is not None:
            if entry['source'] != source:
                raise ValueError('Template model mapping does not match its saved source')
            return self._reviewed_target(entry['target']), (path, config_id)
        if source == '':
            # Delegated templates may intentionally inherit the caller's model.
            return source, None
        if isinstance(source, str) and _is_model_tag(source):
            # All quality tiers are explicitly pinned in this conversion's binding.
            return source, None
        if isinstance(source, str) and source.startswith(('fleet-model://', 'fleet-route://')):
            _validate_pair(source, source)
            return self._reviewed_target(source), None
        raise ValueError('Every direct template model requires an explicit Model Service mapping')

    def require_templates(self, identities):
        if set(identities) != set(self._templates):
            raise ValueError('Model conversion contains missing or unrelated template members')

    def convert_settings(self, path, settings):
        from pantheon.agent import _is_model_tag
        result, used = deepcopy(settings), set()
        for section, field in sorted(PLUGIN_MODEL_FIELDS):
            if section not in result:
                continue
            values = result[section]
            if not isinstance(values, dict):
                raise ValueError('Plugin settings must be objects before model migration')
            if field not in values:
                continue
            source = values[field]
            identity = (path, (section, field))
            entry = self._settings.get(identity)
            if entry is not None:
                if entry['source'] != source:
                    raise ValueError('Plugin model mapping does not match its saved source')
                values[field] = self._reviewed_target(entry['target'])
                used.add(identity)
            elif source is None or isinstance(source, str) and source.strip().lower() in ('', 'auto'):
                continue  # Keep active-model/parent-selector inheritance.
            elif isinstance(source, str) and _is_model_tag(source):
                continue  # Uses this migration's explicitly bound quality tiers.
            elif isinstance(source, str) and source.startswith(('fleet-model://', 'fleet-route://')):
                _validate_pair(source, source)
                self._reviewed_target(source)
            else:
                raise ValueError('Every direct plugin model requires an explicit Model Service mapping')
        return result, used

    def require_settings(self, identities):
        if set(identities) != set(self._settings):
            raise ValueError('Model conversion contains missing or unrelated plugin settings')

    def describe(self):
        return deepcopy(self._bindings)

    def audit(self):
        return deepcopy(self._audit)
