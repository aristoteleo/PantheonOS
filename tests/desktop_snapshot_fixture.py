"""Run Desktop screenshot packaging in an Agent-free, explicitly bound process."""
import json
import os
from pathlib import Path
import subprocess
import sys


def desktop_snapshot(workspace, uri):
    workspace.mkdir(exist_ok=True)
    boot = '''import asyncio,importlib.abc,json,sys
from pathlib import Path
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name in ('pantheon.agent','pantheon.settings','pantheon.chatroom','pantheon.factory','pantheon.apps.builtin.fleet.local_node'):
   raise AssertionError('Desktop consulted ambient consumer state: '+name)
sys.meta_path.insert(0,Boundary())
sys.path.insert(0,sys.argv[1])
from pantheon.apps.builtin.desktop.files_binding import DesktopFilesBinding
from pantheon.apps.builtin.desktop.data_server import DataServerConfig,LiveViewDataServer
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
root=Path(sys.argv[2])
binding=DesktopFilesBinding(workspace=root,app_roots=[(root/'apps','user')],data_roots=[root],
 server=LiveViewDataServer(config=DataServerConfig()),node_id='desktop-node')
service=DesktopToolSet(files_binding=binding)
result=service._package_screenshot(json.load(sys.stdin)['uri'],'win-1',path='screens/window.png')
assert result['success'],result
assert result['node_id']=='desktop-node'
assert result['image_ref'].startswith('pantheon-node:///desktop-node/')
asyncio.run(service.cleanup())
print(json.dumps(result))
'''
    result = subprocess.run([sys.executable, '-I', '-c', boot,
        str(Path(__file__).resolve().parents[1]), str(workspace)],
        input=json.dumps({'uri': uri}), text=True, capture_output=True, timeout=20,
        env={**os.environ, 'PANTHEON_FLEET_NODE_ID': 'wrong-ambient-node'})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
