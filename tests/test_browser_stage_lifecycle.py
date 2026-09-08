"""Reattaching a viewer must not replace retained native browser windows."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine, BrowserWindowBinding, PageSession
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


@pytest.fixture
def retained_browser(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    engine = BrowserEngine()
    page = NS(close=AsyncMock(), is_closed=lambda: False)
    session = PageSession("retained-page", page)
    binding = BrowserWindowBinding(session.id, 27, "pantheon-page-retained-page", session.id)
    engine.pages[session.id] = session
    engine._window_bindings[binding.id] = binding
    engine._context = NS(pages=[page], close=AsyncMock())
    engine._xvfb_display = ":97"
    engine._xpra_proc = NS(poll=lambda: None)
    engine._xpra_password = "cached-stage-password"
    engine.window_page = AsyncMock(return_value=session)
    engine.set_metrics = AsyncMock()
    engine._name_binding = AsyncMock(return_value=True)
    engine._ensure_browser = AsyncMock()
    engine._ensure_xvfb = AsyncMock()
    engine._start_seamless = Mock()
    return engine, session, binding


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["display", "exited_process", "missing_process", "password", "empty_password"])
async def test_unavailable_seamless_stage_fails_before_resolving_or_mutating_window(retained_browser, unavailable):
    engine, session, binding = retained_browser
    if unavailable == "display":
        engine._xvfb_display = None
    elif unavailable == "exited_process":
        engine._xpra_proc.poll = lambda: 1
    elif unavailable == "missing_process":
        engine._xpra_proc = None
    elif unavailable == "password":
        engine._xpra_password = None
    else:
        engine._xpra_password = ""
    before_size = (session.width, session.height)
    context = engine._context

    with pytest.raises(RuntimeError, match="Native display is unavailable"):
        await engine.stage_page(binding.id, 1000, 700)

    assert engine._context is context
    assert engine.pages == {session.id: session}
    assert engine._window_bindings == {binding.id: binding}
    assert (session.width, session.height) == before_size
    for operation in (engine.window_page, engine.set_metrics, engine._name_binding,
                      engine._ensure_browser, engine._ensure_xvfb, engine._start_seamless,
                      session.page.close, context.close):
        operation.assert_not_called()


@pytest.mark.asyncio
async def test_live_stage_reattaches_same_window_and_connection_material(retained_browser):
    engine, session, binding = retained_browser
    before = await engine.stage_page(binding.id, 1000, 700)
    await engine.unstage_page(binding.id)
    after = await engine.stage_page(binding.id, 1000, 700)

    assert before == after
    assert after["page_id"] == binding.id
    assert after["active_page_id"] == session.id
    assert after["window_class"] == binding.window_class
    assert after["password"] == "cached-stage-password"
    assert engine.pages[session.id] is session
    assert engine._window_bindings[binding.id] is binding
    session.page.close.assert_not_called()
    engine._context.close.assert_not_called()
    engine._start_seamless.assert_not_called()


@pytest.mark.asyncio
async def test_ui_stage_reports_dead_transport_without_broadcasting_old_credentials(retained_browser):
    engine, session, _ = retained_browser
    engine._xpra_proc.poll = lambda: 1
    async def call(operation):
        return await operation
    engine.call = call
    tools = DesktopToolSet()
    tools._browser_engine = lambda: engine
    tools._publish_desktop = AsyncMock()

    reply = await tools.browser_ui_stage(session.id, 1000, 700)

    assert reply == {"success": False, "error": "Native display is unavailable"}
    tools._publish_desktop.assert_not_called()
    engine._ensure_browser.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_workspace_ui_stage_reports_missing_page_without_prewarming(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    engine = BrowserEngine()
    engine._ensure_browser = AsyncMock()
    engine._ensure_xvfb = AsyncMock()
    engine._start_seamless = Mock()
    engine.window_page = AsyncMock()
    engine.set_metrics = AsyncMock()

    async def call(operation):
        return await operation

    engine.call = call
    tools = DesktopToolSet()
    tools._browser_engine = lambda: engine
    tools._prewarm_browser = Mock()
    tools._publish_desktop = AsyncMock()

    # No seed page, browser_pages call, or display startup before the stage:
    # this is a persisted shell reaching a newly created workspace process.
    for _ in range(2):
        reply = await tools.browser_ui_stage("previous-workspace-page", 1000, 700)
        assert reply == {
            "success": False,
            "error": "'no such page: previous-workspace-page'",
        }

    assert engine.pages == {}
    assert engine._window_bindings == {}
    assert engine._context is None
    assert engine._xvfb_display is None
    assert engine._xpra_proc is None
    assert engine._xpra_password is None
    for operation in (engine._ensure_browser, engine._ensure_xvfb,
                      engine._start_seamless, engine.window_page,
                      engine.set_metrics, tools._prewarm_browser,
                      tools._publish_desktop):
        operation.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["binding_only", "page_only"])
async def test_retained_identity_keeps_dead_transport_error_without_native_resolution(retained_browser, identity):
    engine, session, binding = retained_browser
    if identity == "binding_only":
        # The original anchor tab can close while its physical window keeps
        # another tab; the binding is still the authoritative window token.
        engine.pages.clear()
    else:
        engine._window_bindings.clear()
    engine._xpra_proc.poll = lambda: 1
    pages = dict(engine.pages)
    bindings = dict(engine._window_bindings)

    with pytest.raises(RuntimeError, match="Native display is unavailable"):
        await engine.stage_page(binding.id, 1000, 700)

    assert engine.pages == pages
    assert engine._window_bindings == bindings
    for operation in (engine.window_page, engine.set_metrics, engine._name_binding,
                      engine._ensure_browser, engine._ensure_xvfb,
                      engine._start_seamless, session.page.close):
        operation.assert_not_called()
