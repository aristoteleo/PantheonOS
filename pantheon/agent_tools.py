"""Running one turn's tool calls (docs/agent-core.md).

execute_tool_calls() runs a model message's tool calls concurrently and
returns their tool messages. Each call passes the Agent's before_tool hooks
(one may answer instead of the tool), runs in the foreground or, with
``_background`` / a timeout / a queued user message, in the background, and
its result passes the after_tool hooks before it becomes a tool message.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from .utils.llm import process_tool_result
from .utils.log import logger

if TYPE_CHECKING:
    from .agent import Agent

#: Min seconds a foreground tool must have been running before a queued user
#: (steer) message moves it to the background so the agent can respond.
STEER_BG_MIN_SECONDS = 5.0



def _job_status(agent, task_id: str) -> str:
    """How the model checks a background call: a core-mode Agent has no
    background_task tool, only the pantheon command."""
    if getattr(agent, "tool_exposure", "all") == "core":
        return f"`pantheon jobs status {task_id}` (or `pantheon jobs wait {task_id}`)"
    return f"background_task(action='status', task_id='{task_id}')"

def parse_tool_arguments(raw: str) -> dict:
    """Best-effort parse of a tool call's ``arguments`` JSON into a dict.

    Weaker models (notably glm) emit arguments with unescaped control
    characters in a long string value, unescaped inner quotes, trailing junk or
    a truncated close; a strict parse would drop an otherwise good call.
    """
    import json

    if raw is None:
        return {}
    s = str(raw)
    if not s.strip():
        return {}
    try:
        value = json.loads(s)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    # Control characters allowed in strings; the first complete object wins.
    try:
        value, _ = json.JSONDecoder(strict=False).raw_decode(s.lstrip())
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    # Truncated close: append the likely missing terminators.
    stripped = s.rstrip()
    for suffix in ("}", '"}', '"}}', "]}", '"]}'):
        try:
            value = json.loads(stripped + suffix, strict=False)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    # Unescaped inner quotes (optional dependency).
    try:
        from json_repair import repair_json

        value = json.loads(repair_json(s))
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    raise ValueError("could not parse tool_call arguments as a JSON object")


def tool_output_dir(agent: "Agent"):
    """Where the full outputs of capped tool results are saved, on the Agent's node."""
    return agent._settings().tmp_dir / "tool-results"


def _content(agent: "Agent", func_name: str, result: Any) -> Any:
    """The model-facing content of a tool result, capped (full output to a file)."""
    return process_tool_result(
        result,
        max_length=agent.max_tool_content_length,
        tool_name=func_name,
        temp_dir=str(tool_output_dir(agent)),
    )


def read_tool_output(agent: "Agent", args: dict) -> dict | None:
    """read_file of a saved tool output: it is on the Agent's node, not in the
    workspace, so the Agent reads it itself. None for any other path."""
    from pathlib import Path

    path = args.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    root = tool_output_dir(agent).resolve()
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if target.parent != root:
        return None
    if not target.is_file():
        return {"success": False, "error": "This saved tool output no longer exists."}
    lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
    start = max(int(args.get("start_line") or 1), 1)
    end = min(int(args.get("end_line") or len(lines)), len(lines))
    limit = int(args.get("max_chars") or agent.max_tool_content_length)
    content = "\n".join(lines[start - 1:end])
    result = {"success": True, "content": content[:limit], "total_lines": len(lines),
              "start_line": start, "end_line": end}
    if len(content) > limit:
        result["truncated"] = True
        result["truncation_info"] = f"Showing the first {limit} characters; read a smaller line range."
    return result


def tool_message(agent: "Agent", func_name: str, tool_call_id: str, result: Any,
                 start_time: float, end_time: float) -> dict:
    """The tool message recording one call's result."""
    from .agent import _RUN_CONTEXT

    message = {
        "role": "tool",
        "tool_name": func_name,
        "name": func_name,
        "id": str(uuid4()),
        "tool_call_id": tool_call_id,
        "timestamp": end_time,
        "_metadata": {
            "start_timestamp": start_time,
            "end_timestamp": end_time,
            "execution_duration": end_time - start_time,
        },
    }
    # A call_agent result carries the sub-agent's execution_context_id, so the
    # UI links it to that exact sub-agent run even among parallel calls.
    run_context = _RUN_CONTEXT.get()
    if run_context is not None and tool_call_id:
        child = run_context.sub_agent_exec_ids.pop(tool_call_id, None)
        if child:
            message["execution_context_id"] = child

    # Cost and other metadata of nested model calls made by the tool.
    if isinstance(result, dict) and "_metadata" in result:
        message["_metadata"].update(result.pop("_metadata", {}))

    # A tool may return images as OpenAI content blocks ("content_blocks"): the
    # model receives them natively after a text summary of the other fields.
    blocks = result.get("content_blocks") if isinstance(result, dict) else None
    if isinstance(blocks, list) and any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks):
        summary = _content(agent, func_name, {k: v for k, v in result.items() if k != "content_blocks"})
        merged = ([{"type": "text", "text": summary if isinstance(summary, str) else str(summary)}]
                  if summary else []) + blocks
        # Base64 images go to the image store; the message keeps file references.
        try:
            holder = {"content": merged}
            agent._image_store().process_message_images(
                holder, agent.memory.id if agent.memory else "default",
                normalize_paths=agent._image_resolver is None)
            merged = holder["content"]
        except Exception as e:
            logger.debug(f"ImageStore processing failed: {e}")
        message.update({"raw_content": result, "content": merged})
    else:
        message.update({"raw_content": result, "content": _content(agent, func_name, result)})
    return message


async def execute_tool_calls(
    agent: "Agent",
    tool_calls: list,
    context_variables: dict,
    timeout: float,
    time_delta: float = 0.5,
    check_stop: Callable | None = None,
) -> list[dict]:
    """Run tool calls concurrently; returns their tool messages in call order."""
    from .background import _bg_output_buffer

    async def run_one(call: dict) -> dict:
        func_name = call["function"]["name"]
        tool_call_id = call["id"]
        start_time = time.time()
        try:
            params = parse_tool_arguments(call["function"]["arguments"]) or {}
            parse_error = None
        except Exception as e:
            logger.warning(f"Failed to parse arguments for tool '{func_name}': {e}")
            params, parse_error = {}, e

        # Delegation stays synchronous, and background_task is how the model
        # waits for backgrounded work; neither moves to the background.
        allow_timeout = func_name not in ("call_agent", "background_task")
        run_in_bg = params.pop("_background", False)
        if func_name == "call_agent" and run_in_bg:
            logger.warning("Ignoring _background=True for call_agent; delegation stays synchronous")
            run_in_bg = False

        # A before_tool hook may answer instead of the tool (first non-None wins).
        short_circuit = None
        for hook in agent.hooks.before_tool:
            try:
                answer = await hook(func_name, params)
            except Exception as e:  # a misbehaving hook must never break tool dispatch
                logger.warning(f"before_tool hook error for '{func_name}': {e}")
                answer = None
            if answer is not None:
                short_circuit = answer
                break

        if short_circuit is not None:
            result = short_circuit
        elif parse_error:
            raw = call["function"]["arguments"]
            result = (f"Error: Failed to parse tool arguments.\nJSON error: {parse_error}\n"
                      f"Raw arguments: {raw[:200]}{'...' if len(raw) > 200 else ''}")
        elif run_in_bg:
            bg_tool_call_id = f"bg_call_{uuid4()}"
            token = _bg_output_buffer.set([])
            try:
                # Snapshot args before call_tool injects context_variables.
                bg_args = dict(params)
                bg_task = agent._bg_manager.start(
                    tool_name=func_name, tool_call_id=bg_tool_call_id, args=bg_args,
                    coro=agent.call_tool(func_name, params, context_variables, tool_call_id=bg_tool_call_id),
                    source="explicit",
                )
            finally:
                _bg_output_buffer.reset(token)
            result = {
                "task_id": bg_task.task_id,
                "status": "running",
                "tool_name": func_name,
                "message": f"Tool launched in background. Use {_job_status(agent, bg_task.task_id)} to check progress.",
            }
        else:
            result = await _foreground(agent, call, func_name, tool_call_id, params, context_variables,
                                       timeout, time_delta, check_stop, allow_timeout, start_time)

        end_time = time.time()
        # after_tool hooks decorate an executed tool's result (a non-None return replaces it).
        if short_circuit is None:
            for hook in agent.hooks.after_tool:
                try:
                    replaced = await hook(func_name, params, result)
                except Exception as e:
                    logger.warning(f"after_tool hook error for '{func_name}': {e}")
                    replaced = None
                if replaced is not None:
                    result = replaced
        return tool_message(agent, func_name, tool_call_id, result, start_time, end_time)

    tasks = [asyncio.create_task(run_one(call)) for call in tool_calls]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        raise


async def _foreground(agent, call, func_name, tool_call_id, params, context_variables, timeout,
                      time_delta, check_stop, allow_timeout, start_time) -> Any:
    """Run one tool call in the foreground. A call past its timeout, or one a
    queued user message is waiting on, is adopted into the background."""
    from .agent import StopRunning
    from .background import _bg_output_buffer

    stdout_buffer: list[str] = []
    token = _bg_output_buffer.set(stdout_buffer)
    call_task = asyncio.create_task(agent.call_tool(func_name, params, context_variables, tool_call_id=tool_call_id))
    adopted = False
    try:
        while True:
            done, _ = await asyncio.wait({call_task}, timeout=time_delta, return_when=asyncio.FIRST_COMPLETED)
            if call_task in done:
                result = call_task.result()
                break
            elapsed = time.time() - start_time
            steer_pending = agent._steer_queue is not None and len(agent._steer_queue) > 0
            timed_out = timeout is not None and elapsed >= timeout
            steer_background = steer_pending and elapsed >= STEER_BG_MIN_SECONDS
            if allow_timeout and (timed_out or steer_background):
                # Keep the same output list so later prints keep accumulating.
                stdout_buffer.extend(agent._tool_output_buffers.pop(tool_call_id, []))
                bg_task = agent._bg_manager.adopt(
                    tool_name=func_name, tool_call_id=tool_call_id, args=params,
                    existing_task=call_task, output_buffer=stdout_buffer)
                adopted = True
                status = _job_status(agent, bg_task.task_id)
                if steer_background and not timed_out:
                    result = (f"Tool '{func_name}' was moved to background execution so you can respond to a "
                              f"new user message that just arrived. task_id='{bg_task.task_id}'. Use {status} "
                              f"to check its progress/results later.")
                else:
                    result = (f"Tool '{func_name}' exceeded timeout ({timeout}s) and was moved to background "
                              f"execution. task_id='{bg_task.task_id}'. Use {status} to check progress and results.")
                break
            if check_stop is not None and check_stop(elapsed):
                call_task.cancel()
                raise StopRunning()
        if not adopted:
            agent._tool_output_buffers.pop(tool_call_id, None)
        return result
    except StopRunning:
        agent._tool_output_buffers.pop(tool_call_id, None)
        raise
    except SystemExit as e:
        if not call_task.done():
            call_task.cancel()
        agent._tool_output_buffers.pop(tool_call_id, None)
        return f"SystemExit: {e}"
    except Exception as e:
        if not call_task.done():
            call_task.cancel()
        agent._tool_output_buffers.pop(tool_call_id, None)
        return repr(e)
    finally:
        _bg_output_buffer.reset(token)
        # If we are cancelled, the tool is too (unless adopted into the background).
        if not call_task.done() and not agent._bg_manager._is_adopted(call_task):
            logger.warning(f"Cancelling orphaned tool task for {func_name}")
            call_task.cancel()
            # Cancellation can still be reaping a subprocess or draining an
            # accepted operation: keep this run alive until it finishes.
            while not call_task.done():
                try:
                    await asyncio.shield(call_task)
                except asyncio.CancelledError:
                    if call_task.done():
                        break
                except Exception:
                    break
            if not call_task.cancelled():
                call_task.result()
