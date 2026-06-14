"""Task C7 — four query/control endpoints assembled on ChatRoom + ownership (§A.3).

These cover the *endpoint layer* the UI calls over NATS (Magique RPC @tool on
ChatRoom). The substance is in ``WorkflowEngine`` (exercised by C1-C6); here we
prove the thin endpoint wrappers:

  * forward ``expected_chat_id`` into the engine as the scope gate, so a cross-
    chat access is denied at the BACKEND (403 semantics), not the frontend.
  * map ``PermissionError`` -> ``{success: False, code: 403}`` and
    ``FileNotFoundError`` -> ``{success: False, code: 404}``.
  * ``workflow_list`` refuses an empty/None ``chat_id`` (would otherwise list
    across all chats = cross-chat leak; the C5 handoff is enforced here).
  * ``control`` correctly forwards the engine's THREE states without a KeyError:
    ``accepted: True`` (CAS match), ``accepted: False`` (stale CAS) and the
    error state (``{error}``, no ``accepted`` key).
  * ``workflow_node_trace`` distinguishes cross-chat (403) from "no artifact"
    (``success: True`` + ``exists: False``, a 404 *semantic* but not an error).

The endpoint methods are unbound ``@tool`` functions on ``ChatRoom``; to keep the
test fast (no run loop / NATS) we bind them to a tiny stub that only carries the
``_workflow_engine`` they reach through, exactly like a real ChatRoom would.
All workflows use a STUB runner — no real LLM.
"""

from __future__ import annotations

import asyncio

import pytest

from pantheon.chatroom.room import ChatRoom

from .test_engine import (
    CHAT_ID,
    CREATED_AT,
    OTHER_CHAT,
    WritingRunner,
    make_engine,
    wait_done,
)


# --- harness --------------------------------------------------------------- #


class _Room:
    """Minimal carrier for the engine the unbound endpoints reach through.

    Reuses ChatRoom's own ``_workflow_denied`` 403/404 mapper so the test
    exercises the real envelope logic, not a re-implementation.
    """

    _workflow_denied = staticmethod(ChatRoom._workflow_denied)

    def __init__(self, engine) -> None:
        self._workflow_engine = engine


# The endpoints are defined as ``@tool async def`` on ChatRoom. Pull the
# underlying functions so we can bind them to the lightweight stub.
def _fn(name):
    attr = getattr(ChatRoom, name)
    # ``@tool`` may wrap the coroutine; unwrap to the raw callable if needed.
    return getattr(attr, "__wrapped__", attr)


async def _make_workflow(tmp_path, *, chat_id=CHAT_ID, wid="wf1", script=None):
    """Create + run-to-completion a small workflow owned by ``chat_id``."""
    if script is None:
        script = (
            'meta = {"goal": "g", "phases": ["A"], '
            '"blueprint": [{"slot_id": "s1", "phase": "A", "label": "L1", '
            '"kind": "task"}]}\n'
            'await node("do", slot="s1")\n'
        )
    engine = make_engine(tmp_path, WritingRunner())
    out = await engine.create(
        chat_id, "g", script, created_at=CREATED_AT, workflow_id=wid
    )
    assert "error" not in out, out
    await wait_done(engine, wid)
    return engine


async def call(engine, name, **kw):
    room = _Room(engine)
    return await _fn(name)(room, **kw)


# --- workflow_list --------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_list_requires_chat_id(tmp_path):
    engine = await _make_workflow(tmp_path)
    for empty in (None, ""):
        out = await call(engine, "workflow_list", chat_id=empty)
        assert out["success"] is False
        assert "chat_id" in out["message"].lower()
        # Must NOT have leaked any workflows.
        assert "workflows" not in out or not out["workflows"]


@pytest.mark.asyncio
async def test_workflow_list_returns_scoped(tmp_path):
    # A workflow that stays running (HangingRunner) so list_workflows (which
    # surfaces only UNSETTLED workflows) returns it.
    from .test_engine import HangingRunner

    script = (
        'meta = {"goal": "g", "phases": ["A"], "blueprint": []}\n'
        'await node("hang")\n'
    )
    engine = make_engine(tmp_path, HangingRunner())
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wfA"
    )
    assert "error" not in out
    await asyncio.sleep(0.05)
    res = await call(engine, "workflow_list", chat_id=CHAT_ID)
    assert res["success"] is True
    ids = [w["workflow_id"] for w in res["workflows"]]
    assert "wfA" in ids
    # Other chat sees nothing of CHAT_ID's workflows.
    other = await call(engine, "workflow_list", chat_id=OTHER_CHAT)
    assert other["success"] is True
    assert all(w["workflow_id"] != "wfA" for w in other["workflows"])


# --- workflow_blueprint ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_blueprint_owner(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine, "workflow_blueprint", workflow_id="wf1", expected_chat_id=CHAT_ID
    )
    assert out["success"] is True
    assert out["preview"] == "full"
    assert any(s["slot_id"] == "s1" for s in out["blueprint"])


@pytest.mark.asyncio
async def test_workflow_blueprint_cross_chat_403(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine, "workflow_blueprint", workflow_id="wf1", expected_chat_id=OTHER_CHAT
    )
    assert out["success"] is False
    assert out["code"] == 403


@pytest.mark.asyncio
async def test_workflow_blueprint_unknown_404(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine, "workflow_blueprint", workflow_id="nope", expected_chat_id=CHAT_ID
    )
    assert out["success"] is False
    assert out["code"] == 404


# --- workflow_state -------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_state_owner_has_reconcile_fields(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine, "workflow_state", workflow_id="wf1", expected_chat_id=CHAT_ID
    )
    assert out["success"] is True
    st = out["state"]
    assert "cascade_epoch" in st
    assert "invalidations" in st
    assert st["nodes"]  # at least one node
    n = st["nodes"][0]
    assert "attempt" in n and "slot_id" in n


@pytest.mark.asyncio
async def test_workflow_state_cross_chat_403(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine, "workflow_state", workflow_id="wf1", expected_chat_id=OTHER_CHAT
    )
    assert out["success"] is False
    assert out["code"] == 403


@pytest.mark.asyncio
async def test_workflow_state_unknown_404(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine, "workflow_state", workflow_id="ghost", expected_chat_id=CHAT_ID
    )
    assert out["success"] is False
    assert out["code"] == 404


# --- workflow_node_trace --------------------------------------------------- #


@pytest.mark.asyncio
async def test_node_trace_has_artifact(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine,
        "workflow_node_trace",
        workflow_id="wf1",
        node_id=0,
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is True
    assert out["exists"] is True
    assert out["node_id"] == 0


@pytest.mark.asyncio
async def test_node_trace_missing_artifact_not_403(tmp_path):
    # A node_id that produced no artifact: success True, exists False (404
    # SEMANTIC, NOT a permission error).
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine,
        "workflow_node_trace",
        workflow_id="wf1",
        node_id=999,
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is True
    assert out["exists"] is False


@pytest.mark.asyncio
async def test_node_trace_cross_chat_403(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine,
        "workflow_node_trace",
        workflow_id="wf1",
        node_id=0,
        expected_chat_id=OTHER_CHAT,
    )
    assert out["success"] is False
    assert out["code"] == 403


@pytest.mark.asyncio
async def test_node_trace_unknown_workflow_404(tmp_path):
    engine = await _make_workflow(tmp_path)
    out = await call(
        engine,
        "workflow_node_trace",
        workflow_id="absent",
        node_id=0,
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is False
    assert out["code"] == 404


# --- control (three states) ------------------------------------------------ #


async def _running_engine(tmp_path):
    """A workflow that stays running (so control actions are meaningful)."""
    from .test_engine import HangingRunner

    script = (
        'meta = {"goal": "g", "phases": ["A"], "blueprint": []}\n'
        'await node("hang")\n'
    )
    engine = make_engine(tmp_path, HangingRunner())
    out = await engine.create(
        CHAT_ID, "g", script, created_at=CREATED_AT, workflow_id="wfR"
    )
    assert "error" not in out, out
    # Give the background task a tick to start.
    await asyncio.sleep(0.05)
    return engine


@pytest.mark.asyncio
async def test_control_accepted_true(tmp_path):
    engine = await _running_engine(tmp_path)
    meta = engine._read_meta("wfR")
    out = await call(
        engine,
        "workflow_control",
        workflow_id="wfR",
        action="pause",
        node_id=None,
        expected_revision=meta.revision,
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is True
    assert out["accepted"] is True
    assert "status" in out and "revision" in out


@pytest.mark.asyncio
async def test_control_accepted_false_stale_cas(tmp_path):
    engine = await _running_engine(tmp_path)
    out = await call(
        engine,
        "workflow_control",
        workflow_id="wfR",
        action="pause",
        node_id=None,
        expected_revision=99999,  # stale
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is True
    assert out["accepted"] is False
    assert "revision" in out


@pytest.mark.asyncio
async def test_control_error_state_no_keyerror(tmp_path):
    # Unknown action -> engine returns {"error": ...} WITHOUT an accepted key.
    # The endpoint must forward it as success: False without raising KeyError.
    engine = await _running_engine(tmp_path)
    meta = engine._read_meta("wfR")
    out = await call(
        engine,
        "workflow_control",
        workflow_id="wfR",
        action="frobnicate",
        node_id=None,
        expected_revision=meta.revision,
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is False
    assert "error" in out
    # And a missing node_id for a node action:
    meta = engine._read_meta("wfR")
    out2 = await call(
        engine,
        "workflow_control",
        workflow_id="wfR",
        action="skip_node",
        node_id=None,
        expected_revision=meta.revision,
        expected_chat_id=CHAT_ID,
    )
    assert out2["success"] is False
    assert "error" in out2


@pytest.mark.asyncio
async def test_control_cross_chat_403_before_cas(tmp_path):
    engine = await _running_engine(tmp_path)
    out = await call(
        engine,
        "workflow_control",
        workflow_id="wfR",
        action="pause",
        node_id=None,
        expected_revision=99999,  # would be stale, but auth must win
        expected_chat_id=OTHER_CHAT,
    )
    assert out["success"] is False
    assert out["code"] == 403


@pytest.mark.asyncio
async def test_control_unknown_workflow_404(tmp_path):
    engine = await _running_engine(tmp_path)
    out = await call(
        engine,
        "workflow_control",
        workflow_id="missing",
        action="pause",
        node_id=None,
        expected_revision=None,
        expected_chat_id=CHAT_ID,
    )
    assert out["success"] is False
    assert out["code"] == 404
