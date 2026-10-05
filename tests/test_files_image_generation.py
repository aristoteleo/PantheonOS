"""Prepared Files image calls reuse the original scoped Model Services stack."""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading

import pytest
from PIL import Image

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.file.build_managed import build
from pantheon.apps.builtin.file.managed import create_service
from pantheon.apps.builtin.file.image_generation import ImageGeneration
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.apps.toolset_backend import register_toolset
from pantheon.models.client import model_ref
from pantheon.models.jobs import InferenceSession
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_model_api_images import upstream
from test_model_image_jobs import png
from test_model_services import serve
from test_model_inference_jobs import wait_done

REF = model_ref('mac', 'chosen-image-model')
SPEC = {'credential': 'models', 'model': REF, 'aliases': {'openai': REF}, 'timeout_seconds': 10}


def configure(dependency, endpoint):
    c = dependency.connector
    c.configure({'engine': 'api', 'endpoint': endpoint})
    dependency.deployment.update(engine='api', config_revision=c.revision,
        models=[{'id': 'chosen-image-model', 'operations': ['image']}])
    dependency.control.policies['agent']['consumer']['instance_id'] = 'files'


def generator(tmp_path, dependency, monkeypatch):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    workspace = tmp_path / 'workspace'; workspace.mkdir(exist_ok=True)
    (workspace / 'source.png').write_bytes(png())
    instance = ImageGeneration(SPEC, {'models': RuntimeCredential(**dependency.credential)}, workspace, state_dir=tmp_path / 'state')
    return instance, workspace


@pytest.mark.asyncio
async def test_real_scoped_generation_editing_local_copy_and_revocation(tmp_path, model_dependency, monkeypatch):
    calls = []
    with serve(upstream(calls)) as endpoint:
        configure(model_dependency, endpoint)
        images, workspace = generator(tmp_path, model_dependency, monkeypatch)
        try:
            result = await images.generate('Edit this image', ['source.png', 'source.png'], model='openai')
            assert result['success'], result
            path = Path(result['images'][0])
            assert path.is_relative_to(workspace) and path.read_bytes() == png()
            assert result['usage']['total_tokens'] == 34
            assert not model_dependency.connector.inference_jobs().list()
            assert not list(model_dependency.connector.media_store().db.execute('SELECT * FROM media'))
            assert len(calls) == 1 and calls[0][0] == '/v1/images/edits'
            assert [v for k, v in calls[0][1] if k == 'image[]'] == [png(), png()]
            assert 'Edit this image' not in json.dumps(model_dependency.controls)
            assert all(g['consumer']['instance_id'] == 'files' for g in model_dependency.grants)
            model_dependency.revoked = True
            assert not (await images.generate('denied'))['success']
            assert len(calls) == 1 and path.exists()
            assert not list(images.records.glob('*.json'))
        finally: await images.close()


@pytest.mark.asyncio
async def test_invalid_references_and_unbound_selectors_do_not_call_model(tmp_path, model_dependency, monkeypatch):
    calls = []
    with serve(upstream(calls)) as endpoint:
        configure(model_dependency, endpoint)
        images, workspace = generator(tmp_path, model_dependency, monkeypatch)
        (tmp_path / 'outside.png').write_bytes(png())
        (workspace / 'escape.png').symlink_to(tmp_path / 'outside.png')
        (workspace / 'bad.png').write_text('not an image')
        try:
            for refs in [['../outside.png'], ['escape.png'], ['bad.png'], [], ['https://example.com/a.png']]:
                assert not (await images.generate('bad references', refs))['success']
            for model in ['gemini', model_ref('other', 'unselected'), ['not a selector']]:
                assert not (await images.generate('unbound', model=model))['success']
            assert not calls and not model_dependency.controls
        finally: await images.close()


@pytest.mark.asyncio
async def test_unusable_output_directory_rejects_before_inference(tmp_path, model_dependency, monkeypatch):
    calls = []
    with serve(upstream(calls)) as endpoint:
        configure(model_dependency, endpoint)
        images, workspace = generator(tmp_path, model_dependency, monkeypatch)
        (workspace / 'generated-images').symlink_to(tmp_path, target_is_directory=True)
        try:
            assert not (await images.generate('must not be submitted'))['success']
            assert not calls and not model_dependency.controls
        finally: await images.close()


@pytest.mark.asyncio
async def test_lost_submission_ack_retains_identity_across_files_restart(tmp_path, model_dependency, monkeypatch):
    calls = []
    original = InferenceSession.submit
    async def lose_ack(session, *args, **kwargs):
        await original(session, *args, **kwargs)
        # The upstream actually receives the request before the client loses its
        # acknowledgement. A new request identity could charge the user twice.
        await asyncio.to_thread(wait_done, model_dependency.connector, kwargs['request_id'])
        raise ConnectionError('injected acknowledgement loss')
    monkeypatch.setattr(InferenceSession, 'submit', lose_ack)
    with serve(upstream(calls)) as endpoint:
        configure(model_dependency, endpoint)
        images, workspace = generator(tmp_path, model_dependency, monkeypatch)
        try:
            result = await images.generate('private prompt', ['source.png'])
            assert not result['success'] and result['job_ref']
            [record] = images.records.glob('*.json')
            before = record.read_bytes()
            retained = json.loads(before)
            assert retained['job_ref'] == result['job_ref']
            assert retained['state'] == 'needs_attention'
            assert b'private prompt' not in before
            assert not list((workspace / 'generated-images').glob('files-*'))
            assert len(calls) == 1
        finally: await images.close()
        reopened, _ = generator(tmp_path, model_dependency, monkeypatch)
        try:
            assert record.read_bytes() == before
            assert len(calls) == 1 and not reopened._pending
        finally: await reopened.close()


@pytest.mark.asyncio
async def test_remote_cleanup_failure_keeps_verified_local_result_and_receipt(tmp_path, model_dependency, monkeypatch):
    calls = []
    async def fail_remove(*args): raise ConnectionError('injected cleanup failure')
    monkeypatch.setattr(InferenceSession, 'remove', fail_remove)
    with serve(upstream(calls)) as endpoint:
        configure(model_dependency, endpoint)
        images, _ = generator(tmp_path, model_dependency, monkeypatch)
        try:
            result = await images.generate('keep my output')
            assert result['success'] and result['cleanup_pending']
            assert Path(result['images'][0]).read_bytes() == png()
            [record] = images.records.glob('*.json')
            retained = json.loads(record.read_text())
            assert retained['state'] == 'copied' and retained['images'] == result['images']
            assert retained['job_ref'] == result['job_ref'] and len(calls) == 1
        finally: await images.close()


@pytest.mark.asyncio
async def test_portable_files_stop_waits_for_generation_and_closes_admission(tmp_path, model_dependency, monkeypatch):
    calls, entered, release = [], threading.Event(), threading.Event()
    with serve(upstream(calls, entered=entered, release=release)) as endpoint:
        configure(model_dependency, endpoint)
        images, workspace = generator(tmp_path, model_dependency, monkeypatch)
        service = create_service({'workspace': str(workspace)}, image_generation=images)
        ctx = AppContext('file-manager', workspace, tmp_path / 'state', None)
        await register_toolset(ctx, service)
        task = asyncio.create_task(ctx._methods['generate_image'](prompt='test'))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            with pytest.raises(RuntimeError, match='draining'): await ctx.before_stop()
            with pytest.raises(RuntimeError, match='stopping'): await ctx._methods['generate_image'](prompt='late')
            release.set()
            assert (await task)['success']
            await ctx.before_stop()
            assert images._closed and not images._pending
            assert len(calls) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await ctx._cleanup()


@pytest.mark.asyncio
async def test_cancel_requests_remote_cancellation_without_replay(tmp_path, model_dependency, monkeypatch):
    calls, entered, release = [], threading.Event(), threading.Event()
    with serve(upstream(calls, entered=entered, release=release)) as endpoint:
        configure(model_dependency, endpoint)
        images, workspace = generator(tmp_path, model_dependency, monkeypatch)
        task = asyncio.create_task(images.generate('cancel me', ['source.png']))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            release.set()
            jobs = model_dependency.connector.inference_jobs()
            [record] = jobs.list()
            assert await asyncio.to_thread(wait_done, model_dependency.connector, record['job_id'])
            assert jobs.status(record['job_id'])['state'] == 'cancelled'
            [retained] = list(images.records.glob('*.json'))
            assert json.loads(retained.read_text())['job_ref'].endswith('/' + record['job_id'])
            assert 'cancel me' not in retained.read_text()
            assert not list(model_dependency.connector.media_store().db.execute('SELECT * FROM media'))
            assert len(calls) == 1 and not images._pending
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await images.close()


@pytest.mark.asyncio
async def test_built_files_generation_has_no_agent_or_provider_sdk(tmp_path, model_dependency):
    package = build(tmp_path / 'files', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64', image_generation=True)
    build_artifact(package)
    manifest = json.loads((package / 'app.json').read_text())
    tools = {tool['name'] for tool in manifest['provides']['tools']}
    assert 'generate_image' in tools and 'observe_images' not in tools
    generated = next(t for t in manifest['provides']['tools'] if t['name'] == 'generate_image')
    prompt = next(p for p in generated['params'] if p['name'] == 'prompt')
    assert prompt['required'] and 'default' not in prompt
    assert any(i['name'] == 'image-generation' for i in manifest['provides']['interfaces'])
    assert not list(package.rglob('settings.py')) and not list(package.rglob('agent.py'))
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    (workspace / 'source.png').write_bytes(png())
    calls = []
    with serve(upstream(calls)) as endpoint:
        configure(model_dependency, endpoint)
        identity = dict(owner='owner', node_id='node', instance_id='files', revision='a'*64, generation=1, component='backend')
        config = {'protocol': 1, **identity, 'values': {'files': {'workspace': str(workspace)}, 'image_generation': SPEC},
                  'credentials': {'models': model_dependency.credential}}
        prepared = tmp_path / 'prepared.json'; prepared.write_text(json.dumps(config))
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))
               and not k.upper().endswith('_PROXY')}
        env.update(PANTHEON_APP_CONFIG=str(prepared), PANTHEON_FLEET_ID='owner', PANTHEON_NODE_ID='node',
            PANTHEON_INSTANCE_ID='files', PANTHEON_APP_REVISION='a'*64, PANTHEON_INSTANCE_GENERATION='1',
            PANTHEON_COMPONENT_NAME='backend', SSL_CERT_FILE=str(tmp_path / 'cert.pem'))
        boot = '''import asyncio, importlib.abc, json, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name.startswith(('pantheon.agent','pantheon.chatroom','pantheon.settings','pantheon.factory','openai','anthropic','litellm')):
   raise AssertionError('Forbidden implementation imported: '+name)
sys.meta_path.insert(0,Guard())
sys.path.insert(0,sys.argv[1])
from app_runtime import AppContext, _load_backend
async def main():
 ctx=AppContext('file-manager',Path.cwd(),Path.cwd()/'state',None)
 try:
  await _load_backend(Path(sys.argv[2])).register(ctx)
  assert ctx.require_rpc_token
  result=await ctx._methods['generate_image'](prompt='Draw an illustration',reference_images=['source.png'])
  assert result['success'] and result['base64_uri'][0].startswith('data:image/'), result
  assert (await ctx._methods['fetch_image_base64'](image_path=result['images'][0]))['success']
  print(json.dumps(result))
 finally:
  if ctx._cleanup: await ctx._cleanup()
asyncio.run(main())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', boot,
            str(package / '.fleet-runtime'), str(package), cwd=tmp_path, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
            assert process.returncode == 0, stderr.decode()[-6000:]
            assert Path(json.loads(stdout)['images'][0]).read_bytes() == png()
            assert len(calls) == 1
        finally:
            if process.returncode is None:
                process.kill(); await process.wait()
