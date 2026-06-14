"""Task C5 — workflow_list(chat_id) scope + unsettled filter (§A.3).

Covers the §A.3 listing contract for ``WorkflowEngine.list_workflows`` (the
backing logic for ``status(workflow_id=None)``):

  * Returns ALL unsettled workflows for the scoped chat (incl.
    ``awaiting_intervention``); each item carries ``chat_id``.
  * Unsettled-only: completed/failed/cancelled are omitted;
    running/paused/interrupted/awaiting_intervention are returned.
  * Scope isolation: chat-A's list never contains chat-B's workflow (blocks
    cross-chat listing).
  * Process-restart discovery: a FRESH engine instance (empty in-memory
    ``_by_chat``) over the same ``base_dir`` still discovers unsettled
    workflows from disk — discovery does not depend on memory.
  * ``workflow.created``-lost discovery: a workflow whose meta exists on disk
    but was never registered in memory is still discovered.
  * Item field completeness: preview (§A.8), revision (§A.4), status, goal,
    progress.

Authorization scope (C5 vs C7): the engine enforces *scope* via meta.chat_id.
The "calling session belongs to this chat_id" → 403 check is enforced at the
C7 endpoint layer; ``chat_id`` here is a scope selector, not a credential.

All tests use STUB runners; no real LLM is invoked.
"""

from __future__ import annotations

import pytest

from pantheon.workflow.engine import UNSETTLED_STATUSES, WorkflowEngine
from pantheon.workflow.models import WorkflowMeta, WorkflowState

from .test_engine import (
    CHAT_ID,
    CREATED_AT,
    RecordingPublisher,
    WritingRunner,
    make_engine,
)


SETTLED = ["completed", "failed", "cancelled"]


def _seed(engine, workflow_id, chat_id, status, *, goal="g", preview="full",
          revision=0, progress=None):
    """Write a workflow's meta + state directly to disk (no memory registration).

    Mirrors the C3 ``write_meta`` pattern: this exercises the disk-only
    discovery path — nothing is added to ``engine._by_chat`` / sessions, so it
    also models the ``workflow.created``-lost case.
    """
    engine.storage.write_meta(
        WorkflowMeta(
            workflow_id=workflow_id,
            chat_id=chat_id,
            goal=goal,
            created_at=CREATED_AT,
            status=status,
            preview=preview,
            revision=revision,
        )
    )
    engine.storage.write_state(
        WorkflowState(
            workflow_id=workflow_id,
            status=status,
            progress=progress or {"total": 3, "done": 1},
        )
    )


# --------------------------------------------------------------------------- #
# 1. Unsettled returned (incl. awaiting_intervention); each item has chat_id
# --------------------------------------------------------------------------- #


def test_returns_all_unsettled_with_chat_id(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    for i, status in enumerate(sorted(UNSETTLED_STATUSES)):
        _seed(engine, f"wf-{i}", CHAT_ID, status)

    listing = engine.list_workflows(CHAT_ID)
    got = {w["workflow_id"]: w for w in listing["workflows"]}

    assert len(got) == len(UNSETTLED_STATUSES)
    returned_statuses = {w["status"] for w in got.values()}
    assert returned_statuses == set(UNSETTLED_STATUSES)
    # awaiting_intervention is explicitly part of the unsettled set.
    assert any(w["status"] == "awaiting_intervention" for w in got.values())
    # Every item carries chat_id (UI scope re-verification).
    assert all(w["chat_id"] == CHAT_ID for w in got.values())


# --------------------------------------------------------------------------- #
# 2. Unsettled-only filter: settled statuses omitted
# --------------------------------------------------------------------------- #


def test_settled_workflows_omitted(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    _seed(engine, "live", CHAT_ID, "running")
    for status in SETTLED:
        _seed(engine, f"done-{status}", CHAT_ID, status)

    ids = {w["workflow_id"] for w in engine.list_workflows(CHAT_ID)["workflows"]}
    assert ids == {"live"}
    for status in SETTLED:
        assert f"done-{status}" not in ids


def test_all_unsettled_statuses_present(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    for status in ("running", "paused", "interrupted", "awaiting_intervention"):
        _seed(engine, f"u-{status}", CHAT_ID, status)

    ids = {w["workflow_id"] for w in engine.list_workflows(CHAT_ID)["workflows"]}
    assert ids == {
        "u-running",
        "u-paused",
        "u-interrupted",
        "u-awaiting_intervention",
    }


# --------------------------------------------------------------------------- #
# 3. Scope isolation: chat-A's list excludes chat-B's workflow
# --------------------------------------------------------------------------- #


def test_scope_isolation_across_chats(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    _seed(engine, "wf-a", "chat-A", "running")
    _seed(engine, "wf-b", "chat-B", "running")

    a_ids = {w["workflow_id"] for w in engine.list_workflows("chat-A")["workflows"]}
    b_ids = {w["workflow_id"] for w in engine.list_workflows("chat-B")["workflows"]}

    assert a_ids == {"wf-a"}
    assert b_ids == {"wf-b"}
    # chat-A must NOT leak chat-B's workflow and vice-versa.
    assert "wf-b" not in a_ids
    assert "wf-a" not in b_ids


# --------------------------------------------------------------------------- #
# 4. Process-restart discovery: fresh engine, empty memory, same base_dir
# --------------------------------------------------------------------------- #


def test_fresh_engine_discovers_from_disk(tmp_path):
    # First engine seeds disk only.
    engine1 = make_engine(tmp_path, WritingRunner())
    _seed(engine1, "wf-persist", CHAT_ID, "interrupted")

    # A brand-new engine instance over the SAME base_dir — empty _by_chat.
    engine2 = WorkflowEngine(
        tmp_path, runner=WritingRunner(), publisher=RecordingPublisher()
    )
    assert not engine2._by_chat  # memory is empty: discovery must hit disk

    ids = {w["workflow_id"] for w in engine2.list_workflows(CHAT_ID)["workflows"]}
    assert "wf-persist" in ids


# --------------------------------------------------------------------------- #
# 5. workflow.created-lost discovery: meta on disk, never registered in memory
# --------------------------------------------------------------------------- #


def test_created_event_lost_still_discovered(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    # _seed writes ONLY disk meta/state — it never touches _by_chat / sessions,
    # modelling a lost workflow.created registration.
    _seed(engine, "wf-orphan", CHAT_ID, "running")
    assert "wf-orphan" not in engine._by_chat.get(CHAT_ID, set())

    ids = {w["workflow_id"] for w in engine.list_workflows(CHAT_ID)["workflows"]}
    assert "wf-orphan" in ids


# --------------------------------------------------------------------------- #
# 6. Item field completeness: preview, revision, status, goal, progress
# --------------------------------------------------------------------------- #


def test_item_fields_complete(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    _seed(
        engine,
        "wf-fields",
        CHAT_ID,
        "paused",
        goal="ship it",
        preview="none",
        revision=7,
        progress={"total": 5, "done": 2},
    )

    [item] = engine.list_workflows(CHAT_ID)["workflows"]
    assert item == {
        "workflow_id": "wf-fields",
        "chat_id": CHAT_ID,
        "status": "paused",
        "goal": "ship it",
        "progress": {"total": 5, "done": 2},
        "preview": "none",
        "revision": 7,
    }


@pytest.mark.asyncio
async def test_status_none_branch_delegates(tmp_path):
    """status(workflow_id=None) routes through list_workflows (unsettled-only)."""
    engine = make_engine(tmp_path, WritingRunner())
    _seed(engine, "wf-live", CHAT_ID, "running")
    _seed(engine, "wf-done", CHAT_ID, "completed")

    listing = await engine.status(chat_id=CHAT_ID)
    ids = {w["workflow_id"] for w in listing["workflows"]}
    assert ids == {"wf-live"}
