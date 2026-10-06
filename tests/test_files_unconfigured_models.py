"""Deferred model choices keep the complete Files surface, without inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.file import managed
from pantheon.apps.builtin.file.build_managed import build
from pantheon.apps.lifecycle import build_artifact


def test_packaged_files_without_model_credentials_remains_complete(tmp_path):
    package = build(tmp_path/'files', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64',
                    model_sampling=True, image_generation=True)
    build_artifact(package)
    manifest = json.loads((package/'app.json').read_text())
    methods = {t['name'] for t in manifest['provides']['tools']}
    assert {'observe_images', 'generate_image'} <= methods
    workspace = tmp_path/'workspace'; workspace.mkdir()
    identity = dict(owner='owner', node_id='node', instance_id='files', revision='a'*64, generation=1, component='backend')
    prepared = tmp_path/'prepared.json'
    prepared.write_text(json.dumps({'protocol': 1, **identity, 'values': {
        'files': {'workspace': str(workspace)}, 'sampling': {'state': 'unconfigured'},
        'image_generation': {'state': 'unconfigured'}}, 'credentials': {}}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))}
    env.update(PANTHEON_APP_CONFIG=str(prepared), PANTHEON_FLEET_ID='owner', PANTHEON_NODE_ID='node',
        PANTHEON_INSTANCE_ID='files', PANTHEON_APP_REVISION='a'*64, PANTHEON_INSTANCE_GENERATION='1',
        PANTHEON_COMPONENT_NAME='backend')
    code = '''import asyncio, importlib.abc, json, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.agent','pantheon.chatroom','pantheon.settings','pantheon.factory','openai','anthropic','litellm')):
   raise AssertionError('Ambient implementation: '+name)
sys.meta_path.insert(0, Guard())
sys.path.insert(0,sys.argv[1])
from app_runtime import AppContext, _load_backend
async def main():
 ctx=AppContext('file-manager',Path.cwd(),Path.cwd()/'state',None)
 try:
  backend=_load_backend(Path(sys.argv[2]))
  from pantheon.apps import model_sampling
  def forbidden(*args, **kwargs): raise AssertionError('Unconfigured model client constructed')
  model_sampling.model_client=forbidden
  await backend.register(ctx)
  assert set(ctx._methods)==set(json.loads(sys.argv[3]))
  async def unread(*args, **kwargs): raise AssertionError('Unconfigured capability read image inputs')
  # RPC closures bind service methods; replace its preview before invoking.
  from pantheon.apps.builtin.file.managed import ManagedFiles
  ManagedFiles.fetch_image_base64=unread
  observed=await ctx._methods['observe_images'](question='What?',image_paths=['missing.png'])
  generated=await ctx._methods['generate_image'](prompt='Draw',model='ambient-provider',reference_images=['missing.png'])
  for capability,result in [('sampling',observed),('image_generation',generated)]:
   assert result['code']=='model_not_configured' and result['success'] is False, result
   assert result['capability']==capability and 'files.'+capability in result['error']
  assert (await ctx._methods['write_file'](file_path='kept.txt',content='FILES_OK'))['success']
  assert 'FILES_OK' in (await ctx._methods['read_file'](file_path='kept.txt'))['content']
  await ctx.before_stop()
  assert not (Path.cwd()/'state/image-jobs').exists()
  print('complete surface; deferred models; real file operations; stopped')
 finally:
  if ctx._cleanup: await ctx._cleanup()
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(package/'.fleet-runtime'), str(package),
                             json.dumps(sorted(methods))], cwd=workspace, env=env, text=True,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (workspace/'kept.txt').read_text() == 'FILES_OK'


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['sampling', 'image_generation'])
@pytest.mark.parametrize('spec', [None, {}, {'state': 'disabled'}, {'state': 'unconfigured', 'model': 'unexpected'},
    'configured-without-credential'])
async def test_invalid_or_missing_credential_is_not_a_deferred_capability(tmp_path, monkeypatch, name, spec):
    values = {'files': {'workspace': str(tmp_path)}, 'sampling': {'state': 'unconfigured'},
              'image_generation': {'state': 'unconfigured'}}
    if spec == 'configured-without-credential':
        spec = {'model': 'fleet-model://node/model', 'credential': 'models', **(
            {'aliases': {}, 'timeout_seconds': 30} if name == 'image_generation'
            else {'max_tokens': 100, 'max_requests_per_call': 1})}
    values[name] = spec
    monkeypatch.setattr(managed, 'load_runtime_configuration', lambda **_: SimpleNamespace(values=values, credentials={}))
    ctx = AppContext('file-manager', tmp_path, tmp_path/'state', None)
    with pytest.raises(ValueError):
        await managed.register_capabilities(ctx, observation=True, generation=True)
    assert not ctx._methods
    if ctx._cleanup:
        await ctx._cleanup()
