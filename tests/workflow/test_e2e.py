"""End-to-end smoke tests for the dynamic-workflow pipeline (Task 11).

These drive a COMPLETE workflow through :meth:`WorkflowEngine.create` / ``resume``
with a STUB :class:`NodeRunner` (no real LLM). They prove the whole module
integrates: sandbox -> api -> journal -> storage -> events -> engine.

Determinism: ``created_at`` and ``workflow_id`` are passed explicitly; the stub
runner sequences results deterministically; the publisher is a recording fake
(not real NATS).

Note on node memory files: the production ``InProcessRunner`` writes a
``nodes/n{id}.jsonl`` memory file per node (see ``runner.py``), but a NodeRunner
stub only writes whatever IT chooses to. Our ``WritingRunner`` writes the
``context/n{id}.json`` envelope (which is what the api/journal layer actually
consumes for cache reload and result loading) and does NOT write node memory.
So these tests assert on the context envelopes — the artifacts the real runner
path produces AND the integration depends on — and explicitly document that
node-memory files are a runner-internal concern not exercised by the stub.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.engine import WorkflowEngine
from pantheon.workflow.models import NodeCall, NodeResult
from pantheon.workflow.runner import NodeRunContext, NodeRunner

CHAT_ID = "chat-e2e"
CREATED_AT = "2026-01-01T00:00:00Z"


# --- stubs ----------------------------------------------------------------- #


class RecordingPublisher:
    """Publisher stub recording every (chat_id, event) in publish order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, chat_id: str, event: dict) -> None:
        self.events.append((chat_id, event))

    def types(self) -> list[str]:
        return [e["type"] for _, e in self.events]

    def of_type(self, type_: str) -> list[dict]:
        return [e for _, e in self.events if e["type"] == type_]


class WritingRunner(NodeRunner):
    """Stub runner writing a real context envelope so cache reloads work.

    Writes ``context/n{id}.json`` as the ``{"kind","result"}`` envelope the api
    layer expects, returns a completed NodeResult, and records every call (in
    completion order) so tests can assert which node_ids the runner actually
    executed vs. which were served from the journal cache.
    """

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
            node_id=nid,
            status="completed",
            result=result,
            result_ref=ref,
            token_cost=0,
        )

    def call_ids(self) -> list[int]:
        return [c.node_id for c in self.calls]


class FailingAtRunner(WritingRunner):
    """WritingRunner that FAILS the node whose instruction contains ``fail_token``."""

    def __init__(self, fail_token: str) -> None:
        super().__init__()
        self._fail_token = fail_token

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        if self._fail_token in node_call.instruction:
            self.calls.append(node_call)
            return NodeResult(
                node_id=node_call.node_id, status="failed", error="boom"
            )
        return await super().run(node_call, ctx)


# --- helpers --------------------------------------------------------------- #


def make_engine(tmp_path, runner, publisher=None, **kw):
    return WorkflowEngine(
        tmp_path,
        runner=runner,
        publisher=publisher or RecordingPublisher(),
        **kw,
    )


async def wait_done(engine, workflow_id, timeout=5.0):
    session = engine.sessions[workflow_id]
    if session.task is not None:
        await asyncio.wait_for(asyncio.shield(session.task), timeout=timeout)


# A full workflow: meta literal, phase("scan"), parallel fan-out of 3 nodes,
# phase("review"), then a sequential node depending on a fan-out output, return.
# node_ids (deterministic source order): scan fan-out = 0,1,2 ; review = 3.
FULL_SCRIPT = (
    'meta = {"goal": "audit", "phases": ["scan", "review"]}\n'
    'phase("scan")\n'
    "scanned = await parallel([\n"
    '    lambda: node("scan alpha", label="alpha"),\n'
    '    lambda: node("scan beta", label="beta"),\n'
    '    lambda: node("scan gamma", label="gamma"),\n'
    "])\n"
    'phase("review")\n'
    'summary = await node("review findings", label="summary", '
    'inputs=("n0.json",))\n'
    "return {\"scanned\": scanned, \"summary\": summary}\n"
)


# --- 1. full workflow: artifacts, events, deterministic node_ids ----------- #


@pytest.mark.asyncio
async def test_e2e_full_workflow_artifacts_events_and_ids(tmp_path):
    runner = WritingRunner()
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, runner, pub)

    out = await engine.create(
        CHAT_ID,
        "audit",
        FULL_SCRIPT,
        args={"seed": 7},
        created_at=CREATED_AT,
        workflow_id="wfe2e",
    )
    assert out == {
        "workflow_id": "wfe2e",
        "phases": ["scan", "review"],
        "status": "running",
    }
    await wait_done(engine, "wfe2e")

    storage = engine.storage
    wf_dir = storage.workflow_dir("wfe2e")

    # ---- on-disk artifacts exist & are well-formed -----------------------
    assert (wf_dir / "meta.json").is_file()
    assert (wf_dir / "state.json").is_file()
    assert (wf_dir / "journal.jsonl").is_file()
    assert (wf_dir / "result.json").is_file()
    assert (wf_dir / "workflow.py").is_file()

    meta = json.loads((wf_dir / "meta.json").read_text())
    assert meta["status"] == "completed"
    assert meta["chat_id"] == CHAT_ID
    assert meta["created_at"] == CREATED_AT
    state = json.loads((wf_dir / "state.json").read_text())
    assert state["status"] == "completed"

    # journal.jsonl: one dict-loadable entry per node (0..3), dict-keyed load.
    lines = [
        json.loads(ln)
        for ln in (wf_dir / "journal.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert {e["node_id"] for e in lines} == {0, 1, 2, 3}
    assert all(e["status"] == "completed" for e in lines)

    # context/inputs.json carries the script args.
    inputs = json.loads((wf_dir / "context" / "inputs.json").read_text())
    assert inputs == {"seed": 7}

    # context/n{id}.json envelopes: {kind, result} for every node.
    for nid in range(4):
        env = json.loads((wf_dir / "context" / f"n{nid}.json").read_text())
        assert env == {"kind": "text", "result": f"r{nid}"}

    # NOTE: nodes/ memory files (nodes/n{id}.jsonl) are written by the real
    # InProcessRunner, NOT by this stub runner — so we assert the context
    # envelopes above (what the api/journal layer actually consumes), not node
    # memory. The nodes/ dir exists (ensure_workflow) but is empty under the stub.
    assert (wf_dir / "nodes").is_dir()

    # result.json: the final return value (scanned list + summary).
    result = json.loads((wf_dir / "result.json").read_text())["result"]
    assert result == {"scanned": ["r0", "r1", "r2"], "summary": "r3"}

    # ---- deterministic node_ids: source order, parallel fan-out ascending --
    assert runner.call_ids() and set(runner.call_ids()) == {0, 1, 2, 3}
    # The review node (id 3) ran after the fan-out ids were allocated.
    review_calls = [c for c in runner.calls if c.label == "summary"]
    assert review_calls and review_calls[0].node_id == 3
    assert review_calls[0].inputs == ("n0.json",)

    # ---- event sequence is complete & correctly ordered ------------------
    types = pub.types()
    # First event is workflow.created carrying the phases.
    assert types[0] == "workflow.created"
    created = pub.of_type("workflow.created")[0]
    assert created["phases"] == ["scan", "review"]
    assert created["goal"] == "audit"

    # node_started / node_finished per node (4 each), each carrying id + label.
    assert types.count("workflow.node_started") == 4
    assert types.count("workflow.node_finished") == 4
    started_ids = {e["node_id"] for e in pub.of_type("workflow.node_started")}
    finished_ids = {e["node_id"] for e in pub.of_type("workflow.node_finished")}
    assert started_ids == finished_ids == {0, 1, 2, 3}
    labels = {
        e["node_id"]: e["label"] for e in pub.of_type("workflow.node_started")
    }
    assert labels == {0: "alpha", 1: "beta", 2: "gamma", 3: "summary"}

    # phase_changed for each phase, in order.
    phases_seen = [e["phase"] for e in pub.of_type("workflow.phase_changed")]
    assert phases_seen == ["scan", "review"]

    # A terminal workflow.status event with status completed.
    final_status = pub.of_type("workflow.status")[-1]
    assert final_status["status"] == "completed"

    # Ordering: created strictly before every node event; the terminal status
    # event is last among status events and after all node_finished events.
    first_node_started = types.index("workflow.node_started")
    assert types.index("workflow.created") < first_node_started
    last_node_finished = len(types) - 1 - types[::-1].index(
        "workflow.node_finished"
    )
    last_status = len(types) - 1 - types[::-1].index("workflow.status")
    assert last_status > last_node_finished


# --- 2. resume unchanged script -> full prefix-cache hit ------------------- #


@pytest.mark.asyncio
async def test_e2e_resume_unchanged_full_cache_hit(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)

    await engine.create(
        CHAT_ID, "audit", FULL_SCRIPT, args={"seed": 7},
        created_at=CREATED_AT, workflow_id="wfe2e",
    )
    await wait_done(engine, "wfe2e")
    assert sorted(runner.call_ids()) == [0, 1, 2, 3]
    calls_after_run1 = list(runner.call_ids())

    # Resume with the IDENTICAL script -> every node is a cache hit.
    out = await engine.resume("wfe2e", CHAT_ID, new_script=FULL_SCRIPT)
    await wait_done(engine, "wfe2e")

    assert out["cached_nodes"] == 4  # all four prior nodes reused
    assert out["will_rerun"] == []   # nothing re-ran
    # The runner was NOT called again at all.
    assert runner.call_ids() == calls_after_run1

    # Result is still correct after the cache-only resume.
    result = engine.storage.read_result("wfe2e")
    assert result == {"scanned": ["r0", "r1", "r2"], "summary": "r3"}
    assert engine.storage.read_meta("wfe2e").status == "completed"


# --- 3. resume after editing the TAIL -> prefix hits, only tail re-runs ---- #


@pytest.mark.asyncio
async def test_e2e_resume_tail_edit_only_tail_reruns(tmp_path):
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)

    await engine.create(
        CHAT_ID, "audit", FULL_SCRIPT, args={"seed": 7},
        created_at=CREATED_AT, workflow_id="wfe2e",
    )
    await wait_done(engine, "wfe2e")
    assert sorted(runner.call_ids()) == [0, 1, 2, 3]

    # Edit ONLY the last (tail) node's instruction. The prefix fan-out
    # (ids 0,1,2) is byte-identical -> cache hits; only node 3 changes -> re-runs.
    edited = FULL_SCRIPT.replace(
        'await node("review findings", label="summary", inputs=("n0.json",))',
        'await node("review findings DEEPLY", label="summary", '
        'inputs=("n0.json",))',
    )
    assert edited != FULL_SCRIPT  # sanity: the replacement actually happened

    out = await engine.resume("wfe2e", CHAT_ID, new_script=edited)
    await wait_done(engine, "wfe2e")

    # Headline resume property: the 3 unchanged prefix nodes are cached, only
    # the changed tail node (id 3) re-ran.
    assert out["cached_nodes"] == 3
    assert out["will_rerun"] == [3]
    # Runner call ledger: run 1 ran 0,1,2,3; resume re-ran ONLY 3.
    assert runner.call_ids() == [0, 1, 2, 3, 3]
    # The re-run call had the edited instruction.
    assert runner.calls[-1].instruction == "review findings DEEPLY"
    assert engine.storage.read_meta("wfe2e").status == "completed"


@pytest.mark.asyncio
async def test_e2e_resume_appended_tail_node_only_new_node_runs(tmp_path):
    """Adding a node at the END: prefix cached, only the appended node runs."""
    runner = WritingRunner()
    engine = make_engine(tmp_path, runner)

    base = (
        'meta = {"goal": "g", "phases": ["work"]}\n'
        'phase("work")\n'
        'a = await node("first", label="a")\n'
        'b = await node("second", label="b")\n'
        "return [a, b]\n"
    )
    await engine.create(
        CHAT_ID, "g", base, created_at=CREATED_AT, workflow_id="wf-append"
    )
    await wait_done(engine, "wf-append")
    assert runner.call_ids() == [0, 1]

    # Append a third node at the tail; nodes 0 and 1 are unchanged.
    extended = base.replace(
        "return [a, b]\n",
        'c = await node("third", label="c")\nreturn [a, b, c]\n',
    )
    out = await engine.resume("wf-append", CHAT_ID, new_script=extended)
    await wait_done(engine, "wf-append")

    assert out["cached_nodes"] == 2     # prior nodes 0,1 cached
    assert out["will_rerun"] == []      # no PRIOR node re-ran
    # The new node (id 2) is genuinely executed (it was never in the journal).
    assert runner.call_ids() == [0, 1, 2]
    result = engine.storage.read_result("wf-append")
    assert result == ["r0", "r1", "r2"]


# --- 4. failing node -> awaiting_intervention + key-event ------------------ #


@pytest.mark.asyncio
async def test_e2e_failing_node_awaits_intervention_and_fires_key_event(tmp_path):
    key_events: list[tuple] = []

    def on_key_event(workflow_id, chat_id, kind, summary):
        key_events.append((workflow_id, chat_id, kind, summary))

    runner = FailingAtRunner(fail_token="FAILME")
    pub = RecordingPublisher()
    engine = make_engine(tmp_path, runner, pub, on_key_event=on_key_event)

    script = (
        'meta = {"goal": "g", "phases": ["go"]}\n'
        'phase("go")\n'
        'a = await node("ok work", label="a")\n'
        'b = await node("FAILME now", label="b")\n'  # this node fails
        "return [a, b]\n"
    )
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-fail"
    )
    await wait_done(engine, "wf-fail")

    # §A.4: an intervenable node failure (NodeError) lands the workflow in the
    # PERSISTENT, NON-terminal awaiting_intervention status — not terminal failed.
    assert engine.storage.read_meta("wf-fail").status == "awaiting_intervention"
    assert engine.storage.read_state("wf-fail").status == "awaiting_intervention"

    # Key-event callback fired with kind="awaiting_intervention".
    assert key_events, "on_key_event should have fired"
    last = key_events[-1]
    assert last[0] == "wf-fail"
    assert last[1] == CHAT_ID
    assert last[2] == "awaiting_intervention"
    assert "fail" in last[3].lower()

    # The failing node's node_finished event still carries status failed.
    finished = pub.of_type("workflow.node_finished")
    assert any(e["status"] == "failed" for e in finished)
    # Terminal workflow.status event is awaiting_intervention.
    assert pub.of_type("workflow.status")[-1]["status"] == "awaiting_intervention"
