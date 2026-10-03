from __future__ import annotations

import copy
from typing import Any

from pantheon.utils.log import logger

SESSION_STORAGE_KEY = "session_storage"


def _normalize_payload(memory: Any | None) -> dict[str, Any]:
    if memory is None:
        return {}
    payload = memory.extra_data.get(SESSION_STORAGE_KEY)
    return payload if isinstance(payload, dict) else {}


def _save_payload(memory: Any | None, payload: dict[str, Any]) -> None:
    if memory is None:
        return
    memory.set_metadata(SESSION_STORAGE_KEY, payload)


def getSessionStorageState(memory: Any | None) -> dict[str, Any]:
    return copy.deepcopy(_normalize_payload(memory))


def resetSessionFilePointer(memory: Any | None) -> None:
    payload = _normalize_payload(memory)
    payload["sessionFile"] = None
    _save_payload(memory, payload)


def adoptResumedSessionFile(memory: Any | None) -> None:
    if memory is None:
        return
    payload = _normalize_payload(memory)
    payload["sessionFile"] = {
        "primary": getattr(memory, "file_path", None),
        "storage_files": list(getattr(memory, "storage_files", []) or []),
        "adopted": True,
    }
    _save_payload(memory, payload)


def restoreSessionMetadata(meta: dict[str, Any], memory: Any | None = None) -> None:
    if memory is None:
        return
    payload = _normalize_payload(memory)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    for key in (
        "customTitle",
        "tag",
        "agentName",
        "agentColor",
        "agentSetting",
        "mode",
        "worktreeSession",
        "prNumber",
        "prUrl",
        "prRepository",
    ):
        if key in meta and meta[key] is not None:
            metadata[key] = copy.deepcopy(meta[key])
        elif key in meta and meta[key] is None and key in metadata:
            metadata.pop(key, None)

    # Once a chat has been auto-renamed (name_generated=True), memory.name is
    # authoritative — don't let a stale customTitle regress it. Sync the other
    # way: keep customTitle in step with memory.name. Otherwise (fresh chat /
    # REPL-resume with user-customized title), customTitle drives memory.name.
    current_title = metadata.get("customTitle")
    if memory.extra_data.get("name_generated"):
        if current_title != memory.name:
            metadata["customTitle"] = memory.name
    elif current_title:
        memory.name = str(current_title)

    payload["metadata"] = metadata
    _save_payload(memory, payload)


def clearSessionMetadata(memory: Any | None) -> None:
    if memory is None:
        return
    payload = _normalize_payload(memory)
    payload.pop("metadata", None)
    _save_payload(memory, payload)


def saveWorktreeState(
    worktreeSession: dict[str, Any] | None,
    memory: Any | None = None,
) -> None:
    if memory is None:
        return
    payload = _normalize_payload(memory)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["worktreeSession"] = copy.deepcopy(worktreeSession)
    payload["metadata"] = metadata
    _save_payload(memory, payload)
    logger.debug(
        "Saved worktree session for memory {}: {}",
        getattr(memory, "id", "unknown"),
        worktreeSession,
    )
