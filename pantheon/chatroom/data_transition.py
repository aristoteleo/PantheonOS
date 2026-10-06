"""Admission barrier shared by Agent startup and data migration."""
import json
import os
from hashlib import sha256
import re
import stat
from pathlib import Path

STATE_FILE = 'migration.json'
RESERVATION_FILE = 'migration-reservation.json'
INITIALIZATION_CAPABILITY = {'protocol': 1, 'dataDirectory': 'agent'}


def import_reservation(root):
    """Read the startup barrier installed before fencing or backup begins."""
    path = Path(root) / RESERVATION_FILE
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('Agent migration reservation must be a regular file') from None
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise ValueError('Agent migration reservation must be owner-private')
        raw = stream.read(4097)
    try:
        value = json.loads(raw) if len(raw) <= 4096 else None
        if (not isinstance(value, dict) or set(value) != {'protocol', 'phase', 'operation', 'namespace', 'fence', 'request'}
                or type(value['protocol']) is not int or value['protocol'] != 1
                or value['phase'] not in ('reserved', 'aborted')
                or any(not isinstance(value[k], str) or not 0 < len(value[k]) <= 256
                       or any(ord(c) < 32 for c in value[k]) for k in ('operation', 'namespace'))
                or any(not isinstance(value[k], str) or not re.fullmatch('[0-9a-f]{64}', value[k])
                       for k in ('fence', 'request'))):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise ValueError('Agent migration reservation is invalid; recovery is required') from None


def check_mcp_launch(expected, actual):
    """Check imported bindings without importing owner-side migration tools.

    This runs in the independently packaged Agent, which intentionally has no
    backup reader, vault provisioner or MCP conversion implementation.
    """
    if not isinstance(actual, dict) or any(actual.get(key) != expected[key] for key in ('owner', 'node_id')):
        raise ValueError
    defaults = actual['defaults']
    profiles = actual['profiles']['mcp_servers']
    expected_profiles = expected['profiles']
    if expected.get('protocol') == 2:
        def stable(values):
            result = {}
            for name, profile in values.items():
                provider = profile['provider']
                if type(provider.get('generation')) is not int or provider['generation'] < 1:
                    raise ValueError
                result[name] = {**profile, 'provider': {k: v for k, v in provider.items() if k != 'generation'}}
            return result
        profiles, expected_profiles = stable(profiles), stable(expected_profiles)
    elif expected.get('protocol') != 1:
        raise ValueError
    if (profiles != expected_profiles
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
    reservation = import_reservation(root)
    if reservation is not None and (reservation['phase'] != 'reserved' or state is None or state['phase'] != 'committed'
            or any(state[k] != reservation[k] for k in ('operation', 'namespace', 'fence'))):
        raise ValueError('Agent data is reserved for migration; resume its original import')
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
