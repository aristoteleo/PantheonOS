"""A shared Atrium shell must not create a native page per viewport."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from pantheon.apps.builtin.desktop.desktop_session import DesktopSessionStore, browser_page_reference
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


class Engine:
    def __init__(self):
        self.pages = {}
        self.created = []
        self.closed = []
        self.gate = None
        self.fail = False
        self._xvfb_display = ":97"

    async def call(self, operation):
        return await operation

    async def open_page(self, url, *, wait_for_load):
        assert wait_for_load is False
        if self.fail:
            raise RuntimeError("injected browser startup failure")
        page = SimpleNamespace(id=f"page-{len(self.created) + 1}", url=url,
                               title=AsyncMock(return_value="Audit"), width=800, height=600)
        self.created.append(page.id)
        self.pages[page.id] = page
        if self.gate:
            await self.gate.wait()
        return page

    async def window_page(self, page_id, **_):
        return self.window_binding(page_id)

    def window_binding(self, page_id):
        if page_id not in self.pages:
            raise KeyError(f"no such page: {page_id}")
        return self.pages[page_id]

    def latest(self, page_id=""):
        return self.window_binding(page_id) if page_id else list(self.pages.values())[-1]

    async def close_page(self, page_id):
        self.closed.append(page_id)
        del self.pages[page_id]


@pytest.fixture
def setup(tmp_path):
    store = DesktopSessionStore(tmp_path)
    engine = Engine()
    desktop = DesktopToolSet()
    desktop._desktop = lambda: store
    desktop._browser_engine = lambda: engine
    desktop._prewarm_browser = Mock()
    desktop._publish_desktop = AsyncMock(return_value=True)
    window = store.apply("open", {"app_id": "browser"})[1]["window_id"]
    return desktop, store, engine, window


def binding(store, window):
    return store.current()["windows"][window]["args"]["browser_binding"]


@pytest.mark.asyncio
async def test_two_viewports_concurrently_ensure_only_one_native_page(setup):
    desktop, store, engine, window = setup
    engine.gate = asyncio.Event()
    first = asyncio.create_task(desktop.browser_ui_page(window_id=window, url="https://same.example"))
    await asyncio.sleep(0)
    second = asyncio.create_task(desktop.browser_ui_page(window_id=window, url="https://same.example"))
    await asyncio.sleep(0)
    assert engine.created == ["page-1"]
    engine.gate.set()
    one, two = await asyncio.gather(first, second)
    assert one["success"] and two["success"]
    assert one["page_id"] == two["page_id"] == binding(store, window)["page_id"]
    assert engine.created == ["page-1"] and engine.closed == []


@pytest.mark.asyncio
async def test_same_url_in_different_shells_is_not_deduplicated(setup):
    desktop, store, engine, first = setup
    second = store.apply("open", {"app_id": "browser"})[1]["window_id"]
    one, two = await asyncio.gather(*[
        desktop.browser_ui_page(window_id=window, url="https://same.example")
        for window in (first, second)])
    assert one["success"] and two["success"]
    assert one["page_id"] != two["page_id"]
    assert len(engine.created) == 2


@pytest.mark.asyncio
async def test_binding_is_durable_before_reply_and_refresh_reuses_it(setup):
    desktop, store, engine, window = setup
    published = asyncio.Event()
    finish = asyncio.Event()

    async def publish(_event):
        published.set()
        await finish.wait()
        return True

    desktop._publish_desktop = publish
    first = asyncio.create_task(desktop.browser_ui_page(window_id=window, url="https://audit.example"))
    await published.wait()
    # Simulate HMR/reload before the first viewport receives its RPC answer.
    assert binding(DesktopSessionStore(store._work_dir), window)["page_id"] == "page-1"
    finish.set()
    await first
    refreshed = await desktop.browser_ui_page(window_id=window, url="https://ignored.example")
    assert refreshed["page_id"] == "page-1"
    assert refreshed["url"] == "https://audit.example"
    assert engine.created == ["page-1"]


@pytest.mark.asyncio
async def test_explicit_new_page_uses_a_separate_replayable_operation(setup):
    desktop, store, engine, window = setup
    first = await desktop.browser_ui_page(window_id=window)
    args = {"window_id": window, "operation_id": "explicit-new", "expected_page_id": first["page_id"]}
    one, two = await asyncio.gather(desktop.browser_ui_page(**args), desktop.browser_ui_page(**args))
    assert one["success"] and two["success"]
    assert one["page_id"] == two["page_id"] == "page-2"
    assert engine.created == ["page-1", "page-2"] and engine.closed == []
    assert desktop._resolve_page(engine, window).id == "page-2"
    conflict = await desktop.browser_ui_page(window_id=window, operation_id="late-new", expected_page_id="page-1")
    assert not conflict["success"] and "binding changed" in conflict["error"]
    assert engine.created == ["page-1", "page-2"]
    third = await desktop.browser_ui_page(window_id=window, operation_id="next-new", expected_page_id="page-2")
    assert third["success"] and third["page_id"] == "page-3"


@pytest.mark.asyncio
async def test_failed_creation_can_retry_without_committing_a_binding(setup):
    desktop, store, engine, window = setup
    engine.fail = True
    failed = await desktop.browser_ui_page(window_id=window)
    assert not failed["success"] and "startup failure" in failed["error"]
    assert not browser_page_reference(store.current()["windows"][window])
    engine.fail = False
    assert (await desktop.browser_ui_page(window_id=window))["success"]
    assert engine.created == ["page-1"]


@pytest.mark.asyncio
async def test_closed_shell_is_not_revived_by_a_late_creation(setup):
    desktop, store, engine, window = setup
    engine.gate = asyncio.Event()
    create = asyncio.create_task(desktop.browser_ui_page(window_id=window))
    await asyncio.sleep(0)
    store.apply("close", {"window_id": window})
    engine.gate.set()
    result = await create
    assert not result["success"]
    assert window not in store.current()["windows"]
    assert engine.pages == {} and engine.closed == ["page-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_superseded_creation_cleans_only_its_own_page(setup, explicit):
    desktop, store, engine, window = setup
    engine.gate = asyncio.Event()
    create = asyncio.create_task(desktop.browser_ui_page(
        window_id=window, operation_id="explicit" if explicit else ""))
    await asyncio.sleep(0)
    winner = SimpleNamespace(id="another-owned-page", url="https://winner.example",
                             title=AsyncMock(return_value="Winner"), width=800, height=600)
    engine.pages[winner.id] = winner
    store.apply("bind_browser", {"window_id": window, "expected_page_id": "", "page_id": winner.id,
                                  "operation_id": "other-operation", "url": winner.url})
    engine.gate.set()
    result = await create
    assert engine.closed == ["page-1"]
    assert list(engine.pages) == [winner.id]
    assert binding(store, window)["page_id"] == winner.id
    if explicit:
        assert not result["success"] and "binding changed" in result["error"]
    else:
        assert result["success"] and result["page_id"] == winner.id


@pytest.mark.asyncio
async def test_ensure_does_not_recreate_a_missing_bound_page(setup):
    desktop, store, engine, window = setup
    store.apply("bind_browser", {"window_id": window, "page_id": "missing", "operation_id": "old"})
    result = await desktop.browser_ui_page(window_id=window)
    assert not result["success"] and "no such page: missing" in result["error"]
    assert engine.created == []
    # An explicit, conditional recovery creates a new page in this same shell.
    result = await desktop.browser_ui_page(window_id=window, operation_id="recover", expected_page_id="missing")
    assert result["success"] and binding(store, window)["page_id"] == result["page_id"]


@pytest.mark.asyncio
async def test_old_shared_echo_does_not_replace_authoritative_binding(setup):
    desktop, store, engine, window = setup
    created = await desktop.browser_ui_page(window_id=window)
    store.apply("set", {"window_id": window, "patch": {
        "args": {"shared": {"by": "old-viewport", "v": {"page": "stale-page"}}}}})
    assert desktop._resolve_page(engine, window).id == created["page_id"]
    assert (await desktop.browser_ui_page(window_id=window))["page_id"] == created["page_id"]


@pytest.mark.asyncio
async def test_non_browser_shell_cannot_be_bound(setup):
    desktop, store, engine, _ = setup
    window = store.apply("open", {"app_id": "files"})[1]["window_id"]
    result = await desktop.browser_ui_page(window_id=window)
    assert not result["success"] and "not a Browser" in result["error"]
    assert engine.created == []
