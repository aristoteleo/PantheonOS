"""Native child handles are derived from the exact owned main window."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from pantheon.apps.builtin.desktop.native_targets import window_targets
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.apps.builtin.desktop.desktop_session import DesktopSessionStore


class Window:
    def __init__(self, xid, *, classes=(), transient=None, mapped=2, children=()):
        self.id = xid
        self.classes = classes
        self.transient = transient
        self.mapped = mapped
        self.children = list(children)

    def query_tree(self):
        return SimpleNamespace(children=self.children)

    def get_attributes(self):
        return SimpleNamespace(map_state=self.mapped)

    def get_wm_class(self):
        return self.classes

    def get_wm_transient_for(self):
        return self.transient

    def get_geometry(self):
        return SimpleNamespace(width=800, height=600)


def engine_for(*windows):
    root = Window(1, children=windows)
    display = SimpleNamespace(screen=lambda: SimpleNamespace(root=root))
    return SimpleNamespace(_x_display=lambda: display, _window_name=lambda d, w: f"Window {w.id}")


def test_includes_main_and_transitive_dialogs_but_excludes_foreign_windows():
    main = Window(10, classes=("pantheon-owned", "QuPath"))
    dialog = Window(20, transient=main)
    popup = Window(30, transient=dialog)
    foreign = Window(40, classes=("other-app",))
    foreign_popup = Window(50, transient=foreign)
    unmapped = Window(60, transient=main, mapped=0)
    targets = window_targets(engine_for(main, dialog, popup, foreign, foreign_popup, unmapped), "win-1", "pantheon-owned")
    assert [t['window_id'] for t in targets] == ['win-1', 'win-1::native:20', 'win-1::native:30']
    assert targets[0]['parent_window_id'] is None
    assert targets[2]['parent_window_id'] == 'win-1'
    assert all(t['coordinate_space'] == 'native_window' for t in targets)


def test_wm_class_must_match_exactly_not_by_prefix():
    main = Window(10, classes=('pantheon-owned-suffix',))
    with pytest.raises(RuntimeError, match='unavailable or ambiguous'):
        window_targets(engine_for(main), 'win-1', 'pantheon-owned')


def test_duplicate_main_identity_fails_closed():
    a = Window(10, classes=('pantheon-owned',))
    b = Window(20, classes=('pantheon-owned',))
    with pytest.raises(RuntimeError, match='ambiguous'):
        window_targets(engine_for(a, b), 'win-1', 'pantheon-owned')


def test_transient_cycles_cannot_grant_ownership_or_loop_forever():
    main = Window(10, classes=('pantheon-owned',))
    a, b = Window(20), Window(30)
    a.transient, b.transient = b, a
    targets = window_targets(engine_for(main, a, b), 'win-1', 'pantheon-owned')
    assert [t['xid'] for t in targets] == [10]


def test_unmapped_main_is_not_resolved_to_a_child():
    main = Window(10, classes=('pantheon-owned',), mapped=0)
    dialog = Window(20, transient=main)
    with pytest.raises(RuntimeError, match='unavailable'):
        window_targets(engine_for(main, dialog), 'win-1', 'pantheon-owned')


def test_handle_is_revalidated_after_dialog_owner_changes():
    main = Window(10, classes=('pantheon-owned',))
    foreign = Window(20)
    popup = Window(30, transient=main)
    engine = engine_for(main, foreign, popup)
    assert len(window_targets(engine, 'win-1', 'pantheon-owned')) == 2
    popup.transient = foreign
    assert len(window_targets(engine, 'win-1', 'pantheon-owned')) == 1


@pytest.mark.asyncio
async def test_browser_read_of_invalid_native_child_fails_before_reading_parent(tmp_path, monkeypatch):
    store = DesktopSessionStore(work_dir=tmp_path)
    store.load()
    win = store.apply('open', {'app_id': 'browser'})[1]['window_id']
    tools = DesktopToolSet()
    monkeypatch.setattr(tools, '_desktop', lambda: store)
    monkeypatch.setattr(tools, '_browser_engine', lambda: Mock())
    monkeypatch.setattr(tools, '_resolve_page', Mock(return_value=Mock()))
    monkeypatch.setattr(tools, '_native_target', AsyncMock(side_effect=ValueError('Native child no longer owned')))
    info = AsyncMock(return_value={'url': 'https://example.test', 'title': 'Parent browser'})
    monkeypatch.setattr(tools, '_browser_page_info', info)
    reply = await tools.desktop_read(win + '::native:999999')
    assert reply['success'] is False
    info.assert_not_called()


@pytest.mark.asyncio
async def test_generic_close_on_qupath_uses_normal_native_close(tmp_path, monkeypatch):
    store = DesktopSessionStore(work_dir=tmp_path)
    store.load()
    win = store.apply('open', {'app_id': 'qupath'})[1]['window_id']
    tools = DesktopToolSet()
    monkeypatch.setattr(tools, '_desktop', lambda: store)
    manager = SimpleNamespace(call=AsyncMock(return_value={'close_requested': True, 'running': True}))
    async def call(coroutine):
        return await coroutine
    monkeypatch.setattr(tools, '_browser_engine', lambda: SimpleNamespace(call=call, native_apps=lambda: manager))
    reply = await tools.desktop_call(win, '$close')
    assert reply['success'] is True and reply['result']['close_requested']
    manager.call.assert_awaited_once_with(win, 'close', {})
