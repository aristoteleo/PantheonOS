"""Disposable CI Fleet node; a one-use token, no reusable Agent credentials.

The local acceptance coordinator owns the node ID and revokes it before stopping
this Actions run. A bounded lifetime also releases the process if the coordinator
disappears. Never publish Fleet logs/state, which may contain enrollment material.
"""
import os
from pathlib import Path
import re
import subprocess

# Read-only diagnostics before enrollment: no Fleet credentials are inherited by
# these probes, and only fixed context/type formats are requested from Docker.
import json
import shutil
node = os.environ.pop('FLEET_ACCEPTANCE_NODE')
token = os.environ.pop('FLEET_ACCEPTANCE_JOIN')
binary_path = shutil.which('docker')
if not binary_path:
    raise RuntimeError('CI Docker CLI unavailable')
context = subprocess.run([binary_path, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
                         text=True, capture_output=True, timeout=10)
host = context.stdout.strip()
if context.returncode or not host.startswith('unix:///'):
    raise RuntimeError('CI requires a local Unix Docker context')
clean = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE':'1',
         'PYTHONUNBUFFERED':'1', 'PYTHONUTF8':'1'}
info = subprocess.run([binary_path, '--host', host, 'info', '--format', '{{.OSType}}'],
                      env=clean, text=True, capture_output=True, timeout=10)
print(json.dumps({'event':'docker_probe', 'context_exit':context.returncode,
                  'info_exit':info.returncode, 'os_type':info.stdout.strip(),
                  'context_warning':context.stderr[:2000], 'info_warning':info.stderr[:2000]}), flush=True)
if info.returncode or info.stdout.strip() != 'linux':
    raise RuntimeError('Local CI Docker probe failed before enrollment')

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
