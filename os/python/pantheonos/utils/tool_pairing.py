from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

INCOMPLETE_TOOL_RESULT_PLACEHOLDER = (
    "[INTERNAL_ERROR] Session interrupted - tool execution incomplete"
)


@dataclass(frozen=True)
class ToolPairingStats:
    dropped_orphan_tool_messages: int = 0
    dropped_duplicate_tool_calls: int = 0
    dropped_duplicate_tool_messages: int = 0
    inserted_placeholder_tool_messages: int = 0
    dropped_empty_assistant_messages: int = 0

    @property
    def changed(self) -> bool:
        return any(
            (
                self.dropped_orphan_tool_messages,
                self.dropped_duplicate_tool_calls,
                self.dropped_duplicate_tool_messages,
                self.inserted_placeholder_tool_messages,
                self.dropped_empty_assistant_messages,
            )
        )


def _message_has_content(message: dict) -> bool:
    content = message.get("content")
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return bool(content)
    return True


def _build_placeholder_tool_message(tool_call: dict, assistant_message: dict) -> dict:
    placeholder = {
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "tool_name": tool_call.get("function", {}).get("name", "unknown"),
        "content": INCOMPLETE_TOOL_RESULT_PLACEHOLDER,
        "id": str(uuid4()),
        "timestamp": time.time(),
        "_recovered": True,
    }
    execution_context_id = assistant_message.get("execution_context_id")
    agent_name = assistant_message.get("agent_name")
    if execution_context_id:
        placeholder["execution_context_id"] = execution_context_id
    if agent_name:
        placeholder["agent_name"] = agent_name
    return placeholder


def ensure_tool_result_pairing(messages: list[dict], *, strict: bool = False) -> list[dict]:
    repaired, _ = ensure_tool_result_pairing_with_stats(messages, strict=strict)
    return repaired


def ensure_tool_result_pairing_with_stats(
    messages: list[dict], *, strict: bool = False
) -> tuple[list[dict], ToolPairingStats]:
    """Repair assistant.tool_calls and tool messages into a canonical sequence.

    Rules:
    - Drop orphan tool messages that are not directly attached to a preceding
      assistant tool-call message.
    - Deduplicate repeated tool_call ids, keeping the first assistant call.
    - Deduplicate repeated tool result messages, keeping the first result.
    - Insert synthetic placeholder tool results for missing responses so the
      history remains valid for providers that require strict pairing.
    """
    if not messages:
        return [], ToolPairingStats()

    result: list[dict] = []
    seen_tool_call_ids: set[str] = set()

    dropped_orphan_tool_messages = 0
    dropped_duplicate_tool_calls = 0
    dropped_duplicate_tool_messages = 0
    inserted_placeholder_tool_messages = 0
    dropped_empty_assistant_messages = 0

    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")

        if role == "assistant" and message.get("tool_calls"):
            unique_tool_calls: list[dict] = []
            seen_in_message: set[str] = set()
            for tool_call in message.get("tool_calls") or []:
                tool_call_id = tool_call.get("id")
                if not tool_call_id:
                    dropped_duplicate_tool_calls += 1
                    continue
                if tool_call_id in seen_in_message or tool_call_id in seen_tool_call_ids:
                    dropped_duplicate_tool_calls += 1
                    continue
                seen_in_message.add(tool_call_id)
                seen_tool_call_ids.add(tool_call_id)
                unique_tool_calls.append(tool_call)

            assistant_out = dict(message)
            if unique_tool_calls:
                assistant_out["tool_calls"] = unique_tool_calls
            else:
                assistant_out.pop("tool_calls", None)

            if assistant_out.get("tool_calls") or _message_has_content(assistant_out):
                result.append(assistant_out)
            else:
                dropped_empty_assistant_messages += 1

            pending_by_id = {tool_call["id"]: tool_call for tool_call in unique_tool_calls}
            emitted_tool_ids: set[str] = set()
            index += 1

            while index < len(messages) and messages[index].get("role") == "tool":
                tool_message = messages[index]
                tool_call_id = tool_message.get("tool_call_id")
                if not tool_call_id or tool_call_id not in pending_by_id:
                    dropped_orphan_tool_messages += 1
                    index += 1
                    continue
                if tool_call_id in emitted_tool_ids:
                    dropped_duplicate_tool_messages += 1
                    index += 1
                    continue

                result.append(dict(tool_message))
                emitted_tool_ids.add(tool_call_id)
                index += 1

            missing_tool_ids = [
                tool_call["id"]
                for tool_call in unique_tool_calls
                if tool_call["id"] not in emitted_tool_ids
            ]
            if missing_tool_ids and strict:
                raise ValueError(
                    "Missing tool response(s) for tool_call_id(s): "
                    + ", ".join(missing_tool_ids)
                )
            for tool_call_id in missing_tool_ids:
                result.append(
                    _build_placeholder_tool_message(
                        pending_by_id[tool_call_id], assistant_out
                    )
                )
                inserted_placeholder_tool_messages += 1
            continue

        if role == "tool":
            dropped_orphan_tool_messages += 1
            index += 1
            continue

        result.append(message)
        index += 1

    stats = ToolPairingStats(
        dropped_orphan_tool_messages=dropped_orphan_tool_messages,
        dropped_duplicate_tool_calls=dropped_duplicate_tool_calls,
        dropped_duplicate_tool_messages=dropped_duplicate_tool_messages,
        inserted_placeholder_tool_messages=inserted_placeholder_tool_messages,
        dropped_empty_assistant_messages=dropped_empty_assistant_messages,
    )
    return result, stats
