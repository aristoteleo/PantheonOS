"""Ordinary ToolSet sampling uses explicit dependencies, never Agent imports."""
import asyncio
import json
import os
import sys
from types import MappingProxyType

from PIL import Image
import pytest

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.file.build_managed import build
from pantheon.apps.model_sampling import ToolModelSampling
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.apps.toolset_backend import register_toolset
from pantheon.apps.dependency_client import DependencyClient
from pantheon.models.dependency import DependencyModelServices
from pantheon.toolset import ToolSet, tool
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_scoped_mcp_sampling import ModelFixture, SPEC


class SamplingTool(ToolSet):
    def __init__(self):
        super().__init__('sampling')
        self.saved = None

    @tool
    async def ask(self, text: str):
        self.saved = self.get_context()
        return await self.saved.call_agent(messages=[{'role': 'user', 'content': text}], use_memory=True)


def sampling(client, **changes):
    return ToolModelSampling(MappingProxyType({**SPEC, **changes}),
        {'models': RuntimeCredential('https://127.0.0.1/rpc', 'a'*64)}, client_factory=lambda _: client)


@pytest.mark.parametrize('pem', [None, '', False, 'not a certificate', 'a' * 16385])
def test_invalid_prepared_trust_never_constructs_or_falls_back(pem):
    def forbidden(_):
        pytest.fail('Invalid trust reached the model client')
    with pytest.raises(ValueError):
        ToolModelSampling({**SPEC, 'trust_roots_pem': pem},
            {'models': RuntimeCredential('https://127.0.0.1/rpc', 'a'*64)}, client_factory=forbidden)


@pytest.mark.asyncio
async def test_concurrent_tools_have_independent_budgets_and_expire_callbacks(tmp_path):
    client = ModelFixture()
    client.hold = True
    sampler = sampling(client, max_requests_per_call=1)
    service = SamplingTool()
    ctx = AppContext('sampling', tmp_path, tmp_path, None)
    await register_toolset(ctx, service, sampling=sampler)
    first = asyncio.create_task(ctx._methods['ask'](text='first'))
    second = asyncio.create_task(ctx._methods['ask'](text='second'))
    try:
        for _ in range(100):
            if len(client.calls) == 2:
                break
            await asyncio.sleep(.01)
        assert len(client.calls) == 2
        with pytest.raises(RuntimeError, match='draining'):
            await ctx.before_stop()
        assert not client.closed
        client.release.set()
        results = await asyncio.gather(first, second)
        assert all(r['success'] for r in results)
        assert results[0]['_metadata']['sampling'] == {
            'execution': 'model_services', 'memory_requested': True, 'memory_used': False}
        assert not (await service.saved.call_agent(messages=[{'role': 'user', 'content': 'late'}]))['success']
        await ctx.before_stop()
        assert client.closed
    finally:
        client.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await ctx._cleanup()


@pytest.mark.asyncio
async def test_unbound_host_never_imports_agent_or_uses_ambient_callback(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in ('pantheon.agent', 'agent'):
            raise AssertionError('Imported Agent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    ctx = AppContext('sampling', tmp_path, tmp_path, None)
    await register_toolset(ctx, SamplingTool())
    try:
        assert (await ctx._methods['ask'](text='unbound')) == {
            'success': False, 'error': 'This App has no Model Services sampling dependency'}
        with pytest.raises(ValueError):
            await ctx._methods['ask'](text='spoof', context_variables={'_call_agent': lambda: None})
    finally:
        await ctx._cleanup()


@pytest.mark.asyncio
async def test_budget_preference_validation_failure_and_cancellation():
    client = ModelFixture()
    sampler = sampling(client, max_requests_per_call=1)
    args = {'messages': [{'role': 'user', 'content': 'sample'}]}
    try:
        with sampler.context() as context:
            call = context['_call_agent']
            assert not (await call(**args, model='openai/ambient'))['success']
            assert not (await call(messages=[{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://private/image'}}]}]))['success']
            client.fail = True
            result = await call(**args)
            assert not result['success'] and 'private-key' not in str(result)
            assert not (await call(**args))['success']
            assert len(client.calls) == 1
        client.fail, client.hold = False, True
        client.entered.clear()
        with sampler.context() as context:
            task = asyncio.create_task(context['_call_agent'](**args))
            await client.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert client.cancelled and not sampler._pending
    finally:
        await sampler.close()


@pytest.mark.asyncio
async def test_callbacks_expire_with_unused_budget_and_cleanup_survives_cancellation():
    client = ModelFixture()
    sampler = sampling(client)
    args = {'messages': [{'role': 'user', 'content': 'sample'}]}
    with sampler.context() as context:
        callback = context['_call_agent']
        assert (await callback(**args))['success']
    assert not (await callback(**args))['success']
    client.hold = True
    client.entered.clear()
    with sampler.context() as context:
        call = asyncio.create_task(context['_call_agent'](**args))
        await client.entered.wait()
        cleanup = asyncio.create_task(sampler.close())
        await asyncio.sleep(0)
        cleanup.cancel()
        await asyncio.sleep(0)
        assert not cleanup.done() and not client.closed
        client.release.set()
        assert (await call)['success']
        with pytest.raises(asyncio.CancelledError):
            await cleanup
    assert client.closed
    await sampler.close()


@pytest.mark.asyncio
async def test_setup_failure_also_closes_owned_model_client(tmp_path):
    from unittest.mock import AsyncMock
    client = ModelFixture()
    sampler = sampling(client)
    service = SamplingTool()
    service.run_setup = AsyncMock(side_effect=RuntimeError('setup failed'))
    ctx = AppContext('sampling', tmp_path, tmp_path, None)
    with pytest.raises(RuntimeError, match='setup failed'):
        await register_toolset(ctx, service, sampling=sampler)
    assert client.closed and not ctx._methods
    await ctx._cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('ref', ['fleet-model://mac/example%3A8b', 'fleet-route://local'])
async def test_tool_sampling_uses_original_connector_and_denies_revoked_grant(
        tmp_path, model_dependency, model_endpoint, tls_material, monkeypatch, ref):
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-forbidden')
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    sampler = ToolModelSampling({**SPEC, 'model': ref, 'trust_roots_pem': (tmp_path/'cert.pem').read_text()},
                               {'models': RuntimeCredential(**model_dependency.credential)})
    ctx = AppContext('sampling', tmp_path, tmp_path, None)
    await register_toolset(ctx, SamplingTool(), sampling=sampler)
    try:
        result = await ctx._methods['ask'](text='only dependency')
        assert result['success'] and result['response'] == 'scoped reply', result
        assert result['_metadata']['model_service']['deployment_id'] == 'mac'
        calls = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
        assert len(calls) == 1 and calls[0][2]['messages'] == [{'role': 'user', 'content': 'only dependency'}]
        assert 'ambient-forbidden' not in json.dumps(calls)
        assert 'only dependency' not in json.dumps(model_dependency.controls)
        model_dependency.revoked = True
        assert not (await ctx._methods['ask'](text='denied'))['success']
        assert len([r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']) == 1
    finally:
        await ctx._cleanup()


@pytest.mark.asyncio
async def test_built_files_observation_runs_without_agent_and_uses_prepared_dependency(
        tmp_path, model_dependency, model_endpoint):
    package = build(tmp_path/'files', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64', model_sampling=True)
    build_artifact(package)
    manifest = json.loads((package/'app.json').read_text())
    assert manifest['dependencies']['model-services-control']['uses'] == ['model-inference@1']
    assert not list(package.rglob('agent.py')) and not list(package.rglob('settings.py'))
    workspace = tmp_path/'workspace'; workspace.mkdir()
    Image.new('RGB', (64, 32), 'red').save(workspace/'image.png')
    identity = dict(owner='owner', node_id='node', instance_id='files', revision='a'*64, generation=1, component='backend')
    model_dependency.control.policies['agent']['consumer']['instance_id'] = 'files'
    config = {'protocol': 1, **identity, 'values': {'files': {'workspace': str(workspace)},
              'sampling': {**SPEC, 'trust_roots_pem': (tmp_path/'cert.pem').read_text()}},
              'credentials': {'models': model_dependency.credential}}
    prepared = tmp_path/'prepared.json'; prepared.write_text(json.dumps(config))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))
           and not k.upper().endswith('_PROXY')}
    env.update(PANTHEON_APP_CONFIG=str(prepared), PANTHEON_FLEET_ID='owner', PANTHEON_NODE_ID='node',
        PANTHEON_INSTANCE_ID='files', PANTHEON_APP_REVISION='a'*64, PANTHEON_INSTANCE_GENERATION='1',
        PANTHEON_COMPONENT_NAME='backend')
    env.pop('SSL_CERT_FILE', None)
    boot = '''import asyncio, importlib.abc, json, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.agent','pantheon.chatroom','pantheon.settings','pantheon.factory','openai','anthropic','litellm')):
   raise AssertionError('Agent/provider implementation imported: '+name)
sys.meta_path.insert(0, Guard())
sys.path.insert(0,sys.argv[1])
from app_runtime import AppContext, _load_backend
async def main():
 module = _load_backend(Path(sys.argv[2]))
 ctx = AppContext('file-manager', Path.cwd(), Path.cwd()/'state', None)
 try:
  await module.register(ctx)
  assert ctx.require_rpc_token
  result = await ctx._methods['observe_images'](question='What color?', image_paths=['image.png'])
  assert result['success'] and result['content'] == 'scoped reply', result
  assert not (await ctx._methods['observe_images'](question='escape', image_paths=['../prepared.json']))['success']
  print(json.dumps(result))
 finally:
  if ctx._cleanup:
   await ctx._cleanup()
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', boot,
        str(package/'.fleet-runtime'), str(package), cwd=tmp_path, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, stderr.decode()[-6000:]
        assert json.loads(stdout)['image_count'] == 1
    finally:
        if process.returncode is None:
            process.kill(); await process.wait()
    calls = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
    assert len(calls) == 1
    assert calls[0][2]['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/')
