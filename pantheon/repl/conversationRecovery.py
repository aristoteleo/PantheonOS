from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pantheon.repl.sessionStorage import getSessionStorageState
from pantheon.utils.log import logger
from pantheon.utils.token_optimization import (
    load_context_collapse_entries,
    load_content_replacement_records,
)
from pantheon.utils.tool_pairing import ensure_tool_result_pairing_with_stats

NO_RESPONSE_REQUESTED = "[NO_RESPONSE_REQUESTED]"


@dataclass(frozen=True)
class TurnInterruptionState:
    kind: str
    message: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeserializeResult:
    messages: list[dict[str, Any]]
    turnInterruptionState: TurnInterruptionState


def _message_has_content(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if content is None:
        return False
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return bool(content)
    return True


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    return message.get("role") == "tool"


def _filterWhitespaceOnlyAssistantMessages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            filtered.append(message)
            continue
        if message.get("tool_calls"):
            filtered.append(message)
            continue
        if _message_has_content(message):
            filtered.append(message)
    return filtered


def _detectTurnInterruption(
    messages: list[dict[str, Any]],
) -> TurnInterruptionState | dict[str, str]:
    if not messages:
        return TurnInterruptionState(kind="none")

    last_index = -1
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") != "system":
            last_index = index
            break
    if last_index < 0:
        return TurnInterruptionState(kind="none")

    last_message = messages[last_index]
    role = last_message.get("role")

    if role == "assistant":
        return TurnInterruptionState(kind="none")
    if role == "tool":
        return {"kind": "interrupted_turn"}
    if role == "user":
        if last_message.get("isMeta") or last_message.get("isCompactSummary"):
            return TurnInterruptionState(kind="none")
        return TurnInterruptionState(kind="interrupted_prompt", message=last_message)
    return TurnInterruptionState(kind="none")


def deserializeMessages(serializedMessages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return deserializeMessagesWithInterruptDetection(serializedMessages).messages


def deserializeMessagesWithInterruptDetection(
    serializedMessages: list[dict[str, Any]],
) -> DeserializeResult:
    try:
        migrated = [copy.deepcopy(message) for message in serializedMessages]
        repaired, _stats = ensure_tool_result_pairing_with_stats(migrated)
        filtered = _filterWhitespaceOnlyAssistantMessages(repaired)

        internal_state = _detectTurnInterruption(filtered)
        if isinstance(internal_state, dict) and internal_state.get("kind") == "interrupted_turn":
            continuation_message = {
                "role": "user",
                "content": "Continue from where you left off.",
                "isMeta": True,
            }
            filtered.append(continuation_message)
            turn_state = TurnInterruptionState(
                kind="interrupted_prompt",
                message=continuation_message,
            )
        else:
            turn_state = internal_state

        last_relevant_index = -1
        for index in range(len(filtered) - 1, -1, -1):
            if filtered[index].get("role") != "system":
                last_relevant_index = index
                break
        if (
            last_relevant_index >= 0
            and filtered[last_relevant_index].get("role") == "user"
        ):
            filtered.insert(
                last_relevant_index + 1,
                {"role": "assistant", "content": NO_RESPONSE_REQUESTED, "isMeta": True},
            )

        return DeserializeResult(messages=filtered, turnInterruptionState=turn_state)
    except Exception as exc:
        logger.error("deserializeMessagesWithInterruptDetection failed: {}", exc)
        raise


def loadConversationForResume(
    source: Any,
    sourceJsonlFile: str | None = None,
    execution_context_id: Any = None,
) -> dict[str, Any] | None:
    del sourceJsonlFile

    if source is None:
        return None

    memory = source if hasattr(source, "get_messages") and hasattr(source, "extra_data") else None
    if memory is not None:
        raw_messages = memory.get_messages(
            execution_context_id=execution_context_id,
            for_llm=False,
        )
        messages = memory.get_messages(
            execution_context_id=execution_context_id,
            for_llm=False,
        )
        contextCollapseCommits, contextCollapseSnapshot = load_context_collapse_entries(
            memory
        )
        contentReplacements = load_content_replacement_records(memory)
        fullPath = getattr(memory, "file_path", None)
        session_storage = getSessionStorageState(memory)
        metadata = session_storage.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
    elif isinstance(source, list):
        raw_messages = source
        messages = source
        contextCollapseCommits = []
        contextCollapseSnapshot = None
        contentReplacements = []
        fullPath = None
        metadata = {}
    else:
        raise TypeError(f"Unsupported resume source: {type(source)!r}")

    deserialized = deserializeMessagesWithInterruptDetection(messages)
    project = memory.extra_data.get("project", {}) if memory is not None else {}
    if not isinstance(project, dict):
        project = {}
    worktree_session = metadata.get("worktreeSession")
    if not isinstance(worktree_session, dict):
        workspace_path = project.get("workspace_path")
        workspace_mode = project.get("workspace_mode")
        if workspace_mode == "isolated" and isinstance(workspace_path, str) and workspace_path:
            worktree_session = {
                "originalCwd": project.get("original_cwd"),
                "worktreePath": workspace_path,
                "worktreeName": Path(workspace_path).name,
                "sessionId": getattr(memory, "id", None),
            }
        else:
            worktree_session = None

    agent_setting = metadata.get("agentSetting")
    if not agent_setting and memory is not None:
        agent_setting = memory.extra_data.get("active_agent")

    custom_title = metadata.get("customTitle")
    if not custom_title and memory is not None:
        custom_title = getattr(memory, "name", None)

    logger.info(
        "[resume] source={} raw_messages={} resumed_messages={} interruption={} commits={} replacements={} full_path={}",
        getattr(memory, "id", "inline"),
        len(raw_messages),
        len(deserialized.messages),
        deserialized.turnInterruptionState.kind,
        len(contextCollapseCommits),
        len(contentReplacements),
        fullPath,
    )
    return {
        "messages": deserialized.messages,
        "turnInterruptionState": deserialized.turnInterruptionState,
        "contentReplacements": contentReplacements,
        "contextCollapseCommits": contextCollapseCommits,
        "contextCollapseSnapshot": contextCollapseSnapshot,
        "fullPath": fullPath,
        "agentName": metadata.get("agentName") or agent_setting,
        "agentColor": metadata.get("agentColor"),
        "agentSetting": agent_setting,
        "customTitle": custom_title,
        "tag": metadata.get("tag"),
        "mode": metadata.get("mode"),
        "worktreeSession": worktree_session,
        "prNumber": metadata.get("prNumber"),
        "prUrl": metadata.get("prUrl"),
        "prRepository": metadata.get("prRepository"),
    }
