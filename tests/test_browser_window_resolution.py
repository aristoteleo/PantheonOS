"""Browser tools accept a desktop Browser WINDOW id where a page id goes.

Agents naturally pass the window they referenced (#app:win-N); the window's
shared tab state names its active page, so the natural guess resolves. An
empty New Tab and an unknown id fail with directions, not a dead end.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pantheon.apps.builtin.desktop.desktop_session import DesktopSessionStore
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


class FakeEngine:
    def __init__(self, pages):
        self.pages = pages

    def latest(self, page_id=""):
        if page_id:
            return self.get(page_id)
        if not self.pages:
            raise KeyError("no browser pages are open")
        return list(self.pages.values())[-1]

    def window_binding(self, token):
        return self.get(token)

    async def current_window_page(self, anchor):
        return anchor

    def get(self, page_id):
        if page_id not in self.pages:
            raise KeyError(f"no such page: {page_id}")
        return self.pages[page_id]


@pytest.fixture
def store(tmp_path):
    s = DesktopSessionStore(work_dir=tmp_path)
    s.load()
    return s


@pytest.fixture
def toolset(monkeypatch, store):
    ts = DesktopToolSet()
    monkeypatch.setattr(ts, "_desktop", lambda: store)
    return ts


def browser_window(store, pages, active):
    win = store.apply("open", {"app_id": "browser"})[1]["window_id"]
    store.apply("set", {"window_id": win, "patch": {
        "args": {"shared": {"by": "vp-1", "v": {"pages": pages, "active": active}}},
    }})
    return win


def test_window_id_resolves_to_its_active_page(toolset, store):
    engine = FakeEngine({"pg-1": "P1", "pg-2": "P2"})
    win = browser_window(store, ["pg-1", "pg-2"], 1)
    assert toolset._resolve_page(engine, win) == "P2"


def test_new_tab_window_explains_the_way_forward(toolset, store):
    engine = FakeEngine({})
    win = browser_window(store, [None], 0)
    with pytest.raises(KeyError, match="no live page"):
        toolset._resolve_page(engine, win)


@pytest.mark.asyncio
async def test_ui_open_requests_stream_before_initial_navigation(monkeypatch, toolset):
    session = SimpleNamespace(id="new-page", width=1280, height=800)

    async def call(coro):
        return await coro

    engine = SimpleNamespace(
        open_page=AsyncMock(return_value=session),
        call=call,
        _xvfb_display=":97",
    )
    monkeypatch.setattr(toolset, "_browser_engine", lambda: engine)
    monkeypatch.setattr(toolset, "_browser_page_info", AsyncMock(return_value={
        "page_id": session.id, "url": "about:blank", "title": "",
    }))
    monkeypatch.setattr(toolset, "_prewarm_browser", Mock())

    result = await toolset.browser_ui_page(url="https://example.com/slow")

    assert result["success"]
    engine.open_page.assert_awaited_once_with(
        "https://example.com/slow", wait_for_load=False,
    )


def test_unknown_id_lists_open_pages(toolset, store):
    engine = FakeEngine({"pg-9": "P9"})
    with pytest.raises(KeyError, match="pg-9"):
        toolset._resolve_page(engine, "nope")


def test_volume_caches_are_evicted_but_the_login_state_is_not():
    """The profile is on a volume so logins survive; caches must not be.

    A real sandbox's profile measured 250 MB, of which 232 MB was Cache and
    Code Cache — read and written over the network on every navigation.
    Opening a page there took nine seconds against under one where the
    profile sat on local disk. Evicting the caches must not touch the
    things the volume is FOR.
    """
    import tempfile
    from pathlib import Path

    from pantheon.apps.builtin.desktop.browser import BrowserEngine

    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "browser-profile"
        (profile / "Default" / "Cache" / "js").mkdir(parents=True)
        (profile / "Default" / "Cache" / "js" / "blob").write_text("x" * 10)
        (profile / "Default" / "Code Cache").mkdir(parents=True)
        (profile / "ShaderCache").mkdir(parents=True)
        (profile / "Default" / "Local Storage").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_text("session")
        (profile / "Default" / "Preferences").write_text("{}")

        BrowserEngine._evict_volume_caches(profile)

        assert not (profile / "Default" / "Cache").exists()
        assert not (profile / "Default" / "Code Cache").exists()
        assert not (profile / "ShaderCache").exists()
        assert (profile / "Default" / "Cookies").read_text() == "session"
        assert (profile / "Default" / "Local Storage").is_dir()
        assert (profile / "Default" / "Preferences").exists()


def test_an_icon_belongs_to_a_site_not_to_a_tab():
    """A tab titled "Google" must not wear Wikipedia's W.

    The favicon used to survive until the next page finished loading and
    an evaluate returned its icon — a second or two of the previous site's
    identity on a tab that had already moved on.
    """
    from pantheon.apps.builtin.desktop.browser import surviving_favicon

    wiki = "https://en.wikipedia.org/static/favicon/wikipedia.ico"
    # Navigating within the site keeps it: no flicker on every click.
    assert surviving_favicon(wiki, "https://en.wikipedia.org/wiki/Osmosis") == wiki
    # Leaving it drops it.
    assert surviving_favicon(wiki, "https://www.google.com/") == ""
    assert surviving_favicon("", "https://www.google.com/") == ""
    # Nonsense in, nothing out — never a stale icon.
    assert surviving_favicon(wiki, "not a url") == ""


def test_a_gesture_becomes_the_events_a_hand_would_produce():
    """One action in, the whole gesture out.

    The engine replays what a hand produces — move, down, up — because that
    is what the viewer sends. An agent asks for a click. Expanding that at
    each call site is how a click ends up missing its mouse-up in one place
    and not another.
    """
    from pantheon.apps.builtin.desktop.browser import input_events

    click = input_events([{"t": "click", "x": 40, "y": 90}])
    assert [e["t"] for e in click] == ["move", "down", "up"]
    assert all(e["x"] == 40 and e["y"] == 90 for e in click)

    assert [e["t"] for e in input_events([{"t": "key", "key": "Enter"}])] \
        == ["keydown", "keyup"]

    drag = input_events([{"t": "drag", "x": 5, "y": 5, "to_x": 80, "to_y": 60}])
    assert [e["t"] for e in drag] == ["move", "down", "move", "up"]
    assert (drag[2]["x"], drag[2]["y"]) == (80, 60), "it must end where asked"

    right = input_events([{"t": "rightclick", "x": 1, "y": 2}])
    assert right[1]["button"] == 2
    assert input_events([{"t": "dblclick", "x": 1, "y": 2}])[1]["clicks"] == 2

    # Order is preserved across a whole sequence: type into a field, submit.
    seq = input_events([{"t": "click", "x": 10, "y": 10},
                        {"t": "text", "text": "hi"},
                        {"t": "key", "key": "Enter"}])
    assert [e["t"] for e in seq] == [
        "move", "down", "up", "text", "keydown", "keyup"]


def test_an_action_that_cannot_be_carried_out_says_so():
    """Silence is the wrong answer: a click with no coordinates is a bug
    in the caller, and swallowing it looks like a page that ignored it."""
    import pytest

    from pantheon.apps.builtin.desktop.browser import input_events

    for bad in ({"t": "click"}, {"t": "drag", "x": 1, "y": 1},
                {"t": "key"}, {"t": "teleport", "x": 1, "y": 1}):
        with pytest.raises(ValueError):
            input_events([bad])


def test_seamless_page_binding_wins_over_legacy_state(toolset, store):
    win = browser_window(store, ["stale-page"], 0)
    store.apply("set", {"window_id": win, "patch": {"args": {"shared": {"v": {"page": "visible-page"}}}}})
    engine = FakeEngine({"stale-page": "WRONG", "visible-page": "VISIBLE"})
    assert toolset._resolve_page(engine, win) == "VISIBLE"


def test_window_resolution_refreshes_shared_document(toolset, store, tmp_path):
    win = browser_window(store, ["stale-page"], 0)
    other = DesktopSessionStore(work_dir=tmp_path)
    other.load()
    other.apply("set", {"window_id": win, "patch": {"args": {"shared": {"v": {"page": "visible-page"}}}}})
    assert toolset._resolve_page(FakeEngine({"visible-page": "VISIBLE"}), win) == "VISIBLE"


@pytest.mark.asyncio
async def test_open_in_explicit_window_reuses_existing_native_page(toolset, store, monkeypatch):
    page = SimpleNamespace(id="pg-shared", url="https://example.com", title=AsyncMock(return_value="Shared"))
    engine = FakeEngine({page.id: page})
    engine.open_page = AsyncMock()
    engine.navigate = AsyncMock()
    async def call(coro): return await coro
    engine.call = call
    monkeypatch.setattr(toolset, "_browser_engine", lambda: engine)
    win = browser_window(store, [], 0)
    store.apply("set", {"window_id": win, "patch": {"args": {"shared": {"v": {"page": page.id}}}}})
    result = await toolset.browser_open("https://example.com/search", window_id=win)
    assert result["success"] and result["reused"]
    assert result["page_id"] == page.id and result["window_id"] == win
    engine.navigate.assert_awaited_once_with(page.id, "goto", "https://example.com/search")
    engine.open_page.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_explicit_window_never_creates_replacement(toolset, monkeypatch):
    engine = FakeEngine({"other": "UNRELATED"})
    engine.open_page = AsyncMock()
    monkeypatch.setattr(toolset, "_browser_engine", lambda: engine)
    reply = await toolset.browser_open("https://example.com", window_id="win-missing")
    assert not reply["success"]
    engine.open_page.assert_not_called()


@pytest.mark.asyncio
async def test_close_window_resolves_its_current_tab_without_closing_other_pages(toolset, store, monkeypatch):
    anchor, active = SimpleNamespace(id="pg-anchor"), SimpleNamespace(id="pg-active")
    engine = FakeEngine({anchor.id: anchor, active.id: active})
    engine.current_window_page = AsyncMock(return_value=active)
    engine.close_page = AsyncMock()
    async def call(coroutine): return await coroutine
    engine.call = call
    monkeypatch.setattr(toolset, "_browser_engine", lambda: engine)
    window = browser_window(store, [anchor.id], 0)
    result = await toolset.browser_close(window)
    assert result == {"success": True, "page_id": active.id}
    engine.close_page.assert_awaited_once_with(active.id)


@pytest.mark.asyncio
async def test_close_exact_page_id_does_not_resolve_window_active_tab(toolset, monkeypatch):
    page = SimpleNamespace(id="pg-exact")
    engine = FakeEngine({page.id: page})
    engine.current_window_page = AsyncMock()
    engine.close_page = AsyncMock()
    async def call(coroutine): return await coroutine
    engine.call = call
    monkeypatch.setattr(toolset, "_browser_engine", lambda: engine)
    result = await toolset.browser_close(page.id)
    assert result["success"] and result["page_id"] == page.id
    engine.close_page.assert_awaited_once_with(page.id)
    engine.current_window_page.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["win-missing", "pg-closed", "", " "])
async def test_close_unknown_or_empty_target_is_not_successful(toolset, monkeypatch, target):
    engine = FakeEngine({"other": SimpleNamespace(id="other")})
    engine.close_page = AsyncMock()
    monkeypatch.setattr(toolset, "_browser_engine", lambda: engine)
    result = await toolset.browser_close(target)
    assert result["success"] is False
    engine.close_page.assert_not_called()


@pytest.mark.asyncio
async def test_retried_native_popup_creates_one_durable_host(toolset, store, monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(toolset, "_publish_desktop", publish)
    child = SimpleNamespace(id="stable-popup-binding")
    await toolset._show_popup_page(child)
    await toolset._show_popup_page(child)
    assert len(store.session.windows) == 1
    window = next(iter(store.session.windows.values()))
    assert window["args"]["shared"]["v"]["page"] == child.id
    publish.assert_awaited_once()
