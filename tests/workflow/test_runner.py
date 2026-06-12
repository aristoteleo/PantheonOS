"""Tests for the NodeRunner abstraction and InProcessRunner (Task 5).

These tests use a STUB agent injected via the ``agent_factory`` seam on
``InProcessRunner`` — no real LLM is called. The fake records constructor
kwargs and the kwargs passed to ``run()`` so we can assert the exact contract
(decision 11: no parent step/chunk hooks; node_id-only filenames; failed
NodeResults never raise).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pantheon.workflow.models import NodeCall
from pantheon.workflow.runner import InProcessRunner, NodeRunContext
from pantheon.workflow.storage import WorkflowStorage

WF_ID = "wf-test"


# --------------------------------------------------------------------------- #
# Fake agent infrastructure
# --------------------------------------------------------------------------- #
class _Details:
    def __init__(self, messages):
        self.messages = messages


class _Response:
    """Minimal stand-in for AgentResponse (.content + .details.messages)."""

    def __init__(self, content, messages=None):
        self.content = content
        self.details = _Details(messages or [])


class FakeAgent:
    """Records construction + run kwargs; returns scripted responses.

    ``script`` is a list of (kind, value) outcomes consumed per run() call:
      - ("content", obj)  -> return _Response(obj)
      - ("raise", exc)    -> raise exc
      - ("hang", None)    -> await forever (for timeout tests)
    """

    instances: list["FakeAgent"] = []
    script: list = []  # shared scripted outcomes; reset per test

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.run_calls: list[dict] = []
        FakeAgent.instances.append(self)

    async def run(self, msg, **kwargs):
        kwargs["msg"] = msg
        self.run_calls.append(kwargs)
        kind, value = FakeAgent.script.pop(0)
        if kind == "raise":
            raise value
        if kind == "hang":
            await asyncio.Event().wait()
        return _Response(value, messages=[{"role": "assistant", "content": "x"}])


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAgent.instances = []
    FakeAgent.script = []
    yield


@pytest.fixture
def storage(tmp_path):
    s = WorkflowStorage(tmp_path)
    s.ensure_workflow(WF_ID)
    return s


@pytest.fixture
def ctx(storage):
    return NodeRunContext(
        workflow_id=WF_ID,
        storage=storage,
        chat_workdir="/tmp/chat",
        execution_context_id="ecid-1",
    )


def _runner():
    return InProcessRunner(agent_factory=FakeAgent)


# --------------------------------------------------------------------------- #
# 1. Agent constructed with correct params
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_agent_constructed_with_correct_params(ctx):
    FakeAgent.script = [("content", "hello")]
    schema = {"type": "object"}
    nc = NodeCall(node_id=7, instruction="Do the thing", schema=schema, model="m1")

    await _runner().run(nc, ctx)

    agent = FakeAgent.instances[0]
    kw = agent.init_kwargs
    assert kw["name"] == f"wf-{WF_ID}-n7"
    # instructions include the engine protocol layer (from compose_node_prompt)
    assert "Execution contract" in kw["instructions"]
    assert kw["model"] == "m1"
    assert kw["response_format"] == schema


@pytest.mark.asyncio
async def test_model_falls_back_to_template(ctx):
    FakeAgent.script = [("content", "ok")]
    nc = NodeCall(node_id=1, instruction="x")  # generic template has model=None
    await _runner().run(nc, ctx)
    assert FakeAgent.instances[0].init_kwargs["model"] is None


# --------------------------------------------------------------------------- #
# 2. Memory file path uses node_id only, ignoring malicious label
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_memory_path_ignores_label(ctx, storage):
    FakeAgent.script = [("content", "ok")]
    nc = NodeCall(node_id=42, instruction="x", label="../../etc/passwd")
    await _runner().run(nc, ctx)

    mem = FakeAgent.instances[0].init_kwargs["memory"]
    # The runner controls the raw file_path it passes to Memory (the public
    # .file_path property is re-derived from a uuid backend id, so assert on
    # the path the runner actually supplied).
    expected = str(storage.node_memory_path(WF_ID, 42))
    assert mem._file_path == expected
    assert expected.endswith("nodes/n42.jsonl")


# --------------------------------------------------------------------------- #
# 3. Result written to context file; result_ref relative
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_result_written_to_context_file(ctx, storage):
    FakeAgent.script = [("content", {"k": "v"})]
    nc = NodeCall(node_id=3, instruction="x", schema={"type": "object"})

    res = await _runner().run(nc, ctx)

    assert res.status == "completed"
    path = storage.node_context_path(WF_ID, 3)
    assert path.exists()
    assert json.loads(path.read_text()) == {"k": "v"}
    assert res.result_ref == "context/n3.json"
    assert res.result == {"k": "v"}


@pytest.mark.asyncio
async def test_text_result_serialization(ctx, storage):
    FakeAgent.script = [("content", "plain text")]
    nc = NodeCall(node_id=5, instruction="x")  # no schema -> text node

    res = await _runner().run(nc, ctx)

    assert res.status == "completed"
    path = storage.node_context_path(WF_ID, 5)
    assert json.loads(path.read_text()) == {"result": "plain text"}


# --------------------------------------------------------------------------- #
# 4. Schema retry: invalid then valid -> completed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_schema_retry_then_success(ctx):
    # First call: schema node returns None (invalid). Second: valid object.
    FakeAgent.script = [("content", None), ("content", {"ok": True})]
    nc = NodeCall(node_id=9, instruction="x", schema={"type": "object"})

    res = await _runner().run(nc, ctx)

    assert res.status == "completed"
    assert res.result == {"ok": True}
    # Exactly two run() invocations (one retry).
    assert len(FakeAgent.instances[0].run_calls) == 2
    # The retry message mentions invalid output.
    retry_msg = FakeAgent.instances[0].run_calls[1]["msg"]
    assert "invalid" in retry_msg.lower() or "schema" in retry_msg.lower()


@pytest.mark.asyncio
async def test_schema_retry_on_raise(ctx):
    FakeAgent.script = [("raise", ValueError("bad json")), ("content", {"ok": 1})]
    nc = NodeCall(node_id=11, instruction="x", schema={"type": "object"})

    res = await _runner().run(nc, ctx)
    assert res.status == "completed"
    assert res.result == {"ok": 1}


# --------------------------------------------------------------------------- #
# 5. Schema retry exhausted -> failed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_schema_retry_exhausted_fails(ctx):
    FakeAgent.script = [("content", None), ("content", None)]
    nc = NodeCall(node_id=13, instruction="x", schema={"type": "object"})

    res = await _runner().run(nc, ctx)
    assert res.status == "failed"
    assert res.error


# --------------------------------------------------------------------------- #
# 6. Timeout
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_timeout(ctx):
    FakeAgent.script = [("hang", None)]
    nc = NodeCall(node_id=15, instruction="x", timeout=0.05)

    res = await _runner().run(nc, ctx)
    assert res.status == "failed"
    assert "timeout" in res.error.lower()


# --------------------------------------------------------------------------- #
# 7. Exception -> failed NodeResult, not raised
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_exception_becomes_failed_result(ctx):
    FakeAgent.script = [("raise", RuntimeError("boom"))]
    nc = NodeCall(node_id=17, instruction="x")  # no schema: no retry

    res = await _runner().run(nc, ctx)
    assert res.status == "failed"
    assert "boom" in res.error


# --------------------------------------------------------------------------- #
# 8. No parent hooks passed (decision 11)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_no_parent_hooks(ctx):
    FakeAgent.script = [("content", "ok")]
    nc = NodeCall(node_id=19, instruction="x")
    await _runner().run(nc, ctx)

    run_kw = FakeAgent.instances[0].run_calls[0]
    assert run_kw.get("process_step_message") is None
    assert run_kw.get("process_chunk") is None
    # And the run gets the expected isolation flags.
    assert run_kw["use_memory"] is False
    assert run_kw["update_memory"] is True
    assert run_kw["allow_transfer"] is False
    assert run_kw["execution_context_id"] == "ecid-1"
