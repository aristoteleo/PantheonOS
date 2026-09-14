import asyncio

import httpx
import pytest

from pantheon.utils.model_request import ModelRequestTimeout, bounded_model_request


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat", [None, {"role": "assistant"}, {"usage": {"total_tokens": 1}}])
async def test_no_output_times_out_even_with_heartbeats(heartbeat):
    cancelled = asyncio.Event()

    async def provider(on_chunk):
        try:
            while True:
                if heartbeat is not None:
                    await on_chunk(heartbeat)
                await asyncio.sleep(0.005)
        finally:
            cancelled.set()

    with pytest.raises(ModelRequestTimeout, match="no model output"):
        await bounded_model_request(provider, None, model="stalled", idle_timeout=0.04, request_timeout=1)
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [
    {"reasoning_content": "thinking"},
    {"content": "answer"},
    {"tool_calls": [{"function": {"arguments": "{}"}}]},
])
async def test_real_output_resets_idle_deadline(delta):
    received = []

    async def provider(on_chunk):
        for _ in range(6):
            await on_chunk(delta)
            await asyncio.sleep(0.015)
        return "done"

    result = await bounded_model_request(provider, received.append, model="healthy", idle_timeout=0.06, request_timeout=1)
    assert result == "done"
    assert received == [delta] * 6


@pytest.mark.asyncio
async def test_continuous_trickle_has_total_deadline():
    cancelled = asyncio.Event()

    async def provider(on_chunk):
        try:
            while True:
                await on_chunk({"reasoning_content": "."})
                await asyncio.sleep(0.005)
        finally:
            cancelled.set()

    with pytest.raises(ModelRequestTimeout, match="request exceeded"):
        await bounded_model_request(provider, None, model="trickle", idle_timeout=1, request_timeout=0.04)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_stop_cancels_provider_without_orphan_request():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def provider(on_chunk):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(bounded_model_request(provider, None, model="stalled"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [ModelRequestTimeout("request exceeded 600s"), httpx.ReadTimeout("read timed out")])
async def test_timeout_moves_to_distinct_fallback_without_retry(monkeypatch, timeout):
    from pantheon.agent import Agent, AgentRunContext
    from pantheon import settings

    monkeypatch.setattr(settings, "get_settings", lambda: {"llm_retry": {"max_retries": 3}})
    agent = object.__new__(Agent)
    agent.name = "Test"
    agent.models = ["provider/slow", "provider/slow", "provider/healthy"]
    agent.model_params = {}
    context = AgentRunContext(agent=agent, memory=None)
    monkeypatch.setattr("pantheon.agent.get_current_run_context", lambda: context)
    calls = []

    async def complete(history, *, model, **kwargs):
        calls.append(model)
        if model == "provider/slow":
            raise timeout
        return {"role": "assistant", "content": "recovered"}

    agent._acompletion = complete
    result = await agent._acompletion_with_models([], True, None, None, True)
    assert calls == ["provider/slow", "provider/healthy"]
    assert result["content"] == "recovered"
    await agent._acompletion_with_models([], True, None, None, True)
    assert calls == ["provider/slow", "provider/healthy", "provider/healthy"]
