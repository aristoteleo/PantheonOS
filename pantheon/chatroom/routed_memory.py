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


def project_memory_dir(project_path: str | Path) -> str:
    """The memory dir for a project directory: ``<project>/.pantheon/memory``."""
    return str(Path(project_path).resolve() / ".pantheon" / "memory")


class ProjectRoutedMemoryManager:
    def __init__(self, home_dir: str | Path, use_jsonl: bool = True):
        self._home_dir = str(Path(home_dir).resolve())
        self._active_dir = self._home_dir
        self._use_jsonl = use_jsonl
        self._managers: dict[str, MemoryManager] = {}
        self._chat_dir: dict[str, str] = {}
        self._search_dirs: list[str] = []
        self._mgr(self._home_dir)  # eagerly create home

    # ---- manager cache ----------------------------------------------------
    def _mgr(self, d: str | Path) -> MemoryManager:
        key = str(Path(d).resolve())
        m = self._managers.get(key)
        if m is None:
            Path(key).mkdir(parents=True, exist_ok=True)
            m = MemoryManager(key, use_jsonl=self._use_jsonl)
            self._managers[key] = m
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
        for cand in [self._active_dir, self._home_dir, *self._search_dirs]:
            if self._exists_in(chat_id, cand):
                self._chat_dir[chat_id] = cand
                return cand
        # Unknown (brand-new chat not yet on disk) → active project.
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

    @property
    def path(self):
        return self._mgr(self._active_dir).path
