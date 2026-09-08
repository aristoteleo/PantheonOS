"""Remote tools retain image sampling after live Agent closures are stripped.

Only the model provider boundary is stubbed; these tests exercise the real
ToolsetProxy sanitizer, pickle boundary, tool decorator and image tool.
"""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import cloudpickle
import pytest
from PIL import Image

from pantheon.apps.builtin.file.file_manager import FileManagerToolSet
from pantheon.apps.proxy import ToolsetProxy
from pantheon.toolset import (
    ExecutionContext, get_current_context_variables,
    reset_current_context_variables, set_current_context_variables, tool,
)


@pytest.fixture(autouse=True)
def isolated_context():
    parent = ExecutionContext()
    token = set_current_context_variables(parent)
    yield parent
    reset_current_context_variables(token)


@pytest.fixture
def image_file(tmp_path):
    path = tmp_path / 'sample.png'
    img = Image.new('RGB', (48, 24), '#135c8f')
    for x in range(24):
        for y in range(24):
            img.putpixel((x, y), (255, 216, 77))
    img.save(path)
    return path


@pytest.fixture
def model_boundary(monkeypatch):
    import pantheon.agent as agent_module
    from pantheon.settings import get_settings
    from pantheon.utils.model_selector import get_model_selector

    monkeypatch.setattr(agent_module, 'get_current_run_model', lambda: None)
    sampler = AsyncMock(return_value={
        'success': True, 'response': 'A yellow block beside a blue block.',
        '_metadata': {'current_cost': 0.0123, 'provider_request_id': 'provider-test'},
    })
    monkeypatch.setattr(agent_module, '_call_agent', sampler)
    monkeypatch.setattr(get_settings(), 'get_vision_model', lambda: 'auto')
    monkeypatch.setattr(get_model_selector(), 'find_capable_models_across_providers', lambda *a, **k: [])
    return sampler


async def remote_observe(image_file, context):
    fm = FileManagerToolSet(name='remote_image_test')
    proxy = ToolsetProxy('remote-image-test-' + uuid4().hex)
    sent = []

    class ProcessBoundary:
        async def invoke(self, method, args):
            # Just like a remote App, there is no parent run ContextVar or
            # callable. No cloudpickle of a live Agent/asyncio state is allowed.
            transmitted = cloudpickle.loads(cloudpickle.dumps(args))
            sent.append(transmitted)
            token = set_current_context_variables(ExecutionContext())
            try:
                return await getattr(fm, method)(**transmitted)
            finally:
                reset_current_context_variables(token)

    proxy.service = ProcessBoundary()
    try:
        result = await proxy.invoke('observe_images', {
            'question': 'Describe these two blocks.',
            'image_paths': str(image_file),
            'context_variables': context,
        })
        return result, sent
    finally:
        ToolsetProxy._instance_pool.pop(proxy.service_id, None)


async def test_remote_image_sampling_keeps_model_images_and_cost(image_file, model_boundary):
    local_callback = AsyncMock(side_effect=AssertionError('Live callback must not cross the wire'))
    result, sent = await remote_observe(image_file, {
        'caller_models': ['moonshot/kimi-k2.5', 'another/fallback'],
        '_call_agent': local_callback, 'chat_id': 'original-chat',
    })
    assert result['success'] is True, result
    assert result['model_used'] == 'moonshot/kimi-k2.5'
    assert result['content'] == 'A yellow block beside a blue block.'
    assert result['_metadata']['current_cost'] == 0.0123
    assert result['_metadata']['provider_request_id'] == 'provider-test'
    assert result['_metadata']['sampling']['memory_requested'] is True
    assert result['_metadata']['sampling']['memory_used'] is False
    assert '_call_agent' not in sent[0]['context_variables']
    local_callback.assert_not_awaited()
    kwargs = model_boundary.await_args.kwargs
    assert kwargs['model'] == 'moonshot/kimi-k2.5'
    assert kwargs['memory'] is None
    assert kwargs['messages'][0]['content'][0]['text'] == 'Describe these two blocks.'
    assert kwargs['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')


async def test_remote_native_model_returns_images_without_extra_sampling(image_file, model_boundary):
    result, _ = await remote_observe(image_file, {'caller_models': ['anthropic/claude-sonnet-4-6']})
    assert result['success'] is True, result
    assert result['mode'] == 'native'
    assert result['image_count'] == 1
    assert result['content_blocks'][0]['image_url']['url'].startswith('data:image/jpeg;base64,')
    model_boundary.assert_not_awaited()


async def test_concurrent_tools_do_not_share_caller_or_local_callback(isolated_context):
    both_entered = asyncio.Event()
    entered = 0

    @tool
    async def inspect_context():
        nonlocal entered
        context = get_current_context_variables()
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        return dict(context)

    callback = AsyncMock()
    first, second = await asyncio.gather(
        inspect_context(context_variables={'caller_models': ['provider/one'], '_call_agent': callback}),
        inspect_context(context_variables={'caller_models': ['provider/two']}),
    )
    assert first['caller_models'] == ['provider/one']
    assert second['caller_models'] == ['provider/two']
    assert first['_call_agent'] is callback
    assert '_call_agent' not in second
    assert isolated_context == {}


async def test_plain_remote_context_samples_without_live_callback(model_boundary):
    context = ExecutionContext(caller_models=['moonshot/kimi-k2.5'])
    result = await context.call_agent([{'role': 'user', 'content': 'Inspect supplied image'}])
    assert result['success'] is True
    assert model_boundary.await_args.kwargs['model'] == 'moonshot/kimi-k2.5'


async def test_local_callback_keeps_memory_and_result_unchanged(model_boundary):
    expected = {'success': True, 'response': 'parent-context result', '_metadata': {'current_cost': 0.5}}
    callback = AsyncMock(return_value=expected)
    context = ExecutionContext(_call_agent=callback, caller_models=['moonshot/kimi-k2.5'])
    messages = [{'role': 'user', 'content': 'local question'}]
    result = await context.call_agent(messages, system_prompt='system', model='low', use_memory=True)
    assert result is expected
    callback.assert_awaited_once_with(messages=messages, system_prompt='system', model='low', use_memory=True)
    model_boundary.assert_not_awaited()


@pytest.mark.parametrize('caller_models', [None, [], [''], 'not-a-model-list', [None, 12]])
async def test_missing_caller_uses_normal_server_settings(model_boundary, caller_models):
    result = await ExecutionContext(caller_models=caller_models).call_agent([{'role': 'user', 'content': 'q'}])
    assert result['success'] is True
    assert model_boundary.await_args.kwargs['model'] is None
    assert result['_metadata']['sampling'] == {
        'execution': 'tool_service', 'memory_requested': False, 'memory_used': False,
    }


async def test_server_quality_tag_uses_caller_provider(model_boundary, monkeypatch):
    from pantheon.utils.model_selector import get_model_selector
    resolve = []

    def selected(tag, provider):
        resolve.append((tag, provider))
        return ['moonshot/kimi-k2.5']

    monkeypatch.setattr(get_model_selector(), 'resolve_model_for_provider', selected)
    await ExecutionContext(caller_models=['moonshot/kimi-k2.5']).call_agent([], model='low')
    assert resolve == [('low', 'moonshot')]
    assert model_boundary.await_args.kwargs['model'] == ['moonshot/kimi-k2.5']


async def test_configured_vision_pin_and_fallback_survive_remote_boundary(image_file, model_boundary, monkeypatch):
    from pantheon.settings import get_settings
    monkeypatch.setattr(get_settings(), 'get_vision_model', lambda: 'zai/glm-4.5v')
    model_boundary.side_effect = [
        {'success': False, 'error': 'provider temporarily unavailable'},
        {'success': True, 'response': 'Recovered image answer', '_metadata': {'current_cost': 0.08}},
    ]
    result, _ = await remote_observe(image_file, {'caller_models': ['moonshot/kimi-k2.5']})
    assert result['success'] is True
    assert result['model_used'] == 'moonshot/kimi-k2.5'
    assert result['_metadata']['current_cost'] == 0.08
    assert [call.kwargs['model'] for call in model_boundary.await_args_list] == ['zai/glm-4.5v', 'moonshot/kimi-k2.5']


@pytest.mark.parametrize('vision_setting', ['auto', 'low'])
async def test_no_caller_can_use_configured_vision_chain(image_file, model_boundary, monkeypatch, vision_setting):
    from pantheon.settings import get_settings
    from pantheon.utils.model_selector import get_model_selector
    monkeypatch.setattr(get_settings(), 'get_vision_model', lambda: vision_setting)
    selection = []

    def models(capability, **kwargs):
        selection.append((capability, kwargs))
        return ['zai/glm-4.5v']

    monkeypatch.setattr(get_model_selector(), 'find_capable_models_across_providers', models)
    result, _ = await remote_observe(image_file, {})
    assert result['success'] is True
    assert result['model_used'] == 'zai/glm-4.5v'
    assert selection == [('vision', {'tier_order': ['low', 'normal', 'high']} if vision_setting == 'low' else {})]


@pytest.mark.parametrize('provider_result', [
    {'success': False, 'error': 'provider rejected request'},
    {'success': True, 'response': ''},
])
async def test_remote_sampling_failure_cannot_look_successful(image_file, model_boundary, provider_result):
    model_boundary.return_value = provider_result
    result, _ = await remote_observe(image_file, {'caller_models': ['moonshot/kimi-k2.5']})
    assert result['success'] is False
    assert 'No call_agent callback' not in result['error']
    assert 'provider rejected' in result['error'] or 'empty content' in result['error']


async def test_nested_tool_context_inherits_but_does_not_modify_parent(isolated_context):
    @tool
    async def inner():
        current = get_current_context_variables()
        current['child_only'] = True
        return dict(current)

    @tool
    async def outer():
        parent = get_current_context_variables()
        child = await inner(context_variables={'caller_models': ['child/model']})
        assert get_current_context_variables() is parent
        return dict(parent), child

    parent, child = await outer(context_variables={'caller_models': ['parent/model'], 'chat_id': 'same-chat'})
    assert parent == {'caller_models': ['parent/model'], 'chat_id': 'same-chat'}
    assert child == {'caller_models': ['child/model'], 'chat_id': 'same-chat', 'child_only': True}
    assert isolated_context == {}


async def test_remote_image_uses_normal_agent_sampler_and_accounts_usage(image_file, monkeypatch):
    """Keep _call_agent real; replace only the expensive model-run boundary."""
    from types import SimpleNamespace
    from pantheon.agent import Agent
    from pantheon.settings import get_settings
    from pantheon.utils.model_selector import get_model_selector

    monkeypatch.setattr('pantheon.agent.get_current_run_model', lambda: None)
    monkeypatch.setattr(get_settings(), 'get_vision_model', lambda: 'auto')
    monkeypatch.setattr(get_model_selector(), 'find_capable_models_across_providers', lambda *a, **k: [])
    observed = []

    async def model_run(agent, messages, **kwargs):
        observed.append((agent, messages, kwargs))
        return SimpleNamespace(content='Actual sampler boundary answer', details=SimpleNamespace(messages=[
            {'role': 'assistant', '_metadata': {'current_cost': 0.047}},
        ]))

    monkeypatch.setattr(Agent, 'run', model_run)
    result, _ = await remote_observe(image_file, {'caller_models': ['moonshot/kimi-k2.5']})
    assert result['success'] is True
    assert result['content'] == 'Actual sampler boundary answer'
    assert result['_metadata']['current_cost'] == 0.047
    assert len(observed) == 1
    agent, messages, kwargs = observed[0]
    assert agent.name == 'sampler'
    assert agent.models == ['moonshot/kimi-k2.5']
    assert messages[0]['content'][1]['type'] == 'image_url'
    assert kwargs['use_memory'] is False and kwargs['update_memory'] is False
