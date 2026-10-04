"""Identify this process's machine without adopting a different Fleet node."""
import json
import os
from pathlib import Path


def local_node_id() -> str | None:
    # Prepared native Apps receive this protected value from the Runner. It
    # takes precedence over legacy/ambient hints or a host runtime.json.
    if node_id := os.environ.get('PANTHEON_NODE_ID') or os.environ.get('PANTHEON_FLEET_NODE_ID'):
        return node_id
    runtime = Path(os.environ.get('PANTHEON_FLEET_STATE_DIR', '/tmp/fleet-node')) / 'runtime.json'
    try:
        return json.loads(runtime.read_text()).get('node_id') or None
    except (OSError, ValueError):
        return None
