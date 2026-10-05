"""A separate ordinary Notebook process; no embedded Agent/settings imports."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import pytest
from pantheon.apps.builtin.notebook.build_managed import build


@pytest.fixture
def notebook_rpc(tmp_path):
    frontend = os.environ.get('PANTHEON_TEST_NOTEBOOK_FRONTEND')
    package = build(tmp_path / 'notebook', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64',
                    frontend=Path(frontend) if frontend else None)
    workspace, data, home = tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'home'
    workspace.mkdir(); home.mkdir()
    kernel = tmp_path / 'jupyter/kernels/python3'
    kernel.mkdir(parents=True)
    (kernel / 'kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
        'display_name': 'Python', 'language': 'python'}))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'protocol': 1, 'generation': 1, 'owner': 'owner', 'node_id': 'node',
        'instance_id': 'notebook', 'revision': 'revision', 'component': 'backend', 'credentials': {},
        'values': {'notebook': {'execution_timeout': 60, 'execution_logging': False}}}))
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, HOME=str(home), JUPYTER_PATH=str(tmp_path / 'jupyter'),
        PANTHEON_APP_CONFIG=str(config), PANTHEON_APP_RPC_TOKEN=token, PANTHEON_FLEET_ID='owner',
        PANTHEON_NODE_ID='node', PANTHEON_INSTANCE_ID='notebook', PANTHEON_APP_REVISION='revision',
        PANTHEON_COMPONENT_NAME='backend', PANTHEON_INSTANCE_GENERATION='1', PANTHEON_PORT_HTTP='0')
    env.pop('PYTHONPATH', None)
    boot = '''import importlib.abc,runpy,sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name in ('pantheon.agent','pantheon.settings','pantheon.chatroom','pantheon.factory'):
   raise AssertionError('Notebook consulted Agent state: '+name)
sys.meta_path.insert(0,Boundary())
sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
    log_path = tmp_path / 'notebook.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([sys.executable, '-I', '-c', boot, str(package / '.fleet-runtime'),
            str(package / '.fleet-runtime/host.py'), 'start', '--package', str(package), '--data', str(data),
            '--workspace', str(workspace)], stdout=log, stderr=log, env=env)
        try:
            for _ in range(200):
                assert process.poll() is None, log_path.read_text()
                descriptor = data / 'backend-endpoint.json'
                if descriptor.exists():
                    base = f"http://127.0.0.1:{json.loads(descriptor.read_text())['port']}"
                    break
                time.sleep(.05)
            else:
                raise AssertionError(log_path.read_text())
            def rpc(method, **args):
                req = Request(base + '/rpc', data=json.dumps({'method': method, 'args': args}).encode(),
                    headers={'Content-Type': 'application/json', 'X-Fleet-RPC-Token': token})
                with urlopen(req, timeout=30) as response:
                    value = json.load(response)
                assert value['success'], value
                return value['result']
            rpc.package = package
            rpc.workspace = workspace
            yield rpc
        finally:
            process.terminate()
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
                raise
        assert process.returncode == 0, log_path.read_text()

