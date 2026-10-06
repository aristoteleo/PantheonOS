"""Admission barrier shared by Agent startup and data migration."""
import json
import os
from hashlib import sha256
import re
import stat
from pathlib import Path

STATE_FILE = 'migration.json'


def check_mcp_launch(expected, actual):
    """Check imported bindings without importing owner-side migration tools.

    This runs in the independently packaged Agent, which intentionally has no
    backup reader, vault provisioner or MCP conversion implementation.
    """
    if not isinstance(actual, dict) or any(actual.get(key) != expected[key] for key in ('owner', 'node_id')):
        raise ValueError
    defaults = actual['defaults']
    if (actual['profiles']['mcp_servers'] != expected['profiles']
            or any(defaults.get(key) != expected['defaults'][key]
                   for key in ('mcp_servers', 'mcp_unified_precedence'))):
        raise ValueError


def transition_state(root):
    path = Path(root) / STATE_FILE
    if path.is_symlink():
        raise ValueError('Agent migration state must not be a symlink')
    try:
        with path.open('rb') as stream:
            raw = stream.read(64 * 1024 + 1)
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw) if len(raw) <= 64 * 1024 else None
        if (not isinstance(value, dict) or type(value.get('protocol')) is not int or value['protocol'] != 1
                or value.get('phase') not in ('importing', 'committed', 'aborted')):
            raise ValueError
        keys = {'protocol', 'phase', 'operation', 'namespace', 'backup', 'fence'}
        digests = ['backup', 'fence']
        if 'model_bindings' in value:
            keys.add('model_bindings'); digests.append('model_bindings')
        if 'mcp_bindings' in value:
            keys.add('mcp_bindings'); digests.append('mcp_bindings')
        if 'project_bindings' in value:
            keys.add('project_bindings'); digests.append('project_bindings')
        if 'workspace_bindings' in value:
            keys.add('workspace_bindings'); digests.append('workspace_bindings')
        if value['phase'] == 'committed':
            keys.add('receipt'); digests.append('receipt')
        if set(value) != keys:
            raise ValueError
        if any(not isinstance(value[key], str) or not 0 < len(value[key]) <= 256
               or any(ord(c) < 32 for c in value[key]) for key in ('operation', 'namespace')):
            raise ValueError
        if any(not isinstance(value[key], str) or not re.fullmatch('[0-9a-f]{64}', value[key]) for key in digests):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise ValueError('Agent data migration state is invalid; recovery is required') from None


def require_ready(root, namespace, model_configuration=None, dependency_configuration=None, projects=None):
    state = transition_state(root)
    if state is not None and (state['phase'] != 'committed' or state.get('namespace') != namespace):
        raise ValueError('Agent data migration has not committed for this namespace')
    if state is not None and 'workspace_bindings' in state:
        try:
            path = Path(root) / 'migration-workspaces.json'
            if path.is_symlink():
                raise ValueError
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024 or sha256(raw).hexdigest() != state['workspace_bindings']:
                raise ValueError
            expected = json.loads(raw)
            if dependency_configuration['owner'] != expected['owner']:
                raise ValueError
            profiles = dependency_configuration['profiles']['toolsets']
            for name, pin in expected['providers'].items():
                profile = profiles[name]
                provider = profile['provider']
                if (profile['alias'] != pin['alias'] or
                        {k: v for k, v in provider.items() if k != 'generation'} != pin['provider']):
                    raise ValueError
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Agent launch must preserve its retained workspace providers') from None
    if state is not None and 'project_bindings' in state:
        try:
            path = Path(root) / 'migration-projects.json'
            if path.is_symlink():
                raise ValueError
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024 or sha256(raw).hexdigest() != state['project_bindings']:
                raise ValueError
            expected = json.loads(raw)
            if (set(expected) != {'protocol', 'projects'} or type(expected['protocol']) is not int
                    or expected['protocol'] != 1 or not isinstance(expected['projects'], dict)):
                raise ValueError
            actual = {p['id']: p['path'] for p in projects}
            # Names, active selection and additional projects can change. An
            # existing identity cannot silently lose or switch its workspace.
            # Paths may belong to a remote Files node: never stat them here.
            if any(actual.get(identity) != path for identity, path in expected['projects'].items()):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Agent launch must preserve its migrated project workspaces') from None
    if state is not None and 'mcp_bindings' in state:
        try:
            path = Path(root) / 'migration-mcp-bindings.json'
            if path.is_symlink():
                raise ValueError
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024 or sha256(raw).hexdigest() != state['mcp_bindings']:
                raise ValueError
            check_mcp_launch(json.loads(raw), dependency_configuration)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Agent launch must preserve its migrated MCP bindings') from None
    if state is not None and 'model_bindings' in state:
        path = Path(root) / 'migration-model-bindings.json'
        try:
            if path.is_symlink():
                raise ValueError
            with path.open('rb') as stream:
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024 or sha256(raw).hexdigest() != state['model_bindings']:
                raise ValueError
            expected = json.loads(raw)
            if 'selection_sha256' in expected:
                audit = Path(root) / 'migration-model-selections.json'
                if (audit.is_symlink() or not isinstance(expected['selection_sha256'], str)
                        or not re.fullmatch('[0-9a-f]{64}', expected['selection_sha256'])):
                    raise ValueError
                digest, size = sha256(), 0
                fd = os.open(audit, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
                with os.fdopen(fd, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > 16 * 1024 * 1024:
                            raise ValueError
                        digest.update(chunk)
                if digest.hexdigest() != expected['selection_sha256']:
                    raise ValueError
            if (not isinstance(model_configuration, dict)
                    or any(model_configuration.get(key) != expected[key] for key in ('owner', 'node_id', 'models'))
                    or any(model_configuration.get('credentials', {}).get(alias) != value['endpoint']
                           for alias, value in expected['credentials'].items())):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Agent launch must preserve its migrated model bindings') from None
