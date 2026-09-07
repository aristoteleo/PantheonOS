"""Native Browser windows must not turn into tabs after the keeper closes."""
import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine, INTERNAL_KEEPER_CLASS


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


@pytest.mark.asyncio
async def test_concurrent_ensure_waits_for_context_initialization():
    engine = BrowserEngine()
    engine._xvfb_display = ":97"
    context_created = asyncio.Event()
    finish_initializing = asyncio.Event()

    async def launch():
        engine._context = SimpleNamespace(pages=[object()])
        context_created.set()
        await finish_initializing.wait()

    engine._launch_browser = AsyncMock(side_effect=launch)
    first = asyncio.create_task(engine._ensure_browser())
    await context_created.wait()
    second = asyncio.create_task(engine._ensure_browser())
    await asyncio.sleep(0)
    assert not second.done(), "a partially initialized context escaped the launch lock"
    finish_initializing.set()
    await asyncio.gather(first, second)
    engine._launch_browser.assert_awaited_once()


@pytest.mark.asyncio
async def test_seamless_keeper_is_named_without_cdp_window_bounds(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    restored = SimpleNamespace(url="https://example.com/restored", close=AsyncMock())
    keeper = SimpleNamespace(url="about:blank", close=AsyncMock())
    engine = BrowserEngine()
    engine._context = SimpleNamespace(
        pages=[restored, keeper], new_cdp_session=AsyncMock(),
    )
    engine._name_native_window = AsyncMock(return_value=True)

    await engine._park_keeper()

    engine._name_native_window.assert_awaited_once_with(
        keeper, "pantheon-window-keeper", INTERNAL_KEEPER_CLASS,
    )
    engine._context.new_cdp_session.assert_not_called()
    restored.close.assert_not_called()
    keeper.close.assert_not_called()


@pytest.mark.asyncio
async def test_real_startup_page_is_not_misclassified_as_keeper(monkeypatch):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    restored = SimpleNamespace(url="https://example.com/restored", close=AsyncMock())
    engine = BrowserEngine()
    engine._context = SimpleNamespace(pages=[restored], new_cdp_session=AsyncMock())
    engine._name_native_window = AsyncMock()

    await engine._park_keeper()

    engine._name_native_window.assert_not_called()
    engine._context.new_cdp_session.assert_not_called()
    restored.close.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_for_load", [False, True])
async def test_initial_navigation_runs_once_after_window_identity(monkeypatch, wait_for_load):
    monkeypatch.setenv("BROWSER_XPRA_MODE", "seamless")
    engine = BrowserEngine()
    navigation_started = asyncio.Event()
    finish_navigation = asyncio.Event()
    order = []

    async def goto(url, **kwargs):
        order.append("navigate")
        navigation_started.set()
        assert url == "https://example.com/slow"
        assert kwargs == {"wait_until": "domcontentloaded", "timeout": 30_000}
        await finish_navigation.wait()

    async def name(_session):
        order.append("name")
        return True

    page = SimpleNamespace(goto=AsyncMock(side_effect=goto))
    engine._ensure_browser = AsyncMock()
    engine._open_windowed = AsyncMock(return_value=page)
    engine._attach = AsyncMock()
    engine._name_window = AsyncMock(side_effect=name)
    engine.reshape = AsyncMock()

    opening = asyncio.create_task(engine.open_page(
        "https://example.com/slow", wait_for_load=wait_for_load,
    ))
    await asyncio.wait_for(navigation_started.wait(), timeout=1)
    assert order == ["name", "navigate"]
    engine._open_windowed.assert_awaited_once_with("about:blank")
    if wait_for_load:
        assert not opening.done(), "agent open must still wait for navigation"
    else:
        session = await asyncio.wait_for(asyncio.shield(opening), timeout=1)
        assert session.loading
        assert session.id in engine.pages
    finish_navigation.set()
    session = await opening
    await session.navigation_task
    page.goto.assert_awaited_once()


def test_concurrent_native_window_stamping_never_shares_an_xlib_socket(monkeypatch):
    engine = BrowserEngine()
    thread_state = threading.local()
    connections = []
    barrier = threading.Barrier(2, timeout=5)

    class Display:
        def __init__(self, _name):
            self.owner = threading.get_ident()
            self.window_class = None
            self.token = getattr(thread_state, "token", "")
            self.child = SimpleNamespace(
                get_full_property=lambda *_: SimpleNamespace(value=self.token.encode()),
                set_wm_class=self.set_class,
            )
            connections.append(self)

        def check_owner(self):
            assert threading.get_ident() == self.owner, "shared Xlib socket"

        def screen(self):
            self.check_owner()
            return SimpleNamespace(root=SimpleNamespace(
                query_tree=lambda: SimpleNamespace(children=[self.child]),
            ))

        def intern_atom(self, _name):
            self.check_owner()
            return 1

        def set_class(self, instance, cls):
            self.check_owner()
            self.window_class = (instance, cls)

        def sync(self):
            self.check_owner()

    monkeypatch.setitem(sys.modules, "Xlib", SimpleNamespace(
        display=SimpleNamespace(Display=Display),
    ))
    main_connection = engine._x_display()

    def stamp(page_id):
        thread_state.token = f"pantheon-window-{page_id}"
        barrier.wait()
        assert engine._stamp_class(thread_state.token, page_id)
        connection = engine._x_display()
        assert engine._x_display() is connection  # same worker reuses its socket
        assert connection.window_class == (f"pantheon-page-{page_id}", "Chromium-browser")
        return connection

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(stamp, "first")
        second = pool.submit(stamp, "second")
        assert first.result(timeout=10) is not second.result(timeout=10)
    assert len(connections) == 3
    assert engine._x_display() is main_connection


def test_reset_xlib_connection_leaves_other_threads_connected(monkeypatch):
    engine = BrowserEngine()
    barrier = threading.Barrier(2, timeout=5)
    monkeypatch.setitem(sys.modules, "Xlib", SimpleNamespace(
        display=SimpleNamespace(Display=lambda _: SimpleNamespace(close=Mock())),
    ))
    main_connection = engine._x_display()

    def use_connection(reset):
        before = engine._x_display()
        barrier.wait()
        if reset:
            engine._reset_x_display()
        barrier.wait()
        return before, engine._x_display()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reset = pool.submit(use_connection, True)
        keep = pool.submit(use_connection, False)
        old, replacement = reset.result(timeout=10)
        kept, reused = keep.result(timeout=10)
    assert old is not replacement
    old.close.assert_called_once()
    assert kept is reused
    kept.close.assert_not_called()
    assert engine._x_display() is main_connection
    main_connection.close.assert_not_called()
