"""Disposable CI Fleet node; a one-use token, no reusable Agent credentials.

The local acceptance coordinator owns the node ID and revokes it before stopping
this Actions run. A bounded lifetime also releases the process if the coordinator
disappears. Never publish Fleet logs/state, which may contain enrollment material.
"""
import os
from pathlib import Path
import re
import subprocess

node = os.environ.pop('FLEET_ACCEPTANCE_NODE')
token = os.environ.pop('FLEET_ACCEPTANCE_JOIN')
if not re.fullmatch(r'n_[a-f0-9]{20}', node) or not token:
    raise ValueError('A scoped node ID and one-use join token are required')
root = Path(os.environ['RUNNER_TEMP']) / 'speech-fleet-node'
root.mkdir(mode=0o700)
(root / 'node_id').write_text(node)
binary = root.parent / 'fleet-acceptance'
with (root / 'private.log').open('w') as output:
    process = subprocess.Popen([
        str(binary), 'up', '--controller', 'https://fleet-staging.aristoteleo.com',
        '--join-token', token, '--name', 'Temporary Docker speech acceptance',
        '--kind', 'sandbox', '--labels', 'cpu,acceptance', '--caps', 'proc,net',
        '--workdir', str(root), '--state-dir', str(root), '--no-files', '--no-capture-setup',
    ], stdout=output, stderr=subprocess.STDOUT)
    del token
    print('Ephemeral Fleet node started; awaiting installed acceptance.', flush=True)
    try:
        process.wait(timeout=900)
        raise RuntimeError('Ephemeral Fleet node exited before acceptance cleanup')
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
