"""Admission barrier shared by Agent startup and data migration."""
import json
import re
from pathlib import Path

STATE_FILE = 'migration.json'


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


def require_ready(root, namespace):
    state = transition_state(root)
    if state is not None and (state['phase'] != 'committed' or state.get('namespace') != namespace):
        raise ValueError('Agent data migration has not committed for this namespace')
