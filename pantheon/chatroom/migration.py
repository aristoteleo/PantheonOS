"""Read-only legacy Agent inventory before a fenced data migration.

This deliberately does not instantiate Settings/MemoryManager: those constructors
can create directories, resolve credentials or repair data. No import, writer
fence or release cutover is implied by a successful inventory.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from .app_data import AppProjects
from .data_fence import CONTROL_FILES, MigrationFence


CONFIG_DATA = frozenset({'agents', 'teams', 'prompts', 'skills', 'brain', 'learning',
                         'memory-store', 'MEMORY.md'})
PLATFORM_DATA = frozenset({'projects.json', 'projects.lock', 'fleet-node', 'app-store',
                          'desktop', 'chatroom', 'logs', 'tmp', 'packages'})


def _absolute(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError('Migration sources must be explicit absolute paths')
    path = Path(value)
    if path.is_symlink():
        raise ValueError('Migration sources must not be symlinks')
    return path.resolve()


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _agent_libraries(values, existing_roots):
    """Explicit external template directories; never discover them from prompts."""
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 128:
        raise ValueError('Agent libraries must be a list of at most 128 absolute directories')
    result = []
    for value in values:
        root = _absolute(value)
        if not root.is_dir():
            raise ValueError('External Agent libraries must be existing directories')
        if any(root.is_relative_to(other) or other.is_relative_to(root)
               for other in (*existing_roots, *result)):
            raise ValueError('External Agent library roots must not overlap other migration sources')
        result.append(root)
    return sorted(result)


def inspect_legacy(*, projects, active_project, default_project, home_memory,
                   global_config, project_config, memory_overrides=None, environment_file=None,
                   agent_libraries=None, model_environment_file=None, mcp_environment_file=None,
                   mcp_configuration_file=None):
    """Inventory explicit launch roots without reading API keys or copying files.

    project_config is the legacy launcher's selected .pantheon directory; every
    other project's configuration is separately reported, not silently merged.
    memory_overrides maps stable project IDs to custom conversation directories.
    agent_libraries explicitly includes external Markdown Agent/Team libraries.
    """
    snapshot = AppProjects(projects, active_id=active_project, default_id=default_project)
    projects = snapshot.list_projects()
    overrides = memory_overrides or {}
    if not isinstance(overrides, dict) or overrides.keys() - {p['id'] for p in projects}:
        raise ValueError('Memory overrides must name registered project IDs')
    files, stores, conversations, issues, retained = [], [], [], [], []
    sources, chat_ids = {}, {}

    def issue(code, path):
        issues.append({'code': code, 'source': str(path)})

    def record(path, target, category):
        if len(files) >= 100000:
            raise ValueError('Migration inventory exceeds 100000 files')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            issue('non_regular_file', path)
            return None
        digest = sha256()
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            if _stamp(os.fstat(stream.fileno())) != _stamp(info):
                raise ValueError('Source changed during migration inventory')
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            if _stamp(os.fstat(stream.fileno())) != _stamp(info):
                raise ValueError('Source changed during migration inventory')
        if _stamp(path.lstat()) != _stamp(info):
            raise ValueError('Source changed during migration inventory')
        files.append({'source': str(path), 'target': target, 'category': category,
                      'size': info.st_size, 'sha256': digest.hexdigest()})
        return info

    def document(path):
        # Conversation metadata/legacy JSON can contain tool output. Bound the
        # parser's memory; larger histories need explicit streaming conversion.
        with path.open('rb') as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('Oversized conversation document')
        return json.loads(raw)

    def conversation_store(path, target, project_id):
        if path in sources:
            if sources[path] != target:
                issue('ambiguous_conversation_store', path)
            return
        sources[path] = target
        stores.append({'source': str(path), 'target': target, 'project_id': project_id,
                       'exists': path.exists()})
        if not path.exists():
            return
        if not path.is_dir():
            issue('invalid_conversation_store', path)
            return
        stamps, metadata, streams, legacy = {}, {}, {}, {}
        for item in sorted(path.iterdir()):
            if item.name in CONTROL_FILES:
                continue
            info = record(item, target + '/' + item.name, 'conversation')
            if info is None:
                continue
            stamps[item] = _stamp(info)
            if item.name.endswith('.meta.json'): metadata[item.name[:-10]] = item
            elif item.name.endswith('.jsonl'): streams[item.name[:-6]] = item
            elif item.name.endswith('.json'): legacy[item.name[:-5]] = item
            else: issue('unrecognized_conversation_file', item)
        for cid in sorted(metadata.keys() | streams.keys() | legacy.keys()):
            if cid in chat_ids and chat_ids[cid] != path:
                issue('duplicate_conversation_id', path / cid)
            chat_ids[cid] = path
            try:
                if cid in metadata or cid in streams:
                    if cid not in metadata or cid in legacy:
                        raise ValueError('Ambiguous or missing metadata')
                    value = document(metadata[cid])
                    count = 0
                    if cid in streams:
                        with streams[cid].open('rb') as stream:
                            while line := stream.readline(16 * 1024 * 1024 + 1):
                                if len(line) > 16 * 1024 * 1024:
                                    raise ValueError('Oversized message')
                                if not line.strip(): continue
                                if not isinstance(json.loads(line), dict): raise ValueError('Invalid message')
                                count += 1
                    format = 'jsonl'
                else:
                    value = document(legacy[cid])
                    messages = value.get('messages') if isinstance(value, dict) else None
                    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
                        raise ValueError('Invalid messages')
                    count, format = len(messages), 'json'
                if (not isinstance(value, dict) or value.get('id') != cid
                        or not isinstance(value.get('name'), str)
                        or not isinstance(value.get('extra_data', {}), dict)):
                    raise ValueError('Invalid conversation identity')
                template = value.get('extra_data', {}).get('team_template')
                conversations.append({'id': cid, 'project_id': project_id, 'format': format,
                                      'messages': count, 'embedded_team': isinstance(template, dict)})
            except (ValueError, KeyError, TypeError, UnicodeError):
                issue('invalid_conversation', path / cid)
        if any(_stamp(item.lstat()) != stamp for item, stamp in stamps.items()):
            raise ValueError('Conversation source changed during inventory')

    project_memories = [(p, _absolute(overrides.get(p['id'], str(Path(p['path']) / '.pantheon/memory'))))
                        for p in projects]
    home = _absolute(home_memory)
    selected, global_root = _absolute(project_config), _absolute(global_config)
    config_roots = {_absolute(str(Path(project['path']) / '.pantheon')) for project in projects}
    external = _agent_libraries(agent_libraries,
        {selected, global_root, home, *config_roots, *(path for _, path in project_memories)})
    from .migration_handoff import handoff_source
    handoffs = {}
    if mcp_environment_file is not None and mcp_configuration_file is not None:
        raise ValueError('Use one MCP runtime handoff; the full configuration already contains its environment')
    for field, value in (('model_environment_file', model_environment_file), ('mcp_environment_file', mcp_environment_file),
                         ('mcp_configuration_file', mcp_configuration_file)):
        handoff = handoff_source({field: value}, field=field)
        if handoff is None:
            continue
        if str(handoff) != value or handoff in handoffs.values():
            raise ValueError('Use distinct canonical paths for private runtime handoffs')
        handoffs[field] = handoff
        # Exclude any location the dry-run scanner would hash/parse as Agent
        # data. The runtime's fleet-node subtree is already platform-owned.
        roots = {selected, global_root, home, *config_roots, *(p for _, p in project_memories), *external}
        for root in roots:
            if handoff.is_relative_to(root):
                relative = handoff.relative_to(root)
                if (not relative.parts or root not in {selected, global_root, *config_roots}
                        or relative.parts[0] not in PLATFORM_DATA):
                    raise ValueError('Runtime environment handoff must be outside inventoried Agent data')
    for project, path in project_memories:
        conversation_store(path, 'conversations/projects/' + sha256(project['id'].encode()).hexdigest(), project['id'])
    # Legacy home often aliases the default project's memory. Import it once,
    # under that project's stable identity; per-chat lookup searches both stores.
    if home not in sources:
        conversation_store(home, 'conversations/home', None)

    def config_tree(root, target):
        if not root.exists(): return
        if not root.is_dir():
            issue('invalid_configuration_root', root)
            return
        for item in sorted(root.iterdir()):
            if item.name in CONTROL_FILES:
                continue
            if item.name == 'memory' and item.resolve() in sources:
                continue
            if item.name in PLATFORM_DATA:
                retained.append({'source': str(item), 'reason': 'platform_owned_or_transient'})
            elif item.name == 'images':
                from .migration_images import image_destination
                pending = [item]
                while pending:
                    entry = pending.pop()
                    if entry.is_symlink():
                        issue('non_regular_file', entry)
                    elif entry.is_dir():
                        pending.extend(sorted(entry.iterdir(), reverse=True))
                    elif entry == item:
                        issue('invalid_configuration_root', entry)
                    else:
                        record(entry, image_destination(item) + '/' + entry.relative_to(item).as_posix(), 'image-store')
            elif item.name in CONFIG_DATA:
                pending = [item]
                while pending:
                    entry = pending.pop()
                    if entry.is_symlink():
                        issue('non_regular_file', entry)
                    elif entry.is_dir():
                        pending.extend(sorted(entry.iterdir(), reverse=True))
                    else:
                        relative = str(entry.relative_to(root))
                        record(entry, target + '/' + relative if target else None, 'configuration')
            elif item.name in ('settings.json', 'mcp.json', '.env') or 'credential' in item.name.lower() or 'oauth' in item.name.lower():
                # Do not read secrets to produce a dry-run report, including a
                # digest susceptible to guessing. Conversion needs explicit refs.
                issue('configuration_requires_explicit_conversion', item)
            else:
                issue('unclassified_source', item)

    config_tree(global_root, 'user')
    config_tree(selected, 'configuration/.pantheon')
    for project in projects:
        root = _absolute(str(Path(project['path']) / '.pantheon'))
        if root not in (selected, global_root):
            config_tree(root, None)
    for root in external:
        # Keep separate libraries separate, including colliding filenames/IDs.
        # They are reachable by rewritten paths, not silently merged as IDs.
        target = 'configuration/.pantheon/agents/_imported/' + sha256(str(root).encode()).hexdigest()
        pending = [root]
        while pending:
            entry = pending.pop()
            if entry.name in CONTROL_FILES:
                continue
            if entry.is_symlink():
                issue('non_regular_file', entry)
            elif entry.is_dir():
                pending.extend(sorted(entry.iterdir(), reverse=True))
            elif entry.suffix != '.md':
                issue('external_agent_library_needs_explicit_conversion', entry)
            else:
                record(entry, target + '/' + entry.relative_to(root).as_posix(), 'configuration')
    if any(f['target'] is None for f in files):
        issue('additional_project_configuration_needs_scope_mapping', selected)
    from .migration_environment import environment_source
    env_spec = {'project_config': str(selected)}
    if environment_file is not None:
        env_spec['environment_file'] = environment_file
    env_path = environment_source(env_spec)
    environment = {'source': str(env_path), 'exists': env_path.exists()}
    if environment['exists']:
        if not env_path.is_file():
            issue('non_regular_file', env_path)
        elif not any(item['source'] == str(env_path) for item in issues):
            issue('configuration_requires_explicit_conversion', env_path)
    manifest = {'protocol': 1, 'projects': projects, 'active_project': active_project,
                'default_project': default_project, 'stores': stores, 'files': files,
                'conversations': conversations, 'issues': issues, 'retained': retained,
                'environment': environment,
                'requires_writer_fence': True, 'ready_to_import': False}
    for field, handoff in handoffs.items():
        if handoff == env_path:
            raise ValueError('Use a private runtime environment handoff separate from dotenv')
        issue('configuration_requires_explicit_conversion', handoff)
        manifest[field.removesuffix('_file')] = {'source': str(handoff), 'exists': True}
    # A digest binds the future backup/import to this exact inventory. This
    # report is not a consistent snapshot while any legacy writer is running.
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return {**manifest, 'sha256': sha256(raw).hexdigest()}


def legacy_source_roots(spec):
    """Resolve the complete declared root set without instantiating a writer."""
    snapshot = AppProjects(spec['projects'], active_id=spec['active_project'],
                           default_id=spec['default_project'])
    projects = snapshot.list_projects()
    overrides = spec.get('memory_overrides') or {}
    if not isinstance(overrides, dict) or overrides.keys() - {p['id'] for p in projects}:
        raise ValueError('Memory overrides must name registered project IDs')
    roots = {_absolute(spec[key]) for key in ('home_memory', 'global_config', 'project_config')}
    for project in projects:
        root = _absolute(str(Path(project['path']) / '.pantheon'))
        roots.add(root)
        roots.add(_absolute(overrides.get(project['id'], str(root / 'memory'))))
    roots.update(_agent_libraries(spec.get('agent_libraries'), roots))
    return sorted(roots)


def fence_legacy(spec, *, operation, target, namespace):
    """Fence every explicit inventory root before taking a consistent snapshot.

    This only fences updated cooperative local runtimes. Deployment-level
    exclusion of older binaries/replicas and separate configuration writers is
    still required before backup/import. No claim of ready-to-import is made.
    """
    return MigrationFence(legacy_source_roots(spec), operation=operation,
                          target=target, namespace=namespace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True, help='Explicit legacy roots and stable project snapshot (JSON)')
    parser.add_argument('--output', required=True, help='New private dry-run report; never overwrite')
    args = parser.parse_args()
    with open(args.spec, 'rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('Migration spec exceeds 1 MiB')
    result = inspect_legacy(**json.loads(raw))
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


if __name__ == '__main__':
    main()
