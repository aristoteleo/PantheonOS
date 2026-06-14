"""Task C4 — control serialization + CAS + unified API + scope (§A.3).

Covers the §A.3 control contract:

  * Per-workflow serialization: concurrent control() calls on the SAME
    workflow_id never interleave (one asyncio.Lock per workflow_id).
  * CAS on ``meta.revision``: an ``expected_revision`` matching the current
    authoritative revision executes the action and returns the post-execution
    revision; a stale ``expected_revision`` is REJECTED (action NOT executed)
    and the current authoritative {status, revision} is returned — no raise.
  * Scope: ``expected_chat_id`` (the ``chat_id`` arg) mismatching ``meta.chat_id``
    is denied with PermissionError, and the auth gate runs BEFORE the CAS check
    (a wrong chat + wrong revision raises PermissionError, never leaks revision).
  * Unified envelope: every accepted control returns a top-level
    ``{workflow_id, accepted, status, revision}``; resume-like stats ride along
    as extra fields without displacing the envelope keys.
  * ``expected_revision=None`` (the Leader path): the action executes against
    whatever the current authoritative revision is (accept-current semantics),
    still inside the per-workflow lock.
  * No deadlock: retry_node / skip_node / resume call resume()/cascade inside
    the lock and do not re-acquire it (asyncio.Lock is non-reentrant).
  * UI vs Leader share one path: toolset.workflow_control routes through the
    same engine.control + same CAS as a direct engine call.

All tests use STUB runners; no real LLM is invoked.
"""

from __future__ import annotations

import asyncio

import pytest

from pantheon.workflow.models import NodeCall, NodeResult
from pantheon.workflow.runner import NodeRunContext, NodeRunner

from .test_engine import (
    CHAT_ID,
    CREATED_AT,
    OTHER_CHAT,
    HangingRunner,
    WritingRunner,
    make_engine,
    wait_done,
)


# --- runners --------------------------------------------------------------- #


class SlowGateRunner(NodeRunner):
    """A runner whose node blocks until ``release`` is set.

    Used to hold a workflow mid-flight so we can fire concurrent control()
    calls and observe whether they interleave.
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, node_call: NodeCall, ctx: NodeRunContext) -> NodeResult:
        self.started.set()
        await self.release.wait()
        return NodeResult(node_id=node_call.node_id, status="completed")


# --- helpers --------------------------------------------------------------- #


async def _make_completed(tmp_path, runner=None):
    """Create + run a 2-node workflow to completion; return the engine."""
    runner = runner or WritingRunner()
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
    return engine


async def _make_paused(tmp_path):
    """Create a hanging workflow and pause it → a non-terminal 'paused' state.

    Cancel/pause on a *completed* workflow is an intentional no-op (the I-2 race
    guard never clobbers a terminal status), so envelope/CAS tests that need a
    real transition use this paused workflow instead.
    """
    runner = HangingRunner()
    engine = make_engine(tmp_path, runner)
    script = 'await node("hang", label="h")\nreturn "done"\n'
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    await asyncio.wait_for(runner.started.wait(), timeout=5.0)
    await engine.control("wf1", CHAT_ID, "pause")
    assert engine.storage.read_meta("wf1").status == "paused"
    return engine


# --------------------------------------------------------------------------- #
# 1. unified envelope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_control_returns_unified_envelope(tmp_path):
    engine = await _make_paused(tmp_path)
    rev_before = engine.storage.read_meta("wf1").revision

    out = await engine.control("wf1", CHAT_ID, "cancel")

    assert out["workflow_id"] == "wf1"
    assert out["accepted"] is True
    assert out["status"] == "cancelled"
    # revision is the POST-execution authoritative value (bumped by transition).
    assert out["revision"] == engine.storage.read_meta("wf1").revision
    assert out["revision"] > rev_before


@pytest.mark.asyncio
async def test_resume_like_envelope_keeps_stats(tmp_path):
    """retry_node returns the envelope AND the resume stats as extra fields."""
    engine = await _make_completed(tmp_path)

    out = await engine.control("wf1", CHAT_ID, "retry_node", node_id=1)
    await wait_done(engine, "wf1")

    assert out["accepted"] is True
    assert out["workflow_id"] == "wf1"
    assert "status" in out and "revision" in out
    # resume stats ride along (must not be dropped).
    assert "cached_nodes" in out
    assert "will_rerun" in out


# --------------------------------------------------------------------------- #
# 2. CAS match / stale
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cas_match_executes_and_bumps(tmp_path):
    engine = await _make_paused(tmp_path)
    rev = engine.storage.read_meta("wf1").revision

    out = await engine.control(
        "wf1", CHAT_ID, "cancel", expected_revision=rev
    )

    assert out["accepted"] is True
    assert out["status"] == "cancelled"
    assert out["revision"] > rev


@pytest.mark.asyncio
async def test_cas_stale_rejected_no_execute(tmp_path):
    engine = await _make_completed(tmp_path)
    meta = engine.storage.read_meta("wf1")
    stale = meta.revision - 1  # an old revision
    status_before = meta.status
    rev_before = meta.revision

    out = await engine.control(
        "wf1", CHAT_ID, "cancel", expected_revision=stale
    )

    # Rejected, no raise, authoritative state echoed back.
    assert out["accepted"] is False
    assert out["status"] == status_before
    assert out["revision"] == rev_before
    # Action did NOT execute — state on disk is unchanged.
    assert engine.storage.read_meta("wf1").status == status_before
    assert engine.storage.read_meta("wf1").revision == rev_before


@pytest.mark.asyncio
async def test_cas_none_executes_accept_current(tmp_path):
    """expected_revision=None (Leader path) executes against current rev."""
    engine = await _make_paused(tmp_path)
    rev = engine.storage.read_meta("wf1").revision

    out = await engine.control(
        "wf1", CHAT_ID, "cancel", expected_revision=None
    )

    assert out["accepted"] is True
    assert out["status"] == "cancelled"
    assert out["revision"] > rev


# --------------------------------------------------------------------------- #
# 3. mixed race — first writer wins, second goes stale
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mixed_race_first_executes_second_stale(tmp_path):
    """tab-A retry(rev=R) + tab-B cancel(rev=R): serialized, second stale."""
    engine = await _make_completed(tmp_path)
    rev = engine.storage.read_meta("wf1").revision

    out_a = await engine.control(
        "wf1", CHAT_ID, "retry_node", node_id=1, expected_revision=rev
    )
    await wait_done(engine, "wf1")
    out_b = await engine.control(
        "wf1", CHAT_ID, "cancel", expected_revision=rev
    )

    assert out_a["accepted"] is True
    # B used the now-stale rev → rejected, authoritative state echoed.
    assert out_b["accepted"] is False
    assert out_b["revision"] == engine.storage.read_meta("wf1").revision
    assert out_b["revision"] > rev


# --------------------------------------------------------------------------- #
# 4. scope / auth precedence
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wrong_chat_raises_permission_error(tmp_path):
    engine = await _make_completed(tmp_path)
    with pytest.raises(PermissionError):
        await engine.control("wf1", OTHER_CHAT, "cancel")


@pytest.mark.asyncio
async def test_auth_runs_before_cas(tmp_path):
    """Wrong chat + wrong revision → PermissionError (auth first, no leak)."""
    engine = await _make_completed(tmp_path)
    with pytest.raises(PermissionError):
        await engine.control(
            "wf1", OTHER_CHAT, "cancel", expected_revision=999999
        )


# --------------------------------------------------------------------------- #
# 5. per-workflow serialization (no interleave)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_control_serialized_no_interleave(tmp_path):
    """Two concurrent controls on the same wf run one-at-a-time.

    We instrument the lock-protected critical section by wrapping an *async*
    method the control body awaits, yielding control to the event loop while
    "inside". A non-reentrant per-workflow lock guarantees the second call
    cannot enter until the first leaves, so the "currently inside" counter never
    exceeds 1 even across the await. (Wrapping a sync method would not prove
    anything — only an await point can interleave coroutines.)
    """
    engine = await _make_paused(tmp_path)

    inside = 0
    max_inside = 0
    orig_cancel_task = engine._cancel_task

    async def tracking_cancel_task(workflow_id):
        nonlocal inside, max_inside
        inside += 1
        max_inside = max(max_inside, inside)
        try:
            await asyncio.sleep(0)  # yield: a broken lock would let #2 enter here
            return await orig_cancel_task(workflow_id)
        finally:
            inside -= 1

    engine._cancel_task = tracking_cancel_task  # type: ignore[assignment]

    # pause then cancel concurrently; both await _cancel_task inside the lock.
    await asyncio.gather(
        engine.control("wf1", CHAT_ID, "pause"),
        engine.control("wf1", CHAT_ID, "cancel"),
    )

    assert max_inside == 1  # never two control bodies inside at once


# --------------------------------------------------------------------------- #
# 6. no deadlock — resume/cascade inside the lock don't re-acquire it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_deadlock_retry_and_skip_under_lock(tmp_path):
    """retry_node/skip_node (which call resume/cascade) complete under timeout.

    If resume()/_cascade_invalidate re-acquired the per-workflow control lock,
    these awaits would hang forever. wait_for asserts they don't.
    """
    engine = await _make_completed(tmp_path)

    out1 = await asyncio.wait_for(
        engine.control("wf1", CHAT_ID, "retry_node", node_id=1), timeout=5.0
    )
    await wait_done(engine, "wf1")
    assert out1["accepted"] is True

    out2 = await asyncio.wait_for(
        engine.control("wf1", CHAT_ID, "skip_node", node_id=1), timeout=5.0
    )
    await wait_done(engine, "wf1")
    assert out2["accepted"] is True


@pytest.mark.asyncio
async def test_concurrent_controls_with_slow_node_no_deadlock(tmp_path):
    """A slow in-flight node + concurrent controls must not deadlock.

    The resume launched by retry_node runs a node that blocks on an event; we
    fire a concurrent pause. The lock serializes them; releasing the gate lets
    everything settle within the timeout (no reentrancy, no deadlock).
    """
    runner = SlowGateRunner()
    engine = make_engine(tmp_path, runner)
    script = 'a = await node("first", label="a")\nreturn a\n'
    await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wf1"
    )
    # let the first run complete
    runner.release.set()
    await wait_done(engine, "wf1")

    # second run: re-arm the gate so the retry's node blocks mid-flight
    runner.started.clear()
    runner.release.clear()

    async def do_retry():
        return await engine.control("wf1", CHAT_ID, "retry_node", node_id=0)

    retry_task = asyncio.create_task(do_retry())
    # wait until the retry's resume has launched the node and it's blocking
    await asyncio.wait_for(runner.started.wait(), timeout=5.0)

    # fire a concurrent pause while the node is blocked; release shortly after
    pause_task = asyncio.create_task(
        engine.control("wf1", CHAT_ID, "pause")
    )
    await asyncio.sleep(0.05)
    runner.release.set()

    out_retry, out_pause = await asyncio.wait_for(
        asyncio.gather(retry_task, pause_task), timeout=5.0
    )
    assert out_retry["accepted"] is True
    assert out_pause["accepted"] is True


# --------------------------------------------------------------------------- #
# 7. unified API — toolset routes through engine.control + same CAS
# --------------------------------------------------------------------------- #


_CTX = {"context_variables": {"chat_id": CHAT_ID}}


@pytest.mark.asyncio
async def test_toolset_passes_revision_and_is_cas_bound(tmp_path):
    from pantheon.workflow.toolset import WorkflowToolSet

    engine = await _make_paused(tmp_path)
    toolset = WorkflowToolSet(engine, name="workflow")
    meta = engine.storage.read_meta("wf1")

    # stale revision through the toolset → rejected envelope (same CAS).
    out_stale = await toolset.workflow_control(
        workflow_id="wf1",
        action="cancel",
        expected_revision=meta.revision - 1,
        **_CTX,
    )
    assert out_stale["accepted"] is False
    assert engine.storage.read_meta("wf1").status == meta.status

    # matching revision through the toolset → executed.
    out_ok = await toolset.workflow_control(
        workflow_id="wf1",
        action="cancel",
        expected_revision=meta.revision,
        **_CTX,
    )
    assert out_ok["accepted"] is True
    assert out_ok["status"] == "cancelled"


@pytest.mark.asyncio
async def test_toolset_revision_omitted_executes(tmp_path):
    """Leader path: omitting expected_revision executes against current rev."""
    from pantheon.workflow.toolset import WorkflowToolSet

    engine = await _make_paused(tmp_path)
    toolset = WorkflowToolSet(engine, name="workflow")

    out = await toolset.workflow_control(
        workflow_id="wf1", action="cancel", **_CTX
    )
    assert out["accepted"] is True
    assert out["status"] == "cancelled"
