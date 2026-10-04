"""MCP sampling reuses scoped Model Services and its original real Connector.

The Hub/grant authority and model output are local fixtures, not a paid call or
live enrolled Fleet. MCP, TLS, Connector HTTP/SSE and client cancellation are real.
"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from fastmcp import Client, Context, FastMCP
from mcp.types import CreateMessageRequestParams, SamplingMessage, TextContent
import pytest

from pantheon.apps.builtin.mcp.sampling import ModelSampling
from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.dependency import DependencyModelServices
from pantheon.apps.dependency_client import DependencyClient
from test_scoped_mcp_app import config, contract
from test_model_dependency import model_dependency, model_endpoint, tls_material


SPEC = {'credential': 'models', 'model': 'fleet-model://mac/example%3A8b',
        'max_tokens': 100, 'max_requests_per_call': 2}


def request(**kw):
    value = dict(messages=[SamplingMessage(role='user', content=TextContent(type='text', text='question'))], maxTokens=500)
    value.update(kw)
    return CreateMessageRequestParams(**value)


class ModelFixture:
    def __init__(self):
        self.calls, self.closed, self.cancelled = [], False, False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.hold = self.fail = False
    async def complete(self, ref, **kwargs):
        self.calls.append((ref, kwargs))
        self.entered.set()
        try:
            if self.hold:
                await self.release.wait()
            if self.fail:
                raise OSError('private-key-upstream')
            return {'content': 'sample', 'model': 'selected-engine', 'finish_reason': 'length'}
        except asyncio.CancelledError:
            self.cancelled = True
            raise
    async def aclose(self):
        self.closed = True


def sampler(client, spec=None):
    return ModelSampling(spec or SPEC, {'models': RuntimeCredential('https://127.0.0.1/rpc', 'a' * 64)},
                         client_factory=lambda _: client)


@pytest.mark.asyncio
@pytest.mark.parametrize('ref', ['fleet-model://mac/example%3A8b', 'fleet-route://local'])
async def test_mcp_sampling_reaches_original_connector_and_honors_revocation(
        tmp_path, tls_material, model_dependency, model_endpoint, monkeypatch, ref):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-must-not-be-used')
    server = FastMCP('Sampling')
    @server.tool
    async def ask(question: str, ctx: Context) -> dict:
        sample = await ctx.sample(question, system_prompt='Follow the given request',
                                  max_tokens=1000, temperature=.2, model_preferences='unapproved-model')
        return {'answer': sample.text}
    cfg = config()
    cfg.values['mcp']['sampling'] = {**SPEC, 'model': ref}
    cfg.credentials['models'] = RuntimeCredential(**model_dependency.credential)
    def model_client(credential):
        return DependencyModelServices(DependencyClient(credential, tls_context=tls_material.tls), direct_executable='')
    host = ScopedMCP(await contract(server, ('ask',)), cfg,
                     client_factory=lambda _: Client(server), model_client_factory=model_client)
    await host.start()
    try:
        result = await host.call('ask', {'question': 'Only through Model Service'})
        assert result['isError'] is False, result
        assert result['structuredContent']['answer'] == 'scoped reply'
        calls = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
        assert len(calls) == 1
        body = calls[0][2]
        assert body['model'] == 'example:8b' and body['max_tokens'] == 100
        assert body['messages'] == [{'role': 'system', 'content': 'Follow the given request'},
                                   {'role': 'user', 'content': 'Only through Model Service'}]
        assert body['temperature'] == .2
        assert 'ambient-must-not-be-used' not in json.dumps(calls)
        assert 'Only through Model Service' not in json.dumps(model_dependency.controls)
        model_dependency.revoked = True
        result = await host.call('ask', {'question': 'Should be denied'})
        assert result['isError'] is True
        assert len([r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']) == 1
    finally:
        await host.close()
    assert host._sampling._closed


@pytest.mark.asyncio
async def test_sampling_is_scoped_to_running_tool_and_bounded_without_fallback():
    fixture = ModelFixture()
    service = sampler(fixture)
    params = request(systemPrompt='system', stopSequences=['END'], temperature=.3)
    with pytest.raises(ValueError, match='active tool'):
        await service.sample('docs', params.messages, params)
    with service.admit('docs'):
        with pytest.raises(ValueError, match='active tool'):
            await service.sample('other', params.messages, params)
        for _ in range(2):
            result = await service.sample('docs', params.messages, params)
            assert result.stopReason == 'maxTokens' and result.model == 'selected-engine'
        with pytest.raises(ValueError, match='remaining budget'):
            await service.sample('docs', params.messages, params)
    assert len(fixture.calls) == 2
    assert fixture.calls[0] == (SPEC['model'], {'messages': [
        {'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'question'}],
        'model_params': {'max_tokens': 100, 'temperature': .3, 'stop': ['END']}})
    await service.close()
    assert fixture.closed


@pytest.mark.asyncio
async def test_tool_callback_cannot_exceed_per_call_budget():
    fixture = ModelFixture()
    server = FastMCP('Budget')
    @server.tool
    async def ask(ctx: Context) -> str:
        for _ in range(3):
            await ctx.sample('sample', max_tokens=10)
        return 'must not get here'
    cfg = config()
    cfg.values['mcp']['sampling'] = SPEC
    cfg.credentials['models'] = RuntimeCredential('https://127.0.0.1/rpc', 'a' * 64)
    host = ScopedMCP(await contract(server, ('ask',)), cfg, client_factory=lambda _: Client(server),
                     model_client_factory=lambda _: fixture)
    await host.start()
    try:
        result = await host.call('ask', {})
        assert result['isError'] is True
        assert len(fixture.calls) == 2
    finally:
        await host.close()
    assert fixture.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['context', 'tools', 'audio', 'temperature', 'size', 'no_messages'])
async def test_unsupported_sampling_does_not_invoke_a_model(change):
    fixture = ModelFixture()
    service = sampler(fixture)
    params = request()
    if change == 'context':
        params.includeContext = 'allServers'
    elif change == 'tools':
        params.tools = [object()]
    elif change == 'audio':
        from mcp.types import AudioContent
        params.messages[0].content = AudioContent(type='audio', data='AA==', mimeType='audio/wav')
    elif change == 'temperature':
        params.temperature = float('nan')
    elif change == 'size':
        params.messages[0].content.text = 'x' * (512 * 1024 + 1)
    else:
        params.messages = []
    with service.admit('docs'), pytest.raises(ValueError, match='unsupported'):
        await service.sample('docs', params.messages, params)
    assert not fixture.calls
    await service.close()


@pytest.mark.asyncio
async def test_images_and_history_preserved_for_model_service_capability_checks():
    from mcp.types import ImageContent
    fixture = ModelFixture()
    service = sampler(fixture)
    params = request(messages=[SamplingMessage(role='user', content=TextContent(type='text', text='previous')),
        SamplingMessage(role='assistant', content=TextContent(type='text', text='response')),
        SamplingMessage(role='user', content=ImageContent(type='image', mimeType='image/png', data='AA=='))])
    with service.admit('docs'):
        await service.sample('docs', params.messages, params)
    assert fixture.calls[0][1]['messages'] == [
        {'role': 'user', 'content': 'previous'}, {'role': 'assistant', 'content': 'response'},
        {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]}]
    await service.close()


@pytest.mark.asyncio
async def test_error_after_model_submission_is_sanitized_and_not_retried():
    fixture = ModelFixture()
    fixture.fail = True
    service = sampler(fixture)
    params = request()
    with service.admit('docs'), pytest.raises(RuntimeError, match='not retried') as error:
        await service.sample('docs', params.messages, params)
    assert len(fixture.calls) == 1 and 'private-key' not in str(error.value)
    await service.close()


@pytest.mark.asyncio
async def test_sampling_cancellation_reaches_model_service_and_cleanup_drains():
    fixture = ModelFixture()
    fixture.hold = True
    service = sampler(fixture)
    params = request()
    with service.admit('docs'):
        task = asyncio.create_task(service.sample('docs', params.messages, params))
        await fixture.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert fixture.cancelled and not service._pending
    await service.close()
    assert fixture.closed


@pytest.mark.parametrize('change', ['native_model', 'unknown_slot', 'tokens', 'requests', 'extra'])
def test_invalid_model_binding_does_not_construct_clients(change):
    spec = deepcopy(SPEC)
    if change == 'native_model': spec['model'] = 'openai/unapproved'
    elif change == 'unknown_slot': spec['credential'] = 'owner'
    elif change == 'tokens': spec['max_tokens'] = True
    elif change == 'requests': spec['max_requests_per_call'] = 0
    else: spec['api_key'] = 'private-key'
    with pytest.raises(ValueError, match='sampling binding') as error:
        sampler(ModelFixture(), spec)
    assert 'private-key' not in str(error.value)


@pytest.mark.asyncio
async def test_built_mcp_sampling_runs_without_agent_or_provider_sdks(
        tmp_path, model_dependency, model_endpoint, monkeypatch):
    import os
    from pathlib import Path
    import sys
    from pantheon.platform.mcp_package import build_package
    server = FastMCP('Contract')
    @server.tool
    async def ask(ctx: Context) -> dict:
        return {'answer': 'schema only'}
    package = build_package(tmp_path / 'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64',
                            exports=await contract(server, ('ask',)), credential_slots=['models'])
    script = tmp_path / 'stdio_server.py'
    script.write_text('''from fastmcp import FastMCP, Context
server = FastMCP("Sampling subprocess")
@server.tool
async def ask(ctx: Context) -> dict:
    result = await ctx.sample("Packaged sampling", max_tokens=200)
    return {"answer": result.text}
server.run(transport="stdio", show_banner=False)
''')
    cfg = config({'docs': {'transport': 'stdio', 'command': [sys.executable, str(script)],
                           'cwd': str(tmp_path), 'env': {}}})
    cfg.values['mcp']['sampling'] = SPEC
    cfg.credentials['models'] = RuntimeCredential(**model_dependency.credential)
    model_dependency.control.policies['agent']['consumer']['instance_id'] = cfg.instance_id
    resolved = dict(protocol=1, values=cfg.values, credentials={
        'models': model_dependency.credential}, **{key: getattr(cfg, key) for key in (
            'owner', 'node_id', 'instance_id', 'revision', 'generation', 'component')})
    prepared = tmp_path / 'prepared.json'
    prepared.write_text(json.dumps(resolved))
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))
           and not key.upper().endswith('_PROXY')}
    env.update(PANTHEON_APP_CONFIG=str(prepared), PANTHEON_FLEET_ID=cfg.owner,
        PANTHEON_NODE_ID=cfg.node_id, PANTHEON_INSTANCE_ID=cfg.instance_id,
        PANTHEON_APP_REVISION=cfg.revision, PANTHEON_INSTANCE_GENERATION='1',
        PANTHEON_COMPONENT_NAME='backend', SSL_CERT_FILE=str(tmp_path / 'cert.pem'))
    boot = '''import asyncio, importlib.abc, json, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.agent', 'pantheon.chatroom', 'pantheon.factory', 'pantheon.settings',
                       'pantheon.toolset', 'pantheon.utils.llm', 'openai', 'anthropic', 'litellm')):
   raise AssertionError('Non-standalone model dependency: ' + name)
sys.meta_path.insert(0, Guard())
sys.path.insert(0, sys.argv[1])
from app_runtime import AppContext, _load_backend
package = Path(sys.argv[2])
module = _load_backend(package)
async def main():
 ctx = AppContext('mcp-gateway', Path.cwd(), Path.cwd() / 'state', None)
 try:
  await module.register(ctx)
  assert ctx.require_rpc_token and set(ctx._methods) == {'ask'}
  result = await ctx._methods['ask']()
  assert result['isError'] is False, result
  print(json.dumps(result))
 finally:
  if ctx._cleanup:
   await ctx._cleanup()
asyncio.run(main())
'''
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', boot,
        str(package / '.fleet-runtime'), str(package), cwd=tmp_path, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 30)
        assert proc.returncode == 0, stderr.decode()[-8000:]
        assert json.loads(stdout)['structuredContent']['answer'] == 'scoped reply'
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    calls = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
    assert len(calls) == 1 and calls[0][2]['max_tokens'] == 100
    assert model_dependency.grants[0]['consumer']['instance_id'] == cfg.instance_id
    assert 'Packaged sampling' not in json.dumps(model_dependency.controls)
