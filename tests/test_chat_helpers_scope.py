"""Chat helpers use App-owned model clients; real localhost HTTP/SSE, no paid APIs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_agent_model_scope import scopes, endpoint
from pantheon.chatroom.special_agents import (
    get_summary_generator, get_suggestion_generator, get_chat_name_generator,
)
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.team.pantheon import create_delegation_task_message

MESSAGES = [{'role': 'user', 'content': 'Explain cell differentiation'},
            {'role': 'assistant', 'content': 'Start with a lineage diagram.'}]
GETTERS = (get_summary_generator, get_suggestion_generator, get_chat_name_generator)


@pytest.mark.asyncio
async def test_two_apps_helpers_use_owned_http_credentials_and_tiers(scopes, endpoint, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Used ambient helper model selector')
    monkeypatch.setattr('pantheon.chatroom.special_agents._resolve_model_spec_with_current_provider', forbidden)
    owned = [scopes({'OPENAI_API_KEY': name, 'OPENAI_API_BASE': endpoint.url + f'/{name}/v1'},
                    resolve_models=lambda spec: ['openai/gpt-4o-mini']) for name in ('first', 'second')]

    async def exercise(scope):
        summary, suggestions, title = [getter(scope) for getter in GETTERS]
        results = await asyncio.gather(summary.generate_summary(MESSAGES),
            suggestions.generate_suggestions(MESSAGES, preferred_model='anthropic/unused'),
            title.generate_name_candidate(MESSAGES, preferred_model='anthropic/unused'))
        assert results[0] == 'scoped reply'
        assert results[1][0].text == 'scoped reply'
        assert results[2] == 'scoped reply'
    await asyncio.gather(*(exercise(scope) for scope in owned))
    assert len(endpoint.requests) == 6
    for name in ('first', 'second'):
        requests = [r for r in endpoint.requests if r[0].startswith(f'/{name}/')]
        assert len(requests) == 3
        assert all(headers['Authorization'] == f'Bearer {name}' and body['model'] == 'gpt-4o-mini'
                   for _, headers, body in requests)
    for getter in GETTERS:
        assert getter(owned[0]) is getter(owned[0])
        assert getter(owned[0]) is not getter(owned[1])


@pytest.mark.asyncio
async def test_fleet_helpers_and_delegation_retain_binding_without_global_client(scopes):
    ref = 'fleet-model://service/model'
    client = SimpleNamespace(complete=AsyncMock(return_value={'content': 'bound helper reply'}),
                             metadata={ref: {'context': 8192, 'vision': False}},
                             describe=AsyncMock(return_value=({}, {'context': 8192, 'vision': False})))
    scope = scopes(fleet_client=client, resolve_models=lambda _: [ref])
    assert (await get_suggestion_generator(scope).generate_suggestions(MESSAGES, preferred_model=ref))[0].text == 'bound helper reply'
    assert await get_chat_name_generator(scope).generate_name_candidate(MESSAGES, preferred_model=ref) == 'bound helper reply'
    result = await create_delegation_task_message(MESSAGES, 'Continue analysis', model_scope=scope, preferred_model=ref)
    assert 'bound helper reply' in result
    assert client.complete.await_count == 3
    assert all(call.args[0] == ref for call in client.complete.await_args_list)


@pytest.mark.asyncio
async def test_runtime_suggestions_and_title_use_composition_scope_even_without_team(scopes, endpoint):
    scope = scopes({'OPENAI_API_KEY': 'runtime', 'OPENAI_API_BASE': endpoint.url + '/runtime/v1'},
                   resolve_models=lambda _: ['openai/gpt-4o-mini'])
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._environment = SimpleNamespace(model_scope=scope)
    memory = SimpleNamespace(id='chat', name='New Chat', extra_data={}, get_messages=lambda *a, **k: MESSAGES)
    memory.update_metadata = memory.extra_data.update
    runtime.memory_manager = SimpleNamespace(get_memory=lambda *a: memory, save_one=lambda *a: None)
    runtime.get_team_for_chat = AsyncMock(side_effect=RuntimeError('team not available'))
    runtime._apply_chat_rename = AsyncMock()
    # Suggestion model resolution failure must not fall back to another App.
    result = await runtime._handle_suggestions('chat', force_refresh=True)
    assert result['success'], result
    await runtime._background_rename_chat(memory, messages=MESSAGES)
    runtime._apply_chat_rename.assert_awaited_once()
    assert runtime._apply_chat_rename.call_args.args[1] == 'scoped reply'
    assert len(endpoint.requests) == 2
    assert all(headers['Authorization'] == 'Bearer runtime' for _, headers, _ in endpoint.requests)


@pytest.mark.asyncio
async def test_missing_app_model_binding_does_not_borrow_another_app(scopes):
    ref = 'fleet-route://helper'
    good = scopes(resolve_models=lambda _: [ref], fleet_client=SimpleNamespace(
        complete=AsyncMock(return_value={'content': 'authorized helper'}),
        metadata={ref: {'context': 8192, 'vision': False}},
        describe=AsyncMock(return_value=({}, {'context': 8192, 'vision': False}))))
    missing = scopes(resolve_models=lambda _: [ref])
    assert await get_suggestion_generator(good).generate_suggestions(MESSAGES, preferred_model=ref)
    assert await get_suggestion_generator(missing).generate_suggestions(MESSAGES, preferred_model=ref) == []
    assert good.fleet_client.complete.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['summary', 'suggestions', 'title'])
async def test_cached_helpers_do_not_retain_other_conversation_messages(scopes, endpoint, kind):
    import json
    scope = scopes({'OPENAI_API_KEY': 'owned', 'OPENAI_API_BASE': endpoint.url + '/owned/v1'},
                   resolve_models=lambda _: ['openai/gpt-4o-mini'])
    method = {'summary': get_summary_generator(scope).generate_summary,
              'suggestions': get_suggestion_generator(scope).generate_suggestions,
              'title': get_chat_name_generator(scope).generate_name_candidate}[kind]
    for marker in ('FIRST_CHAT_PRIVATE_CONTEXT', 'SECOND_CHAT_ONLY'):
        await method([{'role': 'user', 'content': marker}, {'role': 'assistant', 'content': 'Proceed'}])
    assert len(endpoint.requests) == 2
    assert 'FIRST_CHAT_PRIVATE_CONTEXT' not in json.dumps(endpoint.requests[1][2])
    assert 'SECOND_CHAT_ONLY' in json.dumps(endpoint.requests[1][2])


@pytest.mark.asyncio
async def test_delegation_context_policy_uses_parent_scope(scopes, monkeypatch):
    from pantheon.team.pantheon import _resolve_child_delegation_delivery
    def forbidden():
        raise AssertionError('Delegation consulted global settings')
    monkeypatch.setattr('pantheon.team.pantheon.get_settings', forbidden)
    for enabled in (True, False):
        scope = scopes()
        monkeypatch.setattr(scope.settings, 'get_section', lambda key, value=enabled: {'fork_context': value})
        context = SimpleNamespace(agent=SimpleNamespace(model_scope=scope), memory=None)
        variables, summarize = await _resolve_child_delegation_delivery(context, None, {'owner': 'parent'}, True)
        assert variables == {'owner': 'parent'}
        assert summarize is enabled
