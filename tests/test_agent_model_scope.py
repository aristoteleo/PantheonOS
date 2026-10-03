"""Owned model routes exercised over real localhost HTTP/SSE, without paid APIs."""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from pantheon.settings import Settings
from pantheon.utils.llm import acompletion, acompletion_responses
from pantheon.utils.llm_providers import ProviderConfig, ProviderType, call_llm_provider, detect_provider
from pantheon.utils.model_scope import ModelCallScope


@pytest.fixture
def endpoint():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, dict(self.headers), body))
            if self.path.startswith('/unsupported/') and self.path.endswith('/responses'):
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"unsupported","type":"invalid_request_error"}}')
                return
            if '/anthropic/' in self.path:
                events = [
                    {'type': 'message_start', 'message': {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                     'model': body['model'], 'content': [], 'stop_reason': None, 'stop_sequence': None,
                     'usage': {'input_tokens': 3, 'output_tokens': 0}}},
                    {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                    {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'scoped reply'}},
                    {'type': 'content_block_stop', 'index': 0},
                    {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                     'usage': {'output_tokens': 2}},
                    {'type': 'message_stop'},
                ]
            elif self.path.endswith('/responses'):
                events = [
                    {'type': 'response.output_text.delta', 'delta': 'scoped reply'},
                    {'type': 'response.completed', 'response': {'id': 'resp_test', 'object': 'response',
                     'model': body['model'], 'status': 'completed', 'output': [],
                     'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}},
                ]
            else:
                events = [
                    {'id': 'chat_test', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'],
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'scoped reply'}, 'finish_reason': None}]},
                    {'id': 'chat_test', 'object': 'chat.completion.chunk', 'created': 0, 'model': body['model'],
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}},
                ]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for event in events:
                prefix = 'event: ' + event['type'] + '\n' if '/anthropic/' in self.path else ''
                self.wfile.write((prefix + 'data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', requests=requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Scoped inference read ambient configuration')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.setattr('pantheon.models.client.get_client', forbidden)
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-secret')
    monkeypatch.setenv('OPENAI_API_BASE', 'http://invalid.openrouter.test/v1')
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://invalid.test/v1')
    monkeypatch.setenv('OPENAI_ORG_ID', 'ambient-organization')
    monkeypatch.setenv('OPENAI_PROJECT_ID', 'ambient-project')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'ambient-anthropic-token')
    monkeypatch.setenv('LLM_FORCE_PROXY', 'true')
    monkeypatch.setenv('PLATFORM_MODEL_MODE', 'openrouter')
    counter = 0
    def make(environment=None, **kwargs):
        nonlocal counter
        counter += 1
        root = tmp_path / str(counter)
        settings = Settings(root, isolated_env=True, environment=environment or {}, user_home=root / 'user')
        return ModelCallScope(settings=settings, **kwargs)
    return make


@pytest.mark.asyncio
@pytest.mark.parametrize('responses', [False, True])
async def test_budget_and_byok_concurrent_wire_routing(endpoint, scopes, responses):
    budget = scopes({'LLM_FORCE_PROXY': 'true', 'PANTHEON_PLATFORM_PROXY_BASE': endpoint.url + '/budget/v1',
                     'PANTHEON_PLATFORM_PROXY_KEY': 'budget-fixture', 'OPENAI_API_KEY': 'unused-byok',
                     'OPENAI_API_BASE': endpoint.url + '/wrong/v1'})
    byok = scopes({'OPENAI_API_KEY': 'byok-fixture', 'OPENAI_API_BASE': endpoint.url + '/byok/v1'})
    call = acompletion_responses if responses else acompletion
    output = await asyncio.gather(*(call(model='gpt-4o-mini' if responses else 'openai/gpt-4o-mini',
        messages=[{'role': 'user', 'content': label}], scope=scope, num_retries=0)
        for label, scope in [('budget', budget), ('byok', byok)]))
    assert all((result['content'] if responses else result.choices[0].message.content) == 'scoped reply'
               for result in output)
    assert len(endpoint.requests) == 2
    for path, headers, body in endpoint.requests:
        owner = path.split('/')[1]
        assert owner in ('budget', 'byok')
        assert headers['Authorization'] == f'Bearer {owner}-fixture'
        assert headers.get('OpenAI-Organization', '') == ''
        assert headers.get('OpenAI-Project', '') == ''
        assert body['model'] == 'gpt-4o-mini'


@pytest.mark.asyncio
@pytest.mark.parametrize('responses', [False, True])
@pytest.mark.parametrize('environment', [{}, {'LLM_FORCE_PROXY': 'true', 'OPENAI_API_KEY': 'unusable-byok'}])
async def test_missing_scoped_credentials_never_borrow_environment(scopes, endpoint, responses, environment):
    call = acompletion_responses if responses else acompletion
    with pytest.raises(RuntimeError, match='credential|Platform budget'):
        await call(model='openai/gpt-4o-mini', messages=[], scope=scopes(environment))
    assert endpoint.requests == []


@pytest.mark.asyncio
async def test_response_probe_cache_is_owned_by_scope(endpoint, scopes):
    a, b = [scopes({'OPENAI_API_KEY': f'{n}-fixture', 'OPENAI_API_BASE': endpoint.url + '/unsupported/v1'})
            for n in ('a', 'b')]
    for scope in (a, a, b):
        config = detect_provider('openai/gpt-4o-mini', False, settings=scope.settings)
        result = await call_llm_provider(config, [{'role': 'user', 'content': 'hello'}], scope=scope)
        assert result['content'] == 'scoped reply'
    assert len([r for r in endpoint.requests if r[0].endswith('/responses')]) == 2
    assert len([r for r in endpoint.requests if r[0].endswith('/chat/completions')]) == 3
    assert a.responses_unavailable is not b.responses_unavailable


@pytest.mark.asyncio
async def test_fleet_only_uses_bound_client_and_missing_binding_fails(scopes):
    ref = 'fleet-model://node/service/model'
    bound = SimpleNamespace(complete=AsyncMock(return_value={'content': 'fleet reply'}))
    scope = scopes(fleet_client=bound)
    result = await acompletion(model=ref, messages=[], scope=scope)
    assert result['content'] == 'fleet reply'
    bound.complete.assert_awaited_once()
    with pytest.raises(RuntimeError, match='no bound Model Services'):
        await acompletion(model=ref, messages=[], scope=scopes())
    with pytest.raises(RuntimeError, match='no bound Model Services'):
        await call_llm_provider(ProviderConfig(ProviderType.FLEET, ref), [], scope=scopes())


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['codex/gpt-5.4', 'gemini-cli/gemini-2.5-pro'])
async def test_scoped_oauth_cannot_import_local_cli_session(scopes, monkeypatch, model):
    def forbidden(*args, **kwargs):
        raise AssertionError('Constructed ambient OAuth manager')
    monkeypatch.setattr('pantheon.utils.oauth.CodexOAuthManager', forbidden)
    monkeypatch.setattr('pantheon.utils.oauth.GeminiCliOAuthManager', forbidden)
    with pytest.raises(RuntimeError, match='OAuth|OAUTH'):
        await acompletion(model=model, messages=[], scope=scopes())


@pytest.mark.asyncio
async def test_instance_factory_passes_owned_scope_and_selector(scopes, monkeypatch):
    from pantheon.factory.bindings import AgentToolBindings
    from pantheon.factory.instances import AgentInstanceBinding, AgentInstanceFactory, config_revision
    def forbidden(*args, **kwargs):
        raise AssertionError('Consulted global model selector')
    monkeypatch.setattr('pantheon.agent._resolve_model_tag', forbidden)
    scope = scopes(resolve_models=lambda spec: ['openai/gpt-4o-mini'])
    recipe = dict(name='Member', instructions='Be concise', icon='a', model='normal')
    binding = AgentInstanceBinding(str(UUID(int=1)), 'conversation', 'config', config_revision(recipe),
                                   AgentToolBindings({}, {}))
    factory = AgentInstanceFactory([binding], model_scope=scope)
    try:
        agent, = await factory({'config': recipe}, conversation_id='conversation')
        assert agent.model_scope is scope
        assert agent.models == ['openai/gpt-4o-mini']
        assert agent._settings() is scope.settings
    finally:
        await factory.shutdown()


def test_explicit_settings_environment_is_copied_and_requires_isolation(tmp_path):
    environment = {'OPENAI_API_KEY': 'first'}
    settings = Settings(tmp_path, isolated_env=True, environment=environment, user_home=tmp_path / 'user')
    environment['OPENAI_API_KEY'] = 'other'
    assert settings.get_api_key('OPENAI_API_KEY') == 'first'
    with pytest.raises(ValueError, match='isolated_env'):
        Settings(tmp_path, environment={})


@pytest.mark.asyncio
async def test_real_agent_inference_uses_owned_settings_on_wire(scopes, endpoint):
    from pantheon.agent import Agent
    scope = scopes({'OPENAI_API_KEY': 'agent-fixture', 'OPENAI_API_BASE': endpoint.url + '/agent/v1'})
    agent = Agent('Scoped', 'Be concise', model='openai/gpt-4o-mini', model_scope=scope)
    result = await agent._acompletion([{'role': 'user', 'content': 'hello'}], 'openai/gpt-4o-mini', tool_use=False)
    assert result['content'] == 'scoped reply'
    assert len(endpoint.requests) == 1
    assert endpoint.requests[0][0] == '/agent/v1/responses'
    assert endpoint.requests[0][1]['Authorization'] == 'Bearer agent-fixture'


@pytest.mark.asyncio
async def test_compaction_uses_same_scope_on_wire(scopes, endpoint, monkeypatch):
    from pantheon.utils.token_optimization import autocompact_messages
    monkeypatch.setattr('pantheon.utils.token_optimization.should_autocompact', lambda *args, **kwargs: True)
    scope = scopes({'OPENAI_API_KEY': 'compact-fixture', 'OPENAI_API_BASE': endpoint.url + '/compact/v1'})
    history = [{'role': 'user' if n % 2 == 0 else 'assistant', 'content': f'past message {n}'} for n in range(6)]
    compacted, _, _ = await autocompact_messages(history, model='openai/gpt-4o-mini', keep_recent=2,
                                                model_scope=scope)
    assert len(endpoint.requests) == 1
    assert endpoint.requests[0][1]['Authorization'] == 'Bearer compact-fixture'
    assert any('scoped reply' in m['content'] for m in compacted)
    assert compacted[-2:] == history[-2:]


@pytest.mark.asyncio
@pytest.mark.parametrize('responses', [False, True])
@pytest.mark.parametrize('outcome', ['success', 'error', 'cancel'])
async def test_scoped_sdk_client_closes_on_completion_failure_and_cancel(scopes, monkeypatch, responses, outcome):
    import openai
    from pantheon.utils.adapters import openai_adapter
    closed = []
    real = openai.AsyncOpenAI
    class Client(real):
        async def close(self):
            closed.append(self)
            await super().close()
    monkeypatch.setattr(openai, 'AsyncOpenAI', Client)
    monkeypatch.setattr(openai_adapter, 'AsyncOpenAI', Client)
    # Real client lifecycle, controlled SDK response to force cancellation/error deterministically.
    async def create(self, **kwargs):
        if outcome == 'error':
            raise ValueError('fixture failure')
        if outcome == 'cancel':
            raise asyncio.CancelledError()
        async def stream():
            if not responses:
                yield SimpleNamespace(model_dump=lambda: {'model': 'gpt-4o-mini',
                    'choices': [{'index': 0, 'delta': {'content': 'ok'}, 'finish_reason': 'stop'}]}, choices=[])
        return stream()
    from openai.resources.chat.completions import AsyncCompletions
    from openai.resources.responses import AsyncResponses
    monkeypatch.setattr(AsyncResponses if responses else AsyncCompletions, 'create', create)
    scope = scopes({'OPENAI_API_KEY': 'fixture', 'OPENAI_API_BASE': 'http://127.0.0.1:1/v1'})
    call = acompletion_responses if responses else acompletion
    if outcome == 'success':
        await call(model='openai/gpt-4o-mini', messages=[], scope=scope)
    else:
        with pytest.raises(asyncio.CancelledError if outcome == 'cancel' else ValueError):
            await call(model='openai/gpt-4o-mini', messages=[], scope=scope)
    assert len(closed) == 1 and closed[0].is_closed()


@pytest.mark.asyncio
async def test_anthropic_wire_does_not_inherit_bearer_token(scopes, endpoint):
    scope = scopes({'ANTHROPIC_API_KEY': 'native-fixture', 'ANTHROPIC_API_BASE': endpoint.url + '/anthropic'})
    result = await acompletion(model='anthropic/claude-sonnet-4-6', messages=[{'role': 'user', 'content': 'hello'}],
                               scope=scope)
    assert result.choices[0].message.content == 'scoped reply'
    _, headers, body = endpoint.requests[0]
    assert {key.lower(): value for key, value in headers.items()}['x-api-key'] == 'native-fixture'
    assert not headers.get('Authorization')
    assert body['model'] == 'claude-sonnet-4-6'


@pytest.mark.asyncio
async def test_openrouter_budget_preserves_vendor_prefix(scopes, endpoint, monkeypatch):
    monkeypatch.setattr('pantheon.utils.openrouter_catalog.canonical_openrouter_id',
                        lambda model: 'openrouter/acme/test-model')
    scope = scopes({'LLM_FORCE_PROXY': 'true', 'PLATFORM_MODEL_MODE': 'openrouter',
        'PANTHEON_PLATFORM_PROXY_BASE': endpoint.url + '/budget/v1',
        'PANTHEON_PLATFORM_PROXY_KEY': 'budget-fixture'})
    config = detect_provider('acme/test-model', False, settings=scope.settings)
    result = await call_llm_provider(config, [{'role': 'user', 'content': 'hello'}], scope=scope)
    assert result['content'] == 'scoped reply'
    assert endpoint.requests[0][2]['model'] == 'openrouter/acme/test-model'
    assert endpoint.requests[0][1]['Authorization'] == 'Bearer budget-fixture'


@pytest.mark.asyncio
async def test_local_ollama_needs_no_borrowed_key(scopes, endpoint):
    scope = scopes({'OLLAMA_API_BASE': endpoint.url + '/ollama/v1'})
    result = await acompletion(model='ollama/fixture-model', messages=[{'role': 'user', 'content': 'hello'}], scope=scope)
    assert result.choices[0].message.content == 'scoped reply'
    assert endpoint.requests[0][1]['Authorization'] == 'Bearer ollama'
    assert endpoint.requests[0][2]['model'] == 'fixture-model'


@pytest.mark.asyncio
@pytest.mark.parametrize('status,emitted', [(401, False), (403, False), (429, False), (500, False), (404, True)])
async def test_scoped_response_errors_do_not_replay_or_poison_cache(scopes, monkeypatch, status, emitted):
    import httpx
    import openai
    async def fail(**kwargs):
        if emitted:
            await kwargs['process_chunk']({'content': 'partial'})
        raise openai.APIStatusError('fixture', response=httpx.Response(status,
            request=httpx.Request('POST', 'http://127.0.0.1:1/responses')), body=None)
    monkeypatch.setattr('pantheon.utils.llm.acompletion_responses', fail)
    chat = AsyncMock(side_effect=AssertionError('Replayed on Chat Completions'))
    monkeypatch.setattr('pantheon.utils.llm.acompletion', chat)
    scope = scopes()
    with pytest.raises(openai.APIStatusError):
        await call_llm_provider(ProviderConfig(ProviderType.OPENAI, 'gpt-4o-mini'), [], scope=scope)
    assert not scope.responses_unavailable
    chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('model,base_key', [('anthropic/claude-sonnet-4-6', 'ANTHROPIC_API_BASE'),
    ('gemini/gemini-2.5-flash', 'GEMINI_API_BASE'), ('deepseek/deepseek-chat', 'DEEPSEEK_API_BASE')])
async def test_vendor_missing_key_cannot_borrow_openai_key(scopes, endpoint, model, base_key):
    scope = scopes({'OPENAI_API_KEY': 'openai-only', 'OPENAI_API_BASE': endpoint.url + '/openai/v1',
                    base_key: endpoint.url + '/vendor/v1'})
    with pytest.raises(RuntimeError, match='credential'):
        config = detect_provider(model, False, settings=scope.settings)
        await call_llm_provider(config, [{'role': 'user', 'content': 'hello'}], scope=scope)
    assert not endpoint.requests
