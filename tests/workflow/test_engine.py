"""Tests for the WorkflowEngine (Task 8).

All tests use a STUB :class:`NodeRunner` and a no-op / recording publisher.
No real LLM is invoked. Scripts are small real strings executed by the sandbox
against the injected stub runner. ``created_at`` and ``workflow_id`` are passed
explicitly for determinism.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.engine import (
    WorkflowEngine,
    WorkflowLimitError,
    extract_phases,
)
from pantheon.workflow.models import NodeCall, NodeResult
from pantheon.workflow.runner import NodeRunContext, NodeRunner

CHAT_ID = "chat-1"
OTHER_CHAT = "chat-2"
CREATED_AT = "2026-01-01T00:00:00Z"


# --- stubs ----------------------------------------------------------------- #


class RecordingPublisher:
    """Publisher stub recording every (chat_id, event)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, chat_id: str, event: dict) -> None:
        self.events.append((chat_id, event))

    def types(self) -> list[str]:
        return [e["type"] for _, e in self.events]


class WritingRunner(NodeRunner):
    """Stub runner that writes a real context envelope so cache reloads work.

    Writes ``context/n{id}.json`` as the ``{"kind","result"}`` envelope the API
    layer expects, returns a completed NodeResult, and records call order.
    """

    def __init__(self, *, default_status: str = "completed") -> None:
        self._default_status = default_status
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
            node_id=nid,
            status=self._default_status,
            result=result,
            result_ref=ref,
            token_cost=0,
        )

    def call_ids(self) -> list[int]:
        return [c.node_id for c in self.calls]


class HangingRunner(NodeRunner):
    """Runner that never completes — used to test cancel."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        self.started.set()
        await asyncio.Event().wait()  # hang forever
        raise AssertionError("unreachable")


# --- helpers --------------------------------------------------------------- #


def make_engine(tmp_path, runner, publisher=None, **kw):
    return WorkflowEngine(
        tmp_path,
        runner=runner,
        publisher=publisher or RecordingPublisher(),
        **kw,
    )


async def wait_done(engine, workflow_id, timeout=5.0):
    """Await the session's background task."""
    session = engine.sessions[workflow_id]
    if session.task is not None:
        await asyncio.wait_for(asyncio.shield(session.task), timeout=timeout)


# --- extract_phases -------------------------------------------------------- #


def test_extract_phases_literal():
    src = 'meta = {"goal": "g", "phases": ["A", "B"]}\nawait node("x")\n'
    assert extract_phases(src) == ["A", "B"]


def test_extract_phases_missing():
    src = 'await node("x")\n'
    assert extract_phases(src) == []


def test_extract_phases_non_literal():
    # meta value references a name -> literal_eval rejects -> []
    src = "x = 1\nmeta = {'phases': [x]}\nawait node('y')\n"
    assert extract_phases(src) == []


# --- 1. create validation failure ------------------------------------------ #


@pytest.mark.asyncio
async def test_create_validation_failure(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    out = await engine.create(
        CHAT_ID,
        "goal",
        "import os\nawait node('x')\n",  # imports are rejected
        created_at=CREATED_AT,
        workflow_id="wf-bad",
    )
    assert "error" in out
    assert "line" in out
    # No session launched, no workflow dir left running.
    assert "wf-bad" not in engine.sessions


# --- 2. covered by extract_phases tests above ------------------------------ #


# --- 3. normal execution writes the full file set -------------------------- #


@pytest.mark.asyncio
async def test_normal_execution(tmp_path):
    runner = WritingRunner()
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, runner, pub)
    script = (
        'meta = {"goal": "g", "phases": ["A"]}\n'
        'a = await node("first", label="a")\n'
        'b = await node("second", label="b")\n'
        "return [a, b]\n"
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    assert out["workflow_id"] == "wf1"
    assert out["phases"] == ["A"]
    await wait_done(engine, "wf1")

    storage = engine.storage
    wf_dir = storage.workflow_dir("wf1")
    assert (wf_dir / "meta.json").is_file()
    assert (wf_dir / "state.json").is_file()
    assert (wf_dir / "journal.jsonl").is_file()
    assert (wf_dir / "context" / "n0.json").is_file()
    assert (wf_dir / "context" / "n1.json").is_file()
    assert (wf_dir / "result.json").is_file()

    meta = storage.read_meta("wf1")
    assert meta.status == "completed"
    state = storage.read_state("wf1")
    assert state.status == "completed"

    result = json.loads((wf_dir / "result.json").read_text())
    assert result["result"] == ["r0", "r1"]

    assert "workflow.created" in pub.types()
    assert "workflow.status" in pub.types()


# --- 4. resume cache stats ------------------------------------------------- #


@pytest.mark.asyncio
async def test_resume_cache_stats(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)
    script = (
        'a = await node("first", label="a")\n'
        'b = await node("second", label="b")\n'
        "return [a, b]\n"
    )
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")
    assert runner.call_ids() == [0, 1]

    # Resume with the IDENTICAL script -> both nodes are cache hits.
    out = await engine.resume("wf1", CHAT_ID, new_script=script)
    await wait_done(engine, "wf1")
    assert out["cached_nodes"] == 2
    # will_rerun is a count or list; normalize to a count.
    rerun = out["will_rerun"]
    rerun_count = len(rerun) if isinstance(rerun, list) else rerun
    assert rerun_count == 0
    # The runner was NOT called again for the cached nodes.
    assert runner.call_ids() == [0, 1]


# --- 5. skip + retry ------------------------------------------------------- #


@pytest.mark.asyncio
async def test_skip_node(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)
    script = (
        'a = await node("first", label="a")\n'
        'b = await node("second", label="b")\n'
        "return [a, b]\n"
    )
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")

    await engine.control("wf1", CHAT_ID, "skip_node", node_id=1)
    await wait_done(engine, "wf1")

    result = json.loads(
        (engine.storage.workflow_dir("wf1") / "result.json").read_text()
    )
    # node 1 skipped -> returns None; node 0 cached.
    assert result["result"] == ["r0", None]


@pytest.mark.asyncio
async def test_retry_node(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)
    script = (
        'a = await node("first", label="a")\n'
        'b = await node("second", label="b")\n'
        "return [a, b]\n"
    )
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")
    assert runner.call_ids() == [0, 1]

    await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")
    # node 1 re-run (invalidate), node 0 cached.
    assert runner.call_ids() == [0, 1, 1]


# --- 6. cancel ------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancel(tmp_path):
    runner = HangingRunner()
    engine = make_engine(tmp_path, runner)
    script = 'await node("hang", label="h")\nreturn "done"\n'
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await asyncio.wait_for(runner.started.wait(), timeout=5.0)

    out = await engine.control("wf1", CHAT_ID, "cancel")
    assert out["status"] == "cancelled"
    assert engine.storage.read_meta("wf1").status == "cancelled"


# --- 7. interrupted recovery ----------------------------------------------- #


@pytest.mark.asyncio
async def test_recover_interrupted(tmp_path):
    from pantheon.workflow.models import WorkflowMeta, WorkflowState
    from pantheon.workflow.storage import WorkflowStorage

    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow("wf-crash")
    storage.write_meta(
        WorkflowMeta(
            workflow_id="wf-crash",
            chat_id=CHAT_ID,
            goal="g",
            created_at=CREATED_AT,
            status="running",
        )
    )
    storage.write_state(WorkflowState(workflow_id="wf-crash", status="running"))

    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)
    interrupted = engine.recover()
    assert "wf-crash" in interrupted
    assert engine.storage.read_meta("wf-crash").status == "interrupted"
    # Not re-run: no session/task created.
    assert "wf-crash" not in engine.sessions
    assert runner.calls == []


# --- 8. key event callback ------------------------------------------------- #


@pytest.mark.asyncio
async def test_key_event_callback_completed(tmp_path):
    events: list[tuple] = []

    def on_key_event(workflow_id, chat_id, kind, summary):
        events.append((workflow_id, chat_id, kind, summary))

    runner = WritingRunner()
    engine = make_engine(tmp_path, runner, on_key_event=on_key_event)
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    assert ("wf1", CHAT_ID, "completed", events[-1][3]) == events[-1]


@pytest.mark.asyncio
async def test_key_event_callback_failed_and_raise_safe(tmp_path):
    events: list[tuple] = []

    def on_key_event(workflow_id, chat_id, kind, summary):
        events.append((workflow_id, chat_id, kind, summary))
        raise RuntimeError("callback boom")  # must NOT crash the engine

    class FailingRunner(NodeRunner):
        async def run(self, node_call, ctx):
            return NodeResult(
                node_id=node_call.node_id, status="failed", error="boom"
            )

    engine = make_engine(tmp_path, FailingRunner(), on_key_event=on_key_event)
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    assert engine.storage.read_meta("wf1").status == "failed"
    assert events[-1][2] == "failed"
    assert "boom" in events[-1][3]


# --- 9. ownership check ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_ownership_checks(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")

    with pytest.raises(PermissionError):
        await engine.status("wf1", chat_id=OTHER_CHAT)
    with pytest.raises(PermissionError):
        await engine.get_output("wf1", OTHER_CHAT)
    with pytest.raises(PermissionError):
        await engine.resume("wf1", OTHER_CHAT)
    with pytest.raises(PermissionError):
        await engine.control("wf1", OTHER_CHAT, "cancel")

    # Correct owner works.
    s = await engine.status("wf1", chat_id=CHAT_ID)
    assert s["status"] == "completed"


# --- 10. max_nodes cap ----------------------------------------------------- #


@pytest.mark.asyncio
async def test_max_nodes_cap(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner, max_nodes=3)
    script = (
        "for i in range(5):\n"
        '    await node("n", label="x")\n'
        'return "done"\n'
    )
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await wait_done(engine, "wf1")
    assert engine.storage.read_meta("wf1").status == "failed"
    state = engine.storage.read_state("wf1")
    assert state.status == "failed"


# --- status & get_output detail -------------------------------------------- #


@pytest.mark.asyncio
async def test_status_and_get_output(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)
    await engine.create(
        CHAT_ID,
        "g",
        'a = await node("x", label="a")\nreturn a\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")

    # Trace summary with per-node entries.
    trace = await engine.status("wf1", chat_id=CHAT_ID)
    assert trace["status"] == "completed"
    assert any(n["node_id"] == 0 for n in trace["nodes"])

    # Final output (summary only -> path + preview, no full dump).
    out = await engine.get_output("wf1", CHAT_ID)
    assert "path" in out
    assert "preview" in out

    # Per-node output.
    node_out = await engine.get_output("wf1", CHAT_ID, node_id=0)
    assert "path" in node_out
    assert node_out["path"].endswith("n0.json")

    # List view (no workflow_id).
    listing = await engine.status(chat_id=CHAT_ID)
    assert any(w["workflow_id"] == "wf1" for w in listing["workflows"])
