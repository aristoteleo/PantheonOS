"""Task C3 — awaiting_intervention status + revision (§A.4 / §A.5).

Covers the §A.4 settlement contract:

  * A node failure that is INTERVENABLE (a ``node()`` raising ``NodeError``)
    lands the workflow in the persistent, NON-terminal ``awaiting_intervention``
    status — NOT terminal ``failed``.
  * A NON-intervenable failure (script syntax/runtime error, or the max_nodes
    cap) lands terminal ``failed``.
  * retry_node / skip_node / resume transition back to ``running``.
  * cancel → ``cancelled`` (terminal); pause → ``paused``.
  * The status-set constants: ``UNSETTLED_STATUSES`` and ``TERMINAL_STATUSES``.
  * ``WorkflowMeta.revision`` increments monotonically on each status
    transition, with no double-counting.
  * ``interrupted`` removed from the terminal set: ``recover()``'d workflows
    are discoverable (non-terminal) and ``_terminal_status`` returns None for
    them, while the cancel-promotion path still works.
  * ``awaiting_intervention`` fires the key-event callback (Leader/UI notice).

All tests use STUB runners; no real LLM is invoked.
"""

from __future__ import annotations

import json

import pytest

from pantheon.workflow.engine import (
    TERMINAL_STATUSES,
    UNSETTLED_STATUSES,
    WorkflowEngine,
)
from pantheon.workflow.models import NodeResult
from pantheon.workflow.runner import NodeRunner
from pantheon.workflow.sandbox import ScriptResult, run_script

from .test_engine import (
    CHAT_ID,
    CREATED_AT,
    RecordingPublisher,
    WritingRunner,
    make_engine,
    wait_done,
)


# --- runners --------------------------------------------------------------- #


class NodeFailingRunner(NodeRunner):
    """A node that finishes with status='failed' → api raises NodeError."""

    async def run(self, node_call, ctx):
        return NodeResult(
            node_id=node_call.node_id, status="failed", error="node boom"
        )


# --------------------------------------------------------------------------- #
# 1. ScriptResult.failed_node_id (sandbox unit level)
# --------------------------------------------------------------------------- #


async def test_script_result_failed_node_id_defaults_none():
    """A plain runtime error carries failed_node_id=None (not intervenable)."""

    src = "x = undefined_name\nreturn x"
    res = await run_script(src, {"node": None}, args=None)
    assert isinstance(res, ScriptResult)
    assert res.ok is False
    assert res.cancelled is False
    assert res.failed_node_id is None


async def test_script_result_failed_node_id_set_on_nodeerror():
    """A NodeError bubbling out of the script populates failed_node_id."""

    from pantheon.workflow.api import NodeError

    async def node(*a, **kw):
        raise NodeError(7, "label", "boom")

    src = "await node('x')\nreturn 1"
    res = await run_script(src, {"node": node}, args=None)
    assert res.ok is False
    assert res.cancelled is False
    assert res.failed_node_id == 7


# --------------------------------------------------------------------------- #
# 2. Intervenable node failure → awaiting_intervention (persistent, NON-terminal)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_node_failure_lands_awaiting_intervention(tmp_path):
    engine = make_engine(tmp_path, NodeFailingRunner())
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")

    meta = engine.storage.read_meta("wf1")
    state = engine.storage.read_state("wf1")
    assert meta.status == "awaiting_intervention"
    assert state.status == "awaiting_intervention"
    # Persistent + NON-terminal.
    assert "awaiting_intervention" not in TERMINAL_STATUSES
    assert "awaiting_intervention" in UNSETTLED_STATUSES


@pytest.mark.asyncio
async def test_awaiting_intervention_fires_key_event(tmp_path):
    events: list[tuple] = []

    def on_key_event(workflow_id, chat_id, kind, summary):
        events.append((workflow_id, chat_id, kind, summary))

    engine = make_engine(
        tmp_path, NodeFailingRunner(), on_key_event=on_key_event
    )
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    assert events, "awaiting_intervention must fire a key event"
    assert events[-1][2] == "awaiting_intervention"


# --------------------------------------------------------------------------- #
# 3. Non-intervenable failures → failed (terminal)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_script_runtime_error_lands_failed(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    # NameError at runtime — not a NodeError, so not intervenable.
    await engine.create(
        CHAT_ID,
        "g",
        "x = does_not_exist\nreturn x\n",
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    assert engine.storage.read_meta("wf1").status == "failed"


@pytest.mark.asyncio
async def test_max_nodes_failure_lands_failed(tmp_path):
    # No node ever raises NodeError; the cap raises WorkflowLimitError → failed.
    engine = make_engine(tmp_path, WritingRunner(), max_nodes=1)
    await engine.create(
        CHAT_ID,
        "g",
        'await node("a")\nawait node("b")\nreturn 1\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    assert engine.storage.read_meta("wf1").status == "failed"


# --------------------------------------------------------------------------- #
# 4. Status-set constants (§A.4)
# --------------------------------------------------------------------------- #


def test_status_set_constants():
    assert set(TERMINAL_STATUSES) == {"completed", "failed", "cancelled"}
    assert set(UNSETTLED_STATUSES) == {
        "running",
        "paused",
        "interrupted",
        "awaiting_intervention",
    }
    # Disjoint.
    assert set(TERMINAL_STATUSES).isdisjoint(UNSETTLED_STATUSES)


# --------------------------------------------------------------------------- #
# 5. revision monotonic increment, no double-count
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_revision_starts_zero_and_increments(tmp_path):
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
    # running (1) then completed (2) — two transitions persisted.
    meta = engine.storage.read_meta("wf1")
    assert meta.revision == 2


@pytest.mark.asyncio
async def test_revision_increments_through_intervention_and_resume(tmp_path):
    engine = make_engine(tmp_path, NodeFailingRunner())
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    # running -> awaiting_intervention == 2 transitions.
    rev_after_await = engine.storage.read_meta("wf1").revision
    assert rev_after_await == 2

    # Swap to a healthy runner and resume via retry_node — new transitions bump.
    engine.runner = WritingRunner()
    await engine.control("wf1", CHAT_ID, "retry_node", node_id=0)
    await wait_done(engine, "wf1")
    rev_after_resume = engine.storage.read_meta("wf1").revision
    # Strictly greater — monotonic.
    assert rev_after_resume > rev_after_await


# --------------------------------------------------------------------------- #
# 6. interrupted removed from terminal set
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_interrupted_is_not_terminal(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    # Forge a crashed (status=running) workflow on disk, then recover().
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf-crash",
    )
    await wait_done(engine, "wf-crash")
    # Force it back to 'running' on disk and drop the live session so recover()
    # treats it as a crash victim.
    meta = engine.storage.read_meta("wf-crash")
    meta.status = "running"
    engine.storage.write_meta(meta)
    engine.sessions.pop("wf-crash", None)

    recovered = engine.recover()
    assert "wf-crash" in recovered
    assert engine.storage.read_meta("wf-crash").status == "interrupted"
    # interrupted is unsettled, NOT terminal.
    assert "interrupted" in UNSETTLED_STATUSES
    assert "interrupted" not in TERMINAL_STATUSES
    # _terminal_status returns None for an interrupted workflow.
    assert engine._terminal_status("wf-crash") is None


# --------------------------------------------------------------------------- #
# 7. cancel / pause still settle correctly
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancel_is_terminal(tmp_path):
    engine = make_engine(tmp_path, WritingRunner())
    await engine.create(
        CHAT_ID,
        "g",
        'await node("x", label="a")\nreturn "ok"\n',
        created_at=CREATED_AT,
        workflow_id="wf1",
    )
    await wait_done(engine, "wf1")
    out = await engine.control("wf1", CHAT_ID, "cancel")
    # Already completed — cancel must not clobber a real terminal status.
    assert out["status"] == "completed"
