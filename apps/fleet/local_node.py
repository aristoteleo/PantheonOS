"""Identify this process's machine without adopting a different Fleet node."""
import json
import os
from pathlib import Path


def local_node_id() -> str | None:
    if node_id := os.environ.get('PANTHEON_FLEET_NODE_ID'):
        return node_id
    runtime = Path(os.environ.get('PANTHEON_FLEET_STATE_DIR', '/tmp/fleet-node')) / 'runtime.json'
    try:
        return json.loads(runtime.read_text()).get('node_id') or None
    except (OSError, ValueError):
        return None
