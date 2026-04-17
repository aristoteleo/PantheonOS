from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger
from pantheon.utils.tool_pairing import ensure_tool_result_pairing
from pantheon.utils.truncate import (
    PERSISTED_OUTPUT_TAG,
    PERSISTED_OUTPUT_CLOSING_TAG,
    PREVIEW_SIZE_BYTES,
    _format_file_size,
)

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"
EMPTY_TOOL_RESULT_PLACEHOLDER = "[No output]"
BYTES_PER_TOKEN = 4
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200_000
# CC default: DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000.  Used as fallback when
# a tool has no explicit entry in PER_TOOL_RESULT_SIZE_CHARS.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
TIME_BASED_MC_GAP_THRESHOLD_MINUTES = 60
TIME_BASED_MC_KEEP_RECENT = 5
STATE_KEY = "token_optimization"
COMPACTABLE_TOOL_SUFFIXES = {
    "read_file",
    "view_file",
    "write_file",
    "update_file",
    "apply_patch",
    "glob",
    "grep",
    "grep_search",
    "find_by_name",
    "shell",
    "bash",
    "web_fetch",
    "web_search",
    "web_crawl",
}

# Per-tool result size limits (chars).  Tools that produce large but
# structured output (web pages, full file reads) get a tighter cap than
# the default; tools whose output is always small are left out so the
# DEFAULT_MAX_RESULT_SIZE_CHARS (50K) applies.
# Mirrors Claude Code's per-tool maxResultSizeChars declarations.
PER_TOOL_RESULT_SIZE_CHARS: dict[str, int | float] = {
    "read_file":   40_000,
    "view_file":   40_000,
    "web_fetch":   30_000,
    "web_crawl":   30_000,
    "web_search":  20_000,
    "shell":       50_000,
    "bash":        50_000,
    "grep":        20_000,
    "grep_search": 20_000,
    "glob":        10_000,
    "find_by_name": 10_000,
}

# Tools that opt out of persistence entirely (CC: maxResultSizeChars = Infinity).
# Their results are never externalized regardless of size — model needs to see
# the full output for correct reasoning.
PERSISTENCE_OPT_OUT_TOOLS: frozenset[str] = frozenset()

# Tools whose results are considered collapsible in contextCollapse.
# Matches CC's collapseReadSearch.ts getToolSearchOrReadInfo().
COLLAPSIBLE_SEARCH_TOOLS = frozenset({
    "grep", "grep_search", "glob", "find_by_name",
    "web_search",
})
COLLAPSIBLE_READ_TOOLS = frozenset({
    "read_file", "view_file", "web_fetch", "web_crawl",
})
COLLAPSIBLE_LIST_TOOLS = frozenset({
    "glob", "find_by_name",
})
ALL_COLLAPSIBLE_TOOLS = COLLAPSIBLE_SEARCH_TOOLS | COLLAPSIBLE_READ_TOOLS | frozenset({
    "shell", "bash",  # absorbed silently like CC's REPL
})


@dataclass
class ContentReplacementState:
    seen_ids: set[str]
    replacements: dict[str, str]


@dataclass
class ToolMessageCandidate:
    tool_use_id: str
    content: str
    size: int


@dataclass(frozen=True)
class TimeBasedMicrocompactConfig:
    enabled: bool
    gap_threshold_minutes: int
    keep_recent: int


@dataclass(frozen=True)
class CacheSafeRuntimeParams:
    model: str
    model_params_raw: dict[str, Any]
    model_params_normalized: Any
    response_format_raw: Any | None
    response_format_normalized: Any | None


@dataclass(frozen=True)
class ContextCollapseDecision:
    total_tokens: int
    context_window: int
    usage_ratio: float
    should_commit: bool
    at_blocking_limit: bool


@dataclass(frozen=True)
class ContextCollapseHealth:
    total_spawns: int = 0
    total_errors: int = 0
    total_empty_spawns: int = 0
    empty_spawn_warning_emitted: bool = False
    last_error: str | None = None


@dataclass(frozen=True)
class ContextCollapseStats:
    collapsed_spans: int
    collapsed_messages: int
    staged_spans: int
    health: ContextCollapseHealth


@dataclass(frozen=True)
class ContextCollapseCommit:
    collapse_id: int
    archived_pattern: tuple[str, ...]
    summary_message: dict[str, Any]
    summary_text: str
    archived_count: int


@dataclass(frozen=True)
class ContextCollapseApplyResult:
    messages: list[dict]
    committed: int
    decision: ContextCollapseDecision


@dataclass(frozen=True)
class ContextCollapseRecoverResult:
    messages: list[dict]
    committed: int
    at_blocking_limit: bool


def _load_state_payload(memory: Any | None) -> dict[str, Any]:
    if memory is None:
        return {}
    return _normalize_state_payload(memory.extra_data.get(STATE_KEY))


def _save_state_payload(memory: Any | None, payload: dict[str, Any]) -> None:
    if memory is None:
        return
    normalized = _normalize_state_payload(payload)
    memory.set_metadata(STATE_KEY, normalized)


def create_content_replacement_state() -> ContentReplacementState:
    return ContentReplacementState(seen_ids=set(), replacements={})


def get_time_based_microcompact_config() -> TimeBasedMicrocompactConfig:
    return TimeBasedMicrocompactConfig(
        enabled=True,
        gap_threshold_minutes=TIME_BASED_MC_GAP_THRESHOLD_MINUTES,
        keep_recent=TIME_BASED_MC_KEEP_RECENT,
    )


CONTEXT_COLLAPSE_COMMIT_THRESHOLD = 0.90
CONTEXT_COLLAPSE_BLOCKING_THRESHOLD = 0.95
AUTOCOMPACT_TRIGGER_BUFFER_TOKENS = 13_000
_COLLAPSE_SKIP_QUERY_SOURCES = frozenset({"compact", "session_memory", "agent_summary"})
_CONTEXT_COLLAPSE_COMMITS_KEY = "contextCollapseCommits"
_CONTEXT_COLLAPSE_SNAPSHOT_KEY = "contextCollapseSnapshot"


def normalize_cache_safe_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {
            str(key): normalize_cache_safe_value(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [normalize_cache_safe_value(item) for item in value]
    if isinstance(value, set):
        return sorted(normalize_cache_safe_value(item) for item in value)
    if hasattr(value, "model_json_schema"):
        try:
            return normalize_cache_safe_value(value.model_json_schema())
        except TypeError:
            pass
    if hasattr(value, "__qualname__") and hasattr(value, "__module__"):
        return f"{value.__module__}.{value.__qualname__}"
    return value


def build_cache_safe_runtime_params(
    model: str,
    model_params: dict[str, Any] | None,
    response_format: Any | None,
) -> CacheSafeRuntimeParams:
    raw_model_params = dict(model_params or {})
    return CacheSafeRuntimeParams(
        model=model,
        model_params_raw=raw_model_params,
        model_params_normalized=normalize_cache_safe_value(raw_model_params),
        response_format_raw=response_format,
        response_format_normalized=normalize_cache_safe_value(response_format),
    )


def _normalize_state_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return data


def _serialize_context_collapse_commit(
    commit: ContextCollapseCommit,
) -> dict[str, Any]:
    return {
        "collapseId": commit.collapse_id,
        "archivedPattern": list(commit.archived_pattern),
        "summaryMessage": copy.deepcopy(commit.summary_message),
        "summaryText": commit.summary_text,
        "archivedCount": commit.archived_count,
    }


def _deserialize_context_collapse_commit(data: Any) -> ContextCollapseCommit | None:
    if isinstance(data, ContextCollapseCommit):
        return data
    if not isinstance(data, dict):
        return None
    archived_pattern = data.get("archivedPattern")
    summary_message = data.get("summaryMessage")
    if not isinstance(archived_pattern, list) or not isinstance(summary_message, dict):
        return None
    try:
        return ContextCollapseCommit(
            collapse_id=int(data.get("collapseId", 0)),
            archived_pattern=tuple(str(item) for item in archived_pattern),
            summary_message=copy.deepcopy(summary_message),
            summary_text=str(data.get("summaryText", "")),
            archived_count=int(data.get("archivedCount", 0)),
        )
    except Exception:
        return None


def load_context_collapse_entries(
    memory: Any | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    payload = _load_state_payload(memory)
    commits = payload.get(_CONTEXT_COLLAPSE_COMMITS_KEY, [])
    snapshot = payload.get(_CONTEXT_COLLAPSE_SNAPSHOT_KEY)
    normalized_commits = [
        commit
        for commit in commits
        if _deserialize_context_collapse_commit(commit) is not None
    ]
    return normalized_commits, snapshot if isinstance(snapshot, dict) else None


def save_context_collapse_entries(
    memory: Any | None,
    commits: list[dict[str, Any]],
    snapshot: dict[str, Any] | None = None,
) -> None:
    if memory is None:
        return
    payload = _load_state_payload(memory)
    payload[_CONTEXT_COLLAPSE_COMMITS_KEY] = [copy.deepcopy(commit) for commit in commits]
    if snapshot is None:
        payload.pop(_CONTEXT_COLLAPSE_SNAPSHOT_KEY, None)
    else:
        payload[_CONTEXT_COLLAPSE_SNAPSHOT_KEY] = copy.deepcopy(snapshot)
    _save_state_payload(memory, payload)


def load_content_replacement_state(memory: Any | None) -> ContentReplacementState:
    if memory is None:
        return create_content_replacement_state()
    payload = _load_state_payload(memory)
    seen_ids = {
        str(item)
        for item in payload.get("seen_ids", [])
        if isinstance(item, str) and item
    }
    replacements = {
        str(tool_use_id): str(replacement)
        for tool_use_id, replacement in payload.get("replacements", {}).items()
        if isinstance(tool_use_id, str) and tool_use_id
    }
    return ContentReplacementState(seen_ids=seen_ids, replacements=replacements)


def reconstruct_content_replacement_state(
    messages: list[dict],
    memory: Any | None = None,
) -> ContentReplacementState:
    """Reconstruct replacement state from message history on session resume.

    Mirrors CC's ``reconstructContentReplacementState()``: scans messages for
    already-externalized tool results and rebuilds the seen_ids/replacements
    maps so the budget logic is consistent with prior decisions.
    """
    state = load_content_replacement_state(memory)

    # Walk all tool messages; if content is already externalized (persisted-output
    # or cleared), record the decision so we don't re-evaluate.
    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_use_id = message.get("tool_call_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if content.startswith(PERSISTED_OUTPUT_TAG):
            state.seen_ids.add(tool_use_id)
            state.replacements[tool_use_id] = content
        elif content == TIME_BASED_MC_CLEARED_MESSAGE:
            state.seen_ids.add(tool_use_id)
        elif content == EMPTY_TOOL_RESULT_PLACEHOLDER:
            state.seen_ids.add(tool_use_id)

    if memory is not None:
        save_content_replacement_state(memory, state)
    return state


def save_content_replacement_state(
    memory: Any | None,
    state: ContentReplacementState,
) -> None:
    if memory is None:
        return
    payload = _load_state_payload(memory)
    payload.update({
        "seen_ids": sorted(state.seen_ids),
        "replacements": dict(sorted(state.replacements.items())),
    })
    _save_state_payload(memory, payload)


def load_content_replacement_records(memory: Any | None) -> list[dict[str, str]]:
    state = load_content_replacement_state(memory)
    return [
        {"tool_use_id": tool_use_id, "replacement": replacement}
        for tool_use_id, replacement in sorted(state.replacements.items())
    ]


# _format_file_size is imported from pantheon.utils.truncate


def generate_preview(content: str, max_bytes: int) -> tuple[str, bool]:
    if len(content) <= max_bytes:
        return content, False
    truncated = content[:max_bytes]
    last_newline = truncated.rfind("\n")
    cut_point = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut_point], True


def _get_tool_results_dir(memory: Any | None, base_dir: Path | None) -> Path:
    if base_dir is not None:
        root = Path(base_dir)
    else:
        from pantheon.settings import get_settings

        root = get_settings().tmp_dir / "tool-results"
    memory_id = getattr(memory, "id", None) or "default"
    return root / str(memory_id)


def _detect_json_content(content: str) -> bool:
    """Return True if *content* looks like a JSON array or object."""
    stripped = content.lstrip()
    if not stripped or stripped[0] not in ("[", "{"):
        return False
    try:
        json.loads(content)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def _get_tool_result_path(
    tool_use_id: str,
    memory: Any | None,
    base_dir: Path | None,
    *,
    is_json: bool = False,
) -> Path:
    ext = ".json" if is_json else ".txt"
    return _get_tool_results_dir(memory, base_dir) / f"{tool_use_id}{ext}"


def persist_tool_result(
    content: str,
    tool_use_id: str,
    memory: Any | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    directory = _get_tool_results_dir(memory, base_dir)
    directory.mkdir(parents=True, exist_ok=True)
    is_json = _detect_json_content(content)
    filepath = _get_tool_result_path(tool_use_id, memory, base_dir, is_json=is_json)
    # Atomic-ish write: skip if file already exists (mirrors CC's 'wx' flag)
    if not filepath.exists():
        filepath.write_text(content, encoding="utf-8")
    preview, has_more = generate_preview(content, PREVIEW_SIZE_BYTES)
    return {
        "filepath": str(filepath),
        "original_size": len(content),
        "preview": preview,
        "has_more": has_more,
    }


def build_large_tool_result_message(result: dict[str, Any]) -> str:
    message = f"{PERSISTED_OUTPUT_TAG}\n"
    message += (
        f"Output too large ({_format_file_size(result['original_size'])}). "
        f"Full output saved to: {result['filepath']}\n\n"
    )
    message += f"Preview (first {_format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    message += result["preview"]
    message += "\n...\n" if result["has_more"] else "\n"
    message += PERSISTED_OUTPUT_CLOSING_TAG
    return message


def _is_already_externalized(content: str) -> bool:
    return (
        content.startswith(PERSISTED_OUTPUT_TAG)
        or content == TIME_BASED_MC_CLEARED_MESSAGE
        or "Full content saved to:" in content
    )


def build_tool_name_map(messages: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = tool_call.get("id")
            function = tool_call.get("function") or {}
            tool_name = function.get("name")
            if tool_call_id and tool_name:
                result[str(tool_call_id)] = str(tool_name)
    return result


def get_tool_name_for_message(message: dict, tool_name_map: dict[str, str]) -> str | None:
    tool_use_id = message.get("tool_call_id")
    if isinstance(tool_use_id, str) and tool_use_id:
        mapped = tool_name_map.get(tool_use_id)
        if mapped:
            return mapped

    tool_name = message.get("tool_name")
    if isinstance(tool_name, str) and tool_name:
        return tool_name
    return None


def normalize_tool_name(tool_name: str | None) -> str:
    if not tool_name:
        return ""
    if "__" in tool_name:
        return tool_name.rsplit("__", 1)[-1]
    return tool_name


def is_compactable_tool_name(tool_name: str | None) -> bool:
    return normalize_tool_name(tool_name) in COMPACTABLE_TOOL_SUFFIXES


def collect_candidates_from_message(message: dict) -> list[ToolMessageCandidate]:
    if message.get("role") != "tool":
        return []
    tool_use_id = message.get("tool_call_id")
    content = message.get("content")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return []
    if not isinstance(content, str) or not content:
        return []
    if _is_already_externalized(content):
        return []
    return [
        ToolMessageCandidate(
            tool_use_id=tool_use_id,
            content=content,
            size=len(content),
        )
    ]


def guard_empty_tool_results(messages: list[dict]) -> list[dict]:
    """Inject a placeholder for empty tool results.

    Mirrors CC's emptiness guard: some models emit a stop-sequence when
    they see an empty tool result.  Injecting ``[No output]`` prevents that.
    """
    result: list[dict] = []
    for message in messages:
        if message.get("role") != "tool":
            result.append(message)
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            result.append(message)
            continue
        new_msg = dict(message)
        new_msg["content"] = EMPTY_TOOL_RESULT_PLACEHOLDER
        result.append(new_msg)
    return result


def collect_candidates_by_message(messages: list[dict]) -> list[list[ToolMessageCandidate]]:
    groups: list[list[ToolMessageCandidate]] = []
    current: list[ToolMessageCandidate] = []
    seen_assistant_ids: set[str] = set()

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
        current = []

    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "tool":
            current.extend(collect_candidates_from_message(message))
            continue
        if role == "assistant":
            assistant_id = str(message.get("id") or f"assistant-{index}")
            if assistant_id not in seen_assistant_ids:
                flush()
                seen_assistant_ids.add(assistant_id)
            continue
        flush()
    flush()
    return groups


def partition_by_prior_decision(
    candidates: list[ToolMessageCandidate],
    state: ContentReplacementState,
) -> tuple[list[tuple[ToolMessageCandidate, str]], list[ToolMessageCandidate], list[ToolMessageCandidate]]:
    must_reapply: list[tuple[ToolMessageCandidate, str]] = []
    frozen: list[ToolMessageCandidate] = []
    fresh: list[ToolMessageCandidate] = []
    for candidate in candidates:
        replacement = state.replacements.get(candidate.tool_use_id)
        if replacement is not None:
            must_reapply.append((candidate, replacement))
        elif candidate.tool_use_id in state.seen_ids:
            frozen.append(candidate)
        else:
            fresh.append(candidate)
    return must_reapply, frozen, fresh


def select_fresh_to_replace(
    fresh: list[ToolMessageCandidate],
    frozen_size: int,
    limit: int,
) -> list[ToolMessageCandidate]:
    sorted_candidates = sorted(fresh, key=lambda item: item.size, reverse=True)
    selected: list[ToolMessageCandidate] = []
    remaining = frozen_size + sum(item.size for item in fresh)
    for candidate in sorted_candidates:
        if remaining <= limit:
            break
        selected.append(candidate)
        remaining -= candidate.size
    return selected


def replace_tool_message_contents(
    messages: list[dict],
    replacement_map: dict[str, str],
) -> list[dict]:
    result: list[dict] = []
    for message in messages:
        if message.get("role") != "tool":
            result.append(message)
            continue
        tool_use_id = message.get("tool_call_id")
        replacement = replacement_map.get(str(tool_use_id))
        if replacement is None:
            result.append(message)
            continue
        new_message = dict(message)
        new_message["content"] = replacement
        result.append(new_message)
    return result


def get_per_tool_limit(tool_name: str | None, global_limit: int) -> int | float:
    """Return the effective size limit for a single tool result.

    Mirrors Claude Code's ``getPersistenceThreshold()``:
    1. Infinity opt-out (never externalize)
    2. Per-tool override from PER_TOOL_RESULT_SIZE_CHARS
    3. Fallback: ``min(DEFAULT_MAX_RESULT_SIZE_CHARS, global_limit)``
    """
    normalized = normalize_tool_name(tool_name)
    if normalized in PERSISTENCE_OPT_OUT_TOOLS:
        return float("inf")
    explicit = PER_TOOL_RESULT_SIZE_CHARS.get(normalized)
    if explicit is not None:
        return explicit
    return min(DEFAULT_MAX_RESULT_SIZE_CHARS, global_limit)


# querySource values that should NOT write to disk (mirrors CC's logic
# in toolResultStorage.ts — agent_summary, fork calls, etc. only see
# the already-persisted preview, they never create new disk entries).
_PERSISTENCE_SKIP_QUERY_SOURCES = frozenset({
    "agent_summary",
    "memory_agent",
    "title_agent",
})


def _should_persist_to_disk(query_source: str | None) -> bool:
    """Return True if this query source is allowed to write new tool results
    to disk.  Mirrors CC: only ``agent:*`` and ``repl_main_thread*`` persist;
    summaries and fork helpers do not."""
    if query_source is None:
        return True  # conservative default
    return query_source not in _PERSISTENCE_SKIP_QUERY_SOURCES


def apply_tool_result_budget(
    messages: list[dict],
    memory: Any | None = None,
    base_dir: Path | None = None,
    per_message_limit: int = MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    skip_tool_names: set[str] | None = None,
    query_source: str | None = None,
) -> list[dict]:
    """Safety-net budget enforcement for tool results.

    Per-tool externalization is now handled at tool execution time by
    ``process_tool_result`` (using per-tool thresholds from
    ``get_per_tool_limit``).  This function serves as a second pass:

    1. Replays prior externalization decisions (session resume).
    2. Guards empty tool results.
    3. Enforces the **per-message aggregate** limit — if a single turn
       contains many tool results whose combined size exceeds
       *per_message_limit*, the largest fresh results are externalized.
    """
    # Guard empty tool results first (CC emptiness guard)
    messages = guard_empty_tool_results(messages)
    state = load_content_replacement_state(memory)

    # querySource filtering: fork helpers only re-apply existing decisions,
    # they never create new disk entries.
    persist_allowed = _should_persist_to_disk(query_source)
    candidates_by_message = collect_candidates_by_message(messages)
    skip_tool_names = skip_tool_names or set()
    tool_name_map = build_tool_name_map(messages)
    replacement_map: dict[str, str] = {}

    for candidates in candidates_by_message:
        must_reapply, frozen, fresh = partition_by_prior_decision(candidates, state)

        for candidate, replacement in must_reapply:
            replacement_map[candidate.tool_use_id] = replacement

        if not fresh:
            for candidate in candidates:
                state.seen_ids.add(candidate.tool_use_id)
            continue

        skipped = [
            candidate
            for candidate in fresh
            if tool_name_map.get(candidate.tool_use_id) in skip_tool_names
        ]
        for candidate in skipped:
            state.seen_ids.add(candidate.tool_use_id)

        eligible = [candidate for candidate in fresh if candidate not in skipped]

        # Separate opt-out tools (Infinity limit) — never externalize them
        checkable: list[ToolMessageCandidate] = []
        for candidate in eligible:
            tool_name = tool_name_map.get(candidate.tool_use_id)
            normalized = normalize_tool_name(tool_name)
            if normalized in PERSISTENCE_OPT_OUT_TOOLS:
                state.seen_ids.add(candidate.tool_use_id)
            else:
                checkable.append(candidate)

        # Per-message aggregate check: externalize largest results when the
        # combined size of all results in this turn exceeds per_message_limit.
        # Individual per-tool thresholds are already enforced at tool execution
        # time, so this only catches the aggregate-too-large case.
        frozen_size = sum(candidate.size for candidate in frozen)
        fresh_size = sum(candidate.size for candidate in checkable)
        aggregate_selected = (
            select_fresh_to_replace(checkable, frozen_size, per_message_limit)
            if frozen_size + fresh_size > per_message_limit
            else []
        )

        selected_ids = {candidate.tool_use_id for candidate in aggregate_selected}
        for candidate in candidates:
            if candidate.tool_use_id not in selected_ids:
                state.seen_ids.add(candidate.tool_use_id)

        for candidate in aggregate_selected:
            if persist_allowed:
                persisted = persist_tool_result(
                    candidate.content,
                    candidate.tool_use_id,
                    memory=memory,
                    base_dir=base_dir,
                )
                replacement = build_large_tool_result_message(persisted)
                state.seen_ids.add(candidate.tool_use_id)
                state.replacements[candidate.tool_use_id] = replacement
                replacement_map[candidate.tool_use_id] = replacement
            else:
                # Fork / summary agents: mark as seen but don't persist
                state.seen_ids.add(candidate.tool_use_id)

    save_content_replacement_state(memory, state)
    if not replacement_map:
        return messages
    return replace_tool_message_contents(messages, replacement_map)


def _parse_timestamp(timestamp: Any) -> float | None:
    if isinstance(timestamp, (int, float)):
        return float(timestamp)
    if isinstance(timestamp, str):
        try:
            from datetime import datetime

            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _collect_compactable_tool_message_ids(messages: list[dict]) -> list[str]:
    tool_name_map = build_tool_name_map(messages)
    ids: list[str] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        tool_use_id = message.get("tool_call_id")
        content = message.get("content")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        if not isinstance(content, str) or not content:
            continue
        if _is_already_externalized(content):
            continue
        tool_name = get_tool_name_for_message(message, tool_name_map)
        if not is_compactable_tool_name(tool_name):
            continue
        ids.append(tool_use_id)
    return ids


def evaluate_time_based_trigger(
    messages: list[dict],
    *,
    is_main_thread: bool,
    config: TimeBasedMicrocompactConfig | None = None,
) -> tuple[float, TimeBasedMicrocompactConfig] | None:
    resolved_config = config or get_time_based_microcompact_config()
    if not resolved_config.enabled or not is_main_thread:
        return None

    last_assistant_timestamp: float | None = None
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        parsed = _parse_timestamp(message.get("timestamp"))
        if parsed is not None:
            last_assistant_timestamp = parsed
            break

    if last_assistant_timestamp is None:
        return None

    import time

    gap_minutes = (time.time() - last_assistant_timestamp) / 60.0
    if gap_minutes < resolved_config.gap_threshold_minutes:
        return None
    return gap_minutes, resolved_config


def microcompact_messages(
    messages: list[dict],
    *,
    is_main_thread: bool = True,
    config: TimeBasedMicrocompactConfig | None = None,
) -> list[dict]:
    trigger = evaluate_time_based_trigger(
        messages,
        is_main_thread=is_main_thread,
        config=config,
    )
    if trigger is None:
        return messages
    gap_minutes, resolved_config = trigger

    compactable_ids = _collect_compactable_tool_message_ids(messages)
    if not compactable_ids:
        return messages

    keep_count = max(1, resolved_config.keep_recent)
    keep_set = set(compactable_ids[-keep_count:])
    clear_set = {tool_use_id for tool_use_id in compactable_ids if tool_use_id not in keep_set}
    if not clear_set:
        return messages

    changed = False
    tokens_saved = 0
    result: list[dict] = []
    for message in messages:
        if message.get("role") != "tool":
            result.append(message)
            continue
        tool_use_id = message.get("tool_call_id")
        if tool_use_id not in clear_set:
            result.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, str) or _is_already_externalized(content):
            result.append(message)
            continue
        tokens_saved += max(1, len(content) // BYTES_PER_TOKEN)
        new_message = dict(message)
        new_message["content"] = TIME_BASED_MC_CLEARED_MESSAGE
        result.append(new_message)
        changed = True

    if changed:
        logger.info(
            "[token optimization] time-based microcompact cleared {} tool messages after {:.1f} minute gap (~{} tokens saved, kept last {})",
            len(clear_set),
            gap_minutes,
            tokens_saved,
            len(keep_set),
        )
    return result if changed else messages


DEFAULT_SNIP_TOKEN_BUDGET = 80_000
# Minimum messages to keep (system + last N) so the model always has
# immediate context even if it was over budget.
SNIP_KEEP_RECENT = 6


@dataclass(frozen=True)
class SnipConfig:
    enabled: bool
    token_budget: int
    keep_recent: int


def get_snip_config() -> SnipConfig:
    return SnipConfig(
        enabled=True,
        token_budget=DEFAULT_SNIP_TOKEN_BUDGET,
        keep_recent=SNIP_KEEP_RECENT,
    )


def ensure_tool_history_consistency(messages: list[dict]) -> list[dict]:
    """Canonical tool pairing repair pass for optimized message histories."""
    return ensure_tool_result_pairing(messages)


# CC-identical token estimation constants (from microCompact.ts)
IMAGE_MAX_TOKEN_SIZE = 2000  # CC: images/documents ≈ 2000 tokens
_TOKEN_ESTIMATE_PAD_FACTOR = 4 / 3  # CC: pad estimate by 4/3 to be conservative


def get_effective_context_window_size(model: str | None) -> int:
    """Return the model input window used for headroom-based token decisions."""
    if not model:
        return 200_000

    try:
        from pantheon.utils.provider_registry import get_model_info

        model_info = get_model_info(model)
        return int(model_info.get("max_input_tokens") or 200_000)
    except Exception:
        return 200_000


def get_autocompact_threshold(
    model: str | None,
    *,
    fallback_budget: int = 100_000,
) -> int:
    """Claude-style autocompact threshold based on model context window."""
    if not model:
        return fallback_budget

    effective_window = get_effective_context_window_size(model)
    return max(1, effective_window - AUTOCOMPACT_TRIGGER_BUFFER_TOKENS)


def _rough_token_count(text: str) -> int:
    """Rough token estimation matching CC's roughTokenCountEstimation."""
    return max(1, len(text) // BYTES_PER_TOKEN)


def _calculate_tool_result_tokens(block: dict) -> int:
    """CC-identical per-block token calculation (microCompact.ts:137-160)."""
    content = block.get("content")
    if content is None:
        return 0
    if isinstance(content, str):
        return _rough_token_count(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                btype = item.get("type", "")
                if btype == "text":
                    total += _rough_token_count(item.get("text", ""))
                elif btype in ("image", "document"):
                    total += IMAGE_MAX_TOKEN_SIZE
        return total
    return 0


def _estimate_message_tokens(message: dict) -> int:
    """CC-identical message token estimation (microCompact.ts:164-205).

    Handles all block types: text, tool_result, tool_use, image, document,
    thinking, redacted_thinking. Pads estimate by 4/3 for conservatism.
    """
    content = message.get("content")
    if isinstance(content, str):
        return max(1, len(content) // BYTES_PER_TOKEN)
    if isinstance(content, list):
        total = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                total += _rough_token_count(block.get("text", ""))
            elif btype == "tool_result":
                total += _calculate_tool_result_tokens(block)
            elif btype in ("image", "document"):
                total += IMAGE_MAX_TOKEN_SIZE
            elif btype == "thinking":
                total += _rough_token_count(block.get("thinking", ""))
            elif btype == "redacted_thinking":
                total += _rough_token_count(block.get("data", ""))
            elif btype == "tool_use":
                total += _rough_token_count(
                    block.get("name", "") + json.dumps(block.get("input", {}))
                )
            else:
                # Fallback for server_tool_use, web_search_tool_result, etc.
                total += _rough_token_count(json.dumps(block))
        # CC: pad estimate by 4/3 to be conservative
        return max(1, int(total * _TOKEN_ESTIMATE_PAD_FACTOR))
    return 1


def snip_messages_to_budget(
    messages: list[dict],
    *,
    config: SnipConfig | None = None,
) -> tuple[list[dict], int]:
    """Drop the oldest non-system messages until total tokens fit within budget.

    Mirrors Claude Code's HISTORY_SNIP: runs before microcompact so that
    urgent over-budget situations are handled first.

    Returns (new_messages, tokens_freed).
    Protected tail (last *keep_recent* messages) and the system message are
    never dropped.
    """
    resolved = config or get_snip_config()
    if not resolved.enabled:
        return messages, 0

    total = sum(_estimate_message_tokens(m) for m in messages)
    if total <= resolved.token_budget:
        return messages, 0

    # Split: system message (always keep), body, protected tail
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    keep_recent = max(1, resolved.keep_recent)
    if len(non_system) <= keep_recent:
        return messages, 0

    tail = non_system[-keep_recent:]
    candidates = non_system[:-keep_recent]  # oldest messages, eligible to drop

    tokens_freed = 0
    kept_candidates: list[dict] = []
    # Drop from oldest first; stop as soon as we're under budget
    for msg in candidates:
        remaining_total = total - tokens_freed
        if remaining_total <= resolved.token_budget:
            kept_candidates.append(msg)
        else:
            tokens_freed += _estimate_message_tokens(msg)

    if tokens_freed == 0:
        return messages, 0

    result = ensure_tool_history_consistency(system_msgs + kept_candidates + tail)
    logger.info(
        "[token optimization] history snip freed ~{} tokens ({} messages dropped)",
        tokens_freed,
        len(candidates) - len(kept_candidates),
    )
    return result, tokens_freed


# ---------------------------------------------------------------------------
# Opt4 extension: contextCollapse (read/search group folding)
# Mirrors CC's collapseReadSearch.ts — fold consecutive collapsible tool uses
# into compact summary messages.
# ---------------------------------------------------------------------------

def _message_fingerprint(message: dict) -> str:
    """Stable fingerprint for context-collapse commit replay."""
    payload = {
        "role": message.get("role"),
        "content": message.get("content"),
        "tool_call_id": message.get("tool_call_id"),
        "tool_name": message.get("tool_name"),
        "tool_calls": message.get("tool_calls"),
        "collapsed_commit_id": message.get("_context_collapse_commit_id"),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def _fingerprint_messages(messages: list[dict]) -> tuple[str, ...]:
    return tuple(_message_fingerprint(message) for message in messages)


@dataclass
class CollapsedGroup:
    """A group of consecutive collapsible tool-use / tool-result pairs."""
    start_index: int
    end_index: int  # exclusive
    search_count: int
    read_file_paths: list[str]
    read_count: int
    list_count: int
    bash_count: int
    tokens_before: int


def _is_collapsible_message(message: dict, tool_name_map: dict[str, str]) -> bool:
    """Return True if this message is part of a collapsible tool-use chain."""
    role = message.get("role")
    if role == "tool":
        tool_name = get_tool_name_for_message(message, tool_name_map)
        normalized = normalize_tool_name(tool_name)
        return normalized in ALL_COLLAPSIBLE_TOOLS
    if role == "assistant":
        # An assistant message that ONLY has tool_calls (no text output) is
        # absorbable into a collapse group (CC's "silent assistant" logic).
        content = message.get("content")
        has_text = isinstance(content, str) and content.strip()
        has_tool_calls = bool(message.get("tool_calls"))
        return has_tool_calls and not has_text
    return False


def _extract_read_paths_from_tool_calls(message: dict) -> list[str]:
    """Extract file paths from an assistant message's tool_call arguments.

    Looks for common path parameter names (path, file_path, filepath, url)
    in the function arguments of tool_calls that target read/fetch tools.
    """
    paths: list[str] = []
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        func = tc.get("function", {})
        name = normalize_tool_name(func.get("name"))
        if name not in COLLAPSIBLE_READ_TOOLS:
            continue
        try:
            args = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        for key in ("path", "file_path", "filepath", "url"):
            val = args.get(key)
            if isinstance(val, str) and val:
                paths.append(val)
                break
    return paths


def _find_collapsible_groups(
    messages: list[dict],
    *,
    min_group_size: int = 3,
) -> list[CollapsedGroup]:
    tool_name_map = build_tool_name_map(messages)
    groups: list[CollapsedGroup] = []
    i = 0
    n = len(messages)
    while i < n:
        if not _is_collapsible_message(messages[i], tool_name_map):
            i += 1
            continue

        start = i
        search_count = 0
        read_count = 0
        list_count = 0
        bash_count = 0
        read_paths: list[str] = []
        seen_paths: set[str] = set()
        tokens = 0

        while i < n and _is_collapsible_message(messages[i], tool_name_map):
            msg = messages[i]
            tokens += _estimate_message_tokens(msg)
            if msg.get("role") == "assistant":
                for path in _extract_read_paths_from_tool_calls(msg):
                    if path not in seen_paths:
                        seen_paths.add(path)
                        read_paths.append(path)
            if msg.get("role") == "tool":
                tool_name = normalize_tool_name(
                    get_tool_name_for_message(msg, tool_name_map)
                )
                if tool_name in COLLAPSIBLE_SEARCH_TOOLS:
                    search_count += 1
                if tool_name in COLLAPSIBLE_READ_TOOLS:
                    read_count += 1
                if tool_name in COLLAPSIBLE_LIST_TOOLS:
                    list_count += 1
                if tool_name in ("shell", "bash"):
                    bash_count += 1
            i += 1

        group_size = i - start
        if group_size >= min_group_size:
            groups.append(CollapsedGroup(
                start_index=start,
                end_index=i,
                search_count=search_count,
                read_file_paths=read_paths,
                read_count=read_count,
                list_count=list_count,
                bash_count=bash_count,
                tokens_before=tokens,
            ))
    return groups


def _build_collapsed_message(
    group: CollapsedGroup,
    *,
    commit_id: int | None = None,
) -> dict[str, Any]:
    parts: list[str] = []
    if group.search_count:
        parts.append(f"searched {group.search_count} pattern{'s' if group.search_count > 1 else ''}")
    if group.read_count:
        parts.append(f"read {group.read_count} file{'s' if group.read_count > 1 else ''}")
    if group.list_count:
        parts.append(f"listed {group.list_count} dir{'s' if group.list_count > 1 else ''}")
    if group.bash_count:
        parts.append(f"ran {group.bash_count} command{'s' if group.bash_count > 1 else ''}")

    summary = ", ".join(parts) if parts else "performed tool operations"

    file_lines = ""
    if group.read_file_paths:
        paths = group.read_file_paths[:8]
        file_lines = "\nFiles: " + ", ".join(paths)
        if len(group.read_file_paths) > 8:
            file_lines += f" (+{len(group.read_file_paths) - 8} more)"

    collapsed_message = {
        "role": "user",
        "content": f"[contextCollapse] {summary}{file_lines}",
        "_collapsed": True,
        "_collapsed_message_count": group.end_index - group.start_index,
    }
    if commit_id is not None:
        collapsed_message["_context_collapse_commit_id"] = commit_id
    return collapsed_message


def _apply_groups_to_messages(
    messages: list[dict],
    groups: list[CollapsedGroup],
    *,
    commit_ids: list[int | None] | None = None,
) -> tuple[list[dict], int]:
    if not groups:
        return messages, 0

    tokens_saved = 0
    result: list[dict] = []
    prev_end = 0

    for index, group in enumerate(groups):
        result.extend(messages[prev_end:group.start_index])
        commit_id = commit_ids[index] if commit_ids is not None else None
        collapsed_msg = _build_collapsed_message(group, commit_id=commit_id)
        result.append(collapsed_msg)

        collapsed_tokens = _estimate_message_tokens(collapsed_msg)
        tokens_saved += group.tokens_before - collapsed_tokens
        prev_end = group.end_index

    result.extend(messages[prev_end:])
    return result, tokens_saved


class ContextCollapseManager:
    """Stateful replay of Claude Code-style context collapse commits."""

    def __init__(self) -> None:
        self._commits: list[ContextCollapseCommit] = []
        self._next_commit_id = 1
        self._health = ContextCollapseHealth()
        self._snapshot: dict[str, Any] | None = None

    def isContextCollapseEnabled(self) -> bool:
        return True

    def getStats(self) -> ContextCollapseStats:
        return ContextCollapseStats(
            collapsed_spans=len(self._commits),
            collapsed_messages=sum(commit.archived_count for commit in self._commits),
            staged_spans=1 if self._snapshot else 0,
            health=self._health,
        )

    def restoreFromEntries(
        self,
        commits: list[dict[str, Any]] | list[ContextCollapseCommit],
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        restored: list[ContextCollapseCommit] = []
        for commit in commits:
            parsed = _deserialize_context_collapse_commit(commit)
            if parsed is not None:
                restored.append(parsed)
        self._commits = restored
        self._snapshot = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else None
        self._next_commit_id = (
            max((commit.collapse_id for commit in restored), default=0) + 1
        )

    def exportEntries(self) -> list[dict[str, Any]]:
        return [_serialize_context_collapse_commit(commit) for commit in self._commits]

    def getSnapshotEntry(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._snapshot)

    def projectView(self, messages: list[dict]) -> list[dict]:
        view = [copy.deepcopy(message) for message in messages]
        for commit in self._commits:
            view = self._apply_commit(view, commit)
        return view

    def applyCollapsesIfNeeded(
        self,
        messages: list[dict],
        tool_use_context: Any | None = None,
        query_source: str | None = None,
        model: str | None = None,
        min_group_size: int = 3,
    ) -> ContextCollapseApplyResult:
        del tool_use_context

        view = self.projectView(messages)
        decision = get_context_collapse_decision(
            view,
            model=model,
            query_source=query_source,
        )
        if not decision.should_commit:
            return ContextCollapseApplyResult(messages=view, committed=0, decision=decision)

        self._health = ContextCollapseHealth(
            total_spawns=self._health.total_spawns + 1,
            total_errors=self._health.total_errors,
            total_empty_spawns=self._health.total_empty_spawns,
            empty_spawn_warning_emitted=self._health.empty_spawn_warning_emitted,
            last_error=self._health.last_error,
        )

        committed = 0
        current_view = view
        current_decision = decision
        while current_decision.should_commit:
            groups = _find_collapsible_groups(current_view, min_group_size=min_group_size)
            if not groups:
                self._record_empty_spawn()
                break

            group = groups[0]
            collapsed_message = _build_collapsed_message(
                group,
                commit_id=self._next_commit_id,
            )
            if _estimate_message_tokens(collapsed_message) >= group.tokens_before:
                self._record_empty_spawn()
                break

            self._commits.append(ContextCollapseCommit(
                collapse_id=self._next_commit_id,
                archived_pattern=_fingerprint_messages(
                    current_view[group.start_index:group.end_index]
                ),
                summary_message=collapsed_message,
                summary_text=str(collapsed_message.get("content", "")),
                archived_count=group.end_index - group.start_index,
            ))
            self._next_commit_id += 1
            committed += 1
            current_view = self.projectView(messages)
            current_decision = get_context_collapse_decision(
                current_view,
                model=model,
                query_source=query_source,
            )

        if committed > 0:
            logger.info(
                "[token optimization] contextCollapse committed {} span(s), ~{} tokens saved",
                committed,
                max(0, decision.total_tokens - current_decision.total_tokens),
            )

        return ContextCollapseApplyResult(
            messages=current_view,
            committed=committed,
            decision=current_decision,
        )

    def recoverFromOverflow(
        self,
        messages: list[dict],
        query_source: str | None = None,
        model: str | None = None,
        min_group_size: int = 3,
    ) -> ContextCollapseRecoverResult:
        del query_source, model

        committed = 0
        current_view = self.projectView(messages)
        while True:
            groups = _find_collapsible_groups(current_view, min_group_size=min_group_size)
            if not groups:
                break
            group = groups[0]
            collapsed_message = _build_collapsed_message(
                group,
                commit_id=self._next_commit_id,
            )
            if _estimate_message_tokens(collapsed_message) >= group.tokens_before:
                break
            self._commits.append(ContextCollapseCommit(
                collapse_id=self._next_commit_id,
                archived_pattern=_fingerprint_messages(
                    current_view[group.start_index:group.end_index]
                ),
                summary_message=collapsed_message,
                summary_text=str(collapsed_message.get("content", "")),
                archived_count=group.end_index - group.start_index,
            ))
            self._next_commit_id += 1
            committed += 1
            current_view = self.projectView(messages)

        if committed > 0:
            logger.warning(
                "[token optimization] contextCollapse overflow recovery committed {} span(s)",
                committed,
            )

        return ContextCollapseRecoverResult(messages=current_view, committed=committed)

    def isWithheldPromptTooLong(
        self,
        message: Any,
        is_prompt_too_long_message: Any,
        query_source: str | None = None,
    ) -> bool:
        del query_source
        try:
            return bool(is_prompt_too_long_message(message))
        except Exception:
            return False

    def _apply_commit(
        self,
        messages: list[dict],
        commit: ContextCollapseCommit,
    ) -> list[dict]:
        pattern_len = len(commit.archived_pattern)
        if pattern_len == 0 or len(messages) < pattern_len:
            return messages

        for start in range(0, len(messages) - pattern_len + 1):
            window = messages[start:start + pattern_len]
            if _fingerprint_messages(window) != commit.archived_pattern:
                continue
            return [
                *messages[:start],
                copy.deepcopy(commit.summary_message),
                *messages[start + pattern_len:],
            ]
        return messages

    def _record_empty_spawn(self) -> None:
        total_empty = self._health.total_empty_spawns + 1
        self._health = ContextCollapseHealth(
            total_spawns=self._health.total_spawns,
            total_errors=self._health.total_errors,
            total_empty_spawns=total_empty,
            empty_spawn_warning_emitted=total_empty >= 3,
            last_error=self._health.last_error,
        )


def _get_context_collapse_manager() -> ContextCollapseManager:
    try:
        from pantheon.agent import get_current_run_context
    except Exception:
        return ContextCollapseManager()

    run_context = get_current_run_context()
    if run_context is None:
        return ContextCollapseManager()

    manager = getattr(run_context, "context_collapse_manager", None)
    if isinstance(manager, ContextCollapseManager):
        return manager

    manager = ContextCollapseManager()
    if run_context.memory is not None:
        commits, snapshot = load_context_collapse_entries(run_context.memory)
        manager.restoreFromEntries(commits, snapshot)
    run_context.context_collapse_manager = manager
    return manager


def restoreFromEntries(
    commits: list[dict[str, Any]] | list[ContextCollapseCommit],
    snapshot: dict[str, Any] | None = None,
    *,
    memory: Any | None = None,
) -> None:
    manager = _get_context_collapse_manager()
    manager.restoreFromEntries(commits, snapshot)
    if memory is not None:
        save_context_collapse_entries(
            memory,
            manager.exportEntries(),
            manager.getSnapshotEntry(),
        )


def collapse_read_search_groups(
    messages: list[dict],
    *,
    min_group_size: int = 3,
) -> tuple[list[dict], int]:
    """Collapse consecutive read/search tool-use sequences into summaries.

    Mirrors CC's ``collapseReadSearchGroups()`` — identifies groups of
    consecutive collapsible messages, summarizes each group into a single
    compact user message, and returns the reduced list.

    Returns (new_messages, tokens_saved).
    """
    groups = _find_collapsible_groups(messages, min_group_size=min_group_size)
    if not groups:
        return messages, 0

    result, tokens_saved = _apply_groups_to_messages(messages, groups)

    if tokens_saved > 0:
        logger.info(
            "[token optimization] contextCollapse folded {} group(s), ~{} tokens saved",
            len(groups),
            tokens_saved,
        )
    return result, tokens_saved


def get_context_collapse_decision(
    messages: list[dict],
    *,
    model: str | None = None,
    query_source: str | None = None,
) -> ContextCollapseDecision:
    """Claude-style headroom gate for context collapse commit decisions."""
    if query_source in _COLLAPSE_SKIP_QUERY_SOURCES:
        return ContextCollapseDecision(
            total_tokens=0,
            context_window=get_effective_context_window_size(model),
            usage_ratio=0.0,
            should_commit=False,
            at_blocking_limit=False,
        )

    total_tokens = sum(_estimate_message_tokens(message) for message in messages)
    context_window = get_effective_context_window_size(model)
    usage_ratio = (total_tokens / context_window) if context_window > 0 else 0.0
    return ContextCollapseDecision(
        total_tokens=total_tokens,
        context_window=context_window,
        usage_ratio=usage_ratio,
        should_commit=usage_ratio >= CONTEXT_COLLAPSE_COMMIT_THRESHOLD,
        at_blocking_limit=usage_ratio >= CONTEXT_COLLAPSE_BLOCKING_THRESHOLD,
    )


def apply_collapses_if_needed(
    messages: list[dict],
    *,
    model: str | None = None,
    query_source: str | None = None,
    min_group_size: int = 3,
) -> tuple[list[dict], int]:
    """Python wrapper around Claude Code-style applyCollapsesIfNeeded()."""
    result = applyCollapsesIfNeeded(
        messages,
        model=model,
        query_source=query_source,
        min_group_size=min_group_size,
    )
    return result.messages, result.committed


def applyCollapsesIfNeeded(
    messages: list[dict],
    tool_use_context: Any | None = None,
    query_source: str | None = None,
    model: str | None = None,
    min_group_size: int = 3,
) -> ContextCollapseApplyResult:
    """Claude Code-shaped entrypoint for committing context collapses."""
    manager = _get_context_collapse_manager()
    result = manager.applyCollapsesIfNeeded(
        messages,
        tool_use_context=tool_use_context,
        query_source=query_source,
        model=model,
        min_group_size=min_group_size,
    )
    try:
        from pantheon.agent import get_current_run_context

        run_context = get_current_run_context()
    except Exception:
        run_context = None
    if run_context and run_context.memory is not None:
        save_context_collapse_entries(
            run_context.memory,
            manager.exportEntries(),
            manager.getSnapshotEntry(),
        )
    return result


def projectView(messages: list[dict]) -> list[dict]:
    """Claude Code-shaped projection API for already-committed collapses."""
    return _get_context_collapse_manager().projectView(messages)


def recoverFromOverflow(
    messages: list[dict],
    query_source: str | None = None,
    model: str | None = None,
    min_group_size: int = 3,
) -> ContextCollapseRecoverResult:
    """Claude Code-shaped overflow drain hook."""
    manager = _get_context_collapse_manager()
    result = manager.recoverFromOverflow(
        messages,
        query_source=query_source,
        model=model,
        min_group_size=min_group_size,
    )
    try:
        from pantheon.agent import get_current_run_context

        run_context = get_current_run_context()
    except Exception:
        run_context = None
    if run_context and run_context.memory is not None:
        save_context_collapse_entries(
            run_context.memory,
            manager.exportEntries(),
            manager.getSnapshotEntry(),
        )
    return result


def isContextCollapseEnabled() -> bool:
    return _get_context_collapse_manager().isContextCollapseEnabled()


def getContextCollapseStats() -> ContextCollapseStats:
    return _get_context_collapse_manager().getStats()


# ---------------------------------------------------------------------------
# Opt4 extension: autocompact (CC-identical LLM-based summarization)
# Mirrors CC's autoCompactIfNeeded() + compactConversation() —
# when context exceeds budget after all other optimizations, call an LLM
# to generate a structured summary of the older conversation.
# ---------------------------------------------------------------------------

AUTOCOMPACT_TOKEN_BUDGET = 100_000  # trigger threshold
AUTOCOMPACT_KEEP_RECENT = 8  # messages to preserve verbatim
AUTOCOMPACT_MAX_OUTPUT_TOKENS = 20_000  # CC: MAX_OUTPUT_TOKENS_FOR_SUMMARY
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3  # CC circuit breaker

# CC-identical compact prompt (from compact/prompt.ts)
_AUTOCOMPACT_SYSTEM_PROMPT = (
    "You are a helpful AI assistant tasked with summarizing conversations."
)

_AUTOCOMPACT_NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

_AUTOCOMPACT_DETAILED_ANALYSIS = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

_AUTOCOMPACT_USER_PROMPT = (
    _AUTOCOMPACT_NO_TOOLS_PREAMBLE
    + """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

"""
    + _AUTOCOMPACT_DETAILED_ANALYSIS
    + """

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task."""
)

# CC-identical post-compact user message wrapper (from compact/prompt.ts)
_AUTOCOMPACT_SUMMARY_WRAPPER = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

{summary}
Continue the conversation from where it left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened."""


@dataclass
class AutocompactTrackingState:
    """CC-identical tracking state for autocompact circuit breaker."""
    compacted: bool = False
    consecutive_failures: int = 0


def _format_summary(raw_response: str) -> str:
    """Extract <summary> block from LLM response, or use full text."""
    import re
    match = re.search(r"<summary>(.*?)</summary>", raw_response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw_response.strip()


def should_autocompact(
    messages: list[dict],
    *,
    token_budget: int = AUTOCOMPACT_TOKEN_BUDGET,
    model: str | None = None,
    query_source: str | None = None,
    suppress_for_context_collapse: bool = False,
) -> bool:
    """CC-identical predicate: should autocompact fire?

    Guards:
    - Recursion: ``compact`` and ``session_memory`` sources are rejected
    - Budget: total tokens must exceed ``token_budget``
    """
    # Recursion guard (CC: querySource === 'session_memory' || 'compact')
    if query_source in ("compact", "session_memory", "agent_summary"):
        return False
    if suppress_for_context_collapse:
        return False
    threshold = get_autocompact_threshold(model, fallback_budget=token_budget)
    total = sum(_estimate_message_tokens(m) for m in messages)
    return total > threshold


async def autocompact_messages(
    messages: list[dict],
    *,
    model: str | None = None,
    token_budget: int = AUTOCOMPACT_TOKEN_BUDGET,
    keep_recent: int = AUTOCOMPACT_KEEP_RECENT,
    tracking: AutocompactTrackingState | None = None,
    query_source: str | None = None,
    transcript_path: str | None = None,
    suppress_for_context_collapse: bool = False,
) -> tuple[list[dict], int, AutocompactTrackingState]:
    """CC-identical LLM-based autocompact.

    Mirrors CC's ``autoCompactIfNeeded()`` + ``compactConversation()``:
    1. Check budget threshold
    2. Circuit-breaker check (consecutive failures)
    3. Strip images, build compact prompt
    4. Call LLM with CC's exact prompt template
    5. Wrap summary in CC's post-compact user message format
    6. Return [system, summary_msg, ...preserved_tail], tokens_freed, tracking

    Falls back to heuristic summary if no model/LLM available.
    """
    tracking = tracking or AutocompactTrackingState()

    # Budget check
    if not should_autocompact(
        messages,
        token_budget=token_budget,
        model=model,
        query_source=query_source,
        suppress_for_context_collapse=suppress_for_context_collapse,
    ):
        return messages, 0, tracking

    # Circuit breaker (CC: MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3)
    if tracking.consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        logger.warning(
            "[token optimization] autocompact circuit breaker tripped after {} consecutive failures",
            tracking.consecutive_failures,
        )
        return messages, 0, tracking

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_recent:
        return messages, 0, tracking

    tail = non_system[-keep_recent:]
    to_summarize = non_system[:-keep_recent]

    if not to_summarize:
        return messages, 0, tracking

    tokens_before = sum(_estimate_message_tokens(m) for m in to_summarize)

    # ---- LLM-based summarization (CC path) ----
    summary_text: str | None = None
    if model:
        try:
            # Build messages for compact LLM call (CC: streamCompactSummary)
            # Strip images from messages to compress
            compact_messages: list[dict] = []
            for msg in to_summarize:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Strip image/document blocks → replace with [image]/[document]
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict):
                            btype = block.get("type", "")
                            if btype == "text":
                                text_parts.append(block.get("text", ""))
                            elif btype in ("image", "document"):
                                text_parts.append(f"[{btype}]")
                            else:
                                text_parts.append(block.get("text", "") or block.get("content", ""))
                    content = "\n".join(text_parts)
                if not isinstance(content, str):
                    content = str(content)
                if role == "tool":
                    # Embed tool results as assistant message for compact LLM
                    tool_name = msg.get("tool_name", "tool")
                    compact_messages.append({
                        "role": "assistant",
                        "content": f"[Tool result ({tool_name})]\n{content[:10_000]}",
                    })
                elif role in ("user", "assistant"):
                    compact_messages.append({"role": role, "content": content})
                elif role == "compression":
                    compact_messages.append({"role": "user", "content": content})

            # Add the compact request as final user message
            compact_messages.append({
                "role": "user",
                "content": _AUTOCOMPACT_USER_PROMPT,
            })

            # Call LLM (CC: queryModelWithStreaming with querySource='compact')
            from pantheon.utils.llm import acompletion
            response = await acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": _AUTOCOMPACT_SYSTEM_PROMPT},
                    *compact_messages,
                ],
                model_params={
                    "max_tokens": min(AUTOCOMPACT_MAX_OUTPUT_TOKENS, 20_000),
                    "temperature": 0,
                },
            )
            raw_summary = ""
            if isinstance(response, dict):
                raw_summary = response.get("content", "")
            elif hasattr(response, "choices") and response.choices:
                raw_summary = response.choices[0].message.content or ""
            else:
                raw_summary = str(response)
            summary_text = _format_summary(raw_summary)

        except Exception as e:
            logger.error("[token optimization] autocompact LLM call failed: {}", e)
            tracking.consecutive_failures += 1
            # Fall through to heuristic fallback

    # ---- Heuristic fallback (when no model or LLM failed) ----
    if not summary_text:
        summary_parts: list[str] = []
        for msg in to_summarize:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            if not isinstance(content, str):
                content = str(content)
            trimmed = content[:300].strip()
            if trimmed:
                summary_parts.append(f"[{role}] {trimmed}")
        summary_text = "\n".join(summary_parts[:30])
        if len(summary_parts) > 30:
            summary_text += f"\n... (+{len(summary_parts) - 30} more messages)"

    # ---- Build post-compact messages (CC: buildPostCompactMessages) ----
    wrapper = _AUTOCOMPACT_SUMMARY_WRAPPER.format(summary=summary_text)
    if transcript_path:
        wrapper += (
            f"\n\nIf you need specific details from before compaction "
            f"(like exact code snippets, error messages, or content you "
            f"generated), read the full transcript at: {transcript_path}"
        )

    compact_msg = {
        "role": "user",
        "content": wrapper,
        "_autocompacted": True,
    }

    tokens_after = _estimate_message_tokens(compact_msg)
    tokens_freed = tokens_before - tokens_after

    result = system_msgs + [compact_msg] + tail

    # Reset failure count on success (CC: consecutiveFailures: 0)
    tracking.consecutive_failures = 0
    tracking.compacted = True

    logger.info(
        "[token optimization] autocompact summarized {} messages via {} (~{} tokens freed)",
        len(to_summarize),
        "LLM" if model else "heuristic",
        tokens_freed,
    )
    return result, tokens_freed, tracking


def apply_token_optimizations(
    messages: list[dict],
    memory: Any | None = None,
    base_dir: Path | None = None,
    *,
    is_main_thread: bool = True,
    snip_config: SnipConfig | None = None,
    enable_context_collapse: bool = True,
    enable_autocompact: bool = True,
    query_source: str | None = None,
    context_window_model: str | None = None,
) -> list[dict]:
    """Synchronous 4-stage optimization pipeline.

    For the full 5-stage pipeline including LLM-based autocompact,
    use :func:`apply_token_optimizations_async`.
    """
    # 1. Externalize large tool outputs (session-level budget)
    optimized = apply_tool_result_budget(
        messages,
        memory=memory,
        base_dir=base_dir,
        query_source=query_source,
    )
    # 2. Snip over-budget history (HISTORY_SNIP) — before microcompact
    optimized, _ = snip_messages_to_budget(optimized, config=snip_config)
    # 3. Clear old compactable tool results (time-based microcompact)
    optimized = microcompact_messages(
        optimized,
        is_main_thread=is_main_thread,
    )
    # 4. Context Collapse: Claude-style gate first, then fold read/search groups.
    if enable_context_collapse:
        optimized, _ = apply_collapses_if_needed(
            optimized,
            model=context_window_model,
            query_source=query_source,
        )
    optimized = ensure_tool_history_consistency(optimized)
    # Note: autocompact (stage 5) is async — use apply_token_optimizations_async
    return optimized


async def apply_token_optimizations_async(
    messages: list[dict],
    memory: Any | None = None,
    base_dir: Path | None = None,
    *,
    is_main_thread: bool = True,
    snip_config: SnipConfig | None = None,
    enable_context_collapse: bool = True,
    enable_autocompact: bool = True,
    query_source: str | None = None,
    autocompact_model: str | None = None,
    autocompact_tracking: AutocompactTrackingState | None = None,
    transcript_path: str | None = None,
    context_window_model: str | None = None,
) -> tuple[list[dict], AutocompactTrackingState | None]:
    """Full 5-stage CC-identical optimization pipeline (async).

    Stages:
    1. Tool result budget (externalize large outputs)
    2. HISTORY_SNIP (token-budget truncation)
    3. Microcompact (time-based clearing)
    4. contextCollapse (read/search folding)
    5. Autocompact (LLM-based summarization — CC-identical)

    Returns (optimized_messages, tracking_state).
    """
    # Stages 1-4 (sync)
    optimized = apply_token_optimizations(
        messages,
        memory=memory,
        base_dir=base_dir,
        is_main_thread=is_main_thread,
        snip_config=snip_config,
        enable_context_collapse=enable_context_collapse,
        enable_autocompact=False,  # handled below
        query_source=query_source,
        context_window_model=context_window_model or autocompact_model,
    )
    # Stage 5: Autocompact (async, LLM-based)
    tracking = autocompact_tracking
    if enable_autocompact:
        optimized, _, tracking = await autocompact_messages(
            optimized,
            model=autocompact_model,
            query_source=query_source,
            tracking=tracking,
            transcript_path=transcript_path,
            suppress_for_context_collapse=enable_context_collapse,
        )
    optimized = ensure_tool_history_consistency(optimized)
    return optimized, tracking


def project_memory_messages_for_llm(messages: list[dict]) -> list[dict]:
    """Project stored history into the LLM-facing view."""
    from copy import deepcopy

    filtered = [message for message in messages if message.get("role") != "system"]

    last_compression_idx = -1
    for index, message in enumerate(filtered):
        if message.get("role") == "compression":
            last_compression_idx = index

    if last_compression_idx >= 0:
        filtered = filtered[last_compression_idx:]

    result: list[dict] = []
    for message in filtered:
        msg = deepcopy(message)
        if msg.get("role") == "compression":
            msg["role"] = "user"
            if not isinstance(msg.get("content"), str):
                msg["content"] = str(msg.get("content", ""))
        result.append(msg)
    return result


def _prepare_llm_view_messages(
    messages: list[dict],
) -> tuple[dict | None, list[dict]]:
    """Shared projection logic for build_llm_view / build_llm_view_async.

    Returns (system_message_or_None, projected_non_system_messages).
    """
    system_message = next(
        (message for message in messages if message.get("role") == "system"),
        None,
    )
    non_system_messages = [
        message for message in messages if message.get("role") != "system"
    ]
    projected = project_memory_messages_for_llm(non_system_messages)
    projected = [
        m for m in projected
        if m.get("role") in ("user", "assistant", "tool")
    ]
    return system_message, projected


def _wrap_with_system(
    system_message: dict | None,
    optimized: list[dict],
) -> list[dict]:
    if system_message is not None:
        return [system_message, *optimized]
    return optimized


def build_llm_view(
    messages: list[dict],
    memory: Any | None = None,
    base_dir: Path | None = None,
    *,
    is_main_thread: bool = True,
    snip_config: "SnipConfig | None" = None,
    context_window_model: str | None = None,
) -> list[dict]:
    """Build the projected prompt view from raw history (sync, no autocompact)."""
    if not messages:
        return []
    system_message, projected = _prepare_llm_view_messages(messages)
    optimized = apply_token_optimizations(
        projected,
        memory=memory,
        base_dir=base_dir,
        is_main_thread=is_main_thread,
        snip_config=snip_config,
        context_window_model=context_window_model,
    )
    return _wrap_with_system(system_message, optimized)


async def build_llm_view_async(
    messages: list[dict],
    memory: Any | None = None,
    base_dir: Path | None = None,
    *,
    is_main_thread: bool = True,
    snip_config: "SnipConfig | None" = None,
    autocompact_model: str | None = None,
    context_window_model: str | None = None,
) -> list[dict]:
    """Async variant of build_llm_view that enables LLM-based autocompact."""
    if not messages:
        return []
    system_message, projected = _prepare_llm_view_messages(messages)
    optimized, _ = await apply_token_optimizations_async(
        projected,
        memory=memory,
        base_dir=base_dir,
        is_main_thread=is_main_thread,
        snip_config=snip_config,
        autocompact_model=autocompact_model,
        context_window_model=context_window_model or autocompact_model,
    )
    return _wrap_with_system(system_message, optimized)


def stabilize_tool_definitions(tools: list[dict]) -> list[dict]:
    """Return deterministic tool definitions for cache-stable prompts."""

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            normalized = {key: normalize(value[key]) for key in sorted(value)}
            required = normalized.get("required")
            if isinstance(required, list) and all(
                isinstance(item, str) for item in required
            ):
                normalized["required"] = sorted(required)
            return normalized
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    stabilized = [normalize(tool) for tool in tools]
    stabilized.sort(
        key=lambda tool: (
            str(tool.get("function", {}).get("name", "")),
            json.dumps(tool, ensure_ascii=False, sort_keys=True),
        )
    )
    return stabilized


def extract_persisted_file_paths(messages: list[dict]) -> list[str]:
    pattern = re.compile(r"Full output saved to:\s*(.+)")
    paths: list[str] = []
    seen: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for match in pattern.findall(content):
            path = match.strip()
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def build_recent_context_block(
    messages: list[dict],
    max_messages: int = 6,
    max_chars_per_message: int = 1200,
) -> str:
    relevant = [
        message
        for message in messages
        if message.get("role") in {"user", "assistant", "tool"}
    ]
    if not relevant:
        return ""

    def trim(text: str) -> str:
        if len(text) <= max_chars_per_message:
            return text
        return text[:max_chars_per_message] + "\n...[truncated recent context]..."

    blocks: list[str] = []
    for message in relevant[-max_messages:]:
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        role = str(message.get("role", "unknown")).upper()
        blocks.append(f"[{role}]\n{trim(content.strip())}")
    return "\n\n".join(blocks)


ON_DEMAND_HINT = (
    "Note: Only a summary and recent context are provided above. "
    "If you need the full content of any referenced file or tool output, "
    "use read_file or the appropriate tool to retrieve it on demand."
)


def build_delegation_context_message(
    history: list[dict],
    instruction: str,
    summary_text: str | None = None,
) -> str:
    """Build a compact delegation prompt from summary + recent context + file refs.

    The *history* passed here should already be trimmed to a recent tail by the
    caller (``create_delegation_task_message``).  Older context is represented
    by *summary_text*.
    """
    raw_projected = project_memory_messages_for_llm(history)
    projected = build_llm_view(history, is_main_thread=False)
    parts: list[str] = []
    if summary_text:
        parts.append(f"Context Summary:\n{summary_text}")

    recent_context = build_recent_context_block(projected)
    if recent_context:
        parts.append(f"Recent Context:\n{recent_context}")

    file_paths = extract_persisted_file_paths(raw_projected) or extract_persisted_file_paths(projected)
    if file_paths:
        parts.append(
            "Referenced Files (retrieve on demand if needed):\n"
            + "\n".join(f"- {path}" for path in file_paths[:10])
        )

    parts.append(f"Task: {instruction}")

    # Append on-demand retrieval hint when summary was used
    if summary_text:
        parts.append(ON_DEMAND_HINT)

    return "\n\n".join(parts)


def estimate_total_tokens_from_chars(messages: list[dict]) -> int:
    total_chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total_chars += len(content)
    return int(total_chars / BYTES_PER_TOKEN)


# ---------------------------------------------------------------------------
# Opt3: Prompt cache control markers
# ---------------------------------------------------------------------------

_ANTHROPIC_MODEL_PREFIXES = ("claude", "anthropic/", "custom_anthropic/")
_QWEN_MODEL_PREFIXES = ("qwen", "zai/")


def is_anthropic_model(model: str) -> bool:
    """Return True if *model* routes to the Anthropic API."""
    lower = model.lower()
    return any(lower.startswith(p) for p in _ANTHROPIC_MODEL_PREFIXES)


def supports_explicit_cache_control(model: str) -> bool:
    """Return True if *model* supports ``cache_control: {"type": "ephemeral"}`` markers.

    Currently supported:
    - Anthropic (Claude): native cache_control in Messages API
    - Qwen (DashScope): same cache_control format via OpenAI-compatible endpoint
    """
    lower = model.lower()
    return (
        any(lower.startswith(p) for p in _ANTHROPIC_MODEL_PREFIXES)
        or any(lower.startswith(p) for p in _QWEN_MODEL_PREFIXES)
    )


def _make_text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _last_text_block_index(blocks: list[dict]) -> int | None:
    """Return the index of the last block whose type is 'text', or None."""
    for i in range(len(blocks) - 1, -1, -1):
        if blocks[i].get("type") == "text":
            return i
    return None


def _ensure_block_content(message: dict) -> list[dict]:
    """Return message content as a list of blocks, converting str if needed."""
    content = message.get("content")
    if isinstance(content, str):
        return [_make_text_block(content)]
    if isinstance(content, list):
        return list(content)
    return []


def inject_cache_control_markers(
    messages: list[dict],
    *,
    skip_cache_write: bool = False,
) -> list[dict]:
    """Inject Anthropic prompt-cache markers into a message list.

    Mirrors Claude Code's ``addCacheBreakpoints()`` strategy:
    - System message: mark the last text block with cache_control.
    - Conversation: mark the last text block of the last (or
      second-to-last when *skip_cache_write*) user/assistant message
      that has non-empty text content.

    *skip_cache_write* is used for fire-and-forget / fork queries:
    the last message is a short delegation directive whose prefix will
    never be reused, so placing the cache breakpoint one message earlier
    preserves cache for the parent conversation prefix.

    Returns a *new* list; input messages are not mutated.

    Cache control is passed through natively by the Anthropic adapter.
    """
    from copy import deepcopy

    result = deepcopy(messages)
    cache_marker: dict[str, Any] = {"type": "ephemeral"}

    # 1. Mark last text block of system message
    for msg in result:
        if msg.get("role") == "system":
            blocks = _ensure_block_content(msg)
            idx = _last_text_block_index(blocks)
            if idx is not None:
                blocks[idx] = {**blocks[idx], "cache_control": cache_marker}
                msg["content"] = blocks
            break

    # 2. Mark last text block of the Nth-from-last user/assistant message
    #    Normal: last message.  skip_cache_write: second-to-last.
    hits_needed = 2 if skip_cache_write else 1
    hits = 0
    for msg in reversed(result):
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        hits += 1
        if hits < hits_needed:
            continue
        blocks = _ensure_block_content(msg)
        idx = _last_text_block_index(blocks)
        if idx is not None and blocks[idx].get("text", "").strip():
            blocks[idx] = {**blocks[idx], "cache_control": cache_marker}
            msg["content"] = blocks
            break

    return result
