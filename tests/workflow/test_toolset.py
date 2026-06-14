"""Tests for the Leader-facing WorkflowToolSet (Task 9).

These exercise the toolset through the real ``@tool`` decorator + context path:
chat_id is injected the way the framework injects it — by passing
``context_variables={"chat_id": ...}`` (or ``session_id``) as a kwarg to the
tool method. The decorator merges that into the contextvar so the toolset's
``_chat_id()`` resolves it via ``self.get_context()``. A FAKE engine records
call args and returns canned dicts (or raises on demand) so no real
WorkflowEngine is needed.

NOTE for the next task author: to drive a tool with a chat context in tests,
call the tool with ``context_variables={"chat_id": "<id>"}`` — the @tool
wrapper installs it into the contextvar and the toolset reads it back.
"""

from __future__ import annotations

import pytest

from pantheon.toolset import get_current_context_variables
from pantheon.workflow.toolset import WorkflowToolSet


@pytest.fixture(autouse=True)
def _clean_context():
    """Reset the shared ExecutionContext between tests.

    The @tool wrapper merges ``context_variables`` into a process-wide default
    ExecutionContext (it ``.update()``s in place rather than replacing), so a
    chat_id set by one tool call would otherwise leak into the next test and
    defeat the no-chat-context assertions. Clear it before AND after each test.
    """
    get_current_context_variables().clear()
    yield
    get_current_context_variables().clear()


# --- fake engine ---------------------------------------------------------- #


class FakeEngine:
    """Records calls and returns canned dicts; can raise on demand."""

    def __init__(self):
        self.calls: list[tuple] = []
        # per-method override: set to an Exception instance/class to raise.
        self.raise_on: dict[str, BaseException] = {}
        # per-method canned return.
        self.returns: dict[str, dict] = {}

    def _maybe_raise(self, method: str) -> None:
        exc = self.raise_on.get(method)
        if exc is not None:
            raise exc

    async def create(
        self, chat_id, goal, script, args=None, *, auto_start=True, created_at, workflow_id=None
    ):
        self.calls.append(
            ("create", dict(
                chat_id=chat_id, goal=goal, script=script, args=args,
                auto_start=auto_start, created_at=created_at, workflow_id=workflow_id,
            ))
        )
        self._maybe_raise("create")
        return self.returns.get(
            "create", {"workflow_id": "wf-0", "phases": ["a"], "status": "running"}
        )

    async def status(self, workflow_id=None, chat_id=None):
        self.calls.append(("status", dict(workflow_id=workflow_id, chat_id=chat_id)))
        self._maybe_raise("status")
        return self.returns.get("status", {"workflows": []})

    async def get_output(self, workflow_id, chat_id, node_id=None, summary_only=True):
        self.calls.append(
            ("get_output", dict(
                workflow_id=workflow_id, chat_id=chat_id,
                node_id=node_id, summary_only=summary_only,
            ))
        )
        self._maybe_raise("get_output")
        return self.returns.get("get_output", {"path": "/x", "preview": "hi"})

    async def resume(self, workflow_id, chat_id, new_script=None):
        self.calls.append(
            ("resume", dict(
                workflow_id=workflow_id, chat_id=chat_id,
                new_script=new_script,
            ))
        )
        self._maybe_raise("resume")
        return self.returns.get(
            "resume",
            {"resumed_from": workflow_id, "cached_nodes": 2, "will_rerun": [3]},
        )

    async def control(
        self, workflow_id, chat_id, action, node_id=None, expected_revision=None
    ):
        self.calls.append(
            ("control", dict(
                workflow_id=workflow_id, chat_id=chat_id,
                action=action, node_id=node_id,
                expected_revision=expected_revision,
            ))
        )
        self._maybe_raise("control")
        return self.returns.get(
            "control", {"workflow_id": workflow_id, "status": action}
        )

    def last(self, method: str) -> dict:
        for name, kw in reversed(self.calls):
            if name == method:
                return kw
        raise AssertionError(f"engine.{method} was not called")

    def called(self, method: str) -> bool:
        return any(name == method for name, _ in self.calls)


CHAT = "chat-123"
CTX = {"context_variables": {"chat_id": CHAT}}


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def ts(engine):
    return WorkflowToolSet(engine, name="workflow")


# --- 1. tools registered -------------------------------------------------- #


def test_exactly_five_tools_registered(ts):
    names = set(ts.tool_functions.keys())
    expected = {
        "workflow_create",
        "workflow_status",
        "workflow_get_output",
        "workflow_edit",
        "workflow_control",
    }
    assert expected.issubset(names)
    # only those five workflow_* tools (plus possibly framework list_tools).
    workflow_tools = {n for n in names if n.startswith("workflow_")}
    assert workflow_tools == expected


# --- 2. create delegates -------------------------------------------------- #


async def test_create_delegates_with_chat_id_and_created_at(ts, engine):
    out = await ts.workflow_create(
        goal="do it", script="meta = {}\n", args={"k": 1}, **CTX
    )
    assert out == {"workflow_id": "wf-0", "phases": ["a"], "status": "running"}
    kw = engine.last("create")
    assert kw["chat_id"] == CHAT
    assert kw["goal"] == "do it"
    assert kw["script"] == "meta = {}\n"
    assert kw["args"] == {"k": 1}
    assert kw["auto_start"] is True
    assert isinstance(kw["created_at"], str) and kw["created_at"]


async def test_create_passes_through_validation_error(ts, engine):
    engine.returns["create"] = {"error": "bad syntax", "line": 3}
    out = await ts.workflow_create(goal="g", script="oops(", **CTX)
    assert out == {"error": "bad syntax", "line": 3}


async def test_create_respects_auto_start_false(ts, engine):
    await ts.workflow_create(goal="g", script="meta={}", auto_start=False, **CTX)
    assert engine.last("create")["auto_start"] is False


# --- 3. status both modes ------------------------------------------------- #


async def test_status_all_workflows(ts, engine):
    await ts.workflow_status(**CTX)
    kw = engine.last("status")
    assert kw["workflow_id"] is None
    assert kw["chat_id"] == CHAT


async def test_status_single_workflow(ts, engine):
    await ts.workflow_status(workflow_id="wf-7", **CTX)
    kw = engine.last("status")
    assert kw["workflow_id"] == "wf-7"
    assert kw["chat_id"] == CHAT


# --- 4. get_output -------------------------------------------------------- #


async def test_get_output_summary_default(ts, engine):
    out = await ts.workflow_get_output(workflow_id="wf-1", **CTX)
    assert out == {"path": "/x", "preview": "hi"}
    kw = engine.last("get_output")
    assert kw["workflow_id"] == "wf-1"
    assert kw["chat_id"] == CHAT
    assert kw["node_id"] is None
    assert kw["summary_only"] is True


async def test_get_output_node_full(ts, engine):
    await ts.workflow_get_output(
        workflow_id="wf-1", node_id=5, summary_only=False, **CTX
    )
    kw = engine.last("get_output")
    assert kw["node_id"] == 5
    assert kw["summary_only"] is False


# --- 5. edit -> resume ---------------------------------------------------- #


async def test_edit_delegates_to_resume(ts, engine):
    out = await ts.workflow_edit(workflow_id="wf-2", script="meta={}\nx=1", **CTX)
    assert out == {"resumed_from": "wf-2", "cached_nodes": 2, "will_rerun": [3]}
    kw = engine.last("resume")
    assert kw["workflow_id"] == "wf-2"
    assert kw["chat_id"] == CHAT
    assert kw["new_script"] == "meta={}\nx=1"
    # resume does NOT take created_at — meta.created_at is unchanged on re-run.
    assert "created_at" not in kw


# --- 6. control routes each action --------------------------------------- #


@pytest.mark.parametrize("action", ["pause", "resume", "cancel", "skip_node", "retry_node"])
async def test_control_routes_action(ts, engine, action):
    await ts.workflow_control(workflow_id="wf-3", action=action, node_id=4, **CTX)
    kw = engine.last("control")
    assert kw["workflow_id"] == "wf-3"
    assert kw["chat_id"] == CHAT
    assert kw["action"] == action
    assert kw["node_id"] == 4
    assert isinstance(kw["node_id"], int)


# --- 7. cross-chat denial (Codex F2) ------------------------------------- #


async def test_permission_error_returns_error_dict(ts, engine):
    engine.raise_on["status"] = PermissionError("not owned")
    out = await ts.workflow_status(workflow_id="wf-x", **CTX)
    assert "error" in out
    assert "not owned" in out["error"]


# --- 8. unknown workflow -------------------------------------------------- #


async def test_file_not_found_returns_error_dict(ts, engine):
    engine.raise_on["get_output"] = FileNotFoundError("no such workflow")
    out = await ts.workflow_get_output(workflow_id="ghost", **CTX)
    assert "error" in out
    assert "no such workflow" in out["error"]


async def test_unexpected_error_returns_error_dict(ts, engine):
    engine.raise_on["control"] = RuntimeError("boom")
    out = await ts.workflow_control(workflow_id="wf-9", action="pause", **CTX)
    assert "error" in out


# --- 9. no chat context --------------------------------------------------- #


async def test_no_chat_context_create(ts, engine):
    out = await ts.workflow_create(
        goal="g", script="meta={}", context_variables={}
    )
    assert out == {"error": "no chat context"}
    assert not engine.called("create")


async def test_no_chat_context_status(ts, engine):
    out = await ts.workflow_status(context_variables={})
    assert out == {"error": "no chat context"}
    assert not engine.called("status")


# --- bonus: session_id alias resolves chat_id ---------------------------- #


async def test_session_id_resolves_chat_id(ts, engine):
    out = await ts.workflow_status(
        context_variables={"session_id": "sess-9"}
    )
    assert "error" not in out
    assert engine.last("status")["chat_id"] == "sess-9"
