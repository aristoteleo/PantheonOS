"""Browser tools accept a desktop Browser WINDOW id where a page id goes.

Agents naturally pass the window they referenced (#app:win-N); the window's
shared tab state names its active page, so the natural guess resolves. An
empty New Tab and an unknown id fail with directions, not a dead end.
"""

import pytest

from pantheon.toolsets.desktop.desktop_session import DesktopSessionStore
from pantheon.toolsets.desktop.toolset import DesktopToolSet


class FakeEngine:
    def __init__(self, pages):
        self.pages = pages

    def latest(self, page_id=""):
        if page_id:
            return self.get(page_id)
        if not self.pages:
            raise KeyError("no browser pages are open")
        return list(self.pages.values())[-1]

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
    with pytest.raises(KeyError, match="browser_open"):
        toolset._resolve_page(engine, win)


def test_unknown_id_lists_open_pages(toolset, store):
    engine = FakeEngine({"pg-9": "P9"})
    with pytest.raises(KeyError, match="pg-9"):
        toolset._resolve_page(engine, "nope")


def test_adopt_takes_over_the_empty_new_tab_slot(toolset, store):
    win = browser_window(store, [None], 0)
    toolset._adopt_page_into_window(win, "pg-7")
    shared = store.session.windows[win]["args"]["shared"]["v"]
    assert shared["pages"] == ["pg-7"]
    assert shared["active"] == 0


def test_adopt_appends_when_every_tab_is_real(toolset, store):
    win = browser_window(store, ["pg-1"], 0)
    toolset._adopt_page_into_window(win, "pg-7")
    shared = store.session.windows[win]["args"]["shared"]["v"]
    assert shared["pages"] == ["pg-1", "pg-7"]
    assert shared["active"] == 1


def test_adopt_refuses_a_non_browser_window(toolset, store):
    win = store.apply("open", {"app_id": "files"})[1]["window_id"]
    with pytest.raises(ValueError, match="not a Browser"):
        toolset._adopt_page_into_window(win, "pg-7")


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

    from pantheon.toolsets.desktop.browser import BrowserEngine

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
    from pantheon.toolsets.desktop.browser import surviving_favicon

    wiki = "https://en.wikipedia.org/static/favicon/wikipedia.ico"
    # Navigating within the site keeps it: no flicker on every click.
    assert surviving_favicon(wiki, "https://en.wikipedia.org/wiki/Osmosis") == wiki
    # Leaving it drops it.
    assert surviving_favicon(wiki, "https://www.google.com/") == ""
    assert surviving_favicon("", "https://www.google.com/") == ""
    # Nonsense in, nothing out — never a stale icon.
    assert surviving_favicon(wiki, "not a url") == ""
