"""Tests for Task C6: no-preview backend boundary cleansing (§A.7).

Authoritative invariant (§A.7):
  When ``preview == "none"``, every externally visible ``slot_id`` is ``null`` —
  the event stream, the journal, telemetry, and the node trace alike — REGARDLESS
  of what the script wrote. A script that calls ``node(slot="x")`` under a
  no-preview workflow still emits / persists / reports ``slot_id = None``.

  The invariant is ENFORCED BY THE BACKEND at the event/persistence boundary; it
  does NOT depend on the frontend reading the ``preview`` flag.

Enforcement point: ``make_api(..., slot_enabled=False)``. ``node()`` computes
``effective_slot = slot if slot_enabled else None`` and uses that for BOTH the
node events and the recorded journal entry. status() and slots_invalidated read
the journal entry's slot_id, so they inherit ``None`` automatically.

Construction note ("preview=none but script has slot="):
  The create() normal path REJECTS a script that carries ``slot=`` without a
  matching blueprint (§A.8 rule ③: "node references undeclared slot"), so a
  no-preview workflow created normally never has ``slot=``. The §A.8 fallback
  path (Leader regeneration exhausted → force ``preview="none"`` on a script that
  may STILL carry ``slot=``) is the real consumer of C6 cleansing, but that
  fallback is NOT yet implemented in Phase 1 (see test below + module conclusion).
  We therefore exercise the cleansing two ways:
    1. Unit: drive ``make_api(slot_enabled=False)`` directly with a slot.
    2. End-to-end: write a ``preview="none"`` meta + a ``slot=``-carrying script
       to storage directly (simulating the not-yet-implemented fallback's output)
       and run the engine's ``_run`` over it via a forced launch helper.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.api import make_api
from pantheon.workflow.engine import WorkflowEngine, WorkflowSession
from pantheon.workflow.events import (
    WORKFLOW_NODE_FINISHED,
    WORKFLOW_NODE_STARTED,
    WORKFLOW_SLOTS_INVALIDATED,
)
from pantheon.workflow.journal import Journal
from pantheon.workflow.models import NodeCall, NodeResult, WorkflowMeta, WorkflowState
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


def force_no_preview_workflow(engine, workflow_id, script, *, blueprint=None):
    """Persist a ``preview="none"`` workflow whose script carries ``slot=``.

    Simulates the §A.8 fallback path's output: a script the Leader could not get
    a blueprint for (regeneration exhausted) is forced to ``preview="none"`` even
    though it still contains ``node(slot="...")`` calls. The create() static
    validator would reject this, so we write meta/state/script directly and index
    a session, bypassing create.
    """
    storage = engine.storage
    storage.ensure_workflow(workflow_id)
    storage.write_meta(
        WorkflowMeta(
            workflow_id=workflow_id,
            chat_id=CHAT_ID,
            goal="g",
            created_at=CREATED_AT,
            status="pending",
            preview="none",
            blueprint=blueprint or [],
        )
    )
    storage.write_state(
        WorkflowState(workflow_id=workflow_id, status="pending")
    )
    storage.write_script(workflow_id, script)
    storage.context_dir(workflow_id).mkdir(parents=True, exist_ok=True)
    (storage.context_dir(workflow_id) / "inputs.json").write_text(
        "{}", encoding="utf-8"
    )
    journal = Journal(storage.workflow_dir(workflow_id) / "journal.jsonl")
    session = WorkflowSession(
        workflow_id=workflow_id,
        chat_id=CHAT_ID,
        script=script,
        args=None,
        journal=journal,
        status="pending",
    )
    engine._index(session)
    engine._launch(session, goal="g", phases=[])
    return session


# A script that DECLARES slots, used to simulate fallback output where the slots
# are still present in the source but the workflow is forced to no-preview.
SLOTTED_SCRIPT = (
    'a = await node("first", label="a", slot="s1")\n'
    'b = await node("second", label="b", slot="s2")\n'
    'c = await node("third", label="c", slot="s3")\n'
    "return [a, b, c]\n"
)


# === unit: make_api(slot_enabled=False) cleanses events + journal ========== #


@pytest.mark.asyncio
async def test_slot_enabled_false_cleanses_event_slot_id(tmp_path):
    pub = RecordingPublisher()
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    ctx = NodeRunContext(
        workflow_id="wf1",
        storage=storage,
        chat_workdir=str(tmp_path),
        execution_context_id="wf-wf1",
    )
    journal = Journal(storage.workflow_dir("wf1") / "journal.jsonl")
    api = make_api(
        journal, WritingRunner(), pub, CHAT_ID, ctx, slot_enabled=False
    )
    # Script wrote slot="x" but slot_enabled=False -> events carry None.
    await api["node"]("x", label="a", slot="x")

    started = pub.of_type(WORKFLOW_NODE_STARTED)
    finished = pub.of_type(WORKFLOW_NODE_FINISHED)
    assert started and finished
    for e in started + finished:
        assert e["slot_id"] is None


@pytest.mark.asyncio
async def test_slot_enabled_false_cleanses_journal_slot_id(tmp_path):
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    ctx = NodeRunContext(
        workflow_id="wf1",
        storage=storage,
        chat_workdir=str(tmp_path),
        execution_context_id="wf-wf1",
    )
    journal = Journal(storage.workflow_dir("wf1") / "journal.jsonl")
    api = make_api(
        journal, WritingRunner(), RecordingPublisher(), CHAT_ID, ctx,
        slot_enabled=False,
    )
    await api["node"]("x", label="a", slot="x")

    # The recorded journal entry has slot_id None (persistence boundary).
    fresh = Journal(storage.workflow_dir("wf1") / "journal.jsonl")
    assert all(entry.slot_id is None for entry in fresh.entries.values())


@pytest.mark.asyncio
async def test_slot_enabled_false_ignores_nonliteral_slot(tmp_path):
    """Under slot_enabled=False, even a non-str slot is cleansed, not raised.

    The create-time static validator already rejected non-literal slots when a
    blueprint exists; under no-preview the slot is going to None regardless, so
    the runtime ValueError guard (C2 defense-in-depth) is skipped — there is no
    orphan to defend against when the answer is always None.
    """
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    ctx = NodeRunContext(
        workflow_id="wf1",
        storage=storage,
        chat_workdir=str(tmp_path),
        execution_context_id="wf-wf1",
    )
    journal = Journal(storage.workflow_dir("wf1") / "journal.jsonl")
    pub = RecordingPublisher()
    api = make_api(journal, WritingRunner(), pub, CHAT_ID, ctx, slot_enabled=False)
    # A non-str slot would raise under slot_enabled=True; here it is cleansed.
    await api["node"]("x", label="a", slot=123)  # type: ignore[arg-type]
    for e in pub.of_type(WORKFLOW_NODE_STARTED):
        assert e["slot_id"] is None


@pytest.mark.asyncio
async def test_slot_enabled_true_still_raises_nonstr_slot(tmp_path):
    """Default slot_enabled=True keeps the C2 runtime orphan guard."""
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf1")
    ctx = NodeRunContext(
        workflow_id="wf1",
        storage=storage,
        chat_workdir=str(tmp_path),
        execution_context_id="wf-wf1",
    )
    journal = Journal(storage.workflow_dir("wf1") / "journal.jsonl")
    api = make_api(journal, WritingRunner(), RecordingPublisher(), CHAT_ID, ctx)
    with pytest.raises(ValueError):
        api["node"]("x", slot=123)  # type: ignore[arg-type]


# === end-to-end: preview="none" workflow with a slotted script ============= #


@pytest.mark.asyncio
async def test_no_preview_events_slot_id_null_despite_script_slots(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    force_no_preview_workflow(engine, "wf1", SLOTTED_SCRIPT)
    await wait_done(engine, "wf1")

    started = pub.of_type(WORKFLOW_NODE_STARTED)
    finished = pub.of_type(WORKFLOW_NODE_FINISHED)
    assert started and finished
    # Script wrote slot="s1"/"s2"/"s3"; the backend boundary cleared them all.
    for e in started + finished:
        assert e["slot_id"] is None


@pytest.mark.asyncio
async def test_no_preview_journal_slot_id_null(tmp_path):
    engine = make_engine(tmp_path)
    force_no_preview_workflow(engine, "wf1", SLOTTED_SCRIPT)
    await wait_done(engine, "wf1")

    journal = Journal(engine.storage.workflow_dir("wf1") / "journal.jsonl")
    assert journal.entries  # nodes ran
    assert all(entry.slot_id is None for entry in journal.entries.values())


@pytest.mark.asyncio
async def test_no_preview_status_slot_id_null(tmp_path):
    engine = make_engine(tmp_path)
    force_no_preview_workflow(engine, "wf1", SLOTTED_SCRIPT)
    await wait_done(engine, "wf1")

    state = await engine.status("wf1", CHAT_ID)
    assert state["nodes"]
    for n in state["nodes"]:
        assert n["slot_id"] is None


@pytest.mark.asyncio
async def test_no_preview_slots_invalidated_empty(tmp_path):
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    force_no_preview_workflow(engine, "wf1", SLOTTED_SCRIPT)
    await wait_done(engine, "wf1")

    # Retry node 1 -> invalidates 1,2. Their slot_ids in the journal are None,
    # so the slots_invalidated event carries an EMPTY slot_ids list.
    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    inval = pub.of_type(WORKFLOW_SLOTS_INVALIDATED)
    assert len(inval) == 1
    e = inval[0]
    assert e["node_ids"] == [1, 2]
    assert e["slot_ids"] == []  # cleansed: no externally visible orphan slot


@pytest.mark.asyncio
async def test_no_preview_no_orphan_slot_anywhere(tmp_path):
    """End-to-end: no externally visible orphan slot_id across all surfaces."""
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    force_no_preview_workflow(engine, "wf1", SLOTTED_SCRIPT)
    await wait_done(engine, "wf1")

    # Events
    for e in pub.of_type(WORKFLOW_NODE_STARTED) + pub.of_type(
        WORKFLOW_NODE_FINISHED
    ):
        assert e.get("slot_id") is None
    # Journal
    journal = Journal(engine.storage.workflow_dir("wf1") / "journal.jsonl")
    assert all(en.slot_id is None for en in journal.entries.values())
    # status() trace
    state = await engine.status("wf1", CHAT_ID)
    assert all(n["slot_id"] is None for n in state["nodes"])


@pytest.mark.asyncio
async def test_no_preview_cache_hit_events_slot_id_null(tmp_path):
    """Cache-hit re-emitted events under no-preview also carry slot_id None.

    The cleansing must hold on the cache-hit path too (``_execute`` re-publishes
    node_started/node_finished from the cached entry without re-running). After a
    retry of node 1, node 0 is a CACHE HIT and re-emits its events; assert those
    re-emitted events carry slot_id None (not just the slots_invalidated event).
    """
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    force_no_preview_workflow(engine, "wf1", SLOTTED_SCRIPT)
    await wait_done(engine, "wf1")

    # Drop the first-run events; isolate the resume run's re-emitted events.
    pub.events.clear()
    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    # node 0 was NOT invalidated (< retried node 1) -> it is a cache hit on
    # resume and re-emits started/finished. Those are the cache-hit events.
    started = {e["node_id"]: e for e in pub.of_type(WORKFLOW_NODE_STARTED)}
    finished = {e["node_id"]: e for e in pub.of_type(WORKFLOW_NODE_FINISHED)}
    assert 0 in started and 0 in finished  # cache-hit re-emit present
    assert started[0]["slot_id"] is None
    assert finished[0]["slot_id"] is None
    # And every re-emitted event (re-run nodes 1,2 + cache-hit node 0) is clean.
    for e in list(started.values()) + list(finished.values()):
        assert e["slot_id"] is None


@pytest.mark.asyncio
async def test_read_meta_failure_defaults_to_cleansing(tmp_path):
    """§A.7 conservative default: an unreadable preview cleanses slot_id.

    If ``_run`` cannot read the meta (or it lacks a preview), the boundary must
    default to the SAFE "none" behavior (slot_enabled=False) rather than passing
    the script's slot through — over-cleansing never leaks an orphan slot_id.

    We make ONLY the preview read inside ``_run`` raise (it is the first
    ``read_meta`` call in ``_run``, before ``_persist_status``) via a one-shot
    raising wrapper, then assert every emitted slot_id is None even though the
    persisted meta is a normal full-preview blueprint.
    """
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1",
        auto_start=False,
    )
    assert engine.storage.read_meta("wf1").preview == "full"

    real_read_meta = engine.storage.read_meta
    state = {"raised": False}

    def flaky_read_meta(wf_id):
        # Raise exactly once: the first call is _run's preview read.
        if not state["raised"]:
            state["raised"] = True
            raise OSError("simulated transient meta read failure")
        return real_read_meta(wf_id)

    engine.storage.read_meta = flaky_read_meta  # type: ignore[assignment]
    try:
        session = engine.sessions["wf1"]
        engine._launch(session, goal="g", phases=[])
        await wait_done(engine, "wf1")
    finally:
        engine.storage.read_meta = real_read_meta  # type: ignore[assignment]

    assert state["raised"]  # the preview read did fail
    started = pub.of_type(WORKFLOW_NODE_STARTED)
    finished = pub.of_type(WORKFLOW_NODE_FINISHED)
    assert started and finished
    # Despite the meta being full-preview with slots, the failed read forced the
    # conservative no-preview path -> all slot_ids cleansed to None.
    for e in started + finished:
        assert e["slot_id"] is None
    journal = Journal(engine.storage.workflow_dir("wf1") / "journal.jsonl")
    assert all(en.slot_id is None for en in journal.entries.values())


# === regression: preview="full" slot_id NOT cleansed ======================= #


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


@pytest.mark.asyncio
async def test_full_preview_slot_id_threads_normally(tmp_path):
    """Regression: preview="full" (blueprint + slots) is NOT mis-cleansed."""
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, publisher=pub)
    await engine.create(
        CHAT_ID, "g", BP_SCRIPT, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    # meta preview is full
    assert engine.storage.read_meta("wf1").preview == "full"

    started = {e["node_id"]: e for e in pub.of_type(WORKFLOW_NODE_STARTED)}
    assert started[0]["slot_id"] == "s1"
    assert started[1]["slot_id"] == "s2"
    assert started[2]["slot_id"] == "s3"

    state = await engine.status("wf1", CHAT_ID)
    by_id = {n["node_id"]: n for n in state["nodes"]}
    assert by_id[0]["slot_id"] == "s1"
    assert by_id[2]["slot_id"] == "s3"


# === fallback-path status conclusion ======================================= #


def test_fallback_path_not_yet_implemented(tmp_path):
    """Document: create() only ever sets preview from blueprint presence.

    The §A.8 "regeneration exhausted -> force no-preview" fallback is NOT yet
    implemented in Phase 1: the sole assignment of ``preview`` is in create()
    (``"full" if blueprint else "none"``), and a slot-carrying no-blueprint
    script is REJECTED there, never persisted. C6's cleansing is therefore
    READY-but-dormant on the real fallback; it activates automatically once the
    fallback lands. This test pins that current reality so a future fallback
    implementer notices C6 already covers them.
    """
    import inspect

    from pantheon.workflow import engine as engine_mod

    src = inspect.getsource(engine_mod.WorkflowEngine.create)
    # Exactly one preview assignment, derived from blueprint presence.
    assert src.count("preview =") == 1
    assert '"full" if blueprint else "none"' in src
