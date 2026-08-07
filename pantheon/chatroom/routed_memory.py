"""Per-project memory routing.

A chat's memory lives in *its own project's* ``.pantheon/memory`` directory. This
manager presents the same surface as :class:`MemoryManager` but routes every
operation to the right project store:

- **per-chat ops** (``get_memory`` / ``save_one`` / ``delete_memory`` /
  ``update_memory_name``) route to whichever project dir actually holds the chat
  (searched on disk, then cached). So a chat running in project A always
  reads/writes A's store even after you switch the active project to B — no
  global manager to get out of sync, hence concurrency-safe.
- **list + new-chat ops** (``list_memory_metadata`` / ``new_memory`` / ``path``)
  target the *active* project dir (set via :meth:`set_active_dir`). Entering a
  project therefore lists that project's own chats, and new chats are created in
  it.

This replaces the historical conflation where "project" was merely a grouping
tag on a single central memory store.
"""

from pathlib import Path

from loguru import logger

from pantheon.internal.memory import MemoryManager
import time as _time


def project_memory_dir(project_path: str | Path) -> str:
    """The memory dir for a project directory: ``<project>/.pantheon/memory``."""
    return str(Path(project_path).resolve() / ".pantheon" / "memory")



#: How long an id with no memory file is remembered as having none.
_MISS_TTL_SECONDS = 30.0


class ProjectRoutedMemoryManager:
    def __init__(self, home_dir: str | Path, use_jsonl: bool = True):
        self._home_dir = str(Path(home_dir).resolve())
        self._active_dir = self._home_dir
        self._use_jsonl = use_jsonl
        self._managers: dict[str, MemoryManager] = {}
        self._chat_dir: dict[str, str] = {}
        #: ids with no memory file, and when we last looked. See _dir_for_chat.
        self._chat_miss: dict[str, float] = {}
        self._search_dirs: list[str] = []
        self._mgr(self._home_dir)  # eagerly create home

    # ---- manager cache ----------------------------------------------------
    def _mgr(self, d: str | Path) -> MemoryManager:
        # Fast path on the string as given. resolve() is realpath(), and on a
        # network-backed workspace that is a round trip — paid on every call,
        # for a directory that has not moved since the last one.
        raw = str(d)
        m = self._managers.get(raw)
        if m is not None:
            return m

        key = str(Path(d).resolve())
        m = self._managers.get(key)
        if m is None:
            Path(key).mkdir(parents=True, exist_ok=True)
            m = MemoryManager(key, use_jsonl=self._use_jsonl)
            self._managers[key] = m
        self._managers[raw] = m      # alias, so the next call skips resolve()
        return m

    # ---- active / search dirs --------------------------------------------
    def set_active_dir(self, d: str | Path) -> None:
        self._active_dir = str(Path(d).resolve())
        self._mgr(self._active_dir)

    @property
    def active_dir(self) -> str:
        return self._active_dir

    def set_search_dirs(self, dirs) -> None:
        out: list[str] = []
        for d in dirs:
            try:
                out.append(str(Path(d).resolve()))
            except Exception:
                continue
        self._search_dirs = out

    # ---- per-chat routing -------------------------------------------------
    def _exists_in(self, chat_id: str, d: str) -> bool:
        base = Path(d)
        return any(
            (base / f"{chat_id}{ext}").exists()
            for ext in (".jsonl", ".json", ".meta.json")
        )

    def _dir_for_chat(self, chat_id: str) -> str:
        cached = self._chat_dir.get(chat_id)
        if cached and self._exists_in(chat_id, cached):
            return cached

        # Remember the misses too, briefly.
        #
        # A hit was cached and a miss was not, so any id with no memory file
        # re-ran the whole search on every call: one _exists_in per candidate
        # directory, three stat()s each, and on a network-backed workspace
        # every one of those is a round trip — on the event loop.
        #
        # Ids with no memory file are not the rare case. `ChatRoom.proxy_toolset`
        # routes on `args["session_id"]`, and toolsets use that name for their
        # own sessions: a pty session id arrives here on every keystroke and
        # never matches anything. Measured from inside a sandbox, `pty_write`
        # (which carries one) against `pty_list` (which does not) on the same
        # toolset and the same path: 72 ms versus 62 ms typically, and
        # 1519 ms versus 63 ms when the search went wide.
        #
        # Short-lived, because a brand-new chat's file appears once it is
        # first saved, and the answer for an unknown id — the active project —
        # is what this returns anyway.
        now = _time.monotonic()
        missed_at = self._chat_miss.get(chat_id)
        if missed_at is not None and now - missed_at < _MISS_TTL_SECONDS:
            return self._active_dir

        for cand in [self._active_dir, self._home_dir, *self._search_dirs]:
            if self._exists_in(chat_id, cand):
                self._chat_dir[chat_id] = cand
                self._chat_miss.pop(chat_id, None)
                return cand

        # Unknown (brand-new chat not yet on disk) → active project.
        self._chat_miss[chat_id] = now
        return self._active_dir

    def mgr_for_chat(self, chat_id: str) -> MemoryManager:
        return self._mgr(self._dir_for_chat(chat_id))

    # ---- delegated MemoryManager surface ---------------------------------
    # per-chat (routed by chat_id)
    def get_memory(self, id: str, auto_fix: bool = False):
        return self.mgr_for_chat(id).get_memory(id, auto_fix)

    def save_one(self, memory_id: str):
        return self.mgr_for_chat(memory_id).save_one(memory_id)

    def delete_memory(self, id: str):
        result = self.mgr_for_chat(id).delete_memory(id)
        self._chat_dir.pop(id, None)
        return result

    def update_memory_name(self, memory_id: str, name: str):
        return self.mgr_for_chat(memory_id).update_memory_name(memory_id, name)

    # active-targeted
    def new_memory(self, name: str | None = None):
        memory = self._mgr(self._active_dir).new_memory(name)
        self._chat_dir[memory.id] = self._active_dir
        return memory

    def list_memory_metadata(self, include_errors: bool = False):
        return self._mgr(self._active_dir).list_memory_metadata(include_errors)

    # ---- explicit-project ops (for per-window / multi-project views) ------
    # The desktop runs one window PER project against ONE shared backend, so the
    # chat list and new-chat must be scoped to a *specific* project — not the
    # single global active dir. These target a project explicitly.
    def new_memory_in(self, project_dir: str | Path, name: str | None = None):
        """Create a new chat in a SPECIFIC project's store (not the active one)."""
        d = str(Path(project_dir).resolve())
        memory = self._mgr(d).new_memory(name)
        self._chat_dir[memory.id] = d
        return memory

    def list_memory_metadata_in(self, project_dir: str | Path, include_errors: bool = False):
        """List chat metadata in ONE specific project's store (by directory).

        A desktop project window must show the chats that physically live in its
        project's store — chats are organised by store, NOT reliably tagged with a
        ``project.name`` (pre-existing chats carry only ``workspace_mode``), so a
        name-tag filter would hide them. This lists the store directly instead."""
        d = str(Path(project_dir).resolve())
        return self._mgr(d).list_memory_metadata(include_errors)

    def list_all_memory_metadata(self, dirs=None, include_errors: bool = False):
        """Aggregate chat metadata across ALL known project stores — the home dir,
        the active dir, the configured search dirs, and any extra ``dirs`` passed
        in — deduped by chat id (first store wins). Callers filter by project name.
        This is what lets each window list its own project's chats regardless of
        which project is globally 'active'."""
        all_dirs: list[str] = [self._home_dir, self._active_dir, *self._search_dirs]
        for d in (dirs or []):
            try:
                all_dirs.append(str(Path(d).resolve()))
            except Exception:
                continue
        seen: set[str] = set()
        out: list = []
        for d in dict.fromkeys(all_dirs):  # dedupe dirs, preserve order
            try:
                for item in self._mgr(d).list_memory_metadata(include_errors):
                    cid = item.get("id") if isinstance(item, dict) else None
                    if cid is not None:
                        if cid in seen:
                            continue
                        seen.add(cid)
                    out.append(item)
            except Exception as e:
                logger.debug(f"[routed memory] list {d} failed: {e}")
                continue
        return out

    @property
    def path(self):
        return self._mgr(self._active_dir).path
