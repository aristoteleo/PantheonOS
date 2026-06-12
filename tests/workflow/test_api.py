"""Tests for the orchestration API (Task 7).

All tests use STUB runner / publisher and a real :class:`Journal` on a tmp file.
No real LLM is invoked.
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
    assert len(journal.entries) == 1
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
    assert [e.node_id for e in journal.entries] == [0, 1, 2]
    assert [e.label for e in journal.entries] == ["first", "second", "third"]


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
        api["parallel"]([lambda: api["node"](f"i{i}") for i in range(4)])
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
