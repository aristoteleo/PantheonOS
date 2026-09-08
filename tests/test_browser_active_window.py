"""Window-targeted Browser tools follow the active tab without changing its shell."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine, PageSession


class Page:
    def __init__(self, window_id, visibility="hidden", closed=False):
        self.window_id = window_id
        self.target_id = f"target-{id(self)}"
        self.visibility = visibility
        self.closed = closed
        self.events = {}
        self.cdp_sessions = []
        self.url = "https://example.test/"
        self.evaluate = AsyncMock(side_effect=lambda *_: self.visibility)

    def is_closed(self):
        return self.closed

    def on(self, name, handler):
        self.events[name] = handler

    async def new_cdp(self):
        async def send(method, *args):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": self.target_id}}
            assert method == "Browser.getWindowForTarget"
            return {"windowId": self.window_id}
        cdp = NS(send=AsyncMock(side_effect=send), detach=AsyncMock())
        self.cdp_sessions.append(cdp)
        return cdp


async def setup_engine(pages):
    engine = BrowserEngine()
    engine._xvfb_display = ":97"
    async def new_cdp(page):
        return await page.new_cdp()
    engine._context = NS(pages=pages, new_cdp_session=AsyncMock(side_effect=new_cdp))
    async def active_target(window_id):
        selected = [p for p in pages if p.window_id == window_id and p.visibility == "visible" and not p.closed]
        if len(selected) > 1:
            raise RuntimeError("The requested browser window has no uniquely selected tab")
        return selected[0].target_id if selected else None
    engine._native_active_target = AsyncMock(side_effect=active_target)
    anchor = PageSession("anchor", pages[0])
    anchor.windowed = True
    engine.pages[anchor.id] = anchor
    await engine._attach(anchor)
    if pages[0].window_id is not None:
        await engine._bind_window(anchor)
    engine._name_window = AsyncMock(return_value=True)
    engine.place_window = AsyncMock()
    engine.on_popup_page = AsyncMock()
    return engine, anchor


@pytest.mark.asyncio
async def test_window_resolves_active_unregistered_tab_without_changing_native_identity():
    background, active, different = Page(10), Page(10, "visible"), Page(20, "visible")
    engine, anchor = await setup_engine([background, active, different])
    selected = await engine.current_window_page(anchor)
    assert selected.page is active
    assert not selected.windowed
    assert selected.opener == anchor.id
    assert engine.pages[anchor.id] is anchor and anchor.windowed
    assert len(engine.pages) == 2
    engine._name_window.assert_not_called()
    engine.place_window.assert_not_called()
    engine.on_popup_page.assert_not_called()
    active.cdp_sessions[0].detach.assert_awaited_once()
    different.cdp_sessions[0].detach.assert_awaited_once()


@pytest.mark.asyncio
async def test_known_current_tab_reuses_session_without_attaching_twice():
    anchor_page = Page(10, "visible")
    engine, anchor = await setup_engine([anchor_page])
    assert await engine.current_window_page(anchor) is anchor
    assert len(anchor_page.cdp_sessions) == 1
    anchor.cdp.detach.assert_not_called()


@pytest.mark.asyncio
async def test_emulated_dom_visibility_does_not_override_native_selection():
    first, selected = Page(10, "visible"), Page(10, "visible")
    engine, anchor = await setup_engine([first, selected])
    engine._native_active_target = AsyncMock(return_value=selected.target_id)
    assert (await engine.window_page(anchor.id)).page is selected
    first.evaluate.assert_not_awaited()
    selected.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_native_tab_pending_in_playwright_does_not_destroy_binding():
    engine, anchor = await setup_engine([Page(10, closed=True)])
    engine._native_active_target = AsyncMock(return_value="new-target-not-attached-yet")
    with pytest.raises(RuntimeError, match="not yet available"):
        await engine.window_page(anchor.id)
    assert engine.window_binding(anchor.id).window_id == 10


@pytest.mark.asyncio
async def test_no_active_tab_never_falls_back_to_another_window():
    engine, anchor = await setup_engine([Page(10), Page(20, "visible")])
    with pytest.raises(RuntimeError, match="no uniquely selected"):
        await engine.current_window_page(anchor)
    assert list(engine.pages) == ["anchor"]


@pytest.mark.asyncio
async def test_ambiguous_visibility_is_reported_instead_of_picking_one():
    engine, anchor = await setup_engine([Page(10, "visible"), Page(10, "visible")])
    with pytest.raises(RuntimeError, match="no uniquely selected"):
        await engine.current_window_page(anchor)


@pytest.mark.asyncio
async def test_closed_anchor_resolves_the_surviving_tab_in_its_original_window():
    remaining = Page(10, "visible")
    engine, anchor = await setup_engine([Page(10, closed=True), remaining, Page(20, "visible")])
    engine.pages.pop(anchor.id)
    assert (await engine.window_page(anchor.id)).page is remaining
    assert engine.window_binding(anchor.id).window_id == 10


@pytest.mark.asyncio
async def test_unknown_window_identity_does_not_allow_active_page_guess():
    engine, anchor = await setup_engine([Page(None, "visible"), Page(20, "visible")])
    with pytest.raises(KeyError, match="No such native browser window"):
        await engine.current_window_page(anchor)


@pytest.mark.asyncio
async def test_concurrent_active_tab_queries_attach_native_tab_once():
    active = Page(10, "visible")
    engine, anchor = await setup_engine([Page(10), active])
    first, second = await asyncio.gather(engine.current_window_page(anchor), engine.current_window_page(anchor))
    assert first is second
    assert len(engine.pages) == 2
    assert len(active.cdp_sessions) == 2  # One temporary inspection + one retained session.


@pytest.mark.asyncio
async def test_popup_tab_does_not_rename_window_or_open_duplicate_atrium_shell(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(10, "visible")
    engine, anchor = await setup_engine([Page(10), popup])
    child = await engine._adopt_popup(anchor, popup)
    assert child.page is popup and not child.windowed
    assert child.opener == anchor.id
    engine._name_window.assert_not_called()
    engine.place_window.assert_not_called()
    engine.on_popup_page.assert_not_called()


@pytest.mark.asyncio
async def test_independent_popup_window_keeps_its_own_atrium_shell(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(20, "visible")
    engine, anchor = await setup_engine([Page(10), popup])
    child = await engine._adopt_popup(anchor, popup)
    assert child.page is popup and child.windowed
    engine._name_window.assert_awaited_once_with(child)
    engine.place_window.assert_awaited_once_with(child)
    engine.on_popup_page.assert_awaited_once_with(child)


@pytest.mark.asyncio
async def test_popup_and_native_tab_discovery_share_one_adoption(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(10, "visible")
    engine, anchor = await setup_engine([Page(10), popup])
    adopted, current = await asyncio.gather(engine._adopt_popup(anchor, popup), engine.current_window_page(anchor))
    assert adopted is current
    assert len(engine.pages) == 2
    engine._name_window.assert_not_called()
    engine.on_popup_page.assert_not_called()


@pytest.mark.asyncio
async def test_dragged_anchor_does_not_retarget_its_atrium_window():
    original, remaining, elsewhere = Page(10), Page(10, "visible"), Page(20)
    engine, anchor = await setup_engine([original, remaining, elsewhere])
    original.window_id = 20
    original.visibility = "visible"
    binding = engine.window_binding(anchor.id)
    assert binding.window_id == 10
    assert (await engine.window_page(binding.id)).page is remaining
    assert engine.get(anchor.id).page is original  # Explicit page id still means that exact tab.
    assert binding.window_class == "pantheon-page-anchor"


@pytest.mark.asyncio
async def test_closed_first_tab_keeps_window_binding_and_named_identity():
    original, remaining = Page(10), Page(10, "visible")
    engine, anchor = await setup_engine([original, remaining])
    engine._named.add(anchor.id)
    original.closed = True
    await engine.close_page(anchor.id)
    assert anchor.id not in engine.pages
    assert anchor.id in engine._named
    assert engine.window_binding(anchor.id).window_id == 10
    assert (await engine.window_page(anchor.id)).page is remaining


@pytest.mark.asyncio
async def test_failed_native_close_keeps_live_page_registration():
    original = Page(10, "visible")
    engine, anchor = await setup_engine([original])
    original.close = AsyncMock(side_effect=RuntimeError("Chromium refused close"))
    with pytest.raises(RuntimeError, match="refused close"):
        await engine.close_page(anchor.id)
    assert engine.get(anchor.id) is anchor
    assert engine.window_binding(anchor.id).window_id == 10


@pytest.mark.asyncio
async def test_last_tab_close_drops_only_its_window_binding():
    original, remaining, elsewhere = Page(10), Page(10, "visible"), Page(20, "visible")
    engine, anchor = await setup_engine([original, remaining, elsewhere])
    child = await engine.window_page(anchor.id)
    original.closed = True
    await engine.close_page(anchor.id)
    remaining.closed = True
    await engine.close_page(child.id)
    with pytest.raises(KeyError, match="No such native browser window"):
        engine.window_binding(anchor.id)
    assert not elsewhere.closed


@pytest.mark.asyncio
async def test_empty_bound_window_never_rebinds_to_moved_anchor():
    original = Page(10, "visible")
    engine, anchor = await setup_engine([original])
    original.window_id = 20
    with pytest.raises(RuntimeError, match="no remaining tabs"):
        await engine.window_page(anchor.id)
    assert engine.get(anchor.id).page is original
    assert anchor.id not in engine._window_bindings


@pytest.mark.asyncio
async def test_metadata_of_hidden_window_stays_with_same_window():
    original, remaining, elsewhere = Page(10), Page(10), Page(20, "visible")
    engine, anchor = await setup_engine([original, remaining, elsewhere])
    original.closed = True
    assert (await engine.window_page(anchor.id, require_visible=False)).page is remaining
    with pytest.raises(RuntimeError, match="no uniquely selected"):
        await engine.window_page(anchor.id)


@pytest.mark.asyncio
async def test_ui_stage_and_focus_keep_window_token_and_current_tab(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    original, remaining = Page(10), Page(10, "visible")
    engine, anchor = await setup_engine([original, remaining])
    engine._xpra_proc = NS(poll=lambda: None)
    engine._xpra_password = "test-stage-password"
    engine._named.add(anchor.id)
    engine._ensure_browser = AsyncMock()
    engine.set_metrics = AsyncMock()
    engine.focus_page = AsyncMock()
    engine._focus_named_window = lambda token: None
    original.closed = True
    await engine.close_page(anchor.id)
    stage = await engine.stage_page(anchor.id, 900, 650)
    assert stage['page_id'] == anchor.id and stage['window_class'] == 'pantheon-page-anchor'
    assert stage['active_page_id'] != anchor.id
    assert engine.pages[stage['active_page_id']].page is remaining
    assert (await engine.focus_stage(anchor.id))['ok'] is True
    engine.focus_page.assert_not_called()  # Focusing the shell must not switch native tabs.


@pytest.mark.asyncio
async def test_close_window_closes_remaining_tabs_but_not_dragged_anchor():
    original, remaining, other = Page(10), Page(10, "visible"), Page(20)
    engine, anchor = await setup_engine([original, remaining, other])
    original.window_id = 20
    original.visibility = "visible"
    async def close_remaining():
        remaining.closed = True
    remaining.close = AsyncMock(side_effect=close_remaining)
    original.close = AsyncMock()
    other.close = AsyncMock()
    await engine.close_window(anchor.id)
    remaining.close.assert_awaited_once()
    original.close.assert_not_called()
    other.close.assert_not_called()
    assert anchor.id not in engine._window_bindings


@pytest.mark.asyncio
async def test_popup_classification_failure_retries_same_session_without_repeated_attach(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(20, "visible")
    engine, anchor = await setup_engine([Page(10), popup])
    actual_attach = engine._attach
    async def attach_with_transient_failure(session):
        await actual_attach(session)
        if session.page is popup:
            session.cdp.send.side_effect = [RuntimeError("CDP temporary failure"), {"windowId": 20}]
    engine._attach = AsyncMock(side_effect=attach_with_transient_failure)
    with pytest.raises(RuntimeError, match="temporary failure"):
        await engine._adopt_popup(anchor, popup)
    pending = engine._pending_popups[popup]
    assert pending.id not in engine.pages
    adopted = await engine._adopt_popup(anchor, popup)
    assert adopted is pending and adopted.windowed
    engine._attach.assert_awaited_once()
    engine.on_popup_page.assert_awaited_once_with(adopted)


@pytest.mark.asyncio
async def test_popup_can_be_classified_after_its_opener_closed(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(20, "visible")
    engine, anchor = await setup_engine([Page(10), popup])
    anchor.page.closed = True
    anchor.cdp.send.side_effect = RuntimeError("Opener target is gone")
    child = await engine._adopt_popup(anchor, popup)
    assert child.windowed
    assert engine.window_binding(child.id).window_id == 20
    engine.on_popup_page.assert_awaited_once_with(child)


@pytest.mark.asyncio
async def test_popup_notification_failure_is_retryable_without_duplicate_tab_or_name(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(20, "visible")
    engine, anchor = await setup_engine([Page(10), popup])
    engine.on_popup_page.side_effect = [RuntimeError("Frontend temporarily unavailable"), None]
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await engine._adopt_popup(anchor, popup)
    child = next(session for session in engine.pages.values() if session.page is popup)
    assert child.id not in engine._popup_announced
    assert await engine._adopt_popup(anchor, popup) is child
    assert len([s for s in engine.pages.values() if s.page is popup]) == 1
    assert engine.on_popup_page.await_count == 2
    assert child.id in engine._popup_announced


@pytest.mark.asyncio
async def test_slow_popup_shell_does_not_block_active_tab_queries(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    popup = Page(20, "visible")
    engine, anchor = await setup_engine([Page(10, "visible"), popup])
    started, finish = asyncio.Event(), asyncio.Event()
    async def notify(child):
        started.set()
        await finish.wait()
    engine.on_popup_page.side_effect = notify
    pending = asyncio.create_task(engine._adopt_popup(anchor, popup))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert await asyncio.wait_for(engine.window_page(anchor.id), timeout=1) is anchor
    again = asyncio.create_task(engine._adopt_popup(anchor, popup))
    await asyncio.sleep(0)
    finish.set()
    first, second = await asyncio.gather(pending, again)
    assert first is second
    engine.on_popup_page.assert_awaited_once()
