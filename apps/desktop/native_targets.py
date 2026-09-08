"""Resolve native window identities, including owned transient dialogs.

Only the backend supplies the main WM_CLASS. Child handles are verified
against native ownership on every call; raw X11 ids never grant access.
"""
from __future__ import annotations


def window_targets(engine, window_id: str, window_class: str) -> list[dict]:
    from Xlib import X, Xutil

    d = engine._x_display()
    root = d.screen().root
    found = {}

    def walk(parent, depth=0):
        if depth > 8:
            return
        try:
            children = parent.query_tree().children
        except Exception:
            return
        for child in children:
            try:
                if child.get_attributes().map_state != X.IsUnmapped:
                    found[child.id] = child
            except Exception:
                continue
            walk(child, depth + 1)

    walk(root)
    mains = []
    for win in found.values():
        try:
            if window_class in (win.get_wm_class() or ()):
                mains.append(win)
        except Exception:
            continue
    if len(mains) != 1:
        raise RuntimeError("The requested native window is unavailable or ambiguous")
    main = mains[0]

    # Chromium's native menus deliberately omit WM_TRANSIENT_FOR. Accept
    # those only with a matching PID AND X11 client, an interactive menu
    # type, overlap with this main window, and this main window's focus.
    # Ordinary windows and another browser window's menu never qualify just
    # because the browser shares a process/profile.
    def property_values(win, name):
        value = win.get_full_property(d.intern_atom(name), X.AnyPropertyType)
        return tuple(int(item) for item in value.value) if value else ()

    def rect(win):
        geometry = win.get_geometry()
        position = root.translate_coords(win, 0, 0)
        return position.x, position.y, geometry.width, geometry.height

    def focused_in(win, ancestor):
        for _ in range(32):
            if not win or not hasattr(win, "id"):
                return False
            if win.id == ancestor.id:
                return True
            parent = win.query_tree().parent
            if not parent or parent.id == win.id:
                return False
            win = parent
        return False

    orphan_menus = set()
    try:
        main_pid = property_values(main, '_NET_WM_PID')
        menu_types = {d.intern_atom(name) for name in (
            '_NET_WM_WINDOW_TYPE_MENU', '_NET_WM_WINDOW_TYPE_POPUP_MENU',
            '_NET_WM_WINDOW_TYPE_DROPDOWN_MENU', '_NET_WM_WINDOW_TYPE_COMBO')}
        clients = d.res_query_clients().clients

        def client_for(xid):
            return next((client.resource_base for client in clients
                         if xid & ~client.resource_mask == client.resource_base), None)

        main_client = client_for(main.id)
        mx, my, mw, mh = rect(main)
        candidates = []
        if len(main_pid) == 1 and main_pid[0] > 0 and main_client is not None:
            for win in found.values():
                try:
                    if (not win.get_attributes().override_redirect or win.get_wm_transient_for()
                            or property_values(win, '_NET_WM_PID') != main_pid
                            or client_for(win.id) != main_client
                            or not menu_types.intersection(property_values(win, '_NET_WM_WINDOW_TYPE'))):
                        continue
                    x, y, width, height = rect(win)
                    if max(x, mx) < min(x + width, mx + mw) and max(y, my) < min(y + height, my + mh):
                        candidates.append(win)
                except Exception:
                    continue
        focus = d.get_input_focus().focus
        active = property_values(root, '_NET_ACTIVE_WINDOW')
        if focused_in(focus, main) or (
                active == (main.id,) and any(focused_in(focus, menu) for menu in candidates)):
            orphan_menus = {win.id for win in candidates}
    except Exception:
        # Missing XRes/properties is not evidence of ownership. Explicit
        # WM_TRANSIENT_FOR dialogs continue working on older X11 servers.
        pass

    def belongs(win):
        visited = set()
        while win.id not in visited:
            if win.id == main.id:
                return True
            visited.add(win.id)
            try:
                win = win.get_wm_transient_for()
            except Exception:
                return False
            if not win:
                return False
        return False

    result = []
    for win in [main, *(w for w in found.values() if w.id != main.id)]:
        if not belongs(win) and win.id not in orphan_menus:
            continue
        try:
            g = win.get_geometry()
            target = {
                "window_id": window_id if win.id == main.id else f"{window_id}::native:{win.id}",
                "parent_window_id": None if win.id == main.id else window_id,
                "title": engine._window_name(d, win),
                "width": g.width, "height": g.height, "xid": win.id,
                "coordinate_space": "native_window",
            }
            if win.id in orphan_menus:
                target['_focus_parent_xids'] = [main.id]
            elif win.id != main.id:
                # JavaFX context menus declare a transient owner and the
                # ICCCM no-input focus model. Giving them X focus races the
                # application restoring focus to their owner. Keep the
                # verified owner's focus while addressing this popup.
                parent = win.get_wm_transient_for()
                hints = win.get_wm_hints()
                if (win.get_attributes().override_redirect and hints
                        and hints.flags & Xutil.InputHint and not hints.input
                        and parent and parent.id in found and belongs(parent)):
                    target['_focus_parent_xids'] = [parent.id]
            result.append(target)
        except Exception:
            continue
    return result
