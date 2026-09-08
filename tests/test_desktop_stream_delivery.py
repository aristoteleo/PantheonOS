"""A real stream adapter must expose failed publishes to directed callers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.chatroom.stream import NATSStreamAdapter


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
