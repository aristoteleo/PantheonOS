"""The desktop session — one document, however many views are open on it.

A desktop is not a page: it is a view onto THIS pod, which already owns the
files, the processes and the installed apps. Which windows are open is a
property of the machine, so the record of them belongs here rather than in
whichever browser happens to be looking.

Before this, each viewport kept its own window list and wrote the whole thing
to `.pantheon/desktop.json` on a debounce, reading it back only at boot and
only onto an empty desktop. Two viewports were therefore two writers with no
reader between them: they took turns overwriting each other, neither ever saw
the result, and the loser found out at the next reload. The sidebar panel and
the popped-out page showed different desktops because, in every sense that
mattered, they *were* different desktops.

Now clients send INTENTS and this applies them. Every change bumps `seq` and
goes out as a delta on a pod-scoped stream, so a viewport that misses one can
tell (its own seq no longer follows) and ask for the whole document back
rather than drifting. Ids are minted here, which retires an entire class of
bug: a per-tab counter gave every copy its own `win-3`.

What is deliberately NOT in here: which space a viewport is looking at, and
which window has its keyboard focus. Those belong to the person, not the
machine — sharing them would drag one viewer's screen around when another
switched spaces, and make two people typing fight over one caret. Stacking
order IS shared: that is a property of the arrangement, and one screen has
only one of it.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger

# The stream every viewport listens on. Pod-scoped, NOT per chat: a desktop is
# the machine's, and tying its events to a conversation is what produced "the
# desktop did not answer — is an Atrium window open on this chat?". Both sides
# derive the subject as `<prefix>.pantheon.stream.<id>` and the prefix is the
# pod's, so this reaches every viewport of this pod and no other.
DESKTOP_STREAM = "desktop"

RECORD = ".pantheon/desktop.json"

# Windows the agent opens for its own purposes are not part of the desktop a
# person comes back to: their module URLs are minted against a tunnel that
# outlives nothing, and their sessions belong to the conversation.
EPHEMERAL_APPS = {"agent-view"}

MAX_SPACES = 6

# A token every process in this container agrees on, and that no other
# container can produce. It marks the record with the LIFETIME that wrote it,
# which is what tells a reload "these agent views are still live" apart from
# "these agent views point at a tunnel that died with the last container".
_BOOT_FILE = Path("/tmp/pantheon-desktop-boot")
_BOOT: str | None = None


def boot_token() -> str:
    global _BOOT
    if _BOOT is not None:
        return _BOOT
    token = uuid.uuid4().hex
    try:
        fd = os.open(_BOOT_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
    except FileExistsError:
        # Someone else in this container got there first — theirs is the one.
        try:
            token = _BOOT_FILE.read_text().strip() or token
        except OSError:
            pass
    except OSError:
        # No /tmp to agree through. A per-process token is the safe way to be
        # wrong: every load looks like a restart, so ephemeral windows are
        # dropped rather than resurrected against a dead tunnel.
        pass
    _BOOT = token
    return token


@dataclass
class DesktopSession:
    """Everything about the desktop that is the same in every view of it."""

    seq: int = 0
    # The desktop's own resolution. Viewports scale to fit it, so a window at
    # x=1200 is the same place in a 660px panel and an 1800px page. A viewport
    # may propose a larger one; see `propose_nominal`.
    nominal_w: int = 1280
    nominal_h: int = 800
    spaces: int = 1
    top_z: int = 10
    windows: dict[str, dict[str, Any]] = field(default_factory=dict)
    _next_id: int = 1

    # ── serialisation ────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "nominal_w": self.nominal_w,
            "nominal_h": self.nominal_h,
            "spaces": self.spaces,
            "top_z": self.top_z,
            "windows": self.windows,
        }

    def to_record(self) -> dict[str, Any]:
        """What lands on the volume — the WHOLE document, ephemera included.

        This file is not a backup taken on the way out, it is where the
        document lives between one call and the next, so leaving anything out
        of it deletes that thing. Ephemeral windows are dropped on the way back
        IN, and only when the record was written by an earlier container (see
        `from_record`): within one lifetime an agent view is a real window and
        has to survive the next reader as much as any other.

        The id is carried INSIDE each window: the document keys by it, a JSON
        list does not, and reading back a record whose windows have no id is
        how a restart quietly loses every window on the desktop.
        """
        return {
            "v": 2,
            "boot": boot_token(),
            "seq": self.seq,
            "nominal_w": self.nominal_w,
            "nominal_h": self.nominal_h,
            "spaces": self.spaces,
            "top_z": self.top_z,
            "next_id": self._next_id,
            "windows": [{**w, "id": wid} for wid, w in self.windows.items()],
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "DesktopSession":
        s = cls()
        if not isinstance(data, dict):
            return s
        if data.get("v") == 2:
            s.nominal_w = int(data.get("nominal_w") or 1280)
            s.nominal_h = int(data.get("nominal_h") or 800)
            s.spaces = max(1, min(MAX_SPACES, int(data.get("spaces") or 1)))
            s._next_id = max(1, int(data.get("next_id") or 1))
            s.seq = max(0, int(data.get("seq") or 0))
            s.top_z = max(s.top_z, int(data.get("top_z") or 0))
            # Written by a container that is gone: its agent views point at a
            # tunnel that died with it, so they are not part of the desktop the
            # user comes back to.
            same_life = data.get("boot") == boot_token()
            for w in data.get("windows") or []:
                wid = str(w.get("id") or "")
                if not wid:
                    continue
                if not same_life and w.get("app_id") in EPHEMERAL_APPS:
                    continue
                s.windows[wid] = w
                s.top_z = max(s.top_z, int(w.get("z") or 0))
            return s
        # v1 — the browser's own record, a list of windows with no ids. Adopt
        # it rather than dropping the user's layout on the floor at upgrade.
        s.spaces = max(1, min(MAX_SPACES, int(data.get("spaces") or 1)))
        for w in data.get("windows") or []:
            app_id = str(w.get("appId") or "")
            if not app_id or app_id in EPHEMERAL_APPS:
                continue
            s.windows[s._mint()] = _window(
                s._next_id - 1, app_id, w.get("args") or {}, w.get("title") or app_id,
                x=w.get("x"), y=w.get("y"), width=w.get("width"), height=w.get("height"),
                space=w.get("space"), minimized=w.get("minimized"),
                maximized=w.get("maximized"), fullscreen=w.get("fullscreen"),
                z=s._bump_z(),
            )
        return s

    # ── ids and stacking ─────────────────────────────────────────────────

    def _mint(self) -> str:
        wid = f"win-{self._next_id}"
        self._next_id += 1
        return wid

    def _bump_z(self) -> int:
        self.top_z += 1
        return self.top_z


def _window(
    _n: int, app_id: str, args: dict, title: str, *, z: int,
    x=None, y=None, width=None, height=None, space=None,
    minimized=None, maximized=None, fullscreen=None, path: str = "",
    opened_by: str = "",
) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "title": title,
        "path": path,
        "args": args,
        "x": int(x if x is not None else 80),
        "y": int(y if y is not None else 64),
        "width": int(width if width is not None else 720),
        "height": int(height if height is not None else 460),
        "space": int(space if space is not None else 1),
        "z": z,
        "minimized": bool(minimized),
        "maximized": bool(maximized),
        "fullscreen": bool(fullscreen),
        "status": "opening",
        "opened_by": opened_by,
        "updated_seq": 0,
    }


class DesktopSessionStore:
    """Owns the document, applies intents, and says what changed.

    Every mutator returns `(ops, result)`: the ops go out as a delta, the
    result goes back to whoever asked. The mutators themselves neither read
    nor write the record — they are a pure function of the document, testable
    without a pod — but `apply` wraps them in one.

    THE RECORD IS THE DOCUMENT, not a backup of it. A process-wide singleton
    was not enough: the toolset does not run in one process, so `desktop_intent`
    and `desktop_session_get` could land on different copies of a Python object
    and the second would honestly report an empty desktop while the first had
    just opened a window on it. The only thing every one of them shares is the
    filesystem, so each call reads the record, applies, and writes it back
    under a lock. At desktop rates — a few clicks a second — that costs a
    stat and a few kilobytes, and it is the difference between a shared
    session and three private ones.
    """

    def __init__(self, work_dir: Path | None = None):
        self.session = DesktopSession()
        self._work_dir = work_dir
        self._dirty = False
        self._loaded = False

    # ── persistence ──────────────────────────────────────────────────────

    def load(self) -> None:
        """Adopt the record. A missing or broken one is an empty desktop.

        Called before every read and every write, not once at boot: another
        process may have applied an intent since we last looked, and the whole
        point is to notice. Deliberately NOT skipped when the file looks
        unchanged — an mtime-and-size guard would silently keep a stale
        document whenever two writes of the same length land in one filesystem
        tick, which is the exact failure this read-through exists to prevent.
        The record is kilobytes; re-reading it is cheaper than being wrong.
        """
        self._loaded = True
        path = self._record_path()
        if path is None:
            return
        try:
            raw = path.read_text()
        except OSError:
            return
        try:
            self.session = DesktopSession.from_record(json.loads(raw))
            self._dirty = False
        except Exception as e:  # noqa: BLE001
            logger.warning("desktop session record unreadable, starting empty: {}", e)

    def save(self) -> None:
        if not self._dirty:
            return
        with self._locked():
            self._write()

    def _write(self) -> None:
        """Replace the record atomically. Caller holds the lock."""
        path = self._record_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A reader without the lock (a person, a backup) must never catch
            # the file half-written, so build it beside and rename over.
            tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self.session.to_record(), indent=1))
            os.replace(tmp, path)
            self._dirty = False
        except Exception as e:  # noqa: BLE001
            # A failed save costs one snapshot; a SILENT one costs the whole
            # feature, invisibly. The browser's version shipped with that bug.
            logger.warning("desktop session save failed: {}", e)

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
        """Serialise readers and writers across processes.

        Two intents arriving at once must not both read seq 7 and both write
        seq 8 — one of the two changes would simply not exist, while its
        client had already been told it did.
        """
        path = self._record_path()
        if path is None:
            yield
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path.with_name(path.name + ".lock"), "a+")
        except OSError:
            yield  # unlockable is still better than unusable
            return
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()

    def current(self) -> dict[str, Any]:
        """The document as it stands, from whoever wrote it last."""
        with self._locked():
            self.load()
            return self.session.snapshot()

    def where(self) -> dict[str, Any]:
        """Which copy of the desktop this is — pid, container, record.

        Kept because the failure it diagnoses is silent: a viewport talking to
        a different document simply sees an empty desktop, with no error to
        go on. Compare these across viewports and the answer is immediate.
        """
        return {
            "pid": os.getpid(),
            "boot": boot_token()[:8],
            "record": str(self._record_path() or ""),
        }

    # ── intents ──────────────────────────────────────────────────────────

    def apply(self, kind: str, args: dict[str, Any]) -> tuple[list[dict], dict]:
        handler = getattr(self, f"_do_{kind}", None)
        if handler is None:
            raise ValueError(f"unknown desktop intent '{kind}'")
        with self._locked():
            # Read-modify-write: whatever anyone else did lands before this
            # intent does, so seq stays monotonic and no change is overwritten.
            self.load()
            ops, result = handler(args or {})
            if ops:
                self.session.seq += 1
                for op in ops:
                    op["seq"] = self.session.seq
                self._dirty = True
                self._write()
        return ops, result

    def _touch(self, wid: str) -> dict:
        w = self.session.windows.get(wid)
        if w is None:
            raise KeyError(f"no window '{wid}'")
        w["updated_seq"] = self.session.seq + 1
        return w

    def _upsert(self, wid: str) -> dict:
        return {"op": "upsert", "id": wid, "window": self.session.windows[wid]}

    def _do_open(self, a: dict) -> tuple[list[dict], dict]:
        s = self.session
        app_id = str(a.get("app_id") or "")
        if not app_id:
            raise ValueError("open needs an app_id")
        wid = str(a.get("window_id") or "")
        if wid and wid in s.windows:
            # Reuse: showing a different file in a window the user already has
            # beats piling up another one.
            w = s.windows[wid]
            w["args"] = a.get("args") or w["args"]
            w["path"] = a.get("path") or w["path"]
            w["title"] = a.get("title") or w["title"]
            w["status"] = "opening"
            w["z"] = s._bump_z()
            w["minimized"] = False
            w["updated_seq"] = s.seq + 1
            return [self._upsert(wid)], {"window_id": wid, "reused": True}
        wid = s._mint()
        # Cascade, so a second window of the same app does not hide the first.
        offset = (len(s.windows) % 8) * 28
        s.windows[wid] = _window(
            0, app_id, a.get("args") or {}, str(a.get("title") or app_id),
            z=s._bump_z(), path=str(a.get("path") or ""),
            x=a.get("x", 80 + offset), y=a.get("y", 64 + offset),
            width=a.get("width"), height=a.get("height"),
            space=min(s.spaces, max(1, int(a.get("space") or 1))),
            opened_by=str(a.get("opened_by") or ""),
        )
        s.windows[wid]["updated_seq"] = s.seq + 1
        return [self._upsert(wid)], {"window_id": wid, "reused": False}

    def _do_close(self, a: dict) -> tuple[list[dict], dict]:
        wid = str(a.get("window_id") or "")
        if wid not in self.session.windows:
            return [], {"closed": False}
        self.session.windows.pop(wid)
        return [{"op": "remove", "id": wid}], {"closed": True}

    def _do_move(self, a: dict) -> tuple[list[dict], dict]:
        w = self._touch(str(a.get("window_id") or ""))
        w["x"] = max(0, int(a.get("x") or 0))
        w["y"] = max(0, int(a.get("y") or 0))
        return [self._upsert(str(a["window_id"]))], {}

    def _do_resize(self, a: dict) -> tuple[list[dict], dict]:
        w = self._touch(str(a.get("window_id") or ""))
        w["width"] = max(320, int(a.get("width") or 320))
        w["height"] = max(180, int(a.get("height") or 180))
        return [self._upsert(str(a["window_id"]))], {}

    def _do_raise(self, a: dict) -> tuple[list[dict], dict]:
        wid = str(a.get("window_id") or "")
        w = self._touch(wid)
        w["z"] = self.session._bump_z()
        w["minimized"] = False
        return [self._upsert(wid), {"op": "meta", "top_z": self.session.top_z}], {}

    def _do_set(self, a: dict) -> tuple[list[dict], dict]:
        """Patch the flags a window carries. Only these keys, by name: an
        open-ended merge would let a stale client write geometry back."""
        wid = str(a.get("window_id") or "")
        w = self._touch(wid)
        patch = a.get("patch") or {}
        for key in ("title", "minimized", "maximized", "fullscreen", "status", "path"):
            if key in patch:
                w[key] = patch[key]
        if "space" in patch:
            w["space"] = min(self.session.spaces, max(1, int(patch["space"] or 1)))
        if "args" in patch and isinstance(patch["args"], dict):
            w["args"] = {**(w.get("args") or {}), **patch["args"]}
        return [self._upsert(wid)], {}

    def _do_spaces(self, a: dict) -> tuple[list[dict], dict]:
        n = max(1, min(MAX_SPACES, int(a.get("count") or 1)))
        s = self.session
        if n == s.spaces:
            return [], {"spaces": n}
        ops: list[dict] = []
        if n < s.spaces:
            # Windows on a retired space fall into the one below it, as they do
            # when a space is closed by hand.
            for wid, w in s.windows.items():
                if w["space"] > n:
                    w["space"] = n
                    w["updated_seq"] = s.seq + 1
                    ops.append(self._upsert(wid))
        s.spaces = n
        ops.append({"op": "meta", "spaces": n})
        return ops, {"spaces": n}

    def _do_nominal(self, a: dict) -> tuple[list[dict], dict]:
        """A viewport proposes the desktop's resolution.

        Grow-only, and only from a viewport big enough to hold it: the desktop
        should be as large as the largest screen looking at it, and a narrow
        panel attaching must not shrink the page's desktop under it.
        """
        s = self.session
        w = max(640, int(a.get("w") or 0))
        h = max(480, int(a.get("h") or 0))
        if w <= s.nominal_w and h <= s.nominal_h:
            return [], {"nominal_w": s.nominal_w, "nominal_h": s.nominal_h}
        s.nominal_w = max(s.nominal_w, w)
        s.nominal_h = max(s.nominal_h, h)
        return ([{"op": "meta", "nominal_w": s.nominal_w, "nominal_h": s.nominal_h}],
                {"nominal_w": s.nominal_w, "nominal_h": s.nominal_h})


# One pod, one desktop.
#
# The toolset is instantiated per connection, so holding the store on the
# instance gave every browser its own document: three viewports attached at
# three different seqs and never saw each other's windows, which is the exact
# bug this module exists to fix, reintroduced one layer up.
#
# Process-wide was the second wrong answer. The toolset does not run in one
# process either (`run_toolsets` submits a ProcessJob per toolset), so this is
# a CACHE of the record rather than the document itself — every call reloads it
# if the file has moved on. What makes the desktop shared is the record; this
# just saves re-parsing it when nothing has changed.
_STORE: DesktopSessionStore | None = None


def get_store() -> DesktopSessionStore:
    global _STORE
    if _STORE is None:
        _STORE = DesktopSessionStore()
        _STORE.load()
    return _STORE
