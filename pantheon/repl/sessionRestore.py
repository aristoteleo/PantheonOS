from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger
from pantheon.utils.token_optimization import restoreFromEntries
from pantheon.repl.sessionStorage import (
    adoptResumedSessionFile,
    restoreSessionMetadata,
    saveWorktreeState,
)

_CURRENT_RESTORED_WORKTREE: dict[str, Any] | None = None


def restoreSessionStateFromLog(
    result: dict[str, Any],
    setAppState,
    memory: Any | None = None,
) -> None:
    del setAppState
    if not result:
        return
    restoreFromEntries(
        result.get("contextCollapseCommits") or [],
        result.get("contextCollapseSnapshot"),
        memory=memory,
    )
    if memory is not None:
        restoreSessionMetadata(result, memory=memory)


def computeStandaloneAgentContext(
    agentName: str | None,
    agentColor: str | None,
) -> dict[str, str] | None:
    if not agentName and not agentColor:
        return None
    result: dict[str, str] = {}
    if agentName:
        result["name"] = agentName
    if agentColor and agentColor != "default":
        result["color"] = agentColor
    return result or None


def restoreAgentFromSession(
    agentSetting: str | None,
    currentAgentDefinition: Any,
    agentDefinitions: Any,
) -> dict[str, Any]:
    if currentAgentDefinition is not None:
        return {"agentDefinition": currentAgentDefinition, "agentType": None}
    if not agentSetting:
        return {"agentDefinition": None, "agentType": None}

    restored = None
    if hasattr(agentDefinitions, "team_agents"):
        restored = next(
            (agent for agent in getattr(agentDefinitions, "team_agents", []) if agent.name == agentSetting),
            None,
        )
    elif hasattr(agentDefinitions, "agents"):
        restored = getattr(agentDefinitions, "agents", {}).get(agentSetting)
    if isinstance(agentDefinitions, dict):
        agents = agentDefinitions.get("agents") or {}
        restored = restored or agents.get(agentSetting)

    if restored is None:
        logger.debug(
            "restoreAgentFromSession: agent '{}' no longer available",
            agentSetting,
        )
        return {"agentDefinition": None, "agentType": None}
    return {"agentDefinition": restored, "agentType": agentSetting}


def restoreReadFileState(messages: list[dict[str, Any]], cwd: str) -> list[str]:
    paths: list[str] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_name = str(message.get("tool_name", ""))
        if tool_name not in {"read_file", "file_manager__read_file", "view_file"}:
            continue
        content = message.get("content")
        file_path = message.get("file_path")
        if isinstance(file_path, str) and file_path not in paths:
            paths.append(file_path)
        elif isinstance(content, str) and content.startswith(cwd) and content not in paths:
            paths.append(content)
    return paths


def restoreWorktreeForResume(worktreeSession: Any) -> None:
    global _CURRENT_RESTORED_WORKTREE
    if not isinstance(worktreeSession, dict):
        return
    worktree_path = worktreeSession.get("worktreePath")
    if not isinstance(worktree_path, str) or not worktree_path:
        return
    if not Path(worktree_path).exists():
        logger.warning("[resume] worktree path no longer exists: {}", worktree_path)
        return

    original_cwd = worktreeSession.get("originalCwd") or os.getcwd()
    current = {
        **worktreeSession,
        "originalCwd": original_cwd,
    }
    try:
        os.chdir(worktree_path)
    except OSError as exc:
        logger.warning("[resume] failed to chdir into restored worktree {}: {}", worktree_path, exc)
        return
    _CURRENT_RESTORED_WORKTREE = current
    logger.info(
        "[resume] restored worktree cwd={} original_cwd={}",
        worktree_path,
        original_cwd,
    )


def exitRestoredWorktree() -> None:
    global _CURRENT_RESTORED_WORKTREE
    if not isinstance(_CURRENT_RESTORED_WORKTREE, dict):
        return
    original_cwd = _CURRENT_RESTORED_WORKTREE.get("originalCwd")
    if isinstance(original_cwd, str) and Path(original_cwd).exists():
        try:
            os.chdir(original_cwd)
        except OSError as exc:
            logger.warning("[resume] failed to exit restored worktree to {}: {}", original_cwd, exc)
    _CURRENT_RESTORED_WORKTREE = None


async def processResumedConversation(
    result: dict[str, Any],
    opts: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    memory = context.get("memory")
    restoreSessionStateFromLog(result, lambda f: f, memory=memory)
    if memory is not None:
        if not opts.get("forkSession"):
            adoptResumedSessionFile(memory)
        restoreWorktreeForResume(result.get("worktreeSession"))
        saveWorktreeState(result.get("worktreeSession"), memory=memory)
    return {
        "messages": result.get("messages", []),
        "contentReplacements": result.get("contentReplacements"),
        "agentName": result.get("agentName"),
        "agentColor": result.get("agentColor"),
        "restoredAgentDef": None,
        "initialState": context.get("initialState", {}),
    }
