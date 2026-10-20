"""The agent loop (docs/agent/agent-core.md).

A run is a sequence of turns: one model call, then the tool calls it asked
for, until the model answers without tools. Everything else attaches to the
loop through AgentHooks or arrives as pending messages:

* ``context``: messages added to one model call only (reminders, budgets);
  they are not part of the conversation.
* ``before_tool`` / ``after_tool``: gate or decorate one tool call.
* ``after_tools``: observe a turn's tool calls and results (task tracking).
* ``before_compaction``: see the conversation before its older part is
  summarized (the memory system saves what matters).
* Pending messages: user messages queued while the agent works (steering),
  read before every model call, and background-task completions.

Before every model call the conversation is compacted when the prompt would
not fit the model's window (pantheon/agent_context.py); a provider that still
reports an overflow gets one compaction and the same turn again.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from pantheonos.utils.log import logger
from pantheonos.utils.misc import run_func

if TYPE_CHECKING:
    from .agent import Agent

STEERING = (
    "<STEERING_MESSAGE>\n"
    "The user sent the following message while you are still working. Treat it as a course "
    "correction: pause, reconsider your current plan in light of it, and adjust your next actions "
    "accordingly. Briefly acknowledge it before continuing.\n\n"
    "{text}\n"
    "</STEERING_MESSAGE>"
)


@dataclass
class AgentHooks:
    """Extension points of the agent loop.

    context:     async (history, context_variables) -> list[dict]
    before_tool: async (name, args) -> result | None  (non-None answers instead of the tool)
    after_tool:  async (name, args, result) -> result | None  (non-None replaces the result)
    after_tools: async (tool_calls, tool_messages, context_variables) -> None
    before_compaction: async (messages, memory) -> None
    """

    context: list = field(default_factory=list)
    before_tool: list = field(default_factory=list)
    after_tool: list = field(default_factory=list)
    after_tools: list = field(default_factory=list)
    before_compaction: list = field(default_factory=list)


def _for_model(agent: "Agent", messages: list[dict]) -> list[dict]:
    """Expand file:// image references for a model call (unless the Agent
    resolves images itself, per call)."""
    if agent._image_resolver is not None:
        return messages
    from pantheonos.utils.vision import expand_image_references_for_llm
    return expand_image_references_for_llm(messages)


def steer_queue(agent: "Agent", execution_context_id: str | None):
    """Where a run reads the user's messages from: the conversation's queue,
    or a sub-agent run's own one (pantheon/chatroom/thread.py SteerQueue)."""
    steer = agent._steer_queue
    if steer is not None and execution_context_id is not None and hasattr(steer, "run_queue"):
        return steer.run_queue(execution_context_id)
    return steer


async def _drain_steering(agent: "Agent", history: list[dict], execution_context_id: str | None,
                          process_step_message: Callable | None) -> int:
    """Move user messages queued during the run into the conversation.

    Each is stored and shown like the run's input, then appended to the
    model's history wrapped as a course correction. Returns the count.
    """
    steer = steer_queue(agent, execution_context_id)
    if steer is None or len(steer) == 0:
        return 0
    pending = steer.drain()
    for raw in pending:
        message = copy.deepcopy(raw)
        if execution_context_id is not None and "execution_context_id" not in message:
            message["execution_context_id"] = execution_context_id
        # The step hook publishes this user message so the UI can clear its
        # "Queued" badge (it drops ordinary user steps to avoid echoing input).
        message["_steer_drained"] = True
        if process_step_message:
            await run_func(process_step_message, message)
        for_model = dict(message)
        base = for_model.pop("_llm_content", None)
        base = for_model.get("content") if base is None else base
        for_model["content"] = STEERING.format(text=base) if isinstance(base, str) else base
        history.extend(_for_model(agent, [for_model]))
    if pending:
        logger.info("[steer] drained {} queued user message(s) into agent={} run", len(pending), agent.name)
    return len(pending)


def _background_notice(agent: "Agent") -> dict | None:
    """A note to the model about background tasks that finished."""
    finished = agent._bg_manager.drain_notifications()
    if not finished:
        return None
    lines = []
    for task in finished:
        summary = agent._bg_manager.to_summary(task)
        lines.append(f"- task_id='{task.task_id}', tool='{task.tool_name}', status='{task.status}', "
                     f"result: {str(summary.get('result', ''))[:500]}")
    return {
        "role": "user",
        "content": ("<EPHEMERAL_MESSAGE>\n[Background Task Notification] The following background tasks "
                    "have completed:\n" + "\n".join(lines)
                    + "\nPlease inform the user about these results.\n</EPHEMERAL_MESSAGE>"),
    }


async def _call_model(agent: "Agent", messages: list[dict], check_stop: Callable | None, **kwargs) -> dict:
    """One model call that a stop aborts within ~0.1 s, even before the first
    token (when the per-chunk stop check cannot fire)."""
    from .agent import StopRunning

    task = asyncio.create_task(agent._acompletion_with_models(messages, **kwargs))
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.1)
        if task in done:
            return task.result()
        if check_stop is not None and check_stop(None):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            raise StopRunning()


async def _compact(agent: "Agent", history: list[dict], model: str, process_step_message: Callable | None,
                   execution_context_id: str | None, *, force: bool = False) -> bool:
    """Append a compaction record to history when needed; True when one was added."""
    try:
        record = await agent._compact_history(history, model, force=force)
    except Exception as e:
        if force:
            raise
        logger.warning(f"[context] compaction failed: {e}")
        return False
    if record is None:
        return False
    if execution_context_id is not None:
        record["execution_context_id"] = execution_context_id
    record["agent_name"] = agent.name
    history.append(record)
    if process_step_message:
        await run_func(process_step_message, record)
    return True


async def _record_tool_messages(agent: "Agent", history: list[dict], tool_messages: list[dict],
                                process_step_message: Callable | None) -> None:
    history.extend(tool_messages)
    for tool_msg in tool_messages:
        agent.events_queue.put_nowait(tool_msg)
    if process_step_message:
        from pantheonos.utils.llm import process_messages_for_hook_func
        for step in process_messages_for_hook_func(tool_messages):
            step["agent_name"] = agent.name
            await run_func(process_step_message, step)


EMPTY_REPLY_NOTE = ("(Your last reply had no text and no tool call. Continue the task: call the "
                    "tools you need now, or answer.)")


def _usage_note(message: dict) -> str:
    usage = message.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return f"{usage.get('completion_tokens')} completion tokens, {reasoning} reasoning"


async def run_turns(
    agent: "Agent",
    history: list[dict],
    *,
    process_chunk: Callable | None,
    process_step_message: Callable | None,
    check_stop: Callable | None,
    max_turns: int | float,
    context_variables: dict,
    response_format: Any,
    tool_use: bool,
    tool_timeout: int,
    model: str | list[str] | None,
    execution_context_id: str | None,
) -> int:
    """Run turns on history (which starts with the system message) until the
    model answers without tool calls. New messages are appended to history and
    passed to process_step_message. Returns the index where they start."""
    from .agent import StopRunning

    from .agent_context import is_context_overflow, says_nothing
    from .agent_model import _model_chain
    from .agent_tools import ToolCallsStopped

    init_len = len(history)
    primary = _model_chain(agent, model)[0]
    overflowed = False
    # A reply with neither text nor a tool call is asked for again once, with
    # a note the model reads but history does not keep.
    empty_replies = 0
    nudge: dict | None = None

    async def chunk(c: dict):
        if process_chunk is not None:
            await run_func(process_chunk, c)
        if check_stop is not None and check_stop(c):
            raise StopRunning()

    while len(history) - init_len < max_turns:
        if check_stop is not None and check_stop(None):
            raise StopRunning()
        await _drain_steering(agent, history, execution_context_id, process_step_message)
        await _compact(agent, history, primary, process_step_message, execution_context_id)

        view = list(history)
        for hook in agent.hooks.context:
            view.extend(await hook(history, context_variables))
        notice = _background_notice(agent)
        if notice is not None:
            view.append(notice)
        if nudge is not None:
            view.append(nudge)
            nudge = None

        try:
            message = await _call_model(
                agent, view, check_stop, tool_use=tool_use, response_format=response_format,
                process_chunk=chunk, model=model, context_variables=context_variables)
        except StopRunning:
            raise
        except Exception as error:
            if overflowed or not is_context_overflow(error):
                raise
            # The provider counted more than the estimate: compact and retry the turn once.
            overflowed = True
            logger.warning(f"[context] {agent.name}: the model rejected the prompt as too long; compacting")
            if not await _compact(agent, history, primary, process_step_message, execution_context_id, force=True):
                raise
            continue
        overflowed = False
        await agent._expand_app_tool_calls(message)
        if response_format is not None and message.get("content"):
            message["parsed"] = response_format.model_validate_json(message["content"]).result
        message["agent_name"] = agent.name
        history.append(message)
        agent.events_queue.put_nowait(message)
        if process_step_message:
            await run_func(process_step_message, message)

        if not message.get("tool_calls"):
            if says_nothing(message) and empty_replies < 1:
                # Thinking alone, or a tool call the provider failed to return.
                # Never continue the empty reply itself: a conversation that
                # ends with an assistant message asks the provider to extend it.
                empty_replies += 1
                logger.warning(f"[loop] {agent.name}: empty reply ({_usage_note(message)}); asking again once")
                nudge = {"role": "user", "content": EMPTY_REPLY_NOTE}
                continue
            # A message the user sent during this last turn keeps the run going.
            if await _drain_steering(agent, history, execution_context_id, process_step_message):
                continue
            break
        empty_replies = 0

        try:
            tool_messages = await agent._handle_tool_calls(
                message.get("tool_calls") or [], context_variables=context_variables,
                timeout=tool_timeout, check_stop=check_stop)
        except ToolCallsStopped as stop:
            # The calls ran (some partly): keep them, so the next turn and the
            # user see what was done before the stop.
            await _record_tool_messages(agent, history, stop.messages, process_step_message)
            raise
        for hook in agent.hooks.after_tools:
            await hook(message.get("tool_calls") or [], tool_messages, context_variables)

        await _record_tool_messages(agent, history, tool_messages, process_step_message)

        # notify_user hands the turn back to the user.
        if any(isinstance(m.get("raw_content"), dict) and m["raw_content"].get("interrupt") for m in tool_messages):
            break
    return init_len
