"""Window-scoped native screenshots and input for the shared Desktop tools.

The caller resolves a stable Atrium window id to an owned X11 window. This
module never accepts an agent-supplied root window or discovers arbitrary
targets. All coordinates are pixels in the requested native window.
"""
from __future__ import annotations

import asyncio
import base64
import io
import math
import threading
import time
from typing import Any


MAX_PIXELS = 16_777_216
MAX_ACTIONS = 32
MAX_TEXT = 2000
CLIENT_ACK_TIMEOUT = 3.0
_LOCK_CREATION = threading.Lock()


def native_input_lock(engine):
    """Share with other engine input paths that must not interleave a chord."""
    with _LOCK_CREATION:
        lock = getattr(engine, "_native_input_lock", None)
        if lock is None:
            lock = threading.RLock()
            engine._native_input_lock = lock
        return lock


def _integer(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
        raise ValueError(f"{name} must be an integer")
    value = int(value)
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} is outside its allowed range")
    return value


def _actions(actions):
    """Validate the whole batch before any input is delivered."""
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        raise ValueError(f"actions must contain 1–{MAX_ACTIONS} actions")
    normalized = []
    for item in actions:
        if not isinstance(item, dict):
            raise ValueError("Each action must be an object")
        a = dict(item)
        kind = a.get("type")
        if kind not in {"click", "rightclick", "dblclick", "move", "drag", "wheel", "key", "text"}:
            raise ValueError(f"Unknown native action: {kind!r}")
        if kind in {"click", "rightclick", "dblclick", "move", "drag", "wheel"}:
            a["x"] = _integer(a.get("x"), "x", 0, 8191)
            a["y"] = _integer(a.get("y"), "y", 0, 8191)
        if kind in {"click", "drag"}:
            if a.get("button", "left") not in {"left", "middle", "right"}:
                raise ValueError("button must be left, middle or right")
        if kind == "drag":
            a["to_x"] = _integer(a.get("to_x"), "to_x", 0, 8191)
            a["to_y"] = _integer(a.get("to_y"), "to_y", 0, 8191)
            a["duration_ms"] = _integer(a.get("duration_ms", 300), "duration_ms", 0, 2000)
        elif kind == "wheel":
            for axis in ("delta_x", "delta_y"):
                a[axis] = _integer(a.get(axis, 0), axis, -100, 100)
        elif kind == "key":
            if not isinstance(a.get("key"), str) or not 1 <= len(a["key"]) <= 100:
                raise ValueError("key must name a key or chord, e.g. Ctrl+s or Return")
            a["_key_symbols"] = NativeWindowController._key_symbols(a["key"])
        elif kind == "text":
            if not isinstance(a.get("text"), str) or not 1 <= len(a["text"]) <= MAX_TEXT:
                raise ValueError(f"text must contain 1–{MAX_TEXT} characters")
            if any((ord(c) < 32 and c not in "\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in a["text"]):
                raise ValueError("text contains unsupported control characters")
        normalized.append(a)
    if sum(a.get("duration_ms", 0) for a in normalized) > 10_000:
        raise ValueError("Total drag duration must not exceed 10 seconds")
    if sum(len(a.get("text", "")) for a in normalized) > MAX_TEXT:
        raise ValueError(f"A batch may type at most {MAX_TEXT} characters")
    return normalized


class NativeWindowController:
    def __init__(self, engine):
        self.engine = engine
        self._lock = native_input_lock(engine)

    @staticmethod
    def _allowed(xid, allowed_xids):
        xid = _integer(xid, "Native window", 1, 0xFFFFFFFF)
        allowed = {xid} if allowed_xids is None else {
            _integer(value, "Allowed native window", 1, 0xFFFFFFFF) for value in allowed_xids
        }
        if xid not in allowed:
            raise ValueError("Requested native window is not in the owned window set")
        return xid, allowed

    async def screenshot(self, xid: int, *, allowed_xids=None) -> dict:
        xid, _ = self._allowed(xid, allowed_xids)
        return await asyncio.to_thread(self._screenshot, xid)

    async def act(self, xid: int, actions: list[dict], *, allowed_xids=None, focus_parent_xids=()) -> dict:
        xid, allowed = self._allowed(xid, allowed_xids)
        focus_parents = {_integer(value, "Native focus parent", 1, 0xFFFFFFFF) for value in focus_parent_xids}
        if not focus_parents <= allowed - {xid}:
            raise ValueError("Native focus parents must belong to the verified window set")
        batch = _actions(actions)
        # One thread-local Xlib connection for the entire input batch.
        return await asyncio.to_thread(self._act, xid, batch, allowed, focus_parents)

    @staticmethod
    def _window(d, xid):
        from Xlib import X

        root = d.screen().root
        if xid == root.id:
            raise ValueError("The full desktop is not an input or screenshot target")
        win = d.create_resource_object("window", xid)
        attrs = win.get_attributes()
        if attrs.map_state != X.IsViewable:
            raise RuntimeError("Native window is not viewable; focus/open it before acting")
        g = win.get_geometry()
        if g.width <= 0 or g.height <= 0 or g.width > 8192 or g.height > 8192 or g.width * g.height > MAX_PIXELS:
            raise ValueError("Native window dimensions exceed the capture/input limit")
        position = root.translate_coords(win, 0, 0)
        return win, g, position, attrs

    def _screenshot(self, xid):
        from Xlib import X, error
        from PIL import Image

        with self._lock:
            d = self.engine._x_display()
            win, g, _, attrs = self._window(d, xid)
            info = d.display.info
            fmt = next((f for f in info.pixmap_formats if f.depth == g.depth), None)
            visual = next((v for depth in d.screen().allowed_depths for v in depth.visuals if v.visual_id == attrs.visual), None)
            if fmt is None or visual is None:
                raise RuntimeError("Unsupported native window pixel format")
            masks = (visual.red_mask, visual.green_mask, visual.blue_mask)
            little = info.image_byte_order == X.LSBFirst
            modes = {
                (32, (0xFF0000, 0xFF00, 0xFF), True): "BGRX",
                (32, (0xFF0000, 0xFF00, 0xFF), False): "XRGB",
                (24, (0xFF0000, 0xFF00, 0xFF), True): "BGR",
                (24, (0xFF0000, 0xFF00, 0xFF), False): "RGB",
                (16, (0xF800, 0x7E0, 0x1F), True): "BGR;16",
            }
            raw_mode = modes.get((fmt.bits_per_pixel, masks, little))
            if raw_mode is None:
                raise RuntimeError("Unsupported native window visual; cannot capture correct colors")
            stride = ((g.width * fmt.bits_per_pixel + fmt.scanline_pad - 1) // fmt.scanline_pad) * (fmt.scanline_pad // 8)
            pixels = None
            capture_source = "x11_window"
            # Xpra redirects each window into its own backing pixmap. Reading
            # that owned pixmap keeps pixels available when a dialog covers
            # the main window, without capturing unrelated desktop contents.
            if d.has_extension("Composite"):
                caught = error.CatchError()
                pixmap = win.composite_name_window_pixmap(onerror=caught)
                try:
                    d.sync()
                    if caught.get_error() is None:
                        border = getattr(g, "border_width", 0)
                        pixels = pixmap.get_image(border, border, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
                        capture_source = "xcomposite_window"
                finally:
                    # A non-redirected Xvfb window cannot be named. Freeing
                    # its uncreated resource may also fail; consume only that
                    # cleanup error, then use the ordinary window path.
                    pixmap.free(onerror=error.CatchError())
                    d.sync()
            if pixels is None:
                pixels = win.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
            if pixels is None or len(pixels.data) != stride * g.height:
                raise RuntimeError("Native window returned an incomplete screenshot")
            image = Image.frombytes("RGB", (g.width, g.height), pixels.data, "raw", raw_mode, stride, 1)
            output = io.BytesIO()
            image.save(output, format="PNG")
            return {"data_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
                    "width": g.width, "height": g.height, "coordinate_space": "native_window",
                    "capture_source": capture_source}

    @staticmethod
    def _ancestor_in(win, allowed):
        for _ in range(32):
            if not win or not hasattr(win, "id"):
                return None
            if win.id in allowed:
                return win.id
            tree = win.query_tree()
            if not tree.parent or tree.parent.id == win.id:
                return None
            win = tree.parent
        return None

    @staticmethod
    def _descendant(win, allowed):
        return NativeWindowController._ancestor_in(win, allowed) is not None

    @staticmethod
    def _transient_descendant(win, ancestor):
        visited = set()
        for _ in range(32):
            if win.id in visited:
                return False
            visited.add(win.id)
            win = win.get_wm_transient_for()
            if not win or not hasattr(win, "id"):
                return False
            if win.id == ancestor:
                return True
        return False

    @staticmethod
    def _focused_owned_popup(focus, allowed):
        # A batch may have just opened a popup absent from its initial
        # allowlist. Its transient link can block input, never authorize it.
        for _ in range(32):
            if not focus or not hasattr(focus, "id"):
                return False
            if any(NativeWindowController._transient_descendant(focus, owner) for owner in allowed):
                return True
            tree = focus.query_tree()
            if not tree.parent or tree.parent.id == focus.id:
                return False
            focus = tree.parent
        return False

    @staticmethod
    def _hit(d, allowed):
        # XTest is display-level: verify the actual recipient after moving.
        win = d.screen().root
        for _ in range(32):
            pointer = win.query_pointer()
            child = pointer.child
            if not child or not hasattr(child, "id"):
                break
            win = child
        if not NativeWindowController._descendant(win, allowed):
            raise RuntimeError("Pointer is over another window; use its native child window_id if it belongs to this app")

    @staticmethod
    def _hit_point(d, xid, x, y):
        # Check before MotionNotify too: an obscured target must not receive
        # a synthetic move/drag through a different window at this position.
        root = d.screen().root
        win = root
        for _ in range(32):
            child = win.translate_coords(root, x, y).child
            if not child or not hasattr(child, "id"):
                break
            win = child
        if not NativeWindowController._descendant(win, {xid}):
            raise RuntimeError("Pointer position is over another window; use its native child window_id if it belongs to this app")

    def _focus(self, d, xid, allowed, focus_parents=()):
        from Xlib import X

        win, geometry, position, _ = self._window(d, xid)
        # Preserve focus within the addressed window's widgets. A separately
        # owned popup is a different screenshot/input target, even if modal.
        focus = d.get_input_focus().focus
        if not self._descendant(focus, {xid}):
            focused_owner = self._ancestor_in(focus, allowed)
            if focused_owner in focus_parents:
                # Non-focusing native menus keep keyboard focus on their
                # verified owner and handle keys through their menu grab.
                # Forcing focus onto the menu makes the app restore it
                # immediately (or dismiss the menu). Pointer checks still
                # require the explicitly addressed menu's own pixel subtree.
                return win, geometry, position
            # Explicitly addressing a child may focus it from its transient
            # parent (menus often leave focus there). The reverse would bypass
            # the dialog, and sibling windows must not receive each other's input.
            if ((focused_owner is not None and focused_owner not in focus_parents
                 and not self._transient_descendant(win, focused_owner))
                    or (focused_owner is None and self._focused_owned_popup(focus, allowed))):
                raise RuntimeError("Another owned native window is focused; use its native child window_id")
            d.set_input_focus(win, X.RevertToParent, X.CurrentTime)
            d.sync()
            if not self._descendant(d.get_input_focus().focus, {xid}):
                raise RuntimeError("Could not focus the requested native window")
        return win, geometry, position

    def _point(self, d, xid, allowed, x, y, focus_parents=()):
        from Xlib import X
        from Xlib.ext import xtest

        _, g, position = self._focus(d, xid, allowed, focus_parents)
        if not 0 <= x < g.width or not 0 <= y < g.height:
            raise ValueError("Coordinates are outside the current native window; capture it again")
        root_g = d.screen().root.get_geometry()
        root_x, root_y = position.x + x, position.y + y
        if not 0 <= root_x < root_g.width or not 0 <= root_y < root_g.height:
            raise ValueError("Requested point is outside the X11 display")
        self._hit_point(d, xid, root_x, root_y)
        xtest.fake_input(d, X.MotionNotify, x=root_x, y=root_y)
        d.sync()
        self._hit(d, {xid})

    @staticmethod
    def _mapping(d, keysym):
        mappings = d.keysym_to_keycodes(keysym)
        # Only unmodified/Shift mappings; AltGr or XKB groups need their own
        # handling and must not silently type a different character.
        return next(((int(code), int(index)) for code, index in mappings if index in (0, 1)), None)

    @staticmethod
    def _keysym(name):
        from Xlib import XK

        aliases = {"Ctrl": "Control_L", "Control": "Control_L", "Shift": "Shift_L", "Alt": "Alt_L",
                   "Meta": "Super_L", "Super": "Super_L", "Enter": "Return", "Escape": "Escape",
                   "Esc": "Escape", "Backspace": "BackSpace", "Delete": "Delete", "Space": "space",
                   "ArrowLeft": "Left", "ArrowRight": "Right", "ArrowUp": "Up", "ArrowDown": "Down"}
        if len(name) == 1:
            value = ord(name)
            return value if value <= 255 else 0x01000000 | value
        return XK.string_to_keysym(aliases.get(name, name))

    @staticmethod
    def _key_symbols(chord):
        names = chord.split("+")
        modifier_names = {"Ctrl", "Control", "Control_L", "Control_R", "Shift", "Shift_L", "Shift_R",
                          "Alt", "Alt_L", "Alt_R", "Meta", "Super", "Super_L", "Super_R"}
        symbols = [NativeWindowController._keysym(name) for name in names]
        if any(not symbol for symbol in symbols) or any(name not in modifier_names for name in names[:-1]):
            raise ValueError("Invalid key chord; use modifier names followed by one key")
        return symbols

    @staticmethod
    def _validate_text_for_window(d, xid, actions):
        if not any(any(ord(c) > 0xFFFF for c in a.get("text", a.get("key", ""))) for a in actions):
            return
        # QuPath's JavaFX/GTK input path truncates supplementary Unicode X11
        # keysyms to one UTF-16 code unit. Reject the complete batch before
        # even its first click, rather than reporting success with wrong text.
        win = d.create_resource_object('window', xid)
        visited = set()
        for _ in range(32):
            if win.id in visited:
                break
            visited.add(win.id)
            if {'QuPath', 'qupath.lib.gui.QuPathApp'}.intersection(win.get_wm_class() or ()):
                raise ValueError(
                    "This JavaFX window cannot receive non-BMP characters through native keys. "
                    "Use desktop_call(window_id, action='run_script', args={'thread': 'fx', ...}) "
                    "with the verified focused TextInputControl.replaceSelection; see the QuPath skill. "
                    "No actions in this batch were sent."
                )
            parent = win.get_wm_transient_for() or win.query_tree().parent
            if not parent or parent.id == win.id:
                break
            win = parent

    @staticmethod
    def _ping_target(d, xid):
        ping = d.intern_atom('_NET_WM_PING')
        win = d.create_resource_object('window', xid)
        visited = set()
        for _ in range(32):
            if win.id in visited:
                break
            visited.add(win.id)
            if ping in (win.get_wm_protocols() or ()):
                return win
            parent = win.get_wm_transient_for() or win.query_tree().parent
            if not parent or parent.id == win.id:
                break
            win = parent
        raise RuntimeError('This native app cannot acknowledge remapped Unicode keys; use its text or script interface')

    @staticmethod
    def _wait_client(d, target):
        # XSync only waits for the X server. The application can still be
        # processing an earlier MappingNotify when a temporary keymap is
        # restored, dropping/replacing Unicode characters. A supported EWMH
        # ping makes the application acknowledge its preceding X events.
        import select
        from Xlib import X
        from Xlib.protocol.event import ClientMessage

        root = d.screen().root
        original_mask = root.get_attributes().your_event_mask
        ping, protocols = d.intern_atom('_NET_WM_PING'), d.intern_atom('WM_PROTOCOLS')
        nonce = time.monotonic_ns() & 0xFFFFFFFF
        try:
            root.change_attributes(event_mask=original_mask | X.SubstructureNotifyMask)
            d.sync()
            target.send_event(ClientMessage(window=target.id, client_type=protocols,
                data=(32, [ping, nonce, target.id, 0, 0])), event_mask=0)
            d.flush()
            deadline = time.monotonic() + CLIENT_ACK_TIMEOUT
            while time.monotonic() < deadline:
                while d.pending_events():
                    event = d.next_event()
                    if event.type == X.ClientMessage and event.client_type == protocols:
                        format, values = event.data
                        if format == 32 and list(values[:3]) == [ping, nonce, target.id]:
                            return
                select.select([d.fileno()], [], [], min(0.05, max(0, deadline - time.monotonic())))
            raise RuntimeError('Native app did not acknowledge Unicode input; inspect the field before retrying')
        finally:
            root.change_attributes(event_mask=original_mask)
            d.sync()

    def _act(self, xid, actions, allowed, focus_parents=()):
        from Xlib import X
        from Xlib.ext import xtest

        with self._lock:
            d = self.engine._x_display()
            self._validate_text_for_window(d, xid, actions)
            held_keys, held_buttons = [], []
            completed = 0

            def press(code):
                held_keys.append(code)
                xtest.fake_input(d, X.KeyPress, code)

            def release(code):
                xtest.fake_input(d, X.KeyRelease, code)
                held_keys.remove(code)

            def button_down(number):
                held_buttons.append(number)
                xtest.fake_input(d, X.ButtonPress, number)

            def button_up(number):
                xtest.fake_input(d, X.ButtonRelease, number)
                held_buttons.remove(number)

            def tap_keysym(symbol, extra_modifiers=()):
                mapping = self._mapping(d, symbol)
                if mapping is None:
                    raise ValueError("Requested key is not present in the native keyboard map")
                code, index = mapping
                modifiers = list(extra_modifiers)
                if index == 1:
                    shift = self._mapping(d, self._keysym("Shift"))
                    if shift is None:
                        raise ValueError("Shift is not present in the native keyboard map")
                    if shift[0] not in modifiers:
                        modifiers.append(shift[0])
                self._focus(d, xid, allowed, focus_parents)
                for modifier in modifiers:
                    press(modifier)
                press(code)
                release(code)
                for modifier in reversed(modifiers):
                    release(modifier)
                d.sync()

            def type_character(char):
                symbol = self._keysym({"\n": "Return", "\t": "Tab"}.get(char, char))
                if self._mapping(d, symbol) is not None:
                    tap_keysym(symbol)
                    return
                # X11 supports Unicode keysyms. Borrow an unused keycode only
                # for the event, then restore the exact previous keyboard map.
                info = d.display.info
                first, last = info.min_keycode, info.max_keycode
                mapping = d.get_keyboard_mapping(first, last - first + 1)
                modifier_codes = {code for row in d.get_modifier_mapping() for code in row if code}
                pressed = d.query_keymap()
                spare = next((first + i for i in range(len(mapping) - 1, -1, -1)
                              if not any(mapping[i]) and first + i not in modifier_codes
                              and not (pressed[(first + i) // 8] & (1 << ((first + i) % 8)))), None)
                if spare is None:
                    raise ValueError("No unused native keycode is available for this character")
                original = tuple(mapping[spare - first])
                target = self._ping_target(d, xid)
                try:
                    d.change_keyboard_mapping(spare, [(symbol,) + (0,) * (len(original) - 1)])
                    d.sync()
                    self._wait_client(d, target)
                    self._focus(d, xid, allowed, focus_parents)
                    press(spare)
                    release(spare)
                    d.sync()
                    self._wait_client(d, target)
                finally:
                    # A failed event may leave the borrowed key held; release
                    # it before restoring the keymap to avoid a stuck key.
                    try:
                        if spare in held_keys:
                            release(spare)
                    finally:
                        d.change_keyboard_mapping(spare, [original])
                        d.sync()

            result: dict[str, Any] = {"success": True, "completed": 0, "total": len(actions)}
            try:
                buttons = X.Button1Mask | X.Button2Mask | X.Button3Mask | X.Button4Mask | X.Button5Mask
                if d.screen().root.query_pointer().mask & buttons:
                    raise RuntimeError("A mouse button is already held; release it before native input")
                pressed = d.query_keymap()
                modifiers = {code for row in d.get_modifier_mapping() for code in row if code}
                if any(pressed[code // 8] & (1 << (code % 8)) for code in modifiers):
                    raise RuntimeError("A modifier key is already held; release it before native input")
                self._focus(d, xid, allowed, focus_parents)
                for a in actions:
                    kind = a["type"]
                    self._focus(d, xid, allowed, focus_parents)
                    if "x" in a:
                        self._point(d, xid, allowed, a["x"], a["y"], focus_parents)
                    if kind in {"click", "rightclick", "dblclick"}:
                        number = 3 if kind == "rightclick" else {"left": 1, "middle": 2, "right": 3}[a.get("button", "left")]
                        for index in range(2 if kind == "dblclick" else 1):
                            if index:
                                time.sleep(0.06)
                                self._point(d, xid, allowed, a["x"], a["y"], focus_parents)
                            button_down(number)
                            button_up(number)
                            d.sync()
                    elif kind == "drag":
                        # Check destination before pressing the button.
                        _, g, _ = self._focus(d, xid, allowed, focus_parents)
                        if a["to_x"] >= g.width or a["to_y"] >= g.height:
                            raise ValueError("Drag destination is outside the current native window")
                        number = {"left": 1, "middle": 2, "right": 3}[a.get("button", "left")]
                        button_down(number)
                        steps = max(1, min(120, a["duration_ms"] // 16))
                        for step in range(1, steps + 1):
                            self._point(d, xid, allowed,
                                        round(a["x"] + (a["to_x"] - a["x"]) * step / steps),
                                        round(a["y"] + (a["to_y"] - a["y"]) * step / steps), focus_parents)
                            if a["duration_ms"]:
                                time.sleep(a["duration_ms"] / 1000 / steps)
                        button_up(number)
                    elif kind == "wheel":
                        for value, negative, positive in ((a["delta_y"], 4, 5), (a["delta_x"], 6, 7)):
                            for _ in range(abs(value)):
                                self._point(d, xid, allowed, a["x"], a["y"], focus_parents)
                                number = positive if value > 0 else negative
                                button_down(number)
                                button_up(number)
                    elif kind == "key":
                        symbols = a["_key_symbols"]
                        codes = [self._mapping(d, symbol) for symbol in symbols[:-1]]
                        if any(code is None for code in codes):
                            raise ValueError("Chord modifier is unavailable")
                        tap_keysym(symbols[-1], [code[0] for code in codes])
                    elif kind == "text":
                        for char in a["text"]:
                            type_character(char)
                    d.sync()
                    completed += 1
            except Exception as error:
                result.update(success=False, error=str(error), partial_action=completed,
                              message="Earlier actions, and part of the failed action, may already have executed.")
            finally:
                cleanup_errors = []
                for code in reversed(held_keys[:]):
                    try:
                        release(code)
                    except Exception as error:
                        cleanup_errors.append(str(error))
                for number in reversed(held_buttons[:]):
                    try:
                        button_up(number)
                    except Exception as error:
                        cleanup_errors.append(str(error))
                try:
                    d.sync()
                except Exception as error:
                    cleanup_errors.append(str(error))
                if cleanup_errors:
                    result.update(success=False, cleanup_error="Could not confirm input release: " + "; ".join(cleanup_errors))
                result["completed"] = completed
            return result
