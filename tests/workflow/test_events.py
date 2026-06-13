"""Tests for the workflow.* event protocol (constructors + publisher)."""

import pytest

from pantheon.workflow import events
from pantheon.workflow.events import (
    WORKFLOW_CREATED,
    WORKFLOW_NODE_STARTED,
    WORKFLOW_NODE_FINISHED,
    WORKFLOW_PHASE_CHANGED,
    WORKFLOW_LOG,
    WORKFLOW_STATUS,
    WORKFLOW_RESUMED,
    WorkflowEventPublisher,
)


# ── 1. Event field completeness ──────────────────────────────────────────


def test_make_created_fields():
    e = events.make_created("wf1", "ship it", ["plan", "build"])
    assert e["type"] == WORKFLOW_CREATED == "workflow.created"
    assert set(e.keys()) == {"type", "workflow_id", "goal", "phases"}
    assert e["workflow_id"] == "wf1"
    assert e["goal"] == "ship it"
    assert e["phases"] == ["plan", "build"]


def test_make_node_started_fields_node_id_and_label_distinct():
    e = events.make_node_started("wf1", 7, "Build the thing", "build")
    assert e["type"] == WORKFLOW_NODE_STARTED == "workflow.node_started"
    assert set(e.keys()) == {"type", "workflow_id", "node_id", "label", "phase"}
    assert e["node_id"] == 7
    assert e["label"] == "Build the thing"
    # node_id addresses, label displays — must both be present and distinct
    assert "node_id" in e and "label" in e
    assert e["node_id"] != e["label"]


def test_make_node_started_label_may_be_empty():
    e = events.make_node_started("wf1", 3, "", "plan")
    assert e["label"] == ""
    assert e["node_id"] == 3


def test_make_node_finished_fields():
    e = events.make_node_finished("wf1", 7, "Build", "completed", "nodes/7/result.json")
    assert e["type"] == WORKFLOW_NODE_FINISHED == "workflow.node_finished"
    assert set(e.keys()) == {
        "type",
        "workflow_id",
        "node_id",
        "label",
        "status",
        "result_ref",
    }
    assert e["node_id"] == 7
    assert e["label"] == "Build"
    assert e["status"] == "completed"
    assert e["result_ref"] == "nodes/7/result.json"
    assert e["node_id"] != e["label"]


def test_make_node_finished_result_ref_nullable():
    e = events.make_node_finished("wf1", 1, "skip", "skipped", None)
    assert e["result_ref"] is None
    assert e["status"] == "skipped"


def test_make_phase_changed_fields():
    e = events.make_phase_changed("wf1", "build")
    assert e["type"] == WORKFLOW_PHASE_CHANGED == "workflow.phase_changed"
    assert set(e.keys()) == {"type", "workflow_id", "phase"}
    assert e["phase"] == "build"


def test_make_log_fields():
    e = events.make_log("wf1", "hello")
    assert e["type"] == WORKFLOW_LOG == "workflow.log"
    assert set(e.keys()) == {"type", "workflow_id", "message"}
    assert e["message"] == "hello"


def test_make_status_fields():
    e = events.make_status("wf1", "running", {"total": 5, "done": 2})
    assert e["type"] == WORKFLOW_STATUS == "workflow.status"
    assert set(e.keys()) == {"type", "workflow_id", "status", "progress"}
    assert e["status"] == "running"
    assert e["progress"] == {"total": 5, "done": 2}


def test_make_resumed_fields():
    e = events.make_resumed("wf1", 3, [4, 5])
    assert e["type"] == WORKFLOW_RESUMED == "workflow.resumed"
    assert set(e.keys()) == {"type", "workflow_id", "cached_nodes", "will_rerun"}
    assert e["cached_nodes"] == 3
    assert e["will_rerun"] == [4, 5]


# ── Publisher fakes ───────────────────────────────────────────────────────


class FakeAdapter:
    def __init__(self):
        self.calls = []

    async def publish(self, chat_id, message_type, data):
        self.calls.append((chat_id, message_type, data))


class RaisingAdapter:
    def __init__(self):
        self.calls = []

    async def publish(self, chat_id, message_type, data):
        self.calls.append((chat_id, message_type, data))
        raise RuntimeError("no NATS here")


# ── 2. Publisher calls adapter ────────────────────────────────────────────


async def test_publisher_calls_adapter():
    fake = FakeAdapter()
    pub = WorkflowEventPublisher(nats=fake)
    event = events.make_node_started("wf1", 2, "Do", "build")
    await pub.publish("chat1", event)
    assert fake.calls == [("chat1", "workflow.node_started", event)]


# ── 3. No chat_id → no raise, no adapter call ─────────────────────────────


@pytest.mark.parametrize("bad", [None, ""])
async def test_publish_no_chat_id_no_raise(bad):
    fake = FakeAdapter()
    pub = WorkflowEventPublisher(nats=fake)
    await pub.publish(bad, events.make_log("wf1", "x"))
    assert fake.calls == []


# ── 4. Adapter raising → swallowed ────────────────────────────────────────


async def test_publish_swallows_adapter_error():
    raising = RaisingAdapter()
    pub = WorkflowEventPublisher(nats=raising)
    # Must not propagate.
    await pub.publish("chat1", events.make_log("wf1", "x"))
    assert len(raising.calls) == 1


# ── 4b. Headless degradation: first failure latches the publisher disabled ──


async def test_publish_failure_latches_disabled():
    # After the first failure, subsequent publishes are instant no-ops — no
    # repeated connect attempts / log spam in a no-NATS environment.
    raising = RaisingAdapter()
    pub = WorkflowEventPublisher(nats=raising)
    await pub.publish("chat1", events.make_log("wf1", "a"))
    await pub.publish("chat1", events.make_log("wf1", "b"))
    await pub.publish("chat1", events.make_log("wf1", "c"))
    assert pub._disabled is True
    # The adapter was called exactly once (the first attempt); then disabled.
    assert len(raising.calls) == 1


async def test_publish_timeout_latches_disabled_and_closes_backend():
    # Simulate a NATS client that blocks forever inside connect() (the real
    # infinite-reconnect behavior). A bounded first publish must time out,
    # disable the publisher, and tear the backend down.
    import asyncio

    class HangingBackend:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class HangingAdapter:
        def __init__(self):
            self._backend = HangingBackend()

        async def publish(self, chat_id, message_type, data):
            await asyncio.Event().wait()  # never completes

    adapter = HangingAdapter()
    pub = WorkflowEventPublisher(nats=adapter, connect_timeout=0.05)
    await pub.publish("chat1", events.make_log("wf1", "x"))
    assert pub._disabled is True
    assert adapter._backend.closed is True
    # A later publish is a no-op (does not re-create or re-attempt).
    await pub.publish("chat1", events.make_log("wf1", "y"))


async def test_aclose_disables_publisher():
    fake = FakeAdapter()
    pub = WorkflowEventPublisher(nats=fake)
    await pub.aclose()
    assert pub._disabled is True
    await pub.publish("chat1", events.make_log("wf1", "x"))
    assert fake.calls == []  # disabled -> no publish


# ── 5. Lazy real-adapter path ─────────────────────────────────────────────


def test_construction_does_not_create_nats():
    pub = WorkflowEventPublisher()
    assert pub._nats is None
