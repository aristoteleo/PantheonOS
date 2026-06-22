"""Phase 0 (desktop multi-window): the routed memory manager must support
per-project ops against ONE shared backend — list chats across ALL projects (each
window then filters to its own) and create a new chat in a SPECIFIC project store,
not just the single global active dir.
"""
from pathlib import Path

from pantheon.chatroom.routed_memory import (
    ProjectRoutedMemoryManager,
    project_memory_dir,
)


def _mk_project(base: Path, name: str) -> str:
    proj = base / name
    (proj / ".pantheon" / "memory").mkdir(parents=True, exist_ok=True)
    return str(proj)


def test_new_memory_in_targets_specific_project(tmp_path):
    mgr = ProjectRoutedMemoryManager(str(tmp_path / "home"))
    dirA = project_memory_dir(_mk_project(tmp_path, "projA"))
    dirB = project_memory_dir(_mk_project(tmp_path, "projB"))

    # Active is A, but create a chat EXPLICITLY in B.
    mgr.set_active_dir(dirA)
    memB = mgr.new_memory_in(dirB, "in B")
    memB.set_metadata("name", "in B")  # persist (mirrors create_chat)

    # The chat is routed to B's store, not the active A.
    assert mgr._chat_dir[memB.id] == str(Path(dirB).resolve())
    assert mgr._dir_for_chat(memB.id) == str(Path(dirB).resolve())

    # A new chat via the active path still lands in A.
    memA = mgr.new_memory("in A")
    memA.set_metadata("name", "in A")
    assert mgr._dir_for_chat(memA.id) == str(Path(dirA).resolve())


def test_list_all_aggregates_across_projects_regardless_of_active(tmp_path):
    mgr = ProjectRoutedMemoryManager(str(tmp_path / "home"))
    dirA = project_memory_dir(_mk_project(tmp_path, "projA"))
    dirB = project_memory_dir(_mk_project(tmp_path, "projB"))
    mgr.set_search_dirs([dirA, dirB])
    mgr.set_active_dir(dirA)

    a = mgr.new_memory_in(dirA, "chat A"); a.set_metadata("name", "chat A")
    b = mgr.new_memory_in(dirB, "chat B"); b.set_metadata("name", "chat B")

    # Legacy active-only list sees only A's chat.
    active_ids = {m["id"] for m in mgr.list_memory_metadata(True)}
    assert a.id in active_ids and b.id not in active_ids

    # Aggregate list sees BOTH — this is what lets each window list its own
    # project's chats regardless of which project is globally active.
    all_ids = {m["id"] for m in mgr.list_all_memory_metadata([dirA, dirB], True)}
    assert a.id in all_ids and b.id in all_ids


def test_list_all_dedupes_by_chat_id(tmp_path):
    mgr = ProjectRoutedMemoryManager(str(tmp_path / "home"))
    dirA = project_memory_dir(_mk_project(tmp_path, "projA"))
    a = mgr.new_memory_in(dirA, "chat A"); a.set_metadata("name", "chat A")

    # Same dir reached via search_dirs AND the extra dirs arg → counted once.
    mgr.set_search_dirs([dirA])
    items = mgr.list_all_memory_metadata([dirA, dirA], True)
    assert [m["id"] for m in items].count(a.id) == 1
