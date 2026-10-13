"""What the model sees of a conversation (docs/agent-core.md).

One mechanism keeps a conversation within its model's context window:

* A tool result is capped once, when the tool returns (2,000 lines or 50 KB);
  the model reads the head and the path of a file with the full output
  (``pantheon.utils.truncate``).
* When the next prompt would not fit, the older part of the conversation is
  summarized (goal, progress, decisions, files, next steps) into a compaction
  record appended to the conversation. From then on the model sees the summary
  followed by the recent turns verbatim (about 20k tokens). Nothing is deleted:
  the earlier records stay in the conversation for the UI, fork and revert.

A compaction record is a message with ``role: "compression"``. Its
``first_kept_id`` names the first record kept verbatim; records without it
(written before this mechanism) keep everything after themselves.
"""
from __future__ import annotations

import json
import time
from typing import Awaitable, Callable
from uuid import uuid4

from .utils.log import logger

COMPACTION_ROLE = "compression"

#: Tokens of recent conversation kept verbatim after a compaction.
KEEP_RECENT_TOKENS = 20_000
#: Tokens left free for the model's answer when deciding to compact.
RESERVE_TOKENS = 16_384
#: Window assumed for a model whose limit is unknown.
DEFAULT_WINDOW = 200_000
#: Longest conversation text sent to the summarizer at once.
_SUMMARY_INPUT_CHARS = 400_000
_TOOL_RESULT_CHARS = 4_000
_IMAGE_TOKENS = 2_000

SUMMARY_SYSTEM = "You summarize a conversation so that work on it can continue without it."

SUMMARY_PROMPT = """Summarize the conversation above so the assistant can continue the work with only your summary and the most recent messages. Respond with text only; do not call tools.

Write these sections, in Markdown, keeping exact names, paths, commands, numbers and identifiers:

## Goal
What the user wants, including constraints and preferences they stated.

## Progress
What is done (with results) and what is in progress.

## Decisions
Choices made and why; approaches that failed and why.

## Files and data
Files, data and outputs read, created or changed, with their paths and what they hold.

## Next steps
What remains, in order. Quote the user's latest request verbatim.

## Other context
Anything else needed to continue (environment, credentials named but not their values, open questions)."""

UPDATE_PROMPT = """The conversation above starts with the summary of its earlier part. Write one updated summary covering both that summary and the messages after it, so the assistant can continue the work with only your summary and the most recent messages. Respond with text only; do not call tools.

Keep the same sections (Goal, Progress, Decisions, Files and data, Next steps, Other context), merging new information into them and dropping what is no longer relevant. Keep exact names, paths, commands, numbers and identifiers. Quote the user's latest request verbatim under Next steps."""

SUMMARY_WRAPPER = """The earlier part of this conversation was summarized to fit the context window. The summary follows; the messages after it are the conversation's latest part, unchanged.

<summary>
{summary}
</summary>"""


# --- Token estimate ---------------------------------------------------------

def _text_tokens(text: str) -> int:
    return len(text) // 4


def estimate_tokens(message: dict) -> int:
    """A conservative token estimate of one message (4 bytes per token)."""
    total = 0
    content = message.get("content")
    if isinstance(content, str):
        total += _text_tokens(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind in ("image_url", "image", "document"):
                total += _IMAGE_TOKENS
            elif kind == "text":
                total += _text_tokens(block.get("text") or "")
            else:
                total += _text_tokens(json.dumps(block, ensure_ascii=False, default=str))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += _text_tokens(str(function.get("name") or "") + str(function.get("arguments") or ""))
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        total += _text_tokens(reasoning)
    return total + 4


def estimate_total(messages: list[dict]) -> int:
    return sum(estimate_tokens(m) for m in messages)


def context_window(model: str | None, *, model_scope=None) -> int:
    """The model's input window in tokens.

    A Fleet model's window is the limit confirmed in Model Services; without
    one this raises rather than assuming a window.
    """
    if not model:
        return DEFAULT_WINDOW
    if model_scope is not None and model.startswith(("fleet-model://", "fleet-route://")):
        return int(model_scope.model_info(model)["max_input_tokens"])
    try:
        if model_scope is not None:
            info = model_scope.model_info(model)
        else:
            from .utils.provider_registry import get_model_info
            info = get_model_info(model)
        return int(info.get("max_input_tokens") or DEFAULT_WINDOW)
    except Exception:
        return DEFAULT_WINDOW


# --- The model's view of stored history -------------------------------------

def is_compaction(message: dict) -> bool:
    return message.get("role") == COMPACTION_ROLE


def summary_message(record: dict) -> dict:
    """The user message the model reads in place of a compaction record."""
    content = record.get("content")
    if not isinstance(content, str):
        content = str(content or "")
    if record.get("summary") is not None:
        content = SUMMARY_WRAPPER.format(summary=record["summary"])
    return {"role": "user", "content": content, "id": record.get("id"), "_compaction": True}


EMPTY_TOOL_RESULT = "[No output]"


def guard_empty_tool_results(messages: list[dict]) -> list[dict]:
    """Empty tool results read as "[No output]": some models stop when they see
    an empty result. Content blocks (text, images) are not empty."""
    out: list[dict] = []
    for message in messages:
        content = message.get("content")
        empty = (message.get("role") == "tool"
                 and not (isinstance(content, str) and content.strip())
                 and not (isinstance(content, list) and any(
                     isinstance(b, dict) and b.get("type") in ("text", "image_url") for b in content)))
        out.append({**message, "content": EMPTY_TOOL_RESULT} if empty else message)
    return out


def model_view(messages: list[dict]) -> list[dict]:
    """The conversation as the model reads it: from the latest compaction on.

    System messages are kept at the front; other roles than user, assistant and
    tool are not sent.
    """
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    last = None
    for index in range(len(rest) - 1, -1, -1):
        if is_compaction(rest[index]):
            last = index
            break
    if last is not None:
        record = rest[last]
        kept: list[dict] = []
        first_kept = record.get("first_kept_id")
        if first_kept:
            for index in range(last):
                if rest[index].get("id") == first_kept:
                    kept = [m for m in rest[index:last] if not is_compaction(m)]
                    break
        rest = [summary_message(record), *kept, *rest[last + 1:]]
    return [*system, *guard_empty_tool_results([m for m in rest if m.get("role") in ("user", "assistant", "tool")])]


def trim_to_view(messages: list[dict]) -> list[dict]:
    """The stored records the model's view is built from: from the latest
    compaction's first kept record (or the compaction itself) on. model_view()
    of the result equals model_view() of messages."""
    for index in range(len(messages) - 1, -1, -1):
        if is_compaction(messages[index]):
            first_kept = messages[index].get("first_kept_id")
            start = index
            if first_kept:
                start = next((i for i in range(index) if messages[i].get("id") == first_kept), index)
            system = [m for m in messages[:start] if m.get("role") == "system"]
            return [*system, *messages[start:]]
    return messages


def needs_compaction(view: list[dict], window: int, overhead: int = 0) -> bool:
    """Whether the next prompt (messages plus tool definitions and other
    overhead) leaves less than RESERVE_TOKENS of the window."""
    reserve = min(RESERVE_TOKENS, window // 4)
    return estimate_total(view) + overhead > window - reserve


def is_context_overflow(error: BaseException) -> bool:
    """A provider rejected the prompt for exceeding the model's context."""
    text = str(error).lower()
    return any(marker in text for marker in (
        "context length", "context_length", "context window", "maximum context",
        "prompt is too long", "too many tokens", "input is too long", "longer than the model",
        "exceeds the context", "reduce the length",
    ))


# --- Compaction --------------------------------------------------------------

def find_cut(messages: list[dict], keep_tokens: int = KEEP_RECENT_TOKENS) -> int:
    """Index of the first message kept verbatim (0 when nothing can be cut).

    About keep_tokens of the latest messages are kept. The cut never separates
    tool results from the call that produced them: it lands on a user message
    when one is near, otherwise on an assistant message.
    """
    total, index = 0, len(messages)
    while index > 0 and total < keep_tokens:
        index -= 1
        total += estimate_tokens(messages[index])
    if index <= 0:
        return 0
    # Prefer the start of a user turn at or before the cut, within the kept budget.
    for back in range(index, 0, -1):
        if messages[back].get("role") == "user":
            if estimate_total(messages[back:]) <= keep_tokens * 2:
                return back
            break
    while index < len(messages) and messages[index].get("role") == "tool":
        index += 1
    return index if index < len(messages) else 0


def _summary_input(messages: list[dict]) -> list[dict]:
    """The conversation as plain user/assistant text for the summarizer."""
    out: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(
                block.get("text", "") if block.get("type") == "text" else f"[{block.get('type')}]"
                for block in content if isinstance(block, dict))
        content = "" if content is None else str(content)
        if role == "tool":
            name = message.get("tool_name") or message.get("name") or "tool"
            if len(content) > _TOOL_RESULT_CHARS:
                content = content[:_TOOL_RESULT_CHARS] + f"\n[... {len(content) - _TOOL_RESULT_CHARS} more characters]"
            out.append({"role": "user", "content": f"[Result of {name}]\n{content}"})
        elif role == "assistant":
            calls = [f"[Called {(c.get('function') or {}).get('name')}({(c.get('function') or {}).get('arguments') or ''})]"
                     for c in message.get("tool_calls") or []]
            text = "\n".join([content, *calls]).strip()
            if text:
                out.append({"role": "assistant", "content": text})
        elif role == "user" and message.get("_compaction"):
            out.append({"role": "user", "content": content})
        elif role == "user":
            out.append({"role": "user", "content": content})
    # Oldest material goes first when the summarizer's input is too long.
    size = sum(len(m["content"]) for m in out)
    while out and size > _SUMMARY_INPUT_CHARS:
        size -= len(out.pop(0)["content"])
    return out


Complete = Callable[[list[dict]], Awaitable[str]]


async def summarize(messages: list[dict], complete: Complete) -> str:
    """Summarize messages (which may start with a previous summary)."""
    updating = bool(messages) and bool(messages[0].get("_compaction"))
    prompt = UPDATE_PROMPT if updating else SUMMARY_PROMPT
    request = [{"role": "system", "content": SUMMARY_SYSTEM}, *_summary_input(messages),
               {"role": "user", "content": prompt}]
    text = (await complete(request) or "").strip()
    if not text:
        raise RuntimeError("The summary came back empty")
    return text


async def compact(view: list[dict], complete: Complete, *,
                  keep_tokens: int = KEEP_RECENT_TOKENS) -> dict | None:
    """A compaction record for a model view, or None when nothing can be cut.

    view is model_view() output (system messages first). The record's
    first_kept_id is the first message kept verbatim; the caller appends the
    record to the conversation.
    """
    rest = [m for m in view if m.get("role") != "system"]
    cut = find_cut(rest, keep_tokens)
    if cut <= 0 or (cut == 1 and rest[0].get("_compaction")):
        return None
    head, kept = rest[:cut], rest[cut:]
    first_kept = kept[0].get("id") if kept else None
    if kept and not first_kept:
        return None  # Stored records always carry ids; a view without them cannot be anchored.
    tokens_before = estimate_total(rest)
    summary = await summarize(head, complete)
    record = {
        "role": COMPACTION_ROLE,
        "id": str(uuid4()),
        "summary": summary,
        "content": SUMMARY_WRAPPER.format(summary=summary),
        "first_kept_id": first_kept,
        "timestamp": time.time(),
        "_metadata": {
            "tokens_before": tokens_before,
            "tokens_after": estimate_tokens(summary_message({"summary": summary})) + estimate_total(kept),
            "summarized_messages": len(head),
        },
    }
    logger.info("[context] compacted {} messages (~{} tokens) keeping {} verbatim",
                len(head), tokens_before, len(kept))
    return record


def model_completer(model: str, *, model_scope=None) -> Complete:
    """A summarizer call on the conversation's own model."""
    async def complete(messages: list[dict]) -> str:
        from .utils.llm import acompletion

        response = await acompletion(
            model=model, messages=messages,
            **({"scope": model_scope} if model_scope is not None else {}))
        if isinstance(response, dict):
            return str(response.get("content") or "")
        choices = getattr(response, "choices", None)
        if choices:
            return str(choices[0].message.content or "")
        return str(response or "")
    return complete


async def compact_history(agent, history: list[dict], model: str, *, force: bool = False,
                          keep_tokens: int = KEEP_RECENT_TOKENS, memory=None) -> dict | None:
    """A compaction record for an Agent's conversation, or None.

    Without force, only when the next prompt to model would not fit its
    window. The Agent's before_compaction hooks see the conversation first
    (e.g. the memory system saves what matters). The caller appends the record.
    """
    view = model_view(history)
    if not force:
        scope = getattr(agent, "model_scope", None)
        if scope is not None and model.startswith(("fleet-model://", "fleet-route://")):
            # A fresh conversation may not have called the model yet.
            await scope.fleet().describe(model)
        window = context_window(model, model_scope=scope)
        overhead = agent.__dict__.get("_prompt_overhead_tokens", {}).get(model)
        if overhead is None:  # not measured yet: the system prompt and tool definitions
            tools = await agent.get_tools_for_llm()
            overhead = (estimate_total([m for m in view if m.get("role") == "system"])
                        + _text_tokens(json.dumps(tools, ensure_ascii=False, default=str)))
        if not needs_compaction([m for m in view if m.get("role") != "system"], window, overhead):
            return None
    if memory is None:
        from .agent import get_current_run_context

        context = get_current_run_context()
        memory = context.memory if context is not None else None
    for hook in agent.hooks.before_compaction:
        try:
            await hook(view, memory)
        except Exception as e:  # a hook never blocks compaction
            logger.warning(f"before_compaction hook failed: {e}")
    if force:
        # Asked for (or the provider overflowed): keep at most a quarter verbatim.
        keep_tokens = min(keep_tokens, max(estimate_total([m for m in view if m.get("role") != "system"]) // 4, 1))
    return await compact(view, model_completer(model, model_scope=getattr(agent, "model_scope", None)),
                         keep_tokens=keep_tokens)


__all__ = [
    "COMPACTION_ROLE", "KEEP_RECENT_TOKENS", "RESERVE_TOKENS", "compact", "compact_history", "context_window",
    "estimate_tokens", "estimate_total", "find_cut", "is_compaction", "is_context_overflow",
    "model_completer", "model_view", "needs_compaction", "summarize", "summary_message", "trim_to_view",
]

