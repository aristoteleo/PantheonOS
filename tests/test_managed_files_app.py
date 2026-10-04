"""Prepared Files distribution reuses filesystem code without importing Agent."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pantheon.apps.builtin.file.build_managed import build
from pantheon.apps.builtin.file.managed import METHODS, create_service
from pantheon.apps.lifecycle import build_artifact


def test_package_declares_exact_rpc_surface_and_loads_without_agent(tmp_path):
    package = build(tmp_path/'files', 'darwin-arm64')
    manifest = json.loads((package/'app.json').read_text())
    assert {t['name'] for t in manifest['provides']['tools']} == METHODS
    assert all(set(i['tools']) <= METHODS for i in manifest['provides']['interfaces'])
    assert not list(package.rglob('agent.py')) and not list(package.rglob('settings.py'))
    build_artifact(package)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    code = '''import asyncio, importlib.abc, json, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name in ('pantheon.agent','pantheon.settings','pantheon.factory','pantheon.remote','pantheon.chatroom'):
   raise AssertionError('Files imported Agent or ambient state: '+name)
sys.meta_path.insert(0, Boundary())
sys.path.insert(0,sys.argv[1])
from pantheon.apps.builtin.file.managed import create_service
async def run():
 service=create_service({'workspace':sys.argv[2], 'limits':{'max_file_read_chars':5}})
 assert (await service.write_file('sample.py', 'def sample():\\n    return 42\\n'))['success']
 assert (await service.read_file('sample.py'))['content']=='def s'
 assert (await service.update_file('sample.py','42','43'))['success']
 assert (await service.glob('*.py'))['success']
 assert (await service.grep('return',path='sample.py'))['success']
 assert (await service.view_file_outline('sample.py'))['success']
 assert not (await service.read_file('.pantheon/agents/missing.md'))['success']
 await service.cleanup()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable,'-I','-c',code,str(package/'backend/_vendor'),str(workspace)],
                            cwd=tmp_path,text=True,capture_output=True,timeout=30)
    assert result.returncode == 0, result.stderr
    assert '43' in (workspace/'sample.py').read_text()


@pytest.mark.parametrize('value', [None, {}, {'workspace':'relative'}, {'workspace':'/missing'},
    {'workspace':'$workspace','limits':[]},
    {'workspace':'$workspace','limits':{'max_file_read_chars':True}},
    {'workspace':'$workspace','limits':{'max_file_read_lines':0}},
    {'workspace':'$workspace','limits':{'unknown':1}}, {'workspace':'$workspace','credentials':{}}])
def test_prepared_configuration_fails_before_host_admission(tmp_path,value):
    if isinstance(value,dict) and value.get('workspace') == '$workspace':
        value = {**value, 'workspace':str(tmp_path)}
    with pytest.raises(ValueError):
        create_service(value)
