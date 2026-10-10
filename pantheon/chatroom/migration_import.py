"""Import validated legacy data without replaying Runs or provisioning tools.

MCP requires a captured, reviewed App binding conversion. Unconverted runtime
state and unmapped project configurations remain blockers.
Settings/dotenv/handoff API credentials require a paired local-vault conversion. This importer
handles self-contained team definitions and Agent settings; it never substitutes
a default model or member ID.
The original files remain fenced and untouched for a pre-cutover rollback.
"""
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid5

from pantheon.factory.instance_store import AgentInstanceStore
from pantheon.factory.models import AgentConfig
from pantheon.factory.instances import _config, _identifier
from pantheon.utils.registry_lock import registry_lock
from pantheon.settings import strip_jsonc_comments
from .data_transition import STATE_FILE, RESERVATION_FILE, transition_state, import_reservation
from .data_fence import MigrationFence
from .migration_backup import (_atomic_json, _destination, _encoded, _hash_file,
    _open, _private_dir, _private_file, _read_json, _sync_directory, verify_backup, _plan as _source_plan)

APP_SETTINGS = frozenset({'enable_mcp_tools', 'default_template_auto_update', 'models',
    'image_gen_model', 'image_gen_models', 'context_compression', 'think_system',
    'task_system', 'fleet_system', 'model_services_system', 'delegation', 'memory_system',
    'learning_system', 'vision', 'llm_retry', 'compression'})
PLATFORM_SETTINGS = frozenset({'$schema', 'version', 'endpoint', 'services', 'remote', 'repl'})


def reserve_import(spec, *, target, namespace, operation, request_digest, aborting=False):
    """Block destination startup before acquiring any legacy source fence.

    The owner must hold its deployment preparation boundary. An interrupted
    backup or conversion leaves this marker in place, even before migration.json
    exists. Only the exact committed import can open runtime admission.
    """
    import re
    from .data_fence import fence_identity
    from .migration import legacy_source_roots
    if not isinstance(request_digest, str) or not re.fullmatch('[0-9a-f]{64}', request_digest):
        raise ValueError('Supply a digest of the complete reviewed migration request')
    root = _destination(spec, target)
    identity = fence_identity(legacy_source_roots(spec), operation=operation, target=root, namespace=namespace)
    expected = dict(protocol=1, phase='reserved', operation=operation, namespace=namespace,
                    fence=identity['sha256'], request=request_digest)
    _private_dir(root, create=not aborting)
    with registry_lock(root / 'data-admission.lock', timeout=0):
        current = import_reservation(root)
        if current is not None:
            if current != expected and not (aborting and current == {**expected, 'phase': 'aborted'}):
                raise ValueError('Destination belongs to another migration request')
        else:
            if aborting:
                raise ValueError('No reserved migration exists to abort')
            if set(p.name for p in root.iterdir()) - {'data-admission.lock', '.' + RESERVATION_FILE + '.partial'}:
                raise ValueError('Reserve migration before initializing Agent data')
            _atomic_json(root / RESERVATION_FILE, expected)
    return expected


def _settings(raw, *, source=None, model_credentials=None, environment_checked=False):
    value = json.loads(strip_jsonc_comments(raw.decode('utf-8')))
    if not isinstance(value, dict):
        raise ValueError('Legacy settings must be an object')
    # Values aren't copied to a report or logged. Nonempty credentials require
    # an explicit conversion with provider/endpoint pairing.
    keys = value.get('api_keys', {})
    if model_credentials is not None:
        model_credentials.consume(source, keys)
    elif not isinstance(keys, dict) or any(item not in ('', None) for item in keys.values()):
        raise ValueError('Legacy credentials require explicit credential-reference conversion')
    if value.get('env_file') and not environment_checked:
        raise ValueError('Legacy environment configuration requires explicit conversion')
    if value.keys() - APP_SETTINGS - PLATFORM_SETTINGS - {'api_keys', 'env_file'}:
        raise ValueError('Legacy settings contain fields needing explicit scope conversion')
    result = {key: item for key, item in value.items() if key in APP_SETTINGS}
    # Reject non-JSON numbers without loading Settings or changing process env.
    json.dumps(result, allow_nan=False)
    return result, sorted(value.keys() & PLATFORM_SETTINGS)


def _target(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or PurePosixPath(value).is_absolute() or '..' in value.split('/')
            or PurePosixPath(value).parts[0] not in ('conversations', 'configuration', 'user')):
        raise ValueError('Invalid migration destination within Agent data')
    return value


def _snapshot_bytes(snapshot, item, limit=16 * 1024 * 1024):
    if item['size'] > limit:
        raise ValueError('Legacy conversion document exceeds its size limit')
    fd = _open(snapshot / item['blob'], os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) != item['size'] or sha256(raw).hexdigest() != item['sha256']:
        raise ValueError('Backup content changed during conversion')
    return raw


def _plan(snapshot, manifest, target, *, model_credentials=None, model_selection=None, mcp_configuration=None,
          retained_workspaces=None, oauth_configuration=None):
    from .migration_environment import read_environment
    _, env_source, environment = read_environment(snapshot, manifest)
    from .migration_handoff import read_handoff
    runtime_source, runtime_environment = read_handoff(snapshot, manifest)
    environments = {env_source: environment}
    if runtime_source is not None:
        environments[runtime_source] = runtime_environment
    for source, values in environments.items():
        if model_credentials is not None:
            model_credentials.consume(source, values)
        elif any(value not in (None, '') for value in values.values()):
            raise ValueError('Legacy environment requires explicit credential or scope conversion')
    inventory = manifest['inventory']
    blockers = [issue for issue in inventory['issues']
                if issue['code'] != 'configuration_requires_explicit_conversion'
                and not (retained_workspaces is not None and retained_workspaces.resolves(issue))]
    if blockers:
        raise ValueError('Legacy inventory has unresolved data or scope issues')
    files, conversions = {}, []
    blobs = {item['source']: item for item in manifest['files']}
    for item in inventory['files']:
        if retained_workspaces is not None and retained_workspaces.consumes(item['source']):
            continue
        destination = _target(item['target'])
        if destination in files:
            raise ValueError('Legacy sources have conflicting import destinations')
        files[destination] = dict(blobs[item['source']], target=destination)
    spec = manifest['spec']
    config_targets = {str(Path(spec['global_config']).resolve()): 'user',
                      str(Path(spec['project_config']).resolve()): 'configuration/.pantheon'}
    selected_settings = set()
    for item in manifest['files']:
        if retained_workspaces is not None and retained_workspaces.consumes(item['source']):
            continue
        if item['category'] != 'opaque-configuration':
            continue
        if item['source'] in environments:
            conversions.append(dict(source=item['source'], target=None,
                                    credential_conversion='node-vault' if model_credentials is not None else 'empty'))
            continue
        if mcp_configuration is not None and mcp_configuration.consumes(item['source']):
            conversions.append(dict(source=item['source'], target=None, mcp_conversion='ordinary-app'))
            continue
        if oauth_configuration is not None and oauth_configuration.consumes(item['source']):
            conversions.append(dict(source=item['source'], target=None, oauth_conversion='agent-scoped-oauth'))
            continue
        source = Path(item['source'])
        if source.name != 'settings.json' or str(source.parent) not in config_targets:
            raise ValueError('Opaque legacy configuration requires an explicit converter')
        settings, retained = _settings(_snapshot_bytes(snapshot, item, 1024 * 1024),
                                      source=item['source'], model_credentials=model_credentials, environment_checked=True)
        if model_selection is not None:
            settings, used = model_selection.convert_settings(item['source'], settings)
            selected_settings.update(used)
        destination = config_targets[str(source.parent)] + '/settings.json'
        raw = _encoded(settings)
        files[destination] = dict(item, target=destination, converted=raw,
                                 size=len(raw), sha256=sha256(raw).hexdigest())
        conversions.append(dict(source=item['source'], target=destination,
                                retained_at_source=retained,
                                credential_conversion='node-vault' if model_credentials is not None else 'empty'))
    if model_selection is not None:
        model_selection.require_settings(selected_settings)
    paths = {item['source']: str(target / destination) for destination, item in files.items()}
    from .migration_templates import apply_edits, template_edits, prompt_reference_edits
    templates, prompt_files = {}, {}
    for destination, item in files.items():
        parts = PurePosixPath(destination).parts
        library = parts[1:] if parts[0] == 'user' else parts[2:]
        if (item['category'] == 'configuration' and library
                and library[0] in ('agents', 'teams', 'prompts') and destination.endswith('.md')):
            prompt_files[destination] = item
            if library[0] != 'prompts':
                templates[destination] = item
    relocations = {item['source']: paths[item['source']] for item in prompt_files.values()}
    template_members = set()
    for destination, item in prompt_files.items():
        original = _snapshot_bytes(snapshot, item)
        edits, used = (template_edits(original, path=item['source'], selection=model_selection,
                                     relocations=relocations, dependencies=mcp_configuration) if destination in templates else ([], set()))
        edits = sorted(edits + prompt_reference_edits(original, path=item['source'], relocations=relocations))
        template_members.update(used)
        if edits:
            raw = apply_edits(original, edits)
            files[destination] = dict(item, template_model_edits=edits, original_size=item['size'],
                                     original_sha256=item['sha256'], size=len(raw), sha256=sha256(raw).hexdigest())
    if model_selection is not None:
        model_selection.require_templates(template_members)
    from .migration_images import image_mapping, rewrite_message_images, converted_message_lines
    images = image_mapping(manifest, files, target)
    for destination, item in files.items():
        if item['category'] != 'conversation' or not destination.endswith('.jsonl'):
            continue
        digest, size = sha256(), 0
        for chunk in converted_message_lines(snapshot, item, images):
            digest.update(chunk)
            size += len(chunk)
        if (size, digest.hexdigest()) != (item['size'], item['sha256']):
            files[destination] = dict(item, image_relocations=images,
                original_size=item['size'], original_sha256=item['sha256'],
                size=size, sha256=digest.hexdigest())
    members, seen_chats, selected_members = [], set(), set()
    for conversation in inventory['conversations']:
        cid = conversation['id']
        if not _identifier(cid) or cid in seen_chats:
            raise ValueError('Invalid or ambiguous legacy conversation identity')
        seen_chats.add(cid)
        suffix = cid + ('.meta.json' if conversation['format'] == 'jsonl' else '.json')
        candidates = [(destination, item) for destination, item in files.items()
                      if item['category'] == 'conversation' and PurePosixPath(destination).name == suffix]
        if len(candidates) != 1:
            raise ValueError('Legacy conversation metadata is ambiguous')
        destination, item = candidates[0]
        value = json.loads(_snapshot_bytes(snapshot, item))
        if conversation['format'] == 'json':
            for message in value['messages']:
                rewrite_message_images(message, images)
        template = value.get('extra_data', {}).get('team_template')
        if (not isinstance(template, dict) or not _identifier(template.get('id'))
                or not isinstance(template.get('agents'), list) or not 1 <= len(template['agents']) <= 256):
            raise ValueError('Legacy conversation needs an explicit saved team; no default will be substituted')
        ids, names, model_rewrites, instruction_rewrites = set(), set(), {}, {}
        for agent in template['agents']:
            if not isinstance(agent, dict) or not _identifier(agent.get('id')):
                raise ValueError('Legacy team member has no stable config ID')
            # Validate the original recipe before explicit model conversion.
            # Toolset/MCP names, instructions and logical identity stay intact.
            config, _ = _config(AgentConfig.from_dict(agent).to_creation_payload())
            if mcp_configuration is not None:
                mcp_configuration.check_member(config)
            if agent['id'] in ids or config['name'] in names:
                raise ValueError('Legacy team member identities or names are ambiguous')
            ids.add(agent['id']); names.add(config['name'])
            instructions = agent.get('instructions')
            if isinstance(instructions, str):
                source_path = agent.get('source_path')
                if source_path is not None and not isinstance(source_path, str):
                    raise ValueError('Invalid legacy template source path')
                edits = prompt_reference_edits(instructions.encode(), path=source_path,
                                               relocations=relocations, body_only=True)
                if edits:
                    agent['instructions'] = apply_edits(instructions.encode(), edits).decode()
                    instruction_rewrites[agent['id']] = edits
            if model_selection is not None:
                agent['model'] = model_selection.convert(cid, agent['id'], config['model'])
                model_rewrites[agent['id']] = agent['model']
                selected_members.add((cid, agent['id']))
            identity = str(uuid5(UUID(manifest['fence']['sha256'][:32]), _encoded([cid, agent['id']]).decode()))
            if len(members) >= 100000:
                raise ValueError('Legacy migration exceeds 100000 Agent member identities')
            members.append(dict(conversation_id=cid, config_id=agent['id'], instance_id=identity))
        rewrites = {}
        for definition in (template, *template['agents']):
            source_path = definition.get('source_path')
            if source_path is not None and not isinstance(source_path, str):
                raise ValueError('Invalid legacy template source path')
            if source_path in paths:
                rewrites[source_path] = paths[source_path]
                definition['source_path'] = paths[source_path]
        raw = _encoded(value)
        # Keep only rewrite metadata in the plan, not every conversation body.
        # A large migration must not accumulate all histories in Python memory.
        files[destination] = dict(item, rewrite_paths=rewrites, rewrite_models=model_rewrites,
                                 rewrite_instructions=instruction_rewrites, image_relocations=images, original_size=item['size'],
                                 original_sha256=item['sha256'], size=len(raw), sha256=sha256(raw).hexdigest())
    if model_selection is not None:
        model_selection.require_members(selected_members)
    return files, members, conversions


def _copy(snapshot, root, item):
    path = root / item['target']
    _private_dir(path.parent)
    expected = {key: item[key] for key in ('size', 'sha256')}
    if path.exists() or path.is_symlink():
        _private_file(path)
        if _hash_file(path, max_bytes=item['size']) != expected:
            raise ValueError('Imported data differs from its pending migration; refusing to overwrite')
        return
    partial = path.with_name('.' + path.name + '.import-partial')
    if partial.exists() or partial.is_symlink():
        _private_file(partial); partial.unlink()
    fd = _open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'wb') as output:
        if 'image_relocations' in item and item['target'].endswith('.jsonl'):
            from .migration_images import converted_message_lines
            digest, size = sha256(), 0
            original = {**item, 'size': item['original_size'], 'sha256': item['original_sha256']}
            for chunk in converted_message_lines(snapshot, original, item['image_relocations']):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            actual = dict(size=size, sha256=digest.hexdigest())
        elif 'converted' in item or 'rewrite_paths' in item or 'template_model_edits' in item:
            if 'converted' in item:
                raw = item['converted']
            elif 'template_model_edits' in item:
                from .migration_templates import apply_edits
                original = _snapshot_bytes(snapshot, {**item, 'size': item['original_size'],
                                                      'sha256': item['original_sha256']})
                raw = apply_edits(original, item['template_model_edits'])
            else:
                value = json.loads(_snapshot_bytes(snapshot, {**item, 'size': item['original_size'],
                                                             'sha256': item['original_sha256']}))
                from .migration_images import rewrite_message_images
                if not item['target'].endswith('.meta.json'):
                    for message in value['messages']:
                        rewrite_message_images(message, item['image_relocations'])
                template = value['extra_data']['team_template']
                for definition in (template, *template['agents']):
                    source_path = definition.get('source_path')
                    if source_path in item['rewrite_paths']:
                        definition['source_path'] = item['rewrite_paths'][source_path]
                for agent in template['agents']:
                    if agent['id'] in item.get('rewrite_models', {}):
                        agent['model'] = item['rewrite_models'][agent['id']]
                    if agent['id'] in item.get('rewrite_instructions', {}):
                        from .migration_templates import apply_edits
                        agent['instructions'] = apply_edits(agent['instructions'].encode(),
                            item['rewrite_instructions'][agent['id']]).decode()
                raw = _encoded(value)
            output.write(raw)
            actual = dict(size=len(raw), sha256=sha256(raw).hexdigest())
        else:
            actual = _hash_file(snapshot / item['blob'], max_bytes=item['size'], copy_to=output)
        output.flush(); os.fsync(output.fileno())
    if actual != expected:
        raise ValueError('Backup content changed during import')
    os.replace(partial, path)
    _sync_directory(path.parent)


def _unchanged_sources(manifest):
    current = _source_plan(manifest['spec'], max_bytes=max(1, manifest['total_bytes']))
    if manifest.get('filesystem_metadata') is None:
        # Previously published byte-only backups remain usable. They cannot
        # prove permission/empty-directory fidelity for workspace recovery.
        current['files'] = [{key: value for key, value in row.items() if key != 'source_mode'}
                            for row in current['files']]
    elif (current['filesystem_metadata'] != manifest['filesystem_metadata']
            or current['directories'] != manifest['directories']):
        raise ValueError('Legacy directory metadata changed after backup')
    # Platform-owned data can evolve independently while Agent is fenced. Its
    # retained-path listing is informational and not imported into this App.
    def content(inventory):
        return {key: value for key, value in inventory.items() if key not in ('retained', 'sha256')}
    if (current['files'] != manifest['files'] or current['total_bytes'] != manifest['total_bytes']
            or content(current['inventory']) != content(manifest['inventory'])):
        raise ValueError('Legacy data changed after backup; a new migration snapshot is required')


def import_backup(snapshot, *, digest, fence, model_credentials=None, model_selection=None, mcp_configuration=None,
                  retained_workspaces=None, oauth_configuration=None):
    if not isinstance(fence, MigrationFence):
        raise ValueError('A live legacy migration fence is required')
    fence.assert_owned()
    snapshot = Path(snapshot)
    verify_backup(snapshot, digest=digest)
    manifest = _read_json(snapshot / 'manifest.json')
    if sha256(_encoded(manifest)).hexdigest() != digest or manifest['fence'] != fence.identity:
        raise ValueError('Backup does not belong to this migration fence')
    bindings = None
    if model_credentials is not None:
        from .migration_credentials import ModelCredentialConversion
        if not isinstance(model_credentials, ModelCredentialConversion):
            raise ValueError('Supply an explicit model credential conversion')
        model_credentials.assert_matches(digest, fence)
        bindings = model_credentials.describe()
    if model_selection is not None:
        from .migration_models import ModelSelectionConversion
        if not isinstance(model_selection, ModelSelectionConversion):
            raise ValueError('Supply an explicit saved-member model selection conversion')
        model_selection.assert_matches(digest, fence)
        selected_bindings = model_selection.describe()
        if selected_bindings['models'].get('oauth') and oauth_configuration is None:
            raise ValueError('Preserved OAuth model selections require paired credential migration')
        if bindings is not None:
            if bindings['owner'] != selected_bindings['owner']:
                raise ValueError('Model credential and selection owners must match')
            # Keys stay in the provider node vault. They are not Agent inputs.
            selected_bindings['provisioning'] = bindings
        bindings = selected_bindings
    if model_credentials is not None:
        model_credentials.assert_selection(model_selection)
    if bindings is not None and len(_encoded(bindings)) > 64 * 1024:
        raise ValueError('Model conversion exceeds its binding document limit')
    mcp_bindings = None
    if mcp_configuration is not None:
        from .migration_mcp_import import MCPImportConversion
        if not isinstance(mcp_configuration, MCPImportConversion):
            raise ValueError('Supply explicit reviewed MCP App bindings')
        mcp_configuration.assert_matches(digest, fence)
        mcp_bindings = mcp_configuration.describe()
        if bindings is not None and any(bindings[key] != mcp_bindings[key] for key in ('owner', 'node_id')):
            raise ValueError('Model and MCP migration must select the same Agent owner and node')
    workspace_bindings = None
    oauth_bindings = None
    if oauth_configuration is not None:
        from .migration_oauth import OAuthConfigurationConversion
        if not isinstance(oauth_configuration, OAuthConfigurationConversion):
            raise ValueError('Supply explicit scoped OAuth conversion')
        oauth_configuration.assert_matches(digest, fence)
        oauth_bindings = oauth_configuration.describe()
        if any(item is not None and any(item[k] != oauth_bindings[k] for k in ('owner', 'node_id'))
               for item in (bindings, mcp_bindings)):
            raise ValueError('OAuth and other model conversions must select the same Agent placement')
        if bindings is not None:
            if bindings['models'].get('oauth', oauth_bindings['providers']) != oauth_bindings['providers']:
                raise ValueError('OAuth credentials and model selections must preserve the same providers')
            bindings = {**bindings, 'models': {**bindings['models'], 'oauth': oauth_bindings['providers']}}
            if len(_encoded(bindings)) > 64 * 1024:
                raise ValueError('Model conversion exceeds its binding document limit')
    if retained_workspaces is not None:
        from .migration_workspaces import RetainedWorkspaceConversion
        if not isinstance(retained_workspaces, RetainedWorkspaceConversion):
            raise ValueError('Supply an explicit retained workspace conversion')
        retained_workspaces.assert_matches(digest, fence)
        workspace_bindings = retained_workspaces.describe()
        if any(item is not None and item['owner'] != workspace_bindings['owner']
               for item in (bindings, mcp_bindings, oauth_bindings)):
            raise ValueError('Migration conversions must select the same owner')
    root = _destination(manifest['spec'], fence.identity['target'])
    prior = transition_state(root)
    retained_commit = (workspace_bindings is not None and prior is not None
        and prior['phase'] == 'committed' and prior['backup'] == digest
        and prior['fence'] == fence.identity['sha256']
        and prior.get('workspace_bindings') == sha256(_encoded(workspace_bindings)).hexdigest())
    # Once cut over, retained workspaces are live user data. Repeating the same
    # committed import must verify its receipt, not overwrite or demand that
    # ordinary Files/Shell writes still match the pre-cutover archive.
    if not retained_commit:
        _unchanged_sources(manifest)
    files, members, conversions = _plan(snapshot, manifest, root, model_credentials=model_credentials,
                                       model_selection=model_selection, mcp_configuration=mcp_configuration,
                                       retained_workspaces=retained_workspaces, oauth_configuration=oauth_configuration)
    from .app_data import AppProjects
    projects = dict(protocol=1, projects={p['id']: p['path'] for p in AppProjects(
        manifest['spec']['projects']).list_projects()})
    if len(_encoded(projects)) > 64 * 1024:
        raise ValueError('Migrated project bindings exceed their admission limit')
    state = dict(protocol=1, phase='importing', operation=fence.identity['operation'],
                 namespace=fence.identity['namespace'], backup=digest, fence=fence.identity['sha256'],
                 project_bindings=sha256(_encoded(projects)).hexdigest())
    if bindings is not None:
        state['model_bindings'] = sha256(_encoded(bindings)).hexdigest()
    if mcp_bindings is not None:
        state['mcp_bindings'] = sha256(_encoded(mcp_bindings)).hexdigest()
    if workspace_bindings is not None:
        state['workspace_bindings'] = sha256(_encoded(workspace_bindings)).hexdigest()
    if oauth_bindings is not None:
        state['oauth_bindings'] = sha256(_encoded(oauth_bindings)).hexdigest()
    _private_dir(root)
    with registry_lock(root / 'data-admission.lock', timeout=0):
        reservation = import_reservation(root)
        if reservation is not None and (reservation['phase'] != 'reserved'
                or any(reservation[k] != state[k] for k in ('operation', 'namespace', 'fence'))):
            raise ValueError('Import does not match the reserved migration destination')
        previous = transition_state(root)
        if retained_commit and previous != prior:
            raise ValueError('Committed retained workspace migration changed during retry')
        if previous is not None:
            # Preserve idempotent retry of old completed/pending migrations.
            # They did not capture project admission; do not silently rewrite
            # an existing receipt to claim stronger migration guarantees.
            if 'project_bindings' not in previous:
                state.pop('project_bindings')
            if {**{key: value for key, value in previous.items() if key != 'receipt'}, 'phase': 'importing'} != state:
                raise ValueError('Agent destination belongs to a different migration')
            if previous['phase'] == 'aborted':
                raise ValueError('Agent data import was aborted; choose a new destination')
            if previous['phase'] == 'committed':
                receipt = _read_json(root / 'migration-receipt.json')
                if receipt.get('backup') != digest or sha256(_encoded(receipt)).hexdigest() != previous.get('receipt'):
                    raise ValueError('Committed migration receipt is invalid')
                return receipt
        else:
            if set(p.name for p in root.iterdir()) - {'data-admission.lock', '.migration.json.partial', RESERVATION_FILE}:
                raise ValueError('Agent import destination must be empty or the same pending migration')
            _atomic_json(root / STATE_FILE, state)
        store = AgentInstanceStore(root / 'instances', namespace=state['namespace'])
    try:
        if oauth_bindings is not None:
            oauth_configuration.provision(root)
            _atomic_json(root / 'migration-oauth-bindings.json', oauth_bindings)
        if 'project_bindings' in state:
            _atomic_json(root / 'migration-projects.json', projects)
        if workspace_bindings is not None:
            _atomic_json(root / 'migration-workspaces.json', workspace_bindings)
        if mcp_bindings is not None:
            mcp_configuration.provision()
            _atomic_json(root / 'migration-mcp-bindings.json', mcp_bindings)
        if bindings is not None:
            # A failed/mismatched vault write leaves the target unstartable.
            # Retry ensures identical credentials; it never rotates shared refs.
            if model_credentials is not None:
                model_credentials.provision()
            _atomic_json(root / 'migration-model-bindings.json', bindings)
            if model_selection is not None:
                _atomic_json(root / 'migration-model-selections.json', model_selection.audit())
        for item in files.values():
            fence.assert_owned()
            _copy(snapshot, root, item)
        store.seed_legacy_members(members)
        # Recheck copied data and the archive before opening startup admission.
        for item in files.values():
            _copy(snapshot, root, item)
        verify_backup(snapshot, digest=digest)
        _unchanged_sources(manifest)
        fence.assert_owned()
        if mcp_configuration is not None:
            mcp_configuration.assert_matches(digest, fence)
        if retained_workspaces is not None:
            retained_workspaces.assert_matches(digest, fence)
        if oauth_configuration is not None:
            oauth_configuration.assert_matches(digest, fence)
            oauth_configuration.provision(root)
        receipt = dict(protocol=1, backup=digest, namespace=state['namespace'],
                       conversations=len(manifest['inventory']['conversations']),
                       members=members, conversions=conversions,
                       files=[{key: item[key] for key in ('target', 'size', 'sha256')} for item in files.values()])
        if 'project_bindings' in state:
            receipt['project_bindings'] = projects
        if workspace_bindings is not None:
            receipt['workspace_bindings'] = workspace_bindings
        if bindings is not None:
            receipt['model_bindings'] = bindings
        if mcp_bindings is not None:
            receipt['mcp_bindings'] = mcp_bindings
        if oauth_bindings is not None:
            receipt['oauth_bindings'] = oauth_bindings
        _atomic_json(root / 'migration-receipt.json', receipt)
        # Runtime's admission lock and the instance writer lock close the gap
        # between checking state and acquiring its data namespace.
        _atomic_json(root / STATE_FILE, {**state, 'phase': 'committed', 'receipt': sha256(_encoded(receipt)).hexdigest()})
        return receipt
    finally:
        store.close()


def abort_pending_import(*, fence):
    """Release untouched legacy sources only while the target cannot start.

    Deliberately refuses a committed import: post-cutover writes need the release
    coordinator's explicit rollback policy. Partial destination data is retained.
    """
    fence.assert_owned()
    root = Path(fence.identity['target'])
    _private_dir(root, create=False)
    with registry_lock(root / 'data-admission.lock', timeout=0):
        state = transition_state(root)
        reservation = import_reservation(root)
        if reservation is not None and (
                reservation['fence'] != fence.identity['sha256']
                or any(reservation[k] != fence.identity[k] for k in ('operation', 'namespace'))):
            raise ValueError('Reserved destination belongs to another migration')
        if state is None:
            if reservation is None or set(p.name for p in root.iterdir()) - {
                    RESERVATION_FILE, '.' + RESERVATION_FILE + '.partial', 'data-admission.lock'}:
                raise ValueError('Only an untouched reserved destination can be aborted before import')
            _atomic_json(root / RESERVATION_FILE, {**reservation, 'phase': 'aborted'})
            fence.release_sources()
            return
        if (state is None or state.get('fence') != fence.identity['sha256']
                or state['phase'] not in ('importing', 'aborted')):
            raise ValueError('Only this migration\'s uncommitted destination can be aborted')
        store = AgentInstanceStore(root / 'instances', namespace=fence.identity['namespace'])
        try:
            _atomic_json(root / STATE_FILE, {**state, 'phase': 'aborted'})
            if reservation is not None:
                _atomic_json(root / RESERVATION_FILE, {**reservation, 'phase': 'aborted'})
            fence.release_sources()
        finally:
            store.close()
