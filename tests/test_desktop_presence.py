"""The presence registry: who is looking, and where a chat is anchored.

Leases are the whole point, so every rule here is stated against an explicit
clock rather than a sleep — a TTL you have to wait out is a test nobody runs.
"""

import json

import pytest

from pantheon.toolsets.desktop.presence import TTL_S, Presence, PresenceStore


@pytest.fixture
def store(tmp_path):
    return PresenceStore(work_dir=tmp_path)


def test_a_page_announces_its_viewport_and_its_chat_windows(store):
    """One call per page: a page has one viewport and any number of chat UIs,
    and a heartbeat must not become one message per entity."""
    live, changed = store.announce(
        viewport_id="v1",
        clients=[{"client_id": "c1", "chat_id": "chat-a", "host_viewport_id": "v1"},
                 {"client_id": "c2", "chat_id": "chat-b", "host_viewport_id": "v1"}],
        now=1000.0)
    assert changed is True
    assert list(live["viewports"]) == ["v1"]
    assert sorted(live["clients"]) == ["c1", "c2"]


def test_a_renewal_that_changes_nothing_is_not_worth_broadcasting(store):
    store.announce(viewport_id="v1", now=1000.0)
    _, changed = store.announce(viewport_id="v1", now=1010.0)
    assert changed is False          # or every tab wakes every heartbeat
    _, changed = store.announce(viewport_id="v2", now=1011.0)
    assert changed is True           # someone joined: cursors should appear


def test_a_lease_that_stops_being_renewed_expires(store):
    """The case explicit unregistering cannot cover: a crash, a dropped
    network, a sleeping laptop. None of them send pagehide."""
    store.announce(viewport_id="v1", now=1000.0)
    assert list(store.current(now=1000.0 + TTL_S - 1)["viewports"]) == ["v1"]
    assert list(store.current(now=1000.0 + TTL_S + 1)["viewports"]) == []


def test_expiry_is_applied_when_read_not_only_when_swept(tmp_path):
    """A reaper's period would otherwise be a window in which a dead tab is
    still addressable. Nothing has swept this file — reading it must still
    refuse to report the corpse."""
    a = PresenceStore(work_dir=tmp_path)
    a.announce(viewport_id="v1", now=1000.0)

    fresh = PresenceStore(work_dir=tmp_path)     # a different process
    assert list(fresh.current(now=1000.0 + TTL_S + 1)["viewports"]) == []
    assert fresh.anchor_for("chat-a", now=1000.0 + TTL_S + 1)["viewport_id"] is None


def test_leave_is_immediate_where_expiry_would_take_a_ttl(store):
    store.announce(viewport_id="v1",
                   clients=[{"client_id": "c1", "chat_id": "a", "host_viewport_id": "v1"}],
                   now=1000.0)
    live, changed = store.leave(viewport_id="v1", client_ids=["c1"], now=1001.0)
    assert changed is True
    assert live["viewports"] == {} and live["clients"] == {}


def test_the_anchor_is_the_viewport_hosting_the_chat(store):
    """Two desktops open; only one of them holds this chat's window."""
    store.announce(viewport_id="v1", now=1000.0)
    store.announce(viewport_id="v2",
                   clients=[{"client_id": "c1", "chat_id": "chat-a",
                             "host_viewport_id": "v2"}],
                   now=1000.0)
    got = store.anchor_for("chat-a", now=1001.0)
    assert got["viewport_id"] == "v2"
    assert got["reason"] == "hosts the chat"


def test_three_agent_windows_in_one_page_are_not_an_ambiguity(store):
    """The case a per-page {viewport_id, chats:[…]} model could not express."""
    store.announce(
        viewport_id="v1",
        clients=[{"client_id": f"c{i}", "chat_id": f"chat-{i}", "host_viewport_id": "v1"}
                 for i in range(3)],
        now=1000.0)
    for i in range(3):
        assert store.anchor_for(f"chat-{i}", now=1001.0)["viewport_id"] == "v1"


def test_one_chat_open_in_two_places_anchors_where_the_person_last_was(store):
    """pantheon-ui and a desktop Agent window on the same conversation."""
    store.announce(viewport_id="v1",
                   clients=[{"client_id": "c1", "chat_id": "chat-a", "host_viewport_id": "v1"}],
                   active=True, now=1000.0)
    store.announce(viewport_id="v2",
                   clients=[{"client_id": "c2", "chat_id": "chat-a", "host_viewport_id": "v2"}],
                   active=True, now=1005.0)
    assert store.anchor_for("chat-a", now=1006.0)["viewport_id"] == "v2"

    # …and it follows the person back.
    store.announce(viewport_id="v1",
                   clients=[{"client_id": "c1", "chat_id": "chat-a", "host_viewport_id": "v1"}],
                   active=True, now=1010.0)
    assert store.anchor_for("chat-a", now=1011.0)["viewport_id"] == "v1"


def test_a_beating_background_tab_is_not_a_person(store):
    """Heartbeats must not count as activity, or a hidden tab left open
    overnight outranks the window someone is typing in."""
    store.announce(viewport_id="v1", visible=True, active=True, now=1000.0)
    store.announce(viewport_id="v2", visible=False, active=False, now=1030.0)
    assert store.anchor_for("chat-a", now=1031.0)["viewport_id"] == "v1"


def test_a_chat_with_no_desktop_of_its_own_falls_to_the_live_one(store):
    """Chatting in pantheon-ui with the desktop in another tab."""
    store.announce(viewport_id="v1", active=True, now=1000.0)
    store.announce(clients=[{"client_id": "c1", "chat_id": "chat-a",
                             "host_viewport_id": ""}], now=1000.0)
    got = store.anchor_for("chat-a", now=1001.0)
    assert got["viewport_id"] == "v1"
    assert got["reason"] == "most recently active viewport"


def test_a_client_whose_host_died_does_not_anchor_to_a_ghost(store):
    """The Agent window's page is gone but its lease has not lapsed yet."""
    store.announce(viewport_id="v1",
                   clients=[{"client_id": "c1", "chat_id": "chat-a", "host_viewport_id": "v1"}],
                   now=1000.0)
    store.announce(viewport_id="v2", now=1000.0)
    store.leave(viewport_id="v1", now=1001.0)          # page closed, client lingers
    got = store.anchor_for("chat-a", now=1002.0)
    assert got["viewport_id"] == "v2"
    assert got["reason"] == "most recently active viewport"


def test_no_viewport_at_all_is_said_out_loud(store):
    got = store.anchor_for("chat-a", now=1000.0)
    assert got["viewport_id"] is None
    assert "no viewport" in got["reason"]      # never a silent nothing


def test_two_pages_beating_at_once_are_one_registry(tmp_path):
    """Read-modify-write, like the session document: the toolset is a
    ProcessJob per toolset, so two announcements can land in two processes."""
    a = PresenceStore(work_dir=tmp_path)
    b = PresenceStore(work_dir=tmp_path)
    a.announce(viewport_id="v1", now=1000.0)
    b.announce(viewport_id="v2", now=1000.0)
    assert sorted(a.current(now=1001.0)["viewports"]) == ["v1", "v2"]
    assert sorted(b.current(now=1001.0)["viewports"]) == ["v1", "v2"]


def test_a_registry_from_a_dead_container_describes_nobody(tmp_path):
    """Those browsers lost their connection when the sandbox did — restoring
    them would populate the desktop with a crowd of ghosts."""
    store = PresenceStore(work_dir=tmp_path)
    store.announce(viewport_id="v1", now=1000.0)

    record = json.loads((tmp_path / ".pantheon" / "desktop-presence.json").read_text())
    record["boot"] = "a-previous-container"
    assert Presence.from_record(record).viewports == {}
