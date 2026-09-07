"""Native Browser windows must not turn into tabs after the keeper closes."""
import asyncio
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
    engine._context = SimpleNamespace(pages=[object()])
    engine._xvfb_display = ":97"
    engine._create_window_page = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="separate browser window"):
        await engine._open_windowed("https://example.com")


@pytest.mark.asyncio
async def test_empty_headful_context_is_relaunched_once_before_concurrent_opens():
    engine = BrowserEngine()
    stale_context = SimpleNamespace(pages=[], close=AsyncMock())
    engine._context = stale_context
    engine._xvfb_display = ":97"
    engine._browser_cdp = object()
    relaunched_context = SimpleNamespace(pages=[object()], close=AsyncMock())

    async def launch():
        # A close event normally resets the cached browser session; the
        # explicit reset must also work when the context did not emit one.
        assert engine._context is None
        assert engine._browser_cdp is None
        await asyncio.sleep(0)
        engine._context = relaunched_context

    async def create(_url):
        await asyncio.sleep(0)
        page = object()
        engine._context.pages.append(page)
        return page

    engine._launch_browser = AsyncMock(side_effect=launch)
    engine._create_window_page = AsyncMock(side_effect=create)
    first, second = await asyncio.gather(
        engine._open_windowed("about:blank"),
        engine._open_windowed("about:blank"),
    )
    assert first is not second
    stale_context.close.assert_awaited_once()
    engine._launch_browser.assert_awaited_once()
    assert len(relaunched_context.pages) == 3
    relaunched_context.close.assert_not_called()


@pytest.mark.asyncio
async def test_surviving_headful_page_is_never_closed_for_recovery():
    engine = BrowserEngine()
    context = SimpleNamespace(pages=[object()], close=AsyncMock())
    engine._context = context
    engine._xvfb_display = ":97"
    engine._launch_browser = AsyncMock()
    await engine._ensure_browser()
    context.close.assert_not_called()
    engine._launch_browser.assert_not_called()
    assert engine._context is context


@pytest.mark.asyncio
async def test_empty_headless_context_remains_usable_without_relaunch():
    engine = BrowserEngine()
    context = SimpleNamespace(pages=[], close=AsyncMock())
    engine._context = context
    engine._launch_browser = AsyncMock()
    await engine._ensure_browser()
    context.close.assert_not_called()
    engine._launch_browser.assert_not_called()
