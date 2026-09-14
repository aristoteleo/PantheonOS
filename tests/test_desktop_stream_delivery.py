"""A real stream adapter must expose failed publishes to directed callers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.chatroom.stream import NATSStreamAdapter


@pytest.mark.asyncio
async def test_chat_activity_begin_and_reasoning_are_published_before_step_completion():
    adapter = NATSStreamAdapter()
    adapter.publish = AsyncMock()
    chunk, step = adapter.create_hooks("chat")
    await chunk({"activity": "preparing_context"})
    await chunk({"begin": True, "message_id": "m", "chunk_index": 0})
    await chunk({"reasoning_content": "Inspecting data", "message_id": "m", "chunk_index": 1})
    assert [c.args[2]["chunk"] for c in adapter.publish.await_args_list] == [
        {"activity": "preparing_context"},
        {"begin": True, "message_id": "m", "chunk_index": 0},
        {"reasoning_content": "Inspecting data", "message_id": "m", "chunk_index": 1},
    ]
    await chunk({"execution_context_id": "child", "message_id": "child-m", "tool_calls": [
        {"function": {"name": "read_file", "arguments": "{}"}}
    ]})
    assert adapter.publish.await_args.args[2]["execution_context_id"] == "child"
    await step({"id": "m", "role": "assistant", "tool_calls": []})
    assert adapter.publish.await_args.args[1] == "step"


def desktop_with_channel(channel):
    adapter = NATSStreamAdapter()
    adapter._backend = SimpleNamespace(get_or_create_stream=AsyncMock(return_value=channel))
    desktop = DesktopToolSet()
    desktop._nats = adapter
    desktop._presence = lambda: SimpleNamespace(
        anchor_for=lambda _: {"viewport_id": "v", "reason": "hosts this chat"})
    desktop._chat_id = lambda: ""
    return desktop


@pytest.mark.asyncio
async def test_real_adapter_channel_failure_does_not_start_response_wait(monkeypatch):
    channel = SimpleNamespace(publish=AsyncMock(side_effect=ConnectionError("closed channel")))
    desktop = desktop_with_channel(channel)
    wait = AsyncMock(side_effect=AssertionError("a failed publish must not wait for a reply"))
    monkeypatch.setattr("asyncio.wait_for", wait)

    result = await desktop._desktop_request(
        "desktop.call", {"window_id": "win-1", "action": "newPage"}, timeout=300)

    assert result["success"] is False
    assert "could not be delivered" in result["error"]
    assert "completion" not in result
    assert desktop._pending_desktop == {}
    channel.publish.assert_awaited_once()
    wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_adapter_success_reaches_the_same_pending_request():
    channel = SimpleNamespace(publish=AsyncMock())
    desktop = desktop_with_channel(channel)

    async def answer(message):
        assert message.data["viewport_id"] == "v"
        await desktop.report_desktop_result(message.data["request_id"], value={"page_id": "same-page"})

    channel.publish.side_effect = answer
    result = await desktop._desktop_request("desktop.call", {"window_id": "win-1", "action": "newPage"})
    assert result == {"success": True, "result": {"page_id": "same-page"}}
    assert desktop._pending_desktop == {}


@pytest.mark.asyncio
async def test_best_effort_broadcast_failure_remains_non_throwing():
    channel = SimpleNamespace(publish=AsyncMock(side_effect=ConnectionError("closed channel")))
    desktop = desktop_with_channel(channel)
    assert await desktop._nats.publish_stream("desktop", {"type": "desktop.presence"}) is False
    assert await desktop._publish_desktop({"type": "desktop.presence"}) is False


@pytest.mark.asyncio
async def test_legacy_adapter_with_no_return_value_remains_compatible():
    desktop = DesktopToolSet()
    desktop._nats = SimpleNamespace(publish_stream=AsyncMock(return_value=None))
    assert await desktop._publish_desktop({"type": "desktop.presence"}) is True
