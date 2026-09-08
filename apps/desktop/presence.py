"""Who is looking at this desktop, and where each chat is anchored.

The session document (``desktop_session.py``) says which windows exist. This
says which SCREENS exist — and that is a different kind of fact, so it lives in
a different file with different rules.

Two kinds of client register here:

  * a **viewport** — something rendering the desktop (the side panel, or
    ``desktop.html``). Directed requests are addressed to one, and a cursor
    belongs to one.
  * a **chat client** — one chat UI. One per Agent app *window* in the desktop,
    one per chat pane in pantheon-ui. It carries the chat it shows and the
    ``host_viewport_id`` of the desktop in the same page.

That host field is the whole anchoring link. `anchor_for(chat_id)` takes the
chat's clients, picks the most recently active one, and follows its host — so
three Agent windows in one page all resolve to the same viewport, and a chat
open in two places anchors wherever the person last touched it.

**Registration is a LEASE, not a registration.** Unregistering on close is
best-effort and cannot be anything more: `pagehide` fires when a tab is closed
and not when the browser crashes, the network drops, or a laptop sleeps — and
every one of those would otherwise leave an entry claiming to be a live screen.
An agent addressed to it waits for a timeout and reports "the desktop did not
answer", which is the exact failure this whole line of work exists to end. So
clients renew on a heartbeat and entries expire on their own; the explicit
leave only makes the common case instant instead of taking a TTL.

Expiry is applied when the registry is READ, not only by a sweeper. If a
periodic reaper were the only thing enforcing it, its period would be a window
in which the agent is addressed to a dead tab.

Not in process memory (the toolset is a ProcessJob per toolset — the lesson
desktop_session.py learned the hard way) and deliberately not in the session
document either: the two have opposite lifetimes and opposite broadcast rules.
A heartbeat is not a change anyone needs to hear about, and folding it into the
document would bump `seq` and fan out to every viewport every few seconds per
open tab, for nothing.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger

from .desktop_session import boot_token

RECORD = ".pantheon/desktop-presence.json"

# How long a lease survives without renewal, and how often a client should
# renew. The gap is deliberate: a client gets two chances to miss a beat (a
# stalled tab, one dropped request) before it is declared gone.
TTL_S = 45.0
HEARTBEAT_S = 15.0


@dataclass
class Presence:
    """Every screen currently looking at this desktop."""

    viewports: dict[str, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Keep fences after leases leave/expire: an already timed-out RPC may still
    # execute later. Page ids are never reused, and the boot token clears this
    # compact history when its runtime can no longer deliver old requests.
    sequences: dict[str, int] = field(default_factory=dict)
    entity_owners: dict[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "v": 1,
            # A registry written by a container that is gone describes screens
            # that cannot exist: every one of those browsers lost its
            # connection when the sandbox did.
            "boot": boot_token(),
            "viewports": self.viewports,
            "clients": self.clients,
            "sequences": self.sequences,
            "entity_owners": self.entity_owners,
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "Presence":
        if not isinstance(data, dict) or data.get("boot") != boot_token():
            return cls()
        p = cls()
        p.viewports = dict(data.get("viewports") or {})
        p.clients = dict(data.get("clients") or {})
        p.sequences = dict(data.get("sequences") or {})
        p.entity_owners = dict(data.get("entity_owners") or {})
        return p


class PresenceStore:
    """The registry, read through and written through a lock.

    Mutators take `now` explicitly so the expiry rules can be tested without
    sleeping through a TTL.
    """

    def __init__(self, work_dir: Path | None = None):
        self.presence = Presence()
        self._work_dir = work_dir

    # ── persistence (same discipline as DesktopSessionStore) ─────────────

    def _record_path(self) -> Path | None:
        if self._work_dir is None:
            try:
                from pantheon.settings import get_settings

                self._work_dir = Path(get_settings().work_dir)
            except Exception:  # noqa: BLE001
                return None
        return self._work_dir / RECORD

    @contextmanager
    def _locked(self):
        path = self._record_path()
        if path is None:
            yield
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path.with_name(path.name + ".lock"), "a+")
        except OSError:
            yield
            return
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()

    def load(self) -> None:
        path = self._record_path()
        if path is None:
            return
        try:
            raw = path.read_text()
        except OSError:
            return
        try:
            self.presence = Presence.from_record(json.loads(raw))
        except Exception as e:  # noqa: BLE001
            logger.warning("desktop presence unreadable, starting empty: {}", e)
            self.presence = Presence()

    def _write(self) -> None:
        path = self._record_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self.presence.to_record(), indent=1))
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            logger.warning("desktop presence save failed: {}", e)

    # ── the lease ────────────────────────────────────────────────────────

    def announce(
        self,
        *,
        viewport_id: str = "",
        clients: list[dict[str, Any]] | None = None,
        visible: bool = True,
        active: bool = False,
        presence_id: str = "",
        sequence: int | None = None,
        now: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Renew this page's leases. Returns (registry, membership_changed).

        One call per page: a page has at most one viewport and any number of
        chat clients, and sending them together keeps a heartbeat to a single
        message rather than one per entity.

        `membership_changed` says whether anyone joined or left — the only
        thing worth broadcasting. A renewal that changes nothing must not
        wake every other viewport.
        """
        now = time.time() if now is None else now
        expires = now + TTL_S
        with self._locked():
            # Read-modify-write, like the session document: several pages beat
            # at once, and each must land on what the others already wrote.
            self.load()
            if not self._accept_sequence(presence_id, sequence, viewport_id,
                                         [str(c.get("client_id") or "") for c in clients or []]):
                return {**self.snapshot(now), "applied": False}, False
            p = self.presence
            before = self._members(now)
            if presence_id:
                # Versioned beats contain this page's complete lease set. A
                # newer beat may overtake a leave for an unmounted chat client,
                # so excluding that client must retire it too.
                wanted_clients = {str(c.get("client_id") or "") for c in clients or []}
                for kind, table, wanted in (
                    ("viewport", p.viewports, {viewport_id} if viewport_id else set()),
                    ("client", p.clients, wanted_clients),
                ):
                    for key in list(table):
                        if key not in wanted and p.entity_owners.get(f"{kind}:{key}") == presence_id:
                            del table[key]
            self._announce_into(p, viewport_id, clients, visible, active, now, expires)
            self._sweep(now)
            self._write()
            return self.snapshot(now), self._members(now) != before

    def _announce_into(self, p, viewport_id, clients, visible, active, now, expires):
        if viewport_id:
            prev = p.viewports.get(viewport_id) or {}
            p.viewports[viewport_id] = {
                "visible": bool(visible),
                # Only real input moves this. A background tab beating its
                # heart is not a person, and must not win "most recently
                # active" against the window someone is actually typing in.
                "last_input_at": now if active else prev.get("last_input_at", 0.0),
                "expires_at": expires,
            }
        for c in clients or []:
            cid = str(c.get("client_id") or "")
            if not cid:
                continue
            prev = p.clients.get(cid) or {}
            p.clients[cid] = {
                "chat_id": str(c.get("chat_id") or ""),
                "host_viewport_id": str(c.get("host_viewport_id") or ""),
                "last_input_at": now if active else prev.get("last_input_at", 0.0),
                "expires_at": expires,
            }

    def leave(
        self, *, viewport_id: str = "", client_ids: list[str] | None = None,
        presence_id: str = "", sequence: int | None = None,
        now: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Give up leases now rather than at expiry. Best-effort by nature."""
        now = time.time() if now is None else now
        with self._locked():
            self.load()
            if not self._accept_sequence(presence_id, sequence, viewport_id, client_ids or []):
                return {**self.snapshot(now), "applied": False}, False
            before = self._members(now)
            self.presence.viewports.pop(viewport_id, None)
            for cid in client_ids or []:
                self.presence.clients.pop(cid, None)
            self._sweep(now)
            self._write()
            return self.snapshot(now), self._members(now) != before

    def _accept_sequence(self, presence_id: str, sequence: int | None,
                         viewport_id: str, client_ids: list[str]) -> bool:
        """Fence delayed mutations from one page without requiring a viewport.

        Standalone chats also have an owner but must never be advertised as a
        desktop. Legacy callers omit both fields and retain their lease API.
        """
        entities = ([f"viewport:{viewport_id}"] if viewport_id else [])
        entities.extend(f"client:{cid}" for cid in client_ids if cid)
        if not presence_id and sequence is None:
            # Other legacy pages keep working, but a delayed unversioned
            # request cannot downgrade ids already owned by a versioned page.
            return not any(entity in self.presence.entity_owners for entity in entities)
        if not presence_id or type(sequence) is not int or sequence < 1:
            raise ValueError("presence_id and a positive sequence are required together")
        previous = self.presence.sequences.get(presence_id, 0)
        if sequence <= previous:
            return False
        self.presence.sequences[presence_id] = sequence
        for entity in entities:
            self.presence.entity_owners[entity] = presence_id
        return True

    def current(self, now: float | None = None) -> dict[str, Any]:
        """The registry as whoever wrote it last has it."""
        with self._locked():
            self.load()
            return self.snapshot(now)

    def _sweep(self, now: float) -> None:
        for table in (self.presence.viewports, self.presence.clients):
            for key in [k for k, v in table.items() if v.get("expires_at", 0) <= now]:
                del table[key]

    def _members(self, now: float) -> tuple:
        s = self.snapshot(now)
        return (tuple(sorted(s["viewports"])), tuple(sorted(s["clients"])))

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """The live registry. Expired entries are filtered HERE, not merely
        swept on a timer — a reaper's period would otherwise be a window in
        which a dead tab still looks addressable."""
        now = time.time() if now is None else now
        return {
            "viewports": {k: v for k, v in self.presence.viewports.items()
                          if v.get("expires_at", 0) > now},
            "clients": {k: v for k, v in self.presence.clients.items()
                        if v.get("expires_at", 0) > now},
            "ttl_s": TTL_S,
            "heartbeat_s": HEARTBEAT_S,
        }

    # ── the question this file exists to answer ──────────────────────────

    def anchor_for(self, chat_id: str, now: float | None = None) -> dict[str, Any]:
        """Which viewport should this chat's directed requests reach?

        The ladder, in order (docs/desktop/anchoring.md §2):

          1. the viewport hosting this chat — via the chat's own clients;
          2. failing that, the pod's most recently active viewport;
          3. failing that, nothing, and the caller answers from the document
             or says so honestly. Never a silent nothing.

        `reason` is returned with the answer because "why this screen?" is
        otherwise unanswerable after the fact, and that was the whole
        difficulty of debugging the session work.
        """
        now = time.time() if now is None else now
        live = self.current(now)
        viewports = live["viewports"]

        hosted = [
            c for c in live["clients"].values()
            if c["chat_id"] == chat_id and c["host_viewport_id"] in viewports
        ]
        if hosted:
            # Most recently touched wins, and a visible page beats a hidden
            # one that was touched at the same moment (both fresh at boot).
            best = max(hosted, key=lambda c: (
                c["last_input_at"], viewports[c["host_viewport_id"]]["visible"]))
            return {"viewport_id": best["host_viewport_id"], "reason": "hosts the chat"}

        if viewports:
            vid = max(viewports, key=lambda k: (
                viewports[k]["visible"], viewports[k]["last_input_at"]))
            return {"viewport_id": vid, "reason": "most recently active viewport"}

        return {"viewport_id": None, "reason": "no viewport is open on this pod"}


_STORE: PresenceStore | None = None


def get_store() -> PresenceStore:
    """A cache of the record, not the record. See desktop_session.get_store."""
    global _STORE
    if _STORE is None:
        _STORE = PresenceStore()
    return _STORE
