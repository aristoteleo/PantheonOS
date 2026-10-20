"""Run a sub-agent in its own process (docs/os/pantheon-cli.md).

The Agent App starts ``python -m pantheon.team.subagent_child`` and answers
what it asks with the sub-agent's own Agent: its model calls (the Agent App's
model scope), its tool calls (the Agent App's grants, so its commands get the
sub-agent's tokens) and its compactions. It relays the child's step messages
to the conversation as an in-process sub-agent's are, and the child's final
answer is the result. The child gets no credentials: a minimal environment,
its own empty home and working directory.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

from pantheonos.utils.log import logger

#: How long a stopped child may take to end before it is killed.
STOP_SECONDS = 5.0
_KEEP_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")


def child_environment(home: str) -> dict[str, str]:
    """No credential of the Agent App or its node: only what Python needs."""
    env = {key: os.environ[key] for key in _KEEP_ENV if key in os.environ}
    env.update({"HOME": home, "USERPROFILE": home, "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)})
    return env


async def run_in_child_process(
    target_agent,
    task_message: Any,
    *,
    process_step_message: Callable | None,
    process_chunk: Callable | None,
    execution_context_id: str,
    context_variables: dict,
    max_turns: int | float = float("inf"),
    memory=None,
    model: str | list[str] | None = None,
    steer=None,
):
    """Run target_agent's task in a child process; returns an object with
    ``content`` (its final answer), as Agent.run's response has.

    steer: the run's queue in the chatroom (SteerQueue.open_run). Messages the
    user sends this sub-agent go to the child, which reads them at its next
    turn; the ones it ends without reading go back to the queue. Its
    ``stopped`` stops the child.
    """
    from pantheon.agent import _RUN_CONTEXT, AgentRunContext, StopRunning
    from pantheon.agent_context import compact_history
    from pantheonos.utils.misc import run_func

    run_context = AgentRunContext(agent=target_agent, memory=memory, execution_context_id=execution_context_id,
                                  process_step_message=process_step_message, process_chunk=process_chunk)
    steps: list[dict] = []
    home = tempfile.mkdtemp(prefix="pantheon-subagent-")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pantheon.team.subagent_child",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=child_environment(home), cwd=home, limit=256 * 1024 * 1024)
    write_lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with write_lock:
            process.stdin.write((json.dumps(message, ensure_ascii=False, default=str) + "\n").encode())
            await process.stdin.drain()

    async def answer(call: dict) -> None:
        method, params = call.get("method"), call.get("params") or {}
        token = _RUN_CONTEXT.set(run_context)
        try:
            if method == "complete":
                result = await target_agent._acompletion_with_models(
                    params.get("history") or [], tool_use=params.get("tool_use", True),
                    process_chunk=process_chunk, model=params.get("model"),
                    context_variables=context_variables)
            elif method == "tool":
                result = await target_agent.call_tool(params["name"], params.get("args") or {},
                                                      context_variables, params.get("tool_call_id"))
            elif method == "compact":
                result = await compact_history(target_agent, params.get("history") or [], params.get("model"),
                                               force=bool(params.get("force")))
            else:
                raise ValueError(f"Unknown call {method!r}")
            reply = {"type": "reply", "id": call.get("id"), "ok": True, "result": result}
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - the child decides what a failure means
            reply = {"type": "reply", "id": call.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"[:4000]}
        finally:
            _RUN_CONTEXT.reset(token)
        with contextlib.suppress(ConnectionError, RuntimeError):
            await send(reply)

    async def log_stderr() -> None:
        while line := await process.stderr.readline():
            logger.debug(f"[sub-agent {target_agent.name}] {line.decode(errors='replace').rstrip()}")

    async def forward() -> None:
        while True:
            await asyncio.sleep(0.25)
            if steer.stopped:
                await send({"type": "stop"})
                return
            if len(steer):
                await send({"type": "steer", "messages": steer.drain()})

    calls: set[asyncio.Task] = set()
    stderr = asyncio.create_task(log_stderr())
    forwarding = asyncio.create_task(forward()) if steer is not None else None
    outcome: dict | None = None
    try:
        await send({"type": "start", "name": target_agent.name, "instructions": target_agent.instructions,
                    "description": getattr(target_agent, "description", "") or "",
                    "models": list(getattr(target_agent, "models", None) or []), "task": task_message,
                    "execution_context_id": execution_context_id, "context_variables": context_variables,
                    "model": model,
                    "max_turns": None if max_turns == float("inf") else max_turns})
        while outcome is None:
            line = await process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "call":
                task = asyncio.create_task(answer(message))
                calls.add(task)
                task.add_done_callback(calls.discard)
            elif kind == "step" and isinstance(message.get("message"), dict):
                message["message"].setdefault("id", str(uuid4()))
                steps.append(message["message"])
                if process_step_message is not None:
                    await run_func(process_step_message, message["message"])
            elif kind in ("done", "failed"):
                outcome = message
    except asyncio.CancelledError:
        # The run was stopped: ask the child to stop, then end it.
        with contextlib.suppress(Exception):
            await send({"type": "stop"})
        raise
    finally:
        if forwarding is not None:
            forwarding.cancel()
        for task in calls:
            task.cancel()
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                try:
                    await asyncio.wait_for(process.wait(), STOP_SECONDS if outcome is None else 30)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        stderr.cancel()
        with contextlib.suppress(OSError):
            import shutil
            shutil.rmtree(home, ignore_errors=True)
        if memory is not None and steps:
            memory._messages = steps
    if steer is not None and outcome and outcome.get("unread"):
        steer.put(outcome["unread"])
    if outcome is None:
        raise RuntimeError(f"The sub-agent process ended without an answer (exit {process.returncode})")
    if outcome.get("type") == "failed":
        error = str(outcome.get("error") or "")
        if error.startswith("StopRunning"):
            raise StopRunning(error)
        raise RuntimeError(f"The sub-agent failed: {error}")
    return SimpleNamespace(content=outcome.get("content") or "")
