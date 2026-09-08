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
        return SimpleNamespace(map_state=self.mapped, override_redirect=False)

    def get_wm_class(self):
        return self.classes

    def get_wm_transient_for(self):
        return self.transient

    def get_wm_hints(self):
        return None

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


def chrome_menu_fixture():
    main = Window(0x400007, classes=('pantheon-main',))
    other = Window(0x40000b, classes=('pantheon-other',))
    menu = Window(0x400024)
    engine = engine_for(main, other, menu)
    display = engine._x_display()
    root = display.screen().root
    atoms = {'_NET_WM_PID': 1, '_NET_WM_WINDOW_TYPE': 2, '_NET_ACTIVE_WINDOW': 3,
             '_NET_WM_WINDOW_TYPE_MENU': 4, '_NET_WM_WINDOW_TYPE_POPUP_MENU': 5,
             '_NET_WM_WINDOW_TYPE_DROPDOWN_MENU': 6, '_NET_WM_WINDOW_TYPE_COMBO': 7,
             '_NET_WM_WINDOW_TYPE_TOOLTIP': 8}
    display.intern_atom = lambda name: atoms[name]
    display.res_query_clients = lambda: SimpleNamespace(clients=[
        SimpleNamespace(resource_base=0x400000, resource_mask=0x1fffff),
        SimpleNamespace(resource_base=0x600000, resource_mask=0x1fffff)])
    display.focus = main
    display.get_input_focus = lambda: SimpleNamespace(focus=display.focus)
    root.active = main.id
    root.get_full_property = lambda atom, _: SimpleNamespace(value=[root.active]) if atom == 3 else None
    root.translate_coords = lambda window, x, y: SimpleNamespace(x=window.x + x, y=window.y + y)
    for window in [main, other, menu]:
        window.parent = root
        window.x, window.y = (1000, 0) if window is other else (100, 100)
        window.pid = 290
        window.kind = 4 if window is menu else 0
        window.override = window is menu
        window.get_attributes = lambda window=window: SimpleNamespace(map_state=window.mapped, override_redirect=window.override)
        window.query_tree = lambda window=window: SimpleNamespace(parent=root, children=window.children)
        def property_value(atom, _, window=window):
            return SimpleNamespace(value=[window.pid if atom == 1 else window.kind]) if atom in (1, 2) else None
        window.get_full_property = property_value
    root.query_tree = lambda: SimpleNamespace(parent=root, children=[main, other, menu])
    return engine, display, main, other, menu


def test_chrome_menu_without_transient_uses_exact_focused_window_and_x_client():
    engine, display, main, other, menu = chrome_menu_fixture()
    targets = window_targets(engine, 'win-1', 'pantheon-main')
    assert [target['xid'] for target in targets] == [main.id, menu.id]
    assert targets[1]['_focus_parent_xids'] == [main.id]
    assert [target['xid'] for target in window_targets(engine, 'win-2', 'pantheon-other')] == [other.id]
    # Focusing an explicitly addressed menu keeps it discoverable, without
    # reassigning it to the other browser window sharing the same process.
    display.focus = menu
    assert [target['xid'] for target in window_targets(engine, 'win-1', 'pantheon-main')] == [main.id, menu.id]
    display.focus = other
    assert [target['xid'] for target in window_targets(engine, 'win-1', 'pantheon-main')] == [main.id]


@pytest.mark.parametrize('non_focusing', [True, False])
def test_transient_popup_keeps_owner_focus_only_when_input_hint_disallows_focus(non_focusing):
    from Xlib import Xutil

    engine, _, main, _, menu = chrome_menu_fixture()
    menu.transient = main
    menu.kind = 0  # JavaFX advertises NORMAL even for its context menus.
    menu.get_wm_hints = lambda: SimpleNamespace(flags=Xutil.InputHint, input=not non_focusing)
    targets = window_targets(engine, 'win-1', 'pantheon-main')
    target = next(t for t in targets if t['xid'] == menu.id)
    assert target.get('_focus_parent_xids', []) == ([main.id] if non_focusing else [])


def test_regular_dialog_never_gets_parent_focus_even_with_no_input_hint():
    from Xlib import Xutil

    engine, _, main, _, dialog = chrome_menu_fixture()
    dialog.transient = main
    dialog.override = False
    dialog.get_wm_hints = lambda: SimpleNamespace(flags=Xutil.InputHint, input=False)
    target = next(t for t in window_targets(engine, 'win-1', 'pantheon-main') if t['xid'] == dialog.id)
    assert '_focus_parent_xids' not in target


@pytest.mark.parametrize('mismatch', ['pid', 'client', 'tooltip', 'normal', 'geometry', 'foreign_transient', 'missing_xres'])
def test_chrome_menu_fallback_requires_all_ownership_evidence(mismatch):
    engine, display, main, other, menu = chrome_menu_fixture()
    if mismatch == 'pid': menu.pid = 999
    elif mismatch == 'client': menu.id = 0x600007
    elif mismatch == 'tooltip': menu.kind = 8
    elif mismatch == 'normal': menu.override = False
    elif mismatch == 'geometry': menu.x = 5000
    elif mismatch == 'foreign_transient': menu.transient = other
    elif mismatch == 'missing_xres': display.res_query_clients = Mock(side_effect=RuntimeError('XRes missing'))
    assert [target['xid'] for target in window_targets(engine, 'win-1', 'pantheon-main')] == [main.id]


def test_native_target_public_view_omits_focus_proof_and_xids():
    target = {'window_id': 'win-1::native:42', 'xid': 42, '_focus_parent_xids': [12]}
    assert DesktopToolSet._public_native_targets([target]) == [{'window_id': 'win-1::native:42'}]


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
