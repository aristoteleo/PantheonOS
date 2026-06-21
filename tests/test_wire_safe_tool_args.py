"""Regression test for the cross-process tool-call sanitizer.

Bug: the agent injects live closures into ``context_variables`` (``_call_agent``,
``_report_output``) plus a top-level ``_call_agent``. These capture the running
Agent, which holds asyncio Future/Task objects. With the multi-project refactor,
tool calls are cloudpickled to remote toolset/endpoint subprocesses, so every I/O
tool died with ``cannot pickle '_asyncio.Future' object`` (foreground) or
``'_asyncio.Task'`` (background).

``wire_safe_tool_args`` strips the non-sendable entries while preserving all plain
context, and runs only on the remote (cross-process) path.
"""
import asyncio

import cloudpickle
import pytest

from pantheon.utils.misc import wire_safe_tool_args


def test_non_dict_args_passthrough():
    assert wire_safe_tool_args(None) is None
    assert wire_safe_tool_args("notebook_path") == "notebook_path"


@pytest.mark.asyncio
async def test_strips_unsendable_and_stays_picklable():
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    task = asyncio.ensure_future(asyncio.sleep(0))

    def _call_agent_wrap(*a, **k):
        return fut  # closure captures an asyncio.Future -> unpicklable

    args = {
        "action": "create",
        "notebook_path": "/x/y.ipynb",
        "_background": False,
        "_call_agent": _call_agent_wrap,  # top-level closure (forwarded via _SKIP_PARAMS)
        "context_variables": {
            "workdir": "/Users/me/Desktop/tmp",
            "chat_id": "9c89f7e9",
            "model_params": {"temperature": 0.2},
            "_call_agent": _call_agent_wrap,
            "_report_output": lambda line: None,
            "_live_future": fut,
            "_live_task": task,
        },
    }

    # Reproduce the bug: the raw payload cannot be cloudpickled.
    with pytest.raises(TypeError):
        cloudpickle.dumps(args)

    safe = wire_safe_tool_args(args)

    # The sanitized payload MUST be wire-safe.
    cloudpickle.dumps(safe)

    # Real tool arguments + plain context are preserved verbatim.
    assert safe["action"] == "create"
    assert safe["notebook_path"] == "/x/y.ipynb"
    assert safe["_background"] is False
    cv = safe["context_variables"]
    assert cv["workdir"] == "/Users/me/Desktop/tmp"
    assert cv["chat_id"] == "9c89f7e9"
    assert cv["model_params"] == {"temperature": 0.2}

    # The non-sendable closures / asyncio objects are gone.
    assert "_call_agent" not in safe
    for k in ("_call_agent", "_report_output", "_live_future", "_live_task"):
        assert k not in cv

    task.cancel()


def test_preserves_plain_args_without_context():
    args = {"action": "write", "path": "report.md", "content": "x" * 10}
    assert wire_safe_tool_args(args) == args
