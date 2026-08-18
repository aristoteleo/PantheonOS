"""The desktop session reducer.

A pure function of the document — no pod, no NATS, no volume — so the rules
that keep several viewports agreeing can be pinned down here rather than
discovered on a staging desktop.
"""

import json

import pytest

from pantheon.toolsets.live_view.desktop_session import (
    DesktopSession,
    DesktopSessionStore,
)


@pytest.fixture
def store(tmp_path):
    s = DesktopSessionStore(work_dir=tmp_path)
    s.load()
    return s


def test_open_mints_an_id_and_bumps_seq(store):
    ops, result = store.apply("open", {"app_id": "files", "title": "Files"})
    assert result["window_id"] == "win-1"
    assert result["reused"] is False
    assert [o["op"] for o in ops] == ["upsert"]
    assert store.session.seq == 1
    assert ops[0]["seq"] == 1


def test_ids_are_the_pods_so_two_viewports_cannot_disagree(store):
    """The bug this replaces: a per-tab counter gave every copy its own win-3,
    so an id the agent held could resolve to a different window elsewhere."""
    first = store.apply("open", {"app_id": "files"})[1]["window_id"]
    second = store.apply("open", {"app_id": "terminal"})[1]["window_id"]
    assert first != second


def test_open_with_a_window_id_reuses_it(store):
    wid = store.apply("open", {"app_id": "vitessce"})[1]["window_id"]
    ops, result = store.apply(
        "open", {"app_id": "vitessce", "window_id": wid, "path": "/w/other.h5ad"})
    assert result == {"window_id": wid, "reused": True}
    assert len(store.session.windows) == 1
    assert ops[0]["window"]["path"] == "/w/other.h5ad"


def test_move_and_resize_clamp_to_the_floor(store):
    wid = store.apply("open", {"app_id": "files"})[1]["window_id"]
    store.apply("move", {"window_id": wid, "x": -50, "y": -10})
    store.apply("resize", {"window_id": wid, "width": 10, "height": 10})
    w = store.session.windows[wid]
    assert (w["x"], w["y"]) == (0, 0)
    assert (w["width"], w["height"]) == (320, 180)


def test_close_removes_and_reports(store):
    wid = store.apply("open", {"app_id": "files"})[1]["window_id"]
    ops, result = store.apply("close", {"window_id": wid})
    assert result == {"closed": True}
    assert ops == [{"op": "remove", "id": wid, "seq": store.session.seq}]
    assert store.session.windows == {}


def test_closing_what_is_gone_changes_nothing(store):
    ops, result = store.apply("close", {"window_id": "win-404"})
    assert result == {"closed": False}
    assert ops == []
    assert store.session.seq == 0  # nothing happened, so nothing to tell anyone


def test_retiring_a_space_carries_its_windows_down(store):
    store.apply("spaces", {"count": 3})
    wid = store.apply("open", {"app_id": "files", "space": 3})[1]["window_id"]
    assert store.session.windows[wid]["space"] == 3
    store.apply("spaces", {"count": 1})
    assert store.session.windows[wid]["space"] == 1


def test_nominal_size_only_grows(store):
    """The desktop should be as large as the largest screen looking at it: a
    narrow sidebar panel attaching must not shrink the page's desktop."""
    assert store.apply("nominal", {"w": 1700, "h": 1000})[1] == {
        "nominal_w": 1700, "nominal_h": 1000}
    ops, result = store.apply("nominal", {"w": 660, "h": 900})
    assert result == {"nominal_w": 1700, "nominal_h": 1000}
    assert ops == []


def test_unknown_intent_and_missing_window_raise(store):
    with pytest.raises(ValueError):
        store.apply("teleport", {})
    with pytest.raises(KeyError):
        store.apply("move", {"window_id": "win-404", "x": 1, "y": 1})


def test_set_patches_only_named_keys(store):
    """An open-ended merge would let a stale client write geometry back."""
    wid = store.apply("open", {"app_id": "files"})[1]["window_id"]
    store.apply("set", {"window_id": wid, "patch": {
        "title": "renamed", "minimized": True, "x": 9999, "nonsense": 1}})
    w = store.session.windows[wid]
    assert w["title"] == "renamed"
    assert w["minimized"] is True
    assert w["x"] != 9999
    assert "nonsense" not in w


def test_a_record_round_trips_with_its_ids(store, tmp_path):
    """Regression: to_record wrote windows without their ids while from_record
    keyed on them, so every window vanished on the next restart."""
    a = store.apply("open", {"app_id": "files", "title": "Files"})[1]["window_id"]
    b = store.apply("open", {"app_id": "vitessce", "title": "pbmc"})[1]["window_id"]
    store.apply("move", {"window_id": b, "x": 300, "y": 90})

    back = DesktopSession.from_record(json.loads(json.dumps(store.session.to_record())))
    assert sorted(back.windows) == sorted([a, b])
    assert (back.windows[b]["x"], back.windows[b]["y"]) == (300, 90)
    assert back.seq == store.session.seq   # or every client re-syncs backwards
    # And a window opened after the reload must not reuse a live id.
    reloaded = DesktopSessionStore(work_dir=tmp_path / "elsewhere")
    reloaded.session = back
    assert reloaded.apply("open", {"app_id": "terminal"})[1]["window_id"] not in (a, b)


def test_the_record_survives_a_real_save_and_load(tmp_path):
    first = DesktopSessionStore(work_dir=tmp_path)
    first.load()
    wid = first.apply("open", {"app_id": "files", "title": "Files"})[1]["window_id"]
    first.save()

    second = DesktopSessionStore(work_dir=tmp_path)
    second.load()
    assert list(second.session.windows) == [wid]
    assert second.session.windows[wid]["title"] == "Files"


def test_agent_windows_outlive_a_reader_but_not_a_restart(tmp_path):
    """The record is where the document lives between two calls, not a backup
    taken on the way out — so leaving a window out of it deletes that window.
    Agent views must survive another process reading the record, and must not
    survive the container whose tunnel they point at."""
    store = DesktopSessionStore(work_dir=tmp_path)
    store.load()
    store.apply("open", {"app_id": "files"})
    store.apply("open", {"app_id": "agent-view", "title": "a view"})

    # Another process, same container: both windows are still there.
    peer = DesktopSessionStore(work_dir=tmp_path)
    peer.load()
    assert len(peer.session.windows) == 2

    # A later container: the agent view's module URL died with the old tunnel.
    record = json.loads((tmp_path / ".pantheon" / "desktop.json").read_text())
    record["boot"] = "some-other-container"
    after_restart = DesktopSession.from_record(record)
    assert [w["app_id"] for w in after_restart.windows.values()] == ["files"]


def test_a_v1_browser_record_is_adopted_not_dropped(tmp_path):
    """Upgrading must not throw away the layout the user already had."""
    record = tmp_path / ".pantheon" / "desktop.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"v": 1, "spaces": 2, "windows": [
        {"appId": "files", "title": "Files", "x": 10, "y": 20,
         "width": 700, "height": 500, "space": 2},
        {"appId": "agent-view", "title": "ephemeral"},
    ]}))
    s = DesktopSessionStore(work_dir=tmp_path)
    s.load()
    assert len(s.session.windows) == 1        # agent-view is not restored
    only = next(iter(s.session.windows.values()))
    assert (only["x"], only["y"], only["space"]) == (10, 20, 2)
    assert s.session.spaces == 2


def test_two_stores_on_one_record_are_one_desktop(tmp_path):
    """THE bug this file is about, at the layer it actually bit.

    The toolset is built per connection and run as a ProcessJob per toolset,
    so `desktop_intent` and `desktop_session_get` need not reach the same
    Python object — and when they did not, one viewport opened a window while
    the other was told, honestly, that the desktop was empty. Two stores over
    one record have to behave as one."""
    a = DesktopSessionStore(work_dir=tmp_path)
    b = DesktopSessionStore(work_dir=tmp_path)
    a.load()
    b.load()

    wid = a.apply("open", {"app_id": "terminal", "title": "Terminal"})[1]["window_id"]
    assert list(b.current()["windows"]) == [wid]

    # And B's own intent continues A's sequence rather than colliding with it:
    # two clients that both minted seq 2 would each be told a change landed
    # while one of the two silently did not exist.
    ops, _ = b.apply("move", {"window_id": wid, "x": 400, "y": 120})
    assert ops[0]["seq"] == a.session.seq + 1
    assert a.current()["windows"][wid]["x"] == 400
    assert a.session.seq == b.session.seq


def test_a_reader_does_not_lose_what_it_has_not_written(tmp_path):
    """Reloading must not clobber: B holding a stale copy, then applying its
    own intent, must not roll A's window back out of existence."""
    a = DesktopSessionStore(work_dir=tmp_path)
    b = DesktopSessionStore(work_dir=tmp_path)
    a.load()
    b.load()
    b.apply("open", {"app_id": "files"})          # B's copy is warm
    wid = a.apply("open", {"app_id": "terminal"})[1]["window_id"]
    b.apply("spaces", {"count": 2})               # B never re-read in between
    assert wid in b.session.windows
    assert len(a.current()["windows"]) == 2


def test_an_unreadable_record_is_an_empty_desktop_not_a_crash(tmp_path):
    record = tmp_path / ".pantheon" / "desktop.json"
    record.parent.mkdir(parents=True)
    record.write_text("{not json")
    s = DesktopSessionStore(work_dir=tmp_path)
    s.load()
    assert s.session.windows == {}
