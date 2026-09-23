"""Resolve a named credential through this node's Fleet binary, never over RPC."""
import json
import os
from pathlib import Path
import re
import subprocess


ERROR = 'Node credential unavailable or not authorized for this endpoint; check it on the selected Fleet node'


def validate_ref(value):
    if not isinstance(value, str) or not re.fullmatch(r'node-secret://[a-z][a-z0-9_-]{0,63}', value):
        raise ValueError('Use a node credential reference such as node-secret://openrouter')
    return value


def read(ref, endpoint):
    validate_ref(ref)
    executable = os.environ.get('PANTHEON_FLEET_EXECUTABLE', '')
    root = os.environ.get('PANTHEON_MODEL_CREDENTIALS', '')
    if not executable or not Path(executable).is_absolute() or not root or not Path(root).is_absolute():
        raise ValueError('Update Fleet on this node to use node credential references')
    try:
        result = subprocess.run([executable, 'model-credential-read'],
            input=json.dumps({'ref': ref, 'endpoint': endpoint}).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError(ERROR)
        data = json.loads(result.stdout)
        key = data.get('key')
        if (not isinstance(key, str) or not 0 < len(key) <= 8192
                or any(not 33 <= ord(c) <= 126 for c in key)):
            raise ValueError(ERROR)
        return key
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        # Never forward a child error, credential contents, or a node-local path.
        raise ValueError(ERROR) from None
