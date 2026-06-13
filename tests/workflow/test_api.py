"""Tests for the orchestration API (Task 7).

All tests use STUB runner / publisher and a real :class:`Journal` on a tmp file.
No real LLM is invoked.

The critical tests here prove DETERMINISTIC node_id allocation under
out-of-order completion: ids follow source order (sequential, flat-parallel,
nested-parallel) or the reserved-id scheme (multi-stage pipeline), NOT the order
in which the fake runner happens to finish each node.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.api import API_DEFAULT_CONCURRENCY, NodeError, make_api
from pantheon.workflow.journal import Journal
from pantheon.workflow.models import (
    JournalEntry,
    NodeCall,
    NodeResult,
    compute_node_key,
)
from pantheon.workflow.runner import NodeRunContext, NodeRunner
from pantheon.workflow.storage import WorkflowStorage

WF_ID = "wf-test"
CHAT_ID = "chat-1"


# --- stubs ----------------------------------------------------------------- #


class RecordingPublisher:
    """Publisher stub that records every (chat_id, event) synchronously."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, chat_id: str, event: dict) -> None:
        self.events.append((chat_id, event))

    def types(self) -> list[str]:
        return [e["type"] for _, e in self.events]


class FakeRunner(NodeRunner):
    """Runner that returns canned NodeResults and records call order/args."""

    def __init__(self, results=None, *, default_status="completed") -> None:
        # results: dict node_id -> NodeResult, or a callable(node_call)->NodeResult
        self._results = results or {}
        self._default_status = default_status
        self.calls: list[NodeCall] = []

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        self.calls.append(node_call)
        nid = node_call.node_id
        if callable(self._results):
            return self._results(node_call)
        if nid in self._results:
            return self._results[nid]
        return NodeResult(
            node_id=nid,
            status=self._default_status,
            result=f"r{nid}",
            result_ref=f"context/n{nid}.json",
            token_cost=0,
        )


class GatedRunner(NodeRunner):
    """Runner whose per-node completion is controlled by asyncio.Events.

    ``gates[node_id]`` is an Event the run for that node awaits before
    returning. Tests set the events in a chosen order to force out-of-order
    COMPLETION while node_id ALLOCATION (which happened synchronously at call
    time) is unaffected. ``writes`` envelopes so cache hits on resume work.
    """

    def __init__(self, gates: dict[int, asyncio.Event], storage=None) -> None:
        self._gates = gates
        self._storage = storage
        self.calls: list[NodeCall] = []
        self.completion_order: list[int] = []

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        self.calls.append(node_call)
        nid = node_call.node_id
        gate = self._gates.get(nid)
        if gate is not None:
            await gate.wait()
        self.completion_order.append(nid)
        ref = f"context/n{nid}.json"
        if self._storage is not None:
            self._storage.node_context_path(WF_ID, nid).write_text(
                json.dumps({"kind": "text", "result": f"r{nid}"}),
                encoding="utf-8",
            )
        return NodeResult(
            node_id=nid, status="completed", result=f"r{nid}",
            result_ref=ref, token_cost=0,
        )


@pytest.fixture
def setup(tmp_path):
    storage = WorkflowStorage(tmp_path)
    storage.ensure_workflow(WF_ID)
    journal = Journal(tmp_path / "journal.jsonl")
    ctx = NodeRunContext(
        workflow_id=WF_ID, storage=storage, chat_workdir=str(tmp_path)
    )
    publisher = RecordingPublisher()
    return tmp_path, storage, journal, ctx, publisher


def _write_envelope(storage, node_id, kind, value):
    path = storage.node_context_path(WF_ID, node_id)
    path.write_text(
        json.dumps({"kind": kind, "result": value}), encoding="utf-8"
    )
    return f"context/n{node_id}.json"


def _ids(journal):
    return sorted(journal.entries)


# --- tests ----------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cache_hit_does_not_call_runner(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    # Pre-populate journal entry at node_id 0 whose key matches.
    key = compute_node_key("do it", "generic", None, None, [])
    ref = _write_envelope(storage, 0, "text", "cached value")
    journal.record(JournalEntry(0, key, "lbl", "completed", ref, 0))

    api = make_api(journal, runner, publisher, CHAT_ID, ctx)
    out = await api["node"]("do it", label="lbl")

    assert out == "cached value"
    assert runner.calls == []  # runner NOT called
    types = publisher.types()
    assert "workflow.node_started" in types
    assert "workflow.node_finished" in types


@pytest.mark.asyncio
async def test_cache_hit_skipped_returns_none(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    journal.record(JournalEntry(0, "anykey", "lbl", "skipped", None, 0))

    api = make_api(journal, runner, publisher, CHAT_ID, ctx)
    out = await api["node"]("anything", label="lbl")

    assert out is None
    assert runner.calls == []


@pytest.mark.asyncio
async def test_miss_calls_runner_records_and_emits(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()

    api = make_api(journal, runner, publisher, CHAT_ID, ctx)
    out = await api["node"]("hello", label="greet")

    assert out == "r0"
    assert len(runner.calls) == 1
    assert runner.calls[0].node_id == 0
    assert runner.calls[0].instruction == "hello"
    # journal now has the entry
    assert _ids(journal) == [0]
    assert journal.entries[0].node_id == 0
    assert journal.entries[0].status == "completed"
    types = publisher.types()
    assert types.count("workflow.node_started") == 1
    assert types.count("workflow.node_finished") == 1


@pytest.mark.asyncio
async def test_failed_raises_node_error(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner(
        results={0: NodeResult(0, "failed", error="boom")},
    )
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    with pytest.raises(NodeError) as ei:
        await api["node"]("x", label="lbl")
    assert ei.value.node_id == 0
    assert ei.value.label == "lbl"
    assert ei.value.error == "boom"
    # failed entry still recorded
    assert journal.entries[0].status == "failed"


@pytest.mark.asyncio
async def test_parallel_isolation(setup):
    tmp_path, storage, journal, ctx, publisher = setup

    def make_result(nc):
        if nc.node_id == 1:
            return NodeResult(1, "failed", error="mid")
        return NodeResult(
            nc.node_id, "completed", result=f"r{nc.node_id}",
            result_ref=f"context/n{nc.node_id}.json",
        )

    runner = FakeRunner(results=make_result)
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    out = await api["parallel"](
        [
            lambda: api["node"]("a", label="a"),
            lambda: api["node"]("b", label="b"),
            lambda: api["node"]("c", label="c"),
        ]
    )
    assert out[0] == "r0"
    assert isinstance(out[1], NodeError)
    assert out[2] == "r2"
    # siblings all ran
    assert len(runner.calls) == 3


@pytest.mark.asyncio
async def test_node_id_source_order_under_parallel(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    await api["parallel"](
        [
            lambda: api["node"]("first", label="first"),
            lambda: api["node"]("second", label="second"),
            lambda: api["node"]("third", label="third"),
        ]
    )
    # node_ids assigned in thunk/source order -> journal positions 0,1,2
    assert _ids(journal) == [0, 1, 2]
    assert journal.entries[0].label == "first"
    assert journal.entries[1].label == "second"
    assert journal.entries[2].label == "third"


# --- NEW: deterministic allocation under flat parallel + OOO completion ----- #


@pytest.mark.asyncio
async def test_flat_parallel_deterministic_under_ooo_completion(setup):
    """Node 1 finishes BEFORE node 0; ids still allocate 0,1 in source order.

    Then a resume with the same script cache-hits both (ids reproduce).
    """
    tmp_path, storage, journal, ctx, publisher = setup
    # gate node 0 closed, node 1 open -> node 1 completes first.
    g0, g1 = asyncio.Event(), asyncio.Event()
    runner = GatedRunner({0: g0, 1: g1}, storage=storage)
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    g1.set()  # let node 1 finish first

    async def free_node0_after_node1():
        # wait until node 1 has actually completed, THEN release node 0
        while 1 not in runner.completion_order:
            await asyncio.sleep(0)
        g0.set()

    freer = asyncio.ensure_future(free_node0_after_node1())
    out = await api["parallel"](
        [
            lambda: api["node"]("alpha", label="alpha"),
            lambda: api["node"]("beta", label="beta"),
        ]
    )
    await freer

    # completion order was 1 before 0 ...
    assert runner.completion_order == [1, 0]
    # ... but node_ids are still allocated in SOURCE order, no OOO-record crash.
    assert out == ["r0", "r1"]
    assert _ids(journal) == [0, 1]
    assert journal.entries[0].label == "alpha"
    assert journal.entries[1].label == "beta"

    # --- resume: same script, fresh journal load -> both cache-hit ---
    journal2 = Journal(tmp_path / "journal.jsonl")
    runner2 = FakeRunner()  # would error if called for an unexpected id
    pub2 = RecordingPublisher()
    api2 = make_api(journal2, runner2, pub2, CHAT_ID, ctx)
    out2 = await api2["parallel"](
        [
            lambda: api2["node"]("alpha", label="alpha"),
            lambda: api2["node"]("beta", label="beta"),
        ]
    )
    assert out2 == ["r0", "r1"]
    assert runner2.calls == []  # everything was a cache hit


# --- NEW: deterministic allocation under NESTED parallel -------------------- #


async def _run_nested_parallel(api, completion_gates):
    """parallel([a, parallel([b, c]), d]) — returns flattened source->id map.

    We capture each node's allocated id by reading runner.calls afterwards;
    here we assert via labels recorded in the journal.
    """
    return await api["parallel"](
        [
            lambda: api["node"]("a", label="a"),
            lambda: api["parallel"](
                [
                    lambda: api["node"]("b", label="b"),
                    lambda: api["node"]("c", label="c"),
                ]
            ),
            lambda: api["node"]("d", label="d"),
        ]
    )


@pytest.mark.asyncio
async def test_nested_parallel_deterministic_depth_first(setup):
    """Nested parallel allocates depth-first source order a=0,b=1,c=2,d=3.

    Run TWICE with DIFFERENT scrambled completion orders; assert identical
    id->label mapping both times. THIS proves bug #1 (timing-dependent
    allocation) is fixed.
    """
    def label_map(journal):
        return {nid: journal.entries[nid].label for nid in journal.entries}

    # ---- Run 1: completion order d, c, b, a (reverse) ----
    tmp1 = setup[0] / "run1"
    storage1 = WorkflowStorage(tmp1)
    storage1.ensure_workflow(WF_ID)
    j1 = Journal(tmp1 / "journal.jsonl")
    ctx1 = NodeRunContext(WF_ID, storage1, str(tmp1))
    gates1 = {i: asyncio.Event() for i in range(4)}
    runner1 = GatedRunner(gates1, storage=storage1)
    api1 = make_api(j1, runner1, RecordingPublisher(), CHAT_ID, ctx1)
    # release in reverse completion order: 3,2,1,0
    for i in (3, 2, 1, 0):
        gates1[i].set()
    out1 = await _run_nested_parallel(api1, gates1)
    assert out1[0] == "r0"  # a
    # nested parallel returns a sublist
    assert out1[1] == ["r1", "r2"]  # [b, c]
    assert out1[2] == "r3"  # d
    map1 = label_map(j1)

    # ---- Run 2: completion order scrambled differently: 1,3,0,2 ----
    tmp2 = setup[0] / "run2"
    storage2 = WorkflowStorage(tmp2)
    storage2.ensure_workflow(WF_ID)
    j2 = Journal(tmp2 / "journal.jsonl")
    ctx2 = NodeRunContext(WF_ID, storage2, str(tmp2))
    gates2 = {i: asyncio.Event() for i in range(4)}
    runner2 = GatedRunner(gates2, storage=storage2)
    api2 = make_api(j2, runner2, RecordingPublisher(), CHAT_ID, ctx2)
    for i in (1, 3, 0, 2):
        gates2[i].set()
    out2 = await _run_nested_parallel(api2, gates2)
    map2 = label_map(j2)

    # IDENTICAL mapping despite different completion timing.
    assert map1 == {0: "a", 1: "b", 2: "c", 3: "d"}
    assert map2 == {0: "a", 1: "b", 2: "c", 3: "d"}


# --- NEW: deterministic allocation under multi-stage pipeline --------------- #


@pytest.mark.asyncio
async def test_pipeline_reserved_ids_deterministic(setup):
    """pipeline([X, Y], stage1, stage2): item-major reserved ids.

    Force item Y's stage1 to finish before item X's; assert the reserved-id
    scheme gives deterministic ids (X.s1=0, X.s2=1, Y.s1=2, Y.s2=3) regardless
    of completion order, and that the two interleaved chains do NOT steal each
    other's reserved ids.
    """
    tmp_path, storage, journal, ctx, publisher = setup
    api = make_api(journal, FakeRunner(), publisher, CHAT_ID, ctx)

    seen: list[tuple[str, int, int]] = []  # (item, stage, allocated node_id)
    x_s1_gate = asyncio.Event()

    async def stage1(prev, item, idx):
        # capture the id this stage's node() got, via a custom node call that
        # records into the journal; we read journal back after.
        if item == "X":
            await x_s1_gate.wait()  # X.s1 blocks until Y.s1 ran
        res = await api["node"](f"{item}-s1", label=f"{item}-s1")
        if item == "Y":
            x_s1_gate.set()  # Y.s1 done -> release X.s1
        return res

    async def stage2(prev, item, idx):
        return await api["node"](f"{item}-s2", label=f"{item}-s2")

    out = await api["pipeline"](["X", "Y"], stage1, stage2)
    # all four nodes completed
    assert len(journal.entries) == 4
    label_to_id = {journal.entries[i].label: i for i in journal.entries}
    # item-major, stage-minor: X.s1=0, X.s2=1, Y.s1=2, Y.s2=3
    assert label_to_id == {
        "X-s1": 0, "X-s2": 1, "Y-s1": 2, "Y-s2": 3,
    }
    assert out == ["r1", "r3"]  # last stage result per item


@pytest.mark.asyncio
async def test_pipeline_reserved_ids_identical_across_timings(setup):
    """Run the pipeline twice with DIFFERENT stage-completion timings; the
    id->label mapping must be identical (proves reserved ids are timing-free).
    """

    async def run_once(base, release_y_first):
        storage = WorkflowStorage(base)
        storage.ensure_workflow(WF_ID)
        journal = Journal(base / "journal.jsonl")
        ctx = NodeRunContext(WF_ID, storage, str(base))
        api = make_api(journal, FakeRunner(), RecordingPublisher(), CHAT_ID, ctx)

        gate = asyncio.Event()

        async def stage1(prev, item, idx):
            if release_y_first and item == "X":
                await gate.wait()
            if not release_y_first and item == "Y":
                await gate.wait()
            res = await api["node"](f"{item}-s1", label=f"{item}-s1")
            gate.set()
            return res

        async def stage2(prev, item, idx):
            return await api["node"](f"{item}-s2", label=f"{item}-s2")

        await api["pipeline"](["X", "Y"], stage1, stage2)
        return {journal.entries[i].label: i for i in journal.entries}

    m1 = await run_once(_tmp_subdir("a"), release_y_first=True)
    m2 = await run_once(_tmp_subdir("b"), release_y_first=False)
    assert m1 == m2 == {"X-s1": 0, "X-s2": 1, "Y-s1": 2, "Y-s2": 3}


_TMP_ROOT: list = []


def _tmp_subdir(name):
    import tempfile
    import pathlib

    if not _TMP_ROOT:
        _TMP_ROOT.append(pathlib.Path(tempfile.mkdtemp()))
    d = _TMP_ROOT[0] / name
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.mark.asyncio
async def test_pipeline_chains_do_not_steal_reserved_ids(setup):
    """Heavily interleave two chains; assert no id theft / no duplicate ids.

    Each chain yields control between stages; with a shared (non-isolated)
    contextvar a sibling chain could consume the wrong reserved id. We assert
    each item's own labels map to its own reserved block.
    """
    tmp_path, storage, journal, ctx, publisher = setup
    api = make_api(journal, FakeRunner(), publisher, CHAT_ID, ctx)

    async def stage1(prev, item, idx):
        await asyncio.sleep(0)  # force interleave at the stage boundary
        return await api["node"](f"{item}-s1", label=f"{item}-s1")

    async def stage2(prev, item, idx):
        await asyncio.sleep(0)
        return await api["node"](f"{item}-s2", label=f"{item}-s2")

    await api["pipeline"](["P", "Q", "R"], stage1, stage2)
    label_to_id = {journal.entries[i].label: i for i in journal.entries}
    # item-major: P=0,1 Q=2,3 R=4,5
    assert label_to_id == {
        "P-s1": 0, "P-s2": 1,
        "Q-s1": 2, "Q-s2": 3,
        "R-s1": 4, "R-s2": 5,
    }
    # no duplicate ids
    assert len(set(label_to_id.values())) == 6


@pytest.mark.asyncio
async def test_pipeline_stress_many_chains_no_id_theft(setup):
    """Many items x several stages, each stage awaiting before node(), with
    randomized-ish release order. Asserts every (item,stage) maps to its OWN
    reserved id (item-major) and all ids are unique — the hard proof that the
    copied-context contextvar scheme survives heavy interleaving.
    """
    tmp_path, storage, journal, ctx, publisher = setup
    api = make_api(journal, FakeRunner(), publisher, CHAT_ID, ctx)

    n_items, n_stages = 8, 3

    def mk_stage(s):
        async def stage(prev, item, idx):
            # multiple await points before node() to maximize interleave
            for _ in range(idx % 3 + 1):
                await asyncio.sleep(0)
            return await api["node"](f"{item}-s{s}", label=f"{item}-s{s}")
        return stage

    items = [f"it{i}" for i in range(n_items)]
    stages = [mk_stage(s) for s in range(n_stages)]
    await api["pipeline"](items, *stages)

    label_to_id = {journal.entries[i].label: i for i in journal.entries}
    expected = {
        f"it{i}-s{s}": i * n_stages + s
        for i in range(n_items)
        for s in range(n_stages)
    }
    assert label_to_id == expected
    assert len(set(label_to_id.values())) == n_items * n_stages


@pytest.mark.asyncio
async def test_pipeline_multi_node_stage_warns_on_fallback(setup, caplog):
    """A stage calling node() TWICE: first consumes the reserved id, the second
    falls back to counter.next() and MUST emit the documented Phase-1 warning.
    Also asserts a single-node stage does NOT warn.
    """
    import logging

    tmp_path, storage, journal, ctx, publisher = setup
    api = make_api(journal, FakeRunner(), publisher, CHAT_ID, ctx)

    async def two_node_stage(prev, item, idx):
        a = await api["node"](f"{item}-a", label=f"{item}-a")  # reserved id
        b = await api["node"](f"{item}-b", label=f"{item}-b")  # fallback -> warn
        return (a, b)

    with caplog.at_level(logging.WARNING, logger="pantheon.workflow.api"):
        await api["pipeline"](["X"], two_node_stage)

    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "node() more than once" in r.getMessage()
    ]
    assert len(warnings) == 1, warnings
    # both nodes still recorded (with distinct ids: reserved 0, fallback 1)
    assert _ids(journal) == [0, 1]
    assert {journal.entries[i].label for i in journal.entries} == {"X-a", "X-b"}


@pytest.mark.asyncio
async def test_pipeline_single_node_stage_does_not_warn(setup, caplog):
    """The common one-node-per-stage case must NOT emit the fallback warning."""
    import logging

    tmp_path, storage, journal, ctx, publisher = setup
    api = make_api(journal, FakeRunner(), publisher, CHAT_ID, ctx)

    async def stage1(prev, item, idx):
        return await api["node"](f"{item}-s1", label=f"{item}-s1")

    async def stage2(prev, item, idx):
        return await api["node"](f"{item}-s2", label=f"{item}-s2")

    with caplog.at_level(logging.WARNING, logger="pantheon.workflow.api"):
        await api["pipeline"](["X", "Y"], stage1, stage2)

    assert not [
        r for r in caplog.records if "node() more than once" in r.getMessage()
    ]


# --- NEW: resume across a pipeline ----------------------------------------- #


@pytest.mark.asyncio
async def test_resume_across_pipeline_all_cache_hit(setup):
    """Run a pipeline once (records journal), resume unchanged -> all cache-hit."""
    tmp_path, storage, journal, ctx, publisher = setup

    def build_api(j, runner, pub):
        return make_api(j, runner, pub, CHAT_ID, ctx)

    async def stage1(api, prev, item, idx):
        return await api["node"](f"{item}-s1", label=f"{item}-s1")

    async def stage2(api, prev, item, idx):
        return await api["node"](f"{item}-s2", label=f"{item}-s2")

    # run 1
    runner1 = GatedRunner({}, storage=storage)  # no gates -> immediate
    api1 = build_api(journal, runner1, publisher)
    await api1["pipeline"](
        ["X", "Y"],
        lambda prev, item, idx: stage1(api1, prev, item, idx),
        lambda prev, item, idx: stage2(api1, prev, item, idx),
    )
    assert _ids(journal) == [0, 1, 2, 3]
    assert len(runner1.calls) == 4

    # resume: fresh journal load, unchanged script -> every node hits.
    journal2 = Journal(tmp_path / "journal.jsonl")
    runner2 = FakeRunner()
    pub2 = RecordingPublisher()
    api2 = build_api(journal2, runner2, pub2)
    await api2["pipeline"](
        ["X", "Y"],
        lambda prev, item, idx: stage1(api2, prev, item, idx),
        lambda prev, item, idx: stage2(api2, prev, item, idx),
    )
    assert runner2.calls == []  # all cache hits


# --- existing pipeline behavior tests -------------------------------------- #


@pytest.mark.asyncio
async def test_pipeline_no_barrier(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()  # unused; stages are custom async stubs
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    log: list[str] = []
    # Gate that stage1 of the SLOW item (item 0) blocks on.
    slow_gate = asyncio.Event()

    async def stage1(prev, item, idx):
        if item == "slow":
            log.append("slow:s1:start")
            await slow_gate.wait()
            log.append("slow:s1:end")
            return prev
        log.append(f"{item}:s1")
        return prev

    async def stage2(prev, item, idx):
        log.append(f"{item}:s2")
        if item == "fast":
            # fast reached stage 2; now release the slow item's stage 1.
            slow_gate.set()
        return prev

    out = await api["pipeline"](["slow", "fast"], stage1, stage2)
    assert out == ["slow", "fast"]
    # Prove fast reached s2 before slow finished s1.
    assert log.index("fast:s2") < log.index("slow:s1:end")


@pytest.mark.asyncio
async def test_pipeline_stage_exception(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    reached_s2 = []

    async def stage1(prev, item, idx):
        if item == "bad":
            raise ValueError("nope")
        return item

    async def stage2(prev, item, idx):
        reached_s2.append(item)
        return f"{item}-done"

    out = await api["pipeline"](["good", "bad", "ok"], stage1, stage2)
    assert out[0] == "good-done"
    assert isinstance(out[1], ValueError)
    assert out[2] == "ok-done"
    # bad item skipped stage2; others reached it
    assert "bad" not in reached_s2
    assert set(reached_s2) == {"good", "ok"}


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(setup):
    tmp_path, storage, journal, ctx, publisher = setup

    state = {"in_flight": 0, "max": 0}
    gate = asyncio.Event()
    started = asyncio.Semaphore(0)

    class BlockingRunner(NodeRunner):
        async def run(self, node_call, ctx):
            state["in_flight"] += 1
            state["max"] = max(state["max"], state["in_flight"])
            started.release()
            await gate.wait()
            state["in_flight"] -= 1
            return NodeResult(
                node_call.node_id, "completed", result="ok",
                result_ref=f"context/n{node_call.node_id}.json",
            )

    runner = BlockingRunner()
    api = make_api(journal, runner, publisher, CHAT_ID, ctx, concurrency=2)

    task = asyncio.ensure_future(
        api["parallel"]([lambda i=i: api["node"](f"i{i}") for i in range(4)])
    )
    # wait until 2 nodes are in-flight (semaphore should cap here)
    await started.acquire()
    await started.acquire()
    await asyncio.sleep(0.01)  # give any leakers a chance to start
    assert state["max"] == 2
    gate.set()
    await task
    assert state["max"] == 2


@pytest.mark.asyncio
async def test_phase_and_log(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)

    api["phase"]("Research")
    api["log"]("hello world")
    await asyncio.sleep(0)  # let create_task'd publishes run

    assert api["run_state"].current_phase == "Research"
    types = publisher.types()
    assert "workflow.phase_changed" in types
    assert "workflow.log" in types
    # phase event carries the title
    phase_evt = next(
        e for _, e in publisher.events if e["type"] == "workflow.phase_changed"
    )
    assert phase_evt["phase"] == "Research"


@pytest.mark.asyncio
async def test_input_hash_affects_key(setup):
    """An input file's content participates in the cache key."""
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    # write an input file under context/
    inp = storage.context_dir(WF_ID) / "n5.json"
    inp.write_text("payload", encoding="utf-8")

    api = make_api(journal, runner, publisher, CHAT_ID, ctx)
    await api["node"]("uses input", inputs=("n5.json",), label="x")

    import hashlib

    h = hashlib.sha256(b"payload").hexdigest()
    expected = compute_node_key("uses input", "generic", None, None, [h])
    assert journal.entries[0].key == expected


@pytest.mark.asyncio
async def test_missing_input_hashes_as_empty(setup):
    tmp_path, storage, journal, ctx, publisher = setup
    runner = FakeRunner()
    api = make_api(journal, runner, publisher, CHAT_ID, ctx)
    await api["node"]("m", inputs=("nope.json",), label="x")

    expected = compute_node_key("m", "generic", None, None, [""])
    assert journal.entries[0].key == expected


def test_default_concurrency_constant():
    assert API_DEFAULT_CONCURRENCY == 8
