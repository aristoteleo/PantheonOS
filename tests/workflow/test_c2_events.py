"""Tests for Task C2: event schema increment + persisted slots_invalidated.

Authoritative contract §A.2 / §A.6:
  - §A.2: node_started/node_finished carry slot_id (nullable), attempt,
    cascade_epoch; node_finished additionally carries phase. node(slot="sX")
    threads slot_id onto its events.
  - §A.7 (C1 handoff): node(slot=<non-literal>) is rejected at create (a slot
    must be statically knowable, else it would orphan the node).
  - §A.6: retry_node bumps a per-workflow monotonic cascade_epoch (persisted to
    state.json), appends a {cascade_epoch, slot_ids, node_ids} record to a
    durable invalidations.jsonl sidecar, and emits workflow.slots_invalidated.
    The current workflow_state never contains superseded downstream nodes
    (journal delete semantics); the sidecar gives a reconnecting UI the history.

Pure-stdlib stub runner, no real LLM. created_at / workflow_id injected.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.engine import WorkflowEngine, _nonliteral_slot_lines
from pantheon.workflow.events import (
    WORKFLOW_NODE_FINISHED,
    WORKFLOW_NODE_STARTED,
    WORKFLOW_SLOTS_INVALIDATED,
)
from pantheon.workflow.models import NodeCall, NodeResult
from pantheon.workflow.runner import NodeRunContext, NodeRunner
from pantheon.workflow.storage import WorkflowStorage

CHAT_ID = "chat-1"
CREATED_AT = "2026-01-01T00:00:00Z"


# --- stubs ----------------------------------------------------------------- #


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, chat_id: str, event: dict) -> None:
        self.events.append((chat_id, event))

    def of_type(self, type_: str) -> list[dict]:
        return [e for _, e in self.events if e["type"] == type_]


class WritingRunner(NodeRunner):
    def __init__(self) -> None:
        self.calls: list[NodeCall] = []

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        self.calls.append(node_call)
        nid = node_call.node_id
        result = f"r{nid}"
        ref = f"context/n{nid}.json"
        path = ctx.storage.node_context_path(ctx.workflow_id, nid)
        path.write_text(
            json.dumps({"kind": "text", "result": result}), encoding="utf-8"
        )
        return NodeResult(
            node_id=nid, status="completed", result=result, result_ref=ref
        )

    def call_ids(self) -> list[int]:
        return [c.node_id for c in self.calls]


def make_engine(tmp_path, runner=None, publisher=None):
    return WorkflowEngine(
        tmp_path,
        runner=runner or WritingRunner(),
        publisher=publisher or RecordingPublisher(),
    )


async def wait_done(engine, workflow_id, timeout=5.0):
    session = engine.sessions[workflow_id]
    if session.task is not None:
        await asyncio.wait_for(asyncio.shield(session.task), timeout=timeout)


# A 3-node blueprinted script: s1, s2, s3 (sequential).
BP_SCRIPT = (
    'meta = {"blueprint": ['
    '{"slot_id": "s1", "kind": "node"},'
    '{"slot_id": "s2", "kind": "node"},'
    '{"slot_id": "s3", "kind": "node"}'
    "]}\n"
    'a = await node("first", label="a", slot="s1")\n'
    'b = await node("second", label="b", slot="s2")\n'
    'c = await node("third", label="c", slot="s3")\n'
    "return [a, b, c]\n"
)


# === §A.2: event schema ==================================================== #


@pytest.mark.asyncio
async def test_node_events_carry_reconcile_fields(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    started = pub.of_type(WORKFLOW_NODE_STARTED)
    finished = pub.of_type(WORKFLOW_NODE_FINISHED)
    assert started and finished
    for e in started + finished:
        assert "slot_id" in e
        assert "attempt" in e
        assert "cascade_epoch" in e
        assert e["attempt"] == 0
        assert e["cascade_epoch"] == 0
    # node_finished now also carries phase (§A.2 gap fill).
    for e in finished:
        assert "phase" in e


@pytest.mark.asyncio
async def test_node_slot_threads_into_events(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    started = {e["node_id"]: e for e in pub.of_type(WORKFLOW_NODE_STARTED)}
    assert started[0]["slot_id"] == "s1"
    assert started[1]["slot_id"] == "s2"
    assert started[2]["slot_id"] == "s3"


@pytest.mark.asyncio
async def test_node_without_slot_has_slot_id_none(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    # legacy script: no blueprint, no slot=
    script = 'await node("x", label="a")\nreturn 1\n'
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")
    for e in pub.of_type(WORKFLOW_NODE_STARTED):
        assert e["slot_id"] is None


# === §A.7 / C1 handoff: non-literal slot is rejected at create ============= #


def test_nonliteral_slot_lines_detects_variable():
    src = "x = 's1'\nawait node('a', slot=x)\n"
    assert _nonliteral_slot_lines(src) == [2]


def test_nonliteral_slot_lines_allows_literal_and_none():
    src = 'await node("a", slot="s1")\nawait node("b", slot=None)\nawait node("c")\n'
    assert _nonliteral_slot_lines(src) == []


def test_nonliteral_slot_lines_detects_fstring():
    src = 'i = 1\nawait node("a", slot=f"s{i}")\n'
    assert _nonliteral_slot_lines(src) == [2]


@pytest.mark.asyncio
async def test_create_rejects_nonliteral_slot_variable(tmp_path):
    engine = make_engine(tmp_path)
    script = (
        'meta = {"blueprint": [{"slot_id": "s1", "kind": "node"}]}\n'
        "x = 's1'\n"
        'await node("a", slot=x)\n'
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-nl",
        auto_start=False,
    )
    assert "error" in out
    assert "literal" in out["error"]
    assert out["line"] == 3
    assert "wf-nl" not in engine.sessions
    assert not WorkflowStorage(tmp_path).meta_path("wf-nl").exists()


@pytest.mark.asyncio
async def test_create_rejects_nonliteral_slot_fstring(tmp_path):
    engine = make_engine(tmp_path)
    script = (
        'meta = {"blueprint": [{"slot_id": "s1", "kind": "node"}]}\n'
        "i = 1\n"
        'await node("a", slot=f"s{i}")\n'
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-fs",
        auto_start=False,
    )
    assert "error" in out
    assert out["line"] == 3
    assert "wf-fs" not in engine.sessions


@pytest.mark.asyncio
async def test_create_accepts_literal_slot(tmp_path):
    engine = make_engine(tmp_path)
    out = await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf-ok",
        auto_start=False,
    )
    assert "error" not in out


# === §A.6: cascade_epoch + slots_invalidated persistence =================== #


@pytest.mark.asyncio
async def test_retry_node_bumps_and_persists_cascade_epoch(tmp_path):
    engine = make_engine(tmp_path)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    storage = WorkflowStorage(tmp_path)
    assert storage.read_state("wf1").cascade_epoch == 0

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    # Epoch bumped AND persisted to state.json (survives a fresh read).
    assert storage.read_state("wf1").cascade_epoch == 1

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=2)
    await wait_done(engine, "wf1")
    assert storage.read_state("wf1").cascade_epoch == 2


@pytest.mark.asyncio
async def test_retry_node_computes_downstream_node_ids(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    inval = pub.of_type(WORKFLOW_SLOTS_INVALIDATED)
    assert len(inval) == 1
    e = inval[0]
    # retry of node 1 invalidates 1, 2 (>= node_id), not 0.
    assert e["node_ids"] == [1, 2]
    assert e["slot_ids"] == ["s2", "s3"]
    assert e["cascade_epoch"] == 1


@pytest.mark.asyncio
async def test_slots_invalidated_persisted_and_survives_reload(tmp_path):
    engine = make_engine(tmp_path)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=2)
    await wait_done(engine, "wf1")

    # A FRESH storage object (simulating a process restart) still reads the
    # sidecar — the invalidation history is durable.
    fresh = WorkflowStorage(tmp_path)
    records = fresh.read_invalidations("wf1")
    assert len(records) == 1
    assert records[0]["cascade_epoch"] == 1
    assert records[0]["node_ids"] == [2]
    assert records[0]["slot_ids"] == ["s3"]


@pytest.mark.asyncio
async def test_multiple_retries_append_to_sidecar(tmp_path):
    engine = make_engine(tmp_path)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=2)
    await wait_done(engine, "wf1")
    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    records = WorkflowStorage(tmp_path).read_invalidations("wf1")
    assert [r["cascade_epoch"] for r in records] == [1, 2]
    assert records[0]["node_ids"] == [2]
    assert records[1]["node_ids"] == [1, 2]


# === §A.2: attempt counter ================================================= #


@pytest.mark.asyncio
async def test_attempt_increments_after_retry(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    # Retry node 1; nodes 1,2 re-run with attempt=1, node 0 stays cached at 0.
    pub.events.clear()
    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    finished = {e["node_id"]: e for e in pub.of_type(WORKFLOW_NODE_FINISHED)}
    # node 0 was a cache hit at its original attempt 0.
    assert finished[0]["attempt"] == 0
    # nodes 1, 2 re-ran -> attempt bumped to 1.
    assert finished[1]["attempt"] == 1
    assert finished[2]["attempt"] == 1


@pytest.mark.asyncio
async def test_current_state_has_no_superseded_nodes(tmp_path):
    """Delete semantics: after retry, the journal holds only the current gen."""
    engine = make_engine(tmp_path)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    state = await engine.status("wf1", CHAT_ID)
    # 3 nodes, each present exactly once, each with current attempt.
    assert [n["node_id"] for n in state["nodes"]] == [0, 1, 2]

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    state = await engine.status("wf1", CHAT_ID)
    # Still exactly 3 nodes (no duplicated/old downstream entries).
    assert [n["node_id"] for n in state["nodes"]] == [0, 1, 2]
    by_id = {n["node_id"]: n for n in state["nodes"]}
    assert by_id[1]["attempt"] == 1
    assert by_id[2]["attempt"] == 1
    assert by_id[0]["attempt"] == 0


# === §A.6: workflow_state (status) reconcile payload ======================= #


@pytest.mark.asyncio
async def test_status_returns_cascade_epoch_and_invalidations(tmp_path):
    engine = make_engine(tmp_path)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    state = await engine.status("wf1", CHAT_ID)
    assert state["cascade_epoch"] == 0
    assert state["invalidations"] == []

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    state = await engine.status("wf1", CHAT_ID)
    assert state["cascade_epoch"] == 1
    assert len(state["invalidations"]) == 1
    assert state["invalidations"][0]["node_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_status_nodes_carry_slot_id_and_attempt(tmp_path):
    engine = make_engine(tmp_path)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    state = await engine.status("wf1", CHAT_ID)
    by_id = {n["node_id"]: n for n in state["nodes"]}
    assert by_id[0]["slot_id"] == "s1"
    assert by_id[1]["slot_id"] == "s2"
    assert by_id[2]["slot_id"] == "s3"
    assert all(n["attempt"] == 0 for n in state["nodes"])


# === runtime guard (defense in depth) ====================================== #


@pytest.mark.asyncio
async def test_node_runtime_rejects_nonstr_slot(tmp_path):
    """A non-str slot reaching node() at runtime raises (orphan defense)."""
    from pantheon.workflow.api import make_api
    from pantheon.workflow.journal import Journal

    runner = WritingRunner()
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    ctx = NodeRunContext(
        workflow_id="wf1",
        storage=storage,
        chat_workdir=str(tmp_path),
        execution_context_id="wf-wf1",
    )
    journal = Journal(storage.workflow_dir("wf1") / "journal.jsonl")
    api = make_api(journal, runner, RecordingPublisher(), CHAT_ID, ctx)
    with pytest.raises(ValueError):
        api["node"]("x", slot=123)  # type: ignore[arg-type]
