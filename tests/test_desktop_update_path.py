"""desktop_update writes a patched `path` through to the session record.

The agent switching a window's DATA used to be runtime-only: the patch
reached the live iframes and nothing else, so a page reload re-opened the
window from its ORIGINAL file and the switch silently vanished. A `path` in
the patch is the durable pointer — it must land in the session document
(args included, which is what the client re-opens from) and be broadcast.
"""

import asyncio

import pytest

from pantheon.apps.builtin.desktop.desktop_session import DesktopSessionStore
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


@pytest.fixture
def store(tmp_path):
    s = DesktopSessionStore(work_dir=tmp_path)
    s.load()
    return s


@pytest.fixture
def toolset(monkeypatch, store):
    ts = DesktopToolSet()
    monkeypatch.setattr(ts, "_desktop", lambda: store)
    ts.published = []

    async def publish(event):
        ts.published.append(event)

    async def request(_type, payload, **kw):
        return {"success": True, "requested": payload}

    monkeypatch.setattr(ts, "_publish_desktop", publish)
    monkeypatch.setattr(ts, "_desktop_request", request)
    return ts


def test_path_patch_lands_in_the_session_args(toolset, store):
    win = store.apply("open", {"app_id": "pkg:spatial3d",
                               "args": {"path": "/ws/old.h5ad"}})[1]["window_id"]
    res = asyncio.run(toolset.desktop_update(win, {"path": "/ws/new.h5ad", "colorBy": "gene"}))
    assert res["success"] is True
    w = store.session.windows[win]
    assert w["path"] == "/ws/new.h5ad"
    assert (w.get("args") or {}).get("path") == "/ws/new.h5ad"
    # The change is broadcast so every viewport reloads from the new file.
    assert any(e.get("type") == "desktop.delta" for e in toolset.published)
    # The live update still goes out with the whole patch.
    assert res["requested"]["patch"]["colorBy"] == "gene"


def test_pathless_patch_touches_nothing_durable(toolset, store):
    win = store.apply("open", {"app_id": "pkg:spatial3d",
                               "args": {"path": "/ws/old.h5ad"}})[1]["window_id"]
    seq_before = store.session.seq
    res = asyncio.run(toolset.desktop_update(win, {"colorBy": "gene"}))
    assert res["success"] is True
    assert store.session.seq == seq_before
    assert (store.session.windows[win].get("args") or {}).get("path") == "/ws/old.h5ad"


def test_unknown_window_reports_instead_of_raising(toolset):
    res = asyncio.run(toolset.desktop_update("win-999", {"path": "/ws/x.h5ad"}))
    assert res["success"] is False
