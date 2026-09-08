"""Resolve native window identities, including owned transient dialogs.

Only the backend supplies the main WM_CLASS. Child handles are verified
against its transient chain on every call; raw X11 ids never grant access.
"""
from __future__ import annotations


def window_targets(engine, window_id: str, window_class: str) -> list[dict]:
    from Xlib import X

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
        if not belongs(win):
            continue
        try:
            g = win.get_geometry()
            result.append({
                "window_id": window_id if win.id == main.id else f"{window_id}::native:{win.id}",
                "parent_window_id": None if win.id == main.id else window_id,
                "title": engine._window_name(d, win),
                "width": g.width, "height": g.height, "xid": win.id,
                "coordinate_space": "native_window",
            })
        except Exception:
            continue
    return result
