"""Native Browser windows must not turn into tabs after the keeper closes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine


@pytest.mark.asyncio
async def test_create_window_uses_browser_session_after_keeper_closed():
    surviving_page = object()
    created_page = object()
    context = SimpleNamespace(pages=[surviving_page])

    async def send(method, parameters):
        assert method == "Target.createTarget"
        assert parameters["newWindow"] is True
        context.pages.append(created_page)
        return {"targetId": "new-target"}

    browser_cdp = SimpleNamespace(send=AsyncMock(side_effect=send))
    context.browser = SimpleNamespace(new_browser_cdp_session=AsyncMock(return_value=browser_cdp))
    context.new_cdp_session = AsyncMock(side_effect=RuntimeError("keeper is closed"))
    engine = BrowserEngine()
    engine._context = context

    assert await engine._create_window_page("https://example.com") is created_page
    context.browser.new_browser_cdp_session.assert_awaited_once()
    context.new_cdp_session.assert_not_called()


@pytest.mark.asyncio
async def test_seamless_does_not_fall_back_to_a_tab(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    engine = BrowserEngine()
    engine._context = SimpleNamespace()
    engine._xvfb_display = ":97"
    engine._create_window_page = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="separate browser window"):
        await engine._open_windowed("https://example.com")
