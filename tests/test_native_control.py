"""Native input is confined to an owned window and releases keys on failure."""
import asyncio
import base64
import io
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from PIL import Image
from Xlib import X
from Xlib.ext import xtest

from pantheon.apps.builtin.desktop.native_control import NativeWindowController


class Window:
    def __init__(self, xid, parent=None, width=100, height=80, x=40, y=60):
        self.id = xid
        self.parent = parent
        self.child = 0
        self.transient = None
        self.classes = ()
        self.pointer_mask = 0
        self.geometry = NS(width=width, height=height, depth=24)
        self.x, self.y = x, y
        self.attrs = NS(map_state=X.IsViewable, visual=21)
        self.pixels = None

    def get_attributes(self):
        return self.attrs

    def get_geometry(self):
        return self.geometry

    def translate_coords(self, source, x, y):
        if source.id == 1:
            return NS(x=x - self.x, y=y - self.y, child=self.child)
        assert self.id == 1, "Translation must target the root, not the client"
        return NS(x=source.x + x, y=source.y + y)

    def query_tree(self):
        return NS(parent=self.parent)

    def query_pointer(self):
        return NS(child=self.child, mask=self.pointer_mask)

    def get_wm_transient_for(self):
        return self.transient

    def get_wm_class(self):
        return self.classes

    def get_image(self, x, y, width, height, format, mask):
        assert (x, y, width, height, format) == (0, 0, self.geometry.width, self.geometry.height, X.ZPixmap)
        return NS(data=self.pixels)


class Display:
    def __init__(self):
        self.root = Window(1, width=1920, height=1080, x=0, y=0)
        self.window = Window(5, self.root)
        self.other = Window(6, self.root)
        self.widget = Window(7, self.window)
        self.root.child = self.window
        self.window.child = self.widget
        self.focus = self.widget
        self.keys = {ord("a"): [(38, 0)], ord("A"): [(38, 1)], ord("s"): [(39, 0)],
                     0xFFE1: [(50, 0)], 0xFFE3: [(37, 0)], 0xFF0D: [(36, 0)]}
        self.events = []
        self.maps = []
        self.keymap = [(0,) * 4] * 248
        self.pressed = [0] * 32
        self.display = NS(info=NS(image_byte_order=X.LSBFirst, min_keycode=8, max_keycode=255,
                                 pixmap_formats=[NS(depth=24, bits_per_pixel=32, scanline_pad=32)]))
        self.visual = NS(visual_id=21, red_mask=0xFF0000, green_mask=0xFF00, blue_mask=0xFF)

    def screen(self):
        return NS(root=self.root, allowed_depths=[NS(visuals=[self.visual])])

    def has_extension(self, name):
        return False

    def create_resource_object(self, type, xid):
        return {1: self.root, 5: self.window, 6: self.other, 7: self.widget}[xid]

    def get_input_focus(self):
        return NS(focus=self.focus)

    def set_input_focus(self, window, revert, time):
        self.focus = window

    def sync(self):
        pass

    def keysym_to_keycodes(self, keysym):
        return self.keys.get(keysym, [])

    def query_keymap(self):
        return self.pressed

    def get_modifier_mapping(self):
        return [[50], [37]]

    def get_keyboard_mapping(self, first, count):
        assert first == 8 and count == 248
        return self.keymap

    def change_keyboard_mapping(self, first, keysyms):
        self.maps.append((first, keysyms))


@pytest.fixture
def native(monkeypatch):
    d = Display()
    engine = NS(_x_display=lambda: d)
    controller = NativeWindowController(engine)

    def fake_input(display, kind, detail=0, **kwargs):
        assert display is d
        d.events.append((kind, detail, kwargs))

    monkeypatch.setattr(xtest, "fake_input", fake_input)
    monkeypatch.setattr(controller, '_ping_target', lambda display, xid: display.create_resource_object('window', xid))
    monkeypatch.setattr(controller, '_wait_client', lambda display, target: None)
    return controller, d


@pytest.mark.asyncio
async def test_click_translates_window_pixels_and_delivers_right_button(native):
    controller, d = native
    result = await controller.act(5, [{"type": "rightclick", "x": 11, "y": 12}])
    assert result == {"success": True, "completed": 1, "total": 1}
    assert d.events == [(X.MotionNotify, 0, {"x": 51, "y": 72}),
                        (X.ButtonPress, 3, {}), (X.ButtonRelease, 3, {})]
    assert d.focus is d.widget  # Preserve native widget focus.


@pytest.mark.asyncio
async def test_invalid_later_action_rejects_whole_batch_before_click(native):
    controller, d = native
    with pytest.raises(ValueError, match="Unknown"):
        await controller.act(5, [{"type": "click", "x": 2, "y": 3}, {"type": "execute", "code": "anything"}])
    assert d.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize('as_child', [False, True])
async def test_javafx_non_bmp_rejects_whole_batch_before_input_or_focus(native, as_child):
    controller, d = native
    d.window.classes = ('pantheon-native-qupath-owned', 'QuPath')
    target = d.window
    if as_child:
        target = d.other
        target.transient = d.window
    original_focus = d.focus
    with pytest.raises(ValueError, match='No actions in this batch were sent'):
        await controller.act(target.id, [
            {'type': 'click', 'x': 10, 'y': 10}, {'type': 'text', 'text': '细胞 🧬'},
        ], allowed_xids={d.window.id, d.other.id})
    assert not d.events and not d.maps and d.focus is original_focus


@pytest.mark.asyncio
async def test_other_native_clients_retain_supplementary_unicode_support(native):
    controller, d = native
    d.window.classes = ('pantheon-page-owned', 'Google-chrome')
    result = await controller.act(d.window.id, [{'type': 'text', 'text': '🧬'}])
    assert result['success'] is True and result['completed'] == 1
    assert d.maps[0][1][0][0] == 0x0101F9EC


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["Ctrl++", "Ctrl+", "a+s", "Ctr+s", "UnknownKey", "Ctrl+UnknownKey"])
async def test_invalid_later_key_rejects_whole_batch_before_click(native, key):
    controller, d = native
    original_focus = d.focus
    with pytest.raises(ValueError, match="Invalid key chord"):
        await controller.act(5, [{"type": "click", "x": 2, "y": 3}, {"type": "key", "key": key}])
    assert d.events == [] and d.focus is original_focus


@pytest.mark.asyncio
async def test_refuses_full_desktop_capture_and_input(native):
    controller, d = native
    with pytest.raises(ValueError, match="full desktop"):
        await controller.screenshot(1)
    result = await controller.act(1, [{"type": "text", "text": "a"}])
    assert not result["success"]
    assert d.events == []


@pytest.mark.asyncio
async def test_allowlist_cannot_be_bypassed(native):
    controller, d = native
    with pytest.raises(ValueError, match="owned window"):
        await controller.act(6, [{"type": "key", "key": "Return"}], allowed_xids={5})
    with pytest.raises(ValueError, match="owned window"):
        await controller.screenshot(6, allowed_xids={5})
    assert d.events == []


@pytest.mark.asyncio
async def test_click_obscured_by_another_app_is_not_delivered(native):
    controller, d = native
    d.root.child = d.other
    result = await controller.act(5, [{"type": "click", "x": 10, "y": 10}])
    assert not result["success"] and result["completed"] == 0
    assert "another window" in result["error"]
    assert d.events == []


@pytest.mark.asyncio
async def test_main_window_key_refuses_focused_owned_dialog(native):
    controller, d = native
    d.focus = d.other
    d.other.transient = d.window
    result = await controller.act(5, [{"type": "key", "key": "Return"}], allowed_xids={5, 6})
    assert not result["success"] and "native child window_id" in result["error"]
    assert d.focus is d.other
    assert d.events == []


@pytest.mark.asyncio
async def test_main_window_click_refuses_owned_dialog_overlay_without_moving(native):
    controller, d = native
    d.other.transient = d.window
    d.root.child = d.other
    result = await controller.act(5, [{"type": "click", "x": 10, "y": 10}], allowed_xids={5, 6})
    assert not result["success"] and "native child window_id" in result["error"]
    assert d.events == []


@pytest.mark.asyncio
async def test_explicit_child_can_receive_key_from_its_parent_focus(native):
    controller, d = native
    d.other.transient = d.window
    result = await controller.act(6, [{"type": "key", "key": "Return"}], allowed_xids={5, 6})
    assert result["success"]
    assert d.focus is d.other
    assert [e[:2] for e in d.events] == [(X.KeyPress, 36), (X.KeyRelease, 36)]


@pytest.mark.asyncio
async def test_explicit_child_menu_click_uses_its_own_coordinates(native):
    controller, d = native
    d.other.transient = d.window
    d.root.child = d.other
    d.other.x, d.other.y = 100, 120
    result = await controller.act(6, [{"type": "click", "x": 10, "y": 20}], allowed_xids={5, 6})
    assert result["success"]
    assert d.focus is d.other
    assert d.events[0] == (X.MotionNotify, 0, {"x": 110, "y": 140})


@pytest.mark.asyncio
async def test_verified_orphan_menu_can_focus_only_from_its_recorded_parent(native):
    controller, d = native
    parent_focus = d.focus
    result = await controller.act(6, [{"type": "key", "key": "Return"}],
                                  allowed_xids={5, 6}, focus_parent_xids={5})
    assert result['success'] and d.focus is parent_focus
    assert [event[:2] for event in d.events] == [(X.KeyPress, 36), (X.KeyRelease, 36)]


@pytest.mark.asyncio
async def test_menu_focus_proof_cannot_add_unowned_windows(native):
    controller, d = native
    with pytest.raises(ValueError, match='verified window set'):
        await controller.act(6, [{"type": "key", "key": "Return"}],
                             allowed_xids={6}, focus_parent_xids={5})
    assert d.events == []


@pytest.mark.asyncio
async def test_requested_child_cannot_redirect_input_from_focused_sibling(native):
    controller, d = native
    sibling = Window(8, d.root)
    sibling.transient = d.window
    d.other.transient = d.window
    d.focus = sibling
    result = await controller.act(6, [{"type": "key", "key": "Return"}], allowed_xids={5, 6, 8})
    assert not result["success"] and "native child window_id" in result["error"]
    assert d.focus is sibling and d.events == []


@pytest.mark.asyncio
async def test_new_dialog_opened_by_prior_action_stops_following_key(native, monkeypatch):
    controller, d = native
    d.other.transient = d.window
    dialog_widget = Window(8, d.other)
    original = xtest.fake_input

    def open_dialog(display, kind, detail=0, **kwargs):
        original(display, kind, detail, **kwargs)
        if kind == X.ButtonRelease:
            d.focus = dialog_widget

    monkeypatch.setattr(xtest, "fake_input", open_dialog)
    result = await controller.act(5, [{"type": "click", "x": 10, "y": 10},
                                      {"type": "key", "key": "Return"}], allowed_xids={5})
    assert not result["success"] and result["completed"] == 1
    assert "native child window_id" in result["error"]
    assert d.focus is dialog_widget
    assert all(event[0] not in {X.KeyPress, X.KeyRelease} for event in d.events)


@pytest.mark.asyncio
async def test_geometry_is_refreshed_after_window_resize(native, monkeypatch):
    controller, d = native
    original = xtest.fake_input

    def resize(display, kind, detail=0, **kwargs):
        original(display, kind, detail, **kwargs)
        if kind == X.ButtonRelease:
            d.window.geometry.width = 20

    monkeypatch.setattr(xtest, "fake_input", resize)
    result = await controller.act(5, [{"type": "click", "x": 10, "y": 10}, {"type": "click", "x": 50, "y": 10}])
    assert not result["success"] and result["completed"] == 1
    assert "outside" in result["error"]
    assert sum(event[0] == X.ButtonPress for event in d.events) == 1


@pytest.mark.asyncio
async def test_failed_drag_releases_pressed_button(native, monkeypatch):
    controller, d = native
    original = xtest.fake_input

    def disappear(display, kind, detail=0, **kwargs):
        original(display, kind, detail, **kwargs)
        if kind == X.ButtonPress:
            d.window.attrs.map_state = X.IsUnmapped

    monkeypatch.setattr(xtest, "fake_input", disappear)
    result = await controller.act(5, [{"type": "drag", "x": 10, "y": 10, "to_x": 30, "to_y": 20, "duration_ms": 0}])
    assert not result["success"]
    assert d.events[-1][:2] == (X.ButtonRelease, 1)


@pytest.mark.asyncio
async def test_ctrl_s_releases_in_reverse_order(native):
    controller, d = native
    result = await controller.act(5, [{"type": "key", "key": "Ctrl+s"}])
    assert result["success"]
    assert [e[:2] for e in d.events] == [(X.KeyPress, 37), (X.KeyPress, 39), (X.KeyRelease, 39), (X.KeyRelease, 37)]


@pytest.mark.asyncio
async def test_user_held_modifier_is_not_cleared_or_typed_through(native):
    controller, d = native
    d.pressed[37 // 8] |= 1 << (37 % 8)
    result = await controller.act(5, [{"type": "text", "text": "a"}])
    assert not result["success"] and "already held" in result["error"]
    assert d.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("button", [X.Button1Mask, X.Button2Mask, X.Button3Mask, X.Button4Mask, X.Button5Mask])
async def test_user_held_mouse_button_is_not_moved_or_released(native, button):
    controller, d = native
    d.root.pointer_mask = button
    d.focus = d.other
    result = await controller.act(5, [{"type": "click", "x": 10, "y": 10}])
    assert not result["success"] and "mouse button is already held" in result["error"]
    assert d.events == [] and d.focus is d.other


@pytest.mark.asyncio
async def test_unicode_mapping_survives_until_client_acknowledges_key(native, monkeypatch):
    controller, display = native
    acknowledgements = []

    def acknowledge(d, target):
        assert d.maps[-1][1][0][0] == 0x01004e2d
        acknowledgements.append(list(d.events))

    monkeypatch.setattr(controller, '_wait_client', acknowledge)
    result = await controller.act(5, [{'type': 'text', 'text': '中'}])
    assert result['success']
    assert len(acknowledgements) == 2 and acknowledgements[0] == []
    assert [event[0] for event in acknowledgements[1]] == [X.KeyPress, X.KeyRelease]
    assert not any(display.maps[-1][1][0])


@pytest.mark.asyncio
async def test_missing_unicode_ack_fails_and_restores_keymap(native, monkeypatch):
    controller, display = native
    monkeypatch.setattr(controller, '_wait_client', Mock(side_effect=RuntimeError('No application acknowledgement')))
    result = await controller.act(5, [{'type': 'text', 'text': '中'}])
    assert not result['success'] and 'acknowledgement' in result['error']
    assert display.events == []
    assert not any(display.maps[-1][1][0])


def client_ack_fixture():
    display = Display()
    display.root.attrs.your_event_mask = X.PropertyChangeMask
    masks, events = [], []
    display.root.change_attributes = lambda event_mask: masks.append(event_mask)
    display.intern_atom = lambda name: {'_NET_WM_PING': 11, 'WM_PROTOCOLS': 12}[name]
    display.flush = lambda: None
    display.pending_events = lambda: len(events)
    display.next_event = lambda: events.pop(0)
    def receive_ping(event, event_mask):
        format, values = event.data
        unrelated = list(values); unrelated[1] ^= 1
        events.append(NS(type=X.ClientMessage, client_type=12, data=(format, unrelated)))
        events.append(NS(type=X.ClientMessage, client_type=12, data=(format, list(values))))
    display.window.send_event = receive_ping
    return display, masks


def test_client_ack_matches_nonce_and_restores_root_event_selection():
    display, masks = client_ack_fixture()
    NativeWindowController._wait_client(display, display.window)
    assert masks == [X.PropertyChangeMask | X.SubstructureNotifyMask, X.PropertyChangeMask]


def test_client_ack_timeout_restores_root_event_selection(monkeypatch):
    from pantheon.apps.builtin.desktop import native_control
    display, masks = client_ack_fixture()
    monkeypatch.setattr(native_control, 'CLIENT_ACK_TIMEOUT', 0)
    with pytest.raises(RuntimeError, match='did not acknowledge'):
        NativeWindowController._wait_client(display, display.window)
    assert masks[-1] == X.PropertyChangeMask


@pytest.mark.asyncio
async def test_unicode_restores_exact_mapping_after_event_failure(native, monkeypatch):
    controller, d = native
    original = xtest.fake_input

    def fail(display, kind, detail=0, **kwargs):
        original(display, kind, detail, **kwargs)
        if kind == X.KeyPress:
            raise RuntimeError("synthetic connection failure")

    monkeypatch.setattr(xtest, "fake_input", fail)
    result = await controller.act(5, [{"type": "text", "text": "细"}])
    assert not result["success"]
    assert d.maps[0] == (255, [(0x01000000 | ord("细"), 0, 0, 0)])
    assert d.maps[-1] == (255, [(0, 0, 0, 0)])
    assert d.events[-1][:2] == (X.KeyRelease, 255)


@pytest.mark.asyncio
async def test_unicode_attempts_keymap_restore_even_when_key_release_fails(native, monkeypatch):
    controller, d = native

    def fail(*args, **kwargs):
        raise RuntimeError("Input extension unavailable")

    monkeypatch.setattr(xtest, "fake_input", fail)
    result = await controller.act(5, [{"type": "text", "text": "细"}])
    assert not result["success"] and "cleanup_error" in result
    assert d.maps[-1] == (255, [(0, 0, 0, 0)])


@pytest.mark.asyncio
async def test_shared_engine_serializes_input_across_controllers(native, monkeypatch):
    controller, d = native
    other = NativeWindowController(controller.engine)
    original = xtest.fake_input

    def slow(display, kind, detail=0, **kwargs):
        original(display, kind, detail, **kwargs)
        if kind == X.KeyPress:
            time.sleep(0.01)

    monkeypatch.setattr(xtest, "fake_input", slow)
    results = await asyncio.gather(controller.act(5, [{"type": "key", "key": "Ctrl+s"}]),
                                   other.act(5, [{"type": "key", "key": "Return"}]))
    assert all(r["success"] for r in results)
    events = [event[:2] for event in d.events]
    first = events.index((X.KeyPress, 37))
    assert events[first:first + 4] == [(X.KeyPress, 37), (X.KeyPress, 39), (X.KeyRelease, 39), (X.KeyRelease, 37)]


@pytest.mark.asyncio
async def test_screenshot_window_pixels_color_and_alpha_are_correct(native):
    controller, d = native
    d.window.geometry.width = 2
    d.window.geometry.height = 1
    d.window.pixels = bytes([0, 0, 255, 0, 255, 0, 0, 0])
    result = await controller.screenshot(5)
    assert result["width"] == 2 and result["height"] == 1
    assert result["coordinate_space"] == "native_window"
    image = Image.open(io.BytesIO(base64.b64decode(result["data_url"].split(",")[1])))
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((1, 0)) == (0, 0, 255)


@pytest.mark.asyncio
async def test_screenshot_24bit_row_padding_is_not_rendered_as_pixels(native):
    controller, d = native
    d.window.geometry.width = 1
    d.window.geometry.height = 2
    d.display.info.pixmap_formats[0].bits_per_pixel = 24
    d.window.pixels = bytes([0, 255, 0, 99, 0, 0, 255, 88])
    result = await controller.screenshot(5)
    image = Image.open(io.BytesIO(base64.b64decode(result["data_url"].split(",")[1])))
    assert image.getpixel((0, 0)) == (0, 255, 0)
    assert image.getpixel((0, 1)) == (255, 0, 0)


@pytest.mark.asyncio
async def test_screenshot_reads_owned_composite_pixels_and_frees_pixmap(native):
    controller, d = native
    d.window.geometry.width = d.window.geometry.height = 1
    d.window.geometry.border_width = 2
    d.window.pixels = b"\x00\x00\x00\x00"  # Obscured direct window pixels.
    d.has_extension = lambda name: name == "Composite"
    calls = []
    pixmap = NS(get_image=lambda *args: (calls.append(args) or NS(data=b"\xff\x00\x00\x00")),
                free=lambda **kwargs: calls.append("freed"))
    d.window.composite_name_window_pixmap = lambda **kwargs: pixmap
    result = await controller.screenshot(5)
    assert result["capture_source"] == "xcomposite_window"
    image = Image.open(io.BytesIO(base64.b64decode(result["data_url"].split(",")[1])))
    assert image.getpixel((0, 0)) == (0, 0, 255)
    assert calls == [(2, 2, 1, 1, X.ZPixmap, 0xFFFFFFFF), "freed"]


@pytest.mark.asyncio
async def test_unredirected_window_uses_direct_capture_after_name_error(native):
    controller, d = native
    d.window.geometry.width = d.window.geometry.height = 1
    d.window.pixels = b"\x00\xff\x00\x00"
    d.has_extension = lambda name: True
    freed = []

    def name_pixmap(onerror):
        onerror(RuntimeError("Window is not redirected"), None)
        return NS(free=lambda **kwargs: freed.append(True))

    d.window.composite_name_window_pixmap = name_pixmap
    result = await controller.screenshot(5)
    assert result["capture_source"] == "x11_window"
    assert freed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unsupported", "incomplete", "oversized"])
async def test_screenshot_rejects_unusable_frames(native, failure):
    controller, d = native
    if failure == "unsupported":
        d.visual.red_mask = 0xFFF000
    elif failure == "oversized":
        d.window.geometry.width = d.window.geometry.height = 8192
    else:
        d.window.pixels = b"short"
    with pytest.raises((RuntimeError, ValueError)):
        await controller.screenshot(5)
