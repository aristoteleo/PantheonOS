"""Real end-to-end test of the Pantheon Dynamic Workflow Phase-1/Phase-2 backend.

Drives the FULL stack against a real LLM (openai/gpt-5.4-mini via tok.fan):

    WorkflowEngine
      -> sandbox.run_script (restricted exec of a Leader script)
        -> api.make_api  (node/parallel/pipeline/phase/log, eager node_id)
          -> InProcessRunner (real pantheon.agent.Agent, real LLM call)
            -> file context envelope + journal.jsonl

It checks, with assertions, the properties that unit tests fake out:

  1. A sequential 2-node workflow runs end to end, the second node consumes the
     first node's file output (declared via `inputs=`), and the final return
     value is persisted to result.json.
  2. A `parallel` fan-out runs >1 node concurrently and all complete.
  3. A schema node returns a validated structured object.
  4. Events are emitted in the right shape/order (captured publisher).
  5. RESUME: re-running the SAME script reuses every node from cache
     (cached_nodes == prior node count, will_rerun == []), i.e. zero new LLM
     calls — the journal/resume contract holds against a real run.
  6. EDIT/resume: changing ONE node's instruction re-runs only that node (+ it
     is downstream-independent here), the unchanged node stays cached.

Phase-2 / §A contract extensions (Scenarios 3-6):
  3. §A.8 blueprint-on-create: blueprint declared in meta -> get_blueprint()
     returns the skeleton before nodes run.  Also tests §A.8 rejection: an
     undeclared slot reference -> structured error, no workflow persisted.
  4. §A.4 awaiting_intervention + §A.3 control intervention: deterministic
     failure injection -> workflow lands in awaiting_intervention, is
     discoverable via list_workflows, then control(skip_node) resumes it.
     Also tests §A.4 CAS: stale expected_revision -> {accepted:False}.
  5. §A.3 cross-chat isolation (403) + unknown-wf 404: engine raises
     PermissionError / FileNotFoundError on wrong chat_id / unknown wf_id.
  6. §A.6 cascade slots_invalidated: retry_node on upstream node emits and
     persists a workflow.slots_invalidated event with cascade_epoch/node_ids.
     (Extends the existing scenario_edit_resume workflow instead of new LLM run.)

Run:
    OPENAI_API_BASE=https://tok.fan/v1 OPENAI_API_KEY=sk-... \
      uv run python 20260613-workflow-e2e/e2e_real.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from pantheon.workflow.engine import WorkflowEngine
from pantheon.workflow.events import WorkflowEventPublisher

MODEL = "openai/gpt-5.4-mini"
CHAT = "chat-e2e"


class CapturePublisher(WorkflowEventPublisher):
    """A publisher that records every event instead of hitting NATS."""

    def __init__(self) -> None:
        super().__init__(nats=object())  # non-None so the lazy NATS init never runs
        self.events: list[tuple[str, dict]] = []

    async def publish(self, chat_id: str, event: dict) -> None:  # type: ignore[override]
        self.events.append((chat_id, event))

    def types(self) -> list[str]:
        return [e["type"] for _, e in self.events]


def _runner_calls_count(events) -> int:
    """DEPRECATED heuristic: node_finished/completed fires on cache HITS too, so
    this over-counts on resume. Kept only for the first-run sanity check where
    there are no cache hits. Use CountingRunner.runs for authoritative run counts.
    """
    return sum(
        1
        for _, e in events
        if e["type"] == "workflow.node_finished" and e["status"] == "completed"
    )


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    results.append((bool(cond), label))
    print(f"  [{PASS if cond else FAIL}] {label}")


# --------------------------------------------------------------------------- #
# Scenario 1: sequential + file-passing + schema + parallel, then resume.
# --------------------------------------------------------------------------- #

SCRIPT_V1 = '''
meta = {"phases": ["gather", "summarize"]}

phase("gather")
log("starting gather")

# Node 0: produce three short facts about the number 7. Writes to context/n0.json.
facts = await node(
    "List exactly three one-line interesting facts about the number 7. "
    "Output them as a plain numbered list, nothing else.",
    label="facts",
)

phase("summarize")

# Node 1: consume node 0's output (inlined via inputs=) and summarize.
# inputs= declares the dependency: its content drives cache invalidation AND
# (Phase 1) the engine inlines the upstream value into this node's prompt.
summary = await node(
    "You are given the three facts from a previous step as input. "
    "Summarize those facts in exactly one sentence. Output only the sentence.",
    inputs=("n0.json",),
    label="summary",
)

# Node 2 + 3: a parallel fan-out — two independent classifications.
sentiments = await parallel([
    lambda: node("Reply with exactly one word: is the number 7 'odd' or 'even'?", label="parity"),
    lambda: node("Reply with exactly one word: is 7 'prime' or 'composite'?", label="primality"),
])

# Node 4: a SCHEMA node — must return a validated structured object.
structured = await node(
    "Return a JSON object describing the number 7 with keys: "
    "'value' (integer) and 'is_prime' (boolean).",
    schema={
        "type": "object",
        "properties": {
            "value": {"type": "integer"},
            "is_prime": {"type": "boolean"},
        },
        "required": ["value", "is_prime"],
    },
    label="structured",
)

return {"summary": summary, "sentiments": sentiments, "structured": structured}
'''


async def scenario_sequential_and_resume(base: Path) -> None:
    print("\n=== Scenario 1: full run (seq + file-pass + parallel + schema) ===")
    pub = CapturePublisher()
    engine = WorkflowEngine(base, publisher=pub, agent_factory=None)
    # Real InProcessRunner with a factory that forces gpt-5.4-mini on every node
    # (the script sets no per-node model). Wrap it to count ACTUAL runner.run
    # invocations — the authoritative "how many nodes really executed an LLM
    # call" signal (cache hits never reach the runner).
    from pantheon.agent import Agent
    from pantheon.workflow.runner import InProcessRunner, NodeRunner

    def factory(**kw):
        kw["model"] = MODEL
        return Agent(**kw)

    class CountingRunner(NodeRunner):
        def __init__(self, inner):
            self._inner = inner
            self.runs: list[int] = []

        async def run(self, node_call, ctx):
            self.runs.append(node_call.node_id)
            return await self._inner.run(node_call, ctx)

    counter = CountingRunner(InProcessRunner(factory))
    engine.runner = counter

    out = await engine.create(
        CHAT, "describe the number 7", SCRIPT_V1, auto_start=True,
        created_at="2026-06-13T00:00:00Z",
    )
    wf_id = out["workflow_id"]
    check(out["phases"] == ["gather", "summarize"], "phases extracted from meta")

    # Wait for the background task to finish.
    session = engine.sessions[wf_id]
    await session.task

    meta = engine.storage.read_meta(wf_id)
    check(meta.status == "completed", f"workflow completed (status={meta.status})")

    # Final result.json holds the script return value.
    result = engine.storage.read_result(wf_id)
    check(isinstance(result, dict) and "summary" in result, "result.json has summary")
    check(
        isinstance(result.get("structured"), dict)
        and result["structured"].get("value") == 7
        and result["structured"].get("is_prime") is True,
        f"schema node returned validated object: {result.get('structured')!r}",
    )
    check(
        isinstance(result.get("sentiments"), list) and len(result["sentiments"]) == 2,
        f"parallel returned 2 results: {result.get('sentiments')!r}",
    )

    # Node 0's file exists and node 1 consumed it (summary is non-empty prose).
    n0 = engine.storage.node_context_path(wf_id, 0)
    check(n0.is_file(), "node 0 context file written")
    env0 = json.loads(n0.read_text())
    check(env0.get("kind") == "text" and env0.get("result"), "node 0 envelope shape")
    check(bool((result.get("summary") or "").strip()), "node 1 summary is non-empty")

    # Schema node envelope is kind=schema.
    n4 = engine.storage.node_context_path(wf_id, 4)
    env4 = json.loads(n4.read_text())
    check(env4.get("kind") == "schema", "schema node envelope kind=schema")

    # Journal recorded all 5 nodes (0..4).
    jentries = sorted(session.journal.entries)
    check(jentries == [0, 1, 2, 3, 4], f"journal has nodes 0-4: {jentries}")

    # Events: created first, a status at the end, node_started/finished pairs.
    types = pub.types()
    check(types[0] == "workflow.created", "first event is workflow.created")
    check("workflow.phase_changed" in types, "phase_changed emitted")
    # First run has no cache hits, so the runner ran exactly the 5 nodes.
    check(sorted(counter.runs) == [0, 1, 2, 3, 4],
          f"all 5 nodes executed on first run: {sorted(counter.runs)}")

    # ---- RESUME: same script, expect ALL cached, zero re-runs ----
    print("\n--- resume with identical script (expect full cache hit) ---")
    counter.runs.clear()
    resume_out = await engine.resume(wf_id, CHAT)
    check(
        resume_out["cached_nodes"] == 5 and resume_out["will_rerun"] == [],
        f"resume reused all 5, reran none: {resume_out}",
    )
    # Authoritative: the runner was NOT invoked at all (zero new LLM calls).
    check(counter.runs == [], f"resume made zero new LLM node runs: {counter.runs}")

    return engine, wf_id, pub, counter


# --------------------------------------------------------------------------- #
# Scenario 2: edit one node -> only that node (and dependents) re-run.
# --------------------------------------------------------------------------- #

SCRIPT_V2 = SCRIPT_V1.replace(
    "interesting facts about the number 7",
    "interesting facts about the number 8",
)


async def scenario_edit_resume(engine, wf_id: str, counter) -> None:
    print("\n=== Scenario 2: edit node 0 instruction -> journal prefix-cascade "
          "reruns node 0 and everything after it (intended decision-10 behavior) ===")
    counter.runs.clear()
    out = await engine.resume(wf_id, CHAT, new_script=SCRIPT_V2)
    # DESIGN NOTE (decision 10, kept): the journal's _first_miss cascade means a
    # changed early node invalidates the whole suffix — once node 0's key
    # changes, every node_id >= 0 is treated as a miss. So an edit to node 0
    # reruns all 5 nodes. (A content-hash-only model would rerun only node 0 and
    # its true dependents; the cascade is the conservative prefix guarantee.)
    reran = set(out["will_rerun"])
    check(0 in reran, f"node 0 re-ran after edit: will_rerun={out['will_rerun']}")
    check(
        out["will_rerun"] == [0, 1, 2, 3, 4] and out["cached_nodes"] == 0,
        f"cascade reruns the whole suffix after the edited node: {out}",
    )
    # Authoritative: the runner actually re-executed all 5 nodes.
    check(
        sorted(counter.runs) == [0, 1, 2, 3, 4],
        f"runner re-executed all 5 nodes (cascade): {sorted(counter.runs)}",
    )


# --------------------------------------------------------------------------- #
# Scenario 3: §A.8 blueprint-on-create + create rejection.
# 1 real LLM node (slotted). No extra resume.
# --------------------------------------------------------------------------- #

# A minimal 2-slot blueprint script.  Only 1 node fires an LLM call.
SCRIPT_BLUEPRINT = '''
meta = {
    "phases": ["analyze"],
    "blueprint": [
        {"slot_id": "s0", "phase": "analyze", "label": "Answer", "kind": "text"},
        {"slot_id": "s1", "phase": "analyze", "label": "Fact",   "kind": "text"},
    ],
}

phase("analyze")
answer = await node(
    "What is 1 + 1? Reply with just the number.",
    slot="s0",
    label="answer",
)
return {"answer": answer}
'''

# A bad script: slot="sX" is not in meta.blueprint -> must be rejected.
SCRIPT_UNDECLARED_SLOT = '''
meta = {
    "blueprint": [
        {"slot_id": "declared", "phase": "p", "label": "L", "kind": "text"},
    ],
}
answer = await node("Hello", slot="undeclared", label="bad")
return answer
'''


async def scenario_blueprint(base: Path) -> tuple:
    print("\n=== Scenario 3: §A.8 blueprint-on-create + undeclared-slot rejection ===")
    from pantheon.agent import Agent
    from pantheon.workflow.runner import InProcessRunner, NodeRunner

    def factory(**kw):
        kw["model"] = MODEL
        return Agent(**kw)

    class CountingRunner(NodeRunner):
        def __init__(self, inner):
            self._inner = inner
            self.runs: list[int] = []

        async def run(self, node_call, ctx):
            self.runs.append(node_call.node_id)
            return await self._inner.run(node_call, ctx)

    pub = CapturePublisher()
    engine = WorkflowEngine(base, publisher=pub, agent_factory=None)
    counter = CountingRunner(InProcessRunner(factory))
    engine.runner = counter

    # --- 3a: undeclared-slot rejection (NO LLM call, pure validation) ---
    print("  3a: undeclared slot -> structured rejection, no workflow persisted")
    bad_out = await engine.create(
        CHAT, "bad goal", SCRIPT_UNDECLARED_SLOT, auto_start=False,
        created_at="2026-06-13T01:00:00Z",
    )
    check("error" in bad_out, f"undeclared slot rejected: {bad_out}")
    check("undeclared" in (bad_out.get("error") or ""),
          f"error mentions the bad slot: {bad_out.get('error')!r}")
    # The rejected create must leave no discoverable workflow.
    all_wf = engine.list_workflows(CHAT)
    rejected_ids = [w["workflow_id"] for w in all_wf.get("workflows", [])]
    check(
        not any("wf-0" in wid for wid in rejected_ids),
        f"rejected workflow not in list_workflows: {rejected_ids}",
    )

    # --- 3b: valid blueprint create -> get_blueprint() returns skeleton ---
    print("  3b: valid blueprint -> get_blueprint() returns skeleton before nodes run")
    out = await engine.create(
        CHAT, "blueprint test", SCRIPT_BLUEPRINT, auto_start=False,
        created_at="2026-06-13T01:00:01Z",
    )
    check("workflow_id" in out and "error" not in out,
          f"valid blueprint create succeeded: {out}")
    wf_id_bp = out["workflow_id"]

    bp = engine.get_blueprint(wf_id_bp, CHAT)
    check(bp.get("preview") == "full",
          f"blueprint preview='full': {bp.get('preview')!r}")
    check(
        isinstance(bp.get("blueprint"), list) and len(bp["blueprint"]) == 2,
        f"blueprint has 2 declared slots: {bp.get('blueprint')!r}",
    )
    bp_slot_ids = [s.get("slot_id") for s in bp["blueprint"]]
    check(
        "s0" in bp_slot_ids and "s1" in bp_slot_ids,
        f"blueprint contains s0 and s1: {bp_slot_ids}",
    )
    check(
        "analyze" in bp.get("phases", []),
        f"blueprint phases includes 'analyze': {bp.get('phases')!r}",
    )
    # 3c: node events carry the right slot_id for the slotted node
    # (auto_start=False above; now launch it)
    print("  3c: node_started / node_finished events carry slot_id for slotted nodes")
    from pantheon.workflow.engine import extract_phases
    phases = extract_phases(SCRIPT_BLUEPRINT)
    session = engine.sessions[wf_id_bp]
    engine._launch(session, goal="blueprint test", phases=phases)
    await session.task

    meta_bp = engine.storage.read_meta(wf_id_bp)
    check(
        meta_bp.status == "completed",
        f"blueprint workflow completed (status={meta_bp.status})",
    )
    # Check node events carry slot_id.
    started_events = [
        e for _, e in pub.events
        if e.get("type") == "workflow.node_started"
        and e.get("workflow_id") == wf_id_bp
    ]
    finished_events = [
        e for _, e in pub.events
        if e.get("type") == "workflow.node_finished"
        and e.get("workflow_id") == wf_id_bp
    ]
    started_slot_ids = [e.get("slot_id") for e in started_events]
    finished_slot_ids = [e.get("slot_id") for e in finished_events]
    check(
        "s0" in started_slot_ids,
        f"node_started event carries slot_id='s0': {started_slot_ids}",
    )
    check(
        "s0" in finished_slot_ids,
        f"node_finished event carries slot_id='s0': {finished_slot_ids}",
    )
    # 3d: get_blueprint is ownership-checked (cross-chat -> PermissionError)
    perm_ok = False
    try:
        engine.get_blueprint(wf_id_bp, "wrong-chat")
    except PermissionError:
        perm_ok = True
    check(perm_ok, "get_blueprint with wrong chat_id raises PermissionError (§A.3)")

    return engine, wf_id_bp, pub, counter


# --------------------------------------------------------------------------- #
# Scenario 4: §A.4 awaiting_intervention + §A.3 control + CAS.
# Deterministic failure injection (NO extra LLM calls beyond the 1 successful
# node that runs AFTER the skip).  The failing node is injected as status=failed
# without ever calling the real runner for it.
# --------------------------------------------------------------------------- #

SCRIPT_INTERVENTION = '''
meta = {"phases": ["work"]}
phase("work")
# node 0 will be injected to fail deterministically.
result0 = await node("Say hello.", label="will_fail")
# node 1 only runs after skip.
result1 = await node("Reply with the word 'done'.", label="after_fail")
return {"r0": result0, "r1": result1}
'''


async def scenario_intervention(base: Path) -> None:
    print("\n=== Scenario 4: §A.4 awaiting_intervention + control(skip_node) + CAS ===")
    from pantheon.agent import Agent
    from pantheon.workflow.models import NodeResult
    from pantheon.workflow.runner import InProcessRunner, NodeRunner

    def factory(**kw):
        kw["model"] = MODEL
        return Agent(**kw)

    # A runner that fails node 0 deterministically; all others run normally.
    class FailOneRunner(NodeRunner):
        def __init__(self, inner, fail_node_id: int):
            self._inner = inner
            self._fail = fail_node_id
            self.runs: list[int] = []

        async def run(self, node_call, ctx):
            self.runs.append(node_call.node_id)
            if node_call.node_id == self._fail:
                return NodeResult(
                    node_id=node_call.node_id,
                    status="failed",
                    error="injected failure",
                )
            return await self._inner.run(node_call, ctx)

    pub = CapturePublisher()
    engine = WorkflowEngine(base, publisher=pub, agent_factory=None)
    fail_runner = FailOneRunner(InProcessRunner(factory), fail_node_id=0)
    engine.runner = fail_runner

    out = await engine.create(
        CHAT, "intervention test", SCRIPT_INTERVENTION, auto_start=True,
        created_at="2026-06-13T02:00:00Z",
    )
    wf_id = out["workflow_id"]
    session = engine.sessions[wf_id]
    await session.task

    meta = engine.storage.read_meta(wf_id)
    check(
        meta.status == "awaiting_intervention",
        f"injected failure -> awaiting_intervention (status={meta.status})",
    )

    # Discoverable via list_workflows (unsettled).
    wf_list = engine.list_workflows(CHAT)
    ids = [w["workflow_id"] for w in wf_list.get("workflows", [])]
    check(wf_id in ids, f"awaiting_intervention workflow visible in list_workflows")

    # CAS test: stale expected_revision must return {accepted: False}.
    stale_revision = meta.revision - 1  # intentionally wrong
    cas_out = await engine.control(
        wf_id, CHAT, "skip_node", node_id=0,
        expected_revision=stale_revision,
    )
    check(
        cas_out.get("accepted") is False,
        f"stale revision rejected (accepted={cas_out.get('accepted')}): {cas_out}",
    )
    check(
        cas_out.get("revision") == meta.revision,
        f"stale rejection echoes authoritative revision: got {cas_out.get('revision')!r}, "
        f"want {meta.revision}",
    )

    # Correct revision: skip node 0 -> workflow should resume and complete.
    current_revision = meta.revision
    skip_out = await engine.control(
        wf_id, CHAT, "skip_node", node_id=0,
        expected_revision=current_revision,
    )
    check(
        skip_out.get("accepted") is True,
        f"correct revision accepted: {skip_out}",
    )
    check(
        skip_out.get("status") in ("completed", "running", "awaiting_intervention"),
        f"post-skip status is a valid next state: {skip_out.get('status')!r}",
    )

    # After skip+resume the workflow should be completed (node 1 runs fine).
    final_meta = engine.storage.read_meta(wf_id)
    check(
        final_meta.status == "completed",
        f"after skip_node workflow completed (status={final_meta.status})",
    )


# --------------------------------------------------------------------------- #
# Scenario 5: §A.3 cross-chat isolation (403) + unknown-wf (404).
# Pure auth — NO LLM calls.
# --------------------------------------------------------------------------- #

async def scenario_auth_isolation(base: Path) -> None:
    print("\n=== Scenario 5: §A.3 cross-chat isolation (PermissionError/FileNotFoundError) ===")
    pub = CapturePublisher()
    engine = WorkflowEngine(base, publisher=pub, agent_factory=None)

    # Create a workflow under CHAT (no auto_start so no LLM calls).
    out = await engine.create(
        CHAT, "auth test", "return 1", auto_start=False,
        created_at="2026-06-13T03:00:00Z",
    )
    wf_id = out["workflow_id"]

    # --- 5a: get_blueprint with wrong chat_id ---
    perm_bp = False
    try:
        engine.get_blueprint(wf_id, "other-chat")
    except PermissionError:
        perm_bp = True
    check(perm_bp, "get_blueprint(wrong chat) raises PermissionError")

    # --- 5b: status with wrong chat_id ---
    perm_status = False
    try:
        await engine.status(wf_id, chat_id="other-chat")
    except PermissionError:
        perm_status = True
    check(perm_status, "status(wrong chat) raises PermissionError")

    # --- 5c: control with wrong chat_id ---
    perm_ctrl = False
    try:
        await engine.control(wf_id, "other-chat", "cancel")
    except PermissionError:
        perm_ctrl = True
    check(perm_ctrl, "control(wrong chat) raises PermissionError")

    # --- 5d: unknown wf_id -> FileNotFoundError ---
    fnf = False
    try:
        engine.get_blueprint("wf-nonexistent-9999", CHAT)
    except FileNotFoundError:
        fnf = True
    check(fnf, "get_blueprint(unknown wf_id) raises FileNotFoundError (404)")

    fnf2 = False
    try:
        await engine.status("wf-nonexistent-9999", chat_id=CHAT)
    except FileNotFoundError:
        fnf2 = True
    check(fnf2, "status(unknown wf_id) raises FileNotFoundError (404)")


# --------------------------------------------------------------------------- #
# Scenario 6: §A.6 cascade slots_invalidated — extends Scenario 3 blueprint wf.
# retry_node on node 0 of the already-completed blueprint workflow must emit and
# persist a workflow.slots_invalidated event.  No new LLM calls needed (the
# resume after retry re-runs node 0 with the real LLM, but it was only 1 node
# in SCRIPT_BLUEPRINT, so total is 1 extra LLM call max).
# --------------------------------------------------------------------------- #

async def scenario_slots_invalidated(
    engine, wf_id_bp: str, pub: CapturePublisher, counter
) -> None:
    print("\n=== Scenario 6: §A.6 slots_invalidated event + persisted invalidation ===")
    # Clear prior events so we can inspect only the new ones.
    events_before = len(pub.events)

    # retry_node node 0: _cascade_invalidate fires BEFORE journal.invalidate.
    counter.runs.clear()
    ctrl_out = await engine.control(
        wf_id_bp, CHAT, "retry_node", node_id=0,
        expected_revision=None,  # Leader path: no CAS
    )
    check(
        ctrl_out.get("accepted") is True,
        f"retry_node accepted: {ctrl_out}",
    )

    # Check the slots_invalidated event was emitted in this run's events.
    new_events = pub.events[events_before:]
    inv_events = [
        e for _, e in new_events
        if e.get("type") == "workflow.slots_invalidated"
        and e.get("workflow_id") == wf_id_bp
    ]
    check(
        len(inv_events) >= 1,
        f"slots_invalidated event emitted: {inv_events}",
    )
    if inv_events:
        inv = inv_events[0]
        check(
            isinstance(inv.get("cascade_epoch"), int) and inv["cascade_epoch"] >= 1,
            f"slots_invalidated carries cascade_epoch >= 1: {inv.get('cascade_epoch')!r}",
        )
        check(
            isinstance(inv.get("node_ids"), list) and 0 in inv["node_ids"],
            f"slots_invalidated node_ids includes node 0: {inv.get('node_ids')!r}",
        )

    # Check the invalidation is persisted to invalidations.jsonl.
    persisted = engine.storage.read_invalidations(wf_id_bp)
    check(
        len(persisted) >= 1,
        f"invalidation record persisted to invalidations.jsonl: {persisted}",
    )
    if persisted:
        rec = persisted[-1]
        check(
            "cascade_epoch" in rec and "node_ids" in rec,
            f"persisted record has cascade_epoch + node_ids: {rec}",
        )
        check(
            isinstance(rec.get("cascade_epoch"), int) and rec["cascade_epoch"] >= 1,
            f"persisted cascade_epoch >= 1: {rec.get('cascade_epoch')!r}",
        )


async def main() -> int:
    # Scenarios 1 and 2 share one base dir (wf reused across them).
    base12 = Path(tempfile.mkdtemp(prefix="wf-e2e-s12-"))
    # Scenario 3 + 6 share a second base (blueprint workflow).
    base36 = Path(tempfile.mkdtemp(prefix="wf-e2e-s36-"))
    # Scenario 4 gets its own base (failure injection).
    base4 = Path(tempfile.mkdtemp(prefix="wf-e2e-s4-"))
    # Scenario 5 gets its own base (auth isolation, no conflict with others).
    base5 = Path(tempfile.mkdtemp(prefix="wf-e2e-s5-"))
    try:
        # --- Scenarios 1 & 2: original real-LLM sequential + edit-resume ---
        engine, wf_id, pub, counter = await scenario_sequential_and_resume(base12)
        await scenario_edit_resume(engine, wf_id, counter)

        # --- Scenario 3: §A.8 blueprint-on-create + rejection (1 LLM call) ---
        engine_bp, wf_id_bp, pub_bp, counter_bp = await scenario_blueprint(base36)

        # --- Scenario 4: §A.4 awaiting_intervention + control + CAS ---
        await scenario_intervention(base4)

        # --- Scenario 5: §A.3 cross-chat isolation (no LLM) ---
        await scenario_auth_isolation(base5)

        # --- Scenario 6: §A.6 slots_invalidated (extends Scenario 3 wf) ---
        await scenario_slots_invalidated(engine_bp, wf_id_bp, pub_bp, counter_bp)

    finally:
        for d in (base12, base36, base4, base5):
            shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 60)
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} checks passed")
    for ok, label in results:
        if not ok:
            print(f"  FAILED: {label}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
