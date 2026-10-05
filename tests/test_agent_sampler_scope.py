"""Internal sampler callbacks retain their owner's model access across tasks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_agent_model_scope import scopes, endpoint
from pantheon.agent import Agent, AgentRunContext, _RUN_CONTEXT, _resolve_model_spec_with_current_provider


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['tool', 'injector'])
@pytest.mark.parametrize('model', [None, 'low'])
async def test_callbacks_route_concurrent_apps_to_owned_http(scopes, endpoint, kind, model):
    async def run(owner):
        scope = scopes({'OPENAI_API_KEY': owner, 'OPENAI_API_BASE': endpoint.url + f'/{owner}/v1'},
                       resolve_models=lambda spec: ['openai/gpt-4o-mini'])
        agent = Agent(owner, 'Sample briefly', model='openai/gpt-4o-mini', model_scope=scope)
        results = []
        async def forbidden(**kwargs):
            raise AssertionError('Untrusted context replaced owned sampling callback')
        async def sample(context_variables):
            """Generate a sample using the owning Agent."""
            result = await context_variables['_call_agent'](
                messages=[{'role': 'user', 'content': owner}], model=model)
            results.append(result)
            return result
        if kind == 'tool':
            agent.tool(sample)
            await agent.call_tool('sample', {}, context_variables={'_call_agent': forbidden})
        else:
            class Injector:
                async def inject(self, input_text, context):
                    await sample(context)
                    return 'Injected context'
            agent.context_injectors = [Injector()]
            await agent._inject_context_to_messages([{'role': 'user', 'content': owner}], {'_call_agent': forbidden})
        assert len(results) == 1 and results[0]['success'], results
        assert results[0]['response'] == 'scoped reply'
    await asyncio.gather(run('first'), run('second'))
    assert len(endpoint.requests) == 2
    for path, headers, body in endpoint.requests:
        owner = path.split('/')[1]
        assert headers['Authorization'] == f'Bearer {owner}'
        assert owner in str(body)


@pytest.mark.asyncio
async def test_delayed_tool_callback_cannot_borrow_active_agents_fleet_binding(scopes):
    def make(name):
        ref = f'fleet-route://{name}'
        client = SimpleNamespace(complete=AsyncMock(return_value={'content': name}),
            describe=AsyncMock(return_value=({}, {'context': 8192})), metadata={ref: {'context': 8192}})
        scope = scopes(fleet_client=client, resolve_models=lambda _: [ref])
        return Agent(name, 'Sample', model=ref, model_scope=scope), client
    owner, first = make('first')
    other, second = make('second')
    async def sample(context_variables):
        """Sampling tool."""
    owner.tool(sample)
    callback = owner._prepare_context_variables('sample', {}, {}, None)['context_variables']['_call_agent']
    token = _RUN_CONTEXT.set(AgentRunContext(agent=other, memory=None, current_model=other.models[0]))
    try:
        result = await callback(messages=[{'role': 'user', 'content': 'owner only'}], model='low')
    finally:
        _RUN_CONTEXT.reset(token)
    assert result['success'] and result['response'] == 'first', result
    assert first.complete.await_count == 1 and second.complete.await_count == 0
    assert first.complete.await_args.args[0] == owner.models[0]


def test_scoped_sampling_preserves_reasoning_and_explicit_fleet_placement(scopes):
    scope = scopes(resolve_models=lambda spec: ['openai/gpt-4o-mini', 'openai/gpt-4.1-mini'])
    assert _resolve_model_spec_with_current_provider('low+think:high', model_scope=scope) == [
        'openai/gpt-4o-mini+think:high', 'openai/gpt-4.1-mini+think:high']
    assert _resolve_model_spec_with_current_provider('low+think:high', current_model='fleet-route://owner',
        model_scope=scope) == ['fleet-route://owner+think:high']


@pytest.mark.asyncio
async def test_missing_sampler_binding_never_borrows_active_app(scopes):
    ref = 'fleet-route://other'
    client = SimpleNamespace(complete=AsyncMock(return_value={'content': 'must not be used'}))
    other = Agent('other', 'Other', model=ref, model_scope=scopes(fleet_client=client))
    owner = Agent('owner', 'Owner', model='fleet-route://missing', model_scope=scopes())
    async def sample(context_variables):
        """Sampling tool."""
    owner.tool(sample)
    callback = owner._prepare_context_variables('sample', {}, {}, None)['context_variables']['_call_agent']
    token = _RUN_CONTEXT.set(AgentRunContext(agent=other, memory=None, current_model=ref))
    try:
        result = await callback(messages=[{'role': 'user', 'content': 'private owner context'}], model='low')
    finally:
        _RUN_CONTEXT.reset(token)
    assert result['success'] is False
    assert 'no bound Model Services' in result['error']
    client.complete.assert_not_called()
