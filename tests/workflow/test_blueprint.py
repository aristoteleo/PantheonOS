"""Tests for Task C1: workflow_blueprint declaration parsing + create validation.

Authoritative contract §A.8:
  - declaration: ``meta.blueprint = [{slot_id, phase, label, kind, schema?}]``,
    pure literal.
  - create validation: (1) slot_id unique; (2) same slot_id must not mix kind;
    (3) every ``node(slot="sX")`` referenced in the script must be declared in
    blueprint; (4) script contains ``slot=`` but ``meta.blueprint`` missing/illegal
    => reject (orphan guard).
  - rejection protocol: structured error (duplicate / undeclared slot + line);
    no discoverable workflow is produced (validate-before-persist).
  - preview is explicit state: ``preview = "full" | "none"`` written to meta.
    passing validation => preview="full".

Pure-stdlib stub runner, no real LLM. ``created_at`` / ``workflow_id`` injected
for determinism.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.engine import (
    WorkflowEngine,
    extract_blueprint,
    validate_blueprint,
    _referenced_slots,
)
from pantheon.workflow.models import NodeCall, NodeResult, WorkflowMeta
from pantheon.workflow.runner import NodeRunContext, NodeRunner
from pantheon.workflow.storage import WorkflowStorage, scan_workflows

CHAT_ID = "chat-1"
CREATED_AT = "2026-01-01T00:00:00Z"


# --- stubs ----------------------------------------------------------------- #


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, chat_id: str, event: dict) -> None:
        self.events.append((chat_id, event))


class WritingRunner(NodeRunner):
    """Stub runner that writes a real context envelope so cache reloads work."""

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


def make_engine(tmp_path, runner=None):
    return WorkflowEngine(
        tmp_path,
        runner=runner or WritingRunner(),
        publisher=RecordingPublisher(),
    )


# --- extract_blueprint ----------------------------------------------------- #


def test_extract_blueprint_literal():
    src = (
        'meta = {"goal": "g", "blueprint": ['
        '{"slot_id": "s1", "phase": "A", "label": "L1", "kind": "node"},'
        '{"slot_id": "s2", "phase": "B", "label": "L2", "kind": "fanout"}'
        "]}\n"
        'await node("x", slot="s1")\n'
    )
    bp = extract_blueprint(src)
    assert isinstance(bp, list)
    assert [s["slot_id"] for s in bp] == ["s1", "s2"]
    assert bp[1]["kind"] == "fanout"


def test_extract_blueprint_missing():
    src = 'await node("x")\n'
    assert extract_blueprint(src) == []


def test_extract_blueprint_non_literal():
    # blueprint value references a name -> literal_eval rejects -> [] (no exec)
    src = "x = 1\nmeta = {'blueprint': [x]}\nawait node('y', slot='s1')\n"
    assert extract_blueprint(src) == []


def test_extract_blueprint_syntax_error():
    src = "this is not python ((("
    assert extract_blueprint(src) == []


# --- _referenced_slots ----------------------------------------------------- #


def test_referenced_slots_collects_literals():
    src = (
        'await node("a", slot="s1")\n'
        'await node("b", slot="s2", label="x")\n'
        'await node("c")\n'  # no slot
    )
    assert _referenced_slots(src) == {"s1", "s2"}


def test_referenced_slots_ignores_non_literal_slot():
    # slot=variable is not a literal; it's not collected (can't be statically known)
    src = "x = 's1'\nawait node('a', slot=x)\n"
    assert _referenced_slots(src) == set()


# --- validate_blueprint (pure) --------------------------------------------- #


def test_validate_blueprint_ok():
    bp = [
        {"slot_id": "s1", "kind": "node"},
        {"slot_id": "s2", "kind": "fanout"},
    ]
    ok, error, line = validate_blueprint(bp, {"s1", "s2"})
    assert ok is True
    assert error is None


def test_validate_blueprint_duplicate_slot_id():
    bp = [
        {"slot_id": "s1", "kind": "node"},
        {"slot_id": "s1", "kind": "node"},
    ]
    ok, error, line = validate_blueprint(bp, set())
    assert ok is False
    assert "s1" in error


def test_validate_blueprint_kind_mix():
    bp = [
        {"slot_id": "s1", "kind": "node"},
        {"slot_id": "s1", "kind": "fanout"},
    ]
    ok, error, line = validate_blueprint(bp, set())
    assert ok is False
    assert "s1" in error


def test_validate_blueprint_undeclared_reference():
    bp = [{"slot_id": "s1", "kind": "node"}]
    ok, error, line = validate_blueprint(bp, {"s1", "s2"})
    assert ok is False
    assert "s2" in error


# --- create() integration -------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_rejects_duplicate_slot_id(tmp_path):
    engine = make_engine(tmp_path)
    script = (
        'meta = {"blueprint": ['
        '{"slot_id": "s1", "kind": "node"},'
        '{"slot_id": "s1", "kind": "node"}'
        "]}\n"
        'await node("x", slot="s1")\n'
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-dup",
        auto_start=False,
    )
    assert "error" in out
    assert "s1" in out["error"]
    assert "wf-dup" not in engine.sessions


@pytest.mark.asyncio
async def test_create_rejects_kind_mix(tmp_path):
    engine = make_engine(tmp_path)
    script = (
        'meta = {"blueprint": ['
        '{"slot_id": "s1", "kind": "node"},'
        '{"slot_id": "s1", "kind": "fanout"}'
        "]}\n"
        'await node("x", slot="s1")\n'
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-mix",
        auto_start=False,
    )
    assert "error" in out
    assert "s1" in out["error"]
    assert "wf-mix" not in engine.sessions


@pytest.mark.asyncio
async def test_create_rejects_undeclared_slot(tmp_path):
    engine = make_engine(tmp_path)
    # blueprint declares s1, but script references s2.
    script = (
        'meta = {"blueprint": [{"slot_id": "s1", "kind": "node"}]}\n'
        'await node("x", slot="s1")\n'
        'await node("y", slot="s2")\n'
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-undeclared",
        auto_start=False,
    )
    assert "error" in out
    assert "s2" in out["error"]
    # line points at the offending reference (line 3 in this script)
    assert out.get("line") == 3
    assert "wf-undeclared" not in engine.sessions


@pytest.mark.asyncio
async def test_create_rejects_orphan_slot_without_blueprint(tmp_path):
    engine = make_engine(tmp_path)
    # script uses slot= but no meta.blueprint -> orphan -> reject
    script = 'await node("x", slot="s1")\n'
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-orphan",
        auto_start=False,
    )
    assert "error" in out
    assert "s1" in out["error"]
    assert "wf-orphan" not in engine.sessions


@pytest.mark.asyncio
async def test_rejection_leaves_no_discoverable_workflow(tmp_path):
    engine = make_engine(tmp_path)
    script = 'await node("x", slot="s1")\n'  # orphan
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-ghost",
        auto_start=False,
    )
    assert "error" in out
    # No session, no meta/journal persisted -> not discoverable by list/scan.
    assert "wf-ghost" not in engine.sessions
    metas = scan_workflows(tmp_path)
    assert all(m.workflow_id != "wf-ghost" for m in metas)
    storage = WorkflowStorage(tmp_path)
    assert not storage.meta_path("wf-ghost").exists()


@pytest.mark.asyncio
async def test_create_success_preview_full_and_blueprint_persisted(tmp_path):
    engine = make_engine(tmp_path)
    script = (
        'meta = {"blueprint": ['
        '{"slot_id": "s1", "phase": "A", "label": "L1", "kind": "node"}'
        "]}\n"
        'await node("x", slot="s1")\n'
    )
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-ok",
        auto_start=False,
    )
    assert "error" not in out
    assert out["workflow_id"] == "wf-ok"
    storage = WorkflowStorage(tmp_path)
    meta = storage.read_meta("wf-ok")
    assert meta.preview == "full"
    assert [s["slot_id"] for s in meta.blueprint] == ["s1"]


@pytest.mark.asyncio
async def test_create_no_slot_no_blueprint_preview_none(tmp_path):
    """A legacy script with no slot= and no blueprint is valid; preview=none."""
    engine = make_engine(tmp_path)
    script = 'await node("x")\n'
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf-legacy",
        auto_start=False,
    )
    assert "error" not in out
    storage = WorkflowStorage(tmp_path)
    meta = storage.read_meta("wf-legacy")
    assert meta.preview == "none"
    assert meta.blueprint == []


# --- WorkflowMeta round-trip ----------------------------------------------- #


def test_workflow_meta_roundtrip_preview_and_blueprint(tmp_path):
    storage = WorkflowStorage(tmp_path)
    bp = [{"slot_id": "s1", "phase": "A", "label": "L1", "kind": "node"}]
    meta = WorkflowMeta(
        workflow_id="wf-rt",
        chat_id=CHAT_ID,
        goal="g",
        created_at=CREATED_AT,
        status="pending",
        preview="full",
        blueprint=bp,
    )
    storage.write_meta(meta)
    loaded = storage.read_meta("wf-rt")
    assert loaded.preview == "full"
    assert loaded.blueprint == bp


def test_workflow_meta_defaults():
    meta = WorkflowMeta(
        workflow_id="wf",
        chat_id=CHAT_ID,
        goal="g",
        created_at=CREATED_AT,
        status="pending",
    )
    assert meta.preview == "full"
    assert meta.blueprint == []
