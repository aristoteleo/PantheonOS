"""
MemorySystemPlugin — PantheonTeam adapter for the shared MemoryRuntime.

This plugin does NOT own memory logic; it delegates everything to MemoryRuntime.
ChatRoom uses the same runtime through ChatRoomMemoryAdapter.
"""

from __future__ import annotations

import asyncio
import copy
from typing import TYPE_CHECKING, Any

from pantheon.settings import get_settings
from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger

from .prompts import MEMORY_GUIDANCE

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam

    from .runtime import MemoryRuntime


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_memory_context(results: list, inject_mode: str, base_dir: "Path | None" = None) -> str:
    """Format retrieval results for injection into user message.

    inject_mode="index": title, summary, and relative file path (low token cost).
      base_dir is declared once at the top; each entry uses a relative path.
    inject_mode="full":  complete file content with freshness annotation.
    """
    from pathlib import Path

    if inject_mode == "full":
        parts = [f"### {r.entry.title} ({r.age_text})\n{r.content}" for r in results]
        return "\n\n## Relevant Memories\n\n" + "\n\n---\n\n".join(parts)

    # index mode: declare base once, use relative paths
    parts = []
    for r in results:
        summary = r.entry.summary or r.entry.title
        try:
            rel = r.path.relative_to(base_dir) if base_dir else r.path
        except ValueError:
            rel = r.path
        parts.append(f"### {r.entry.title} ({r.age_text})\n{summary}\n{rel}")

    base_line = f"Memory base: {base_dir}\n" if base_dir else ""
    return f"\n\n## Relevant Memories\n{base_line}\n" + "\n\n---\n\n".join(parts)


def _extract_query(user_input: Any) -> str | None:
    """Extract plain-text query from str or list[dict] input. Returns None if unsupported."""
    if isinstance(user_input, str):
        return user_input.strip() or None
    if isinstance(user_input, list):
        for msg in reversed(user_input):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            llm_content = msg.get("_llm_content") or msg.get("content", "")
            if isinstance(llm_content, str):
                text = llm_content.strip()
            elif isinstance(llm_content, list):
                text = " ".join(
                    p.get("text", "") for p in llm_content
                    if isinstance(p, dict) and p.get("type") == "text"
                ).strip()
            else:
                text = ""
            if text:
                return text
    return None


def _append_to_user_input(user_input: Any, text: str) -> Any:
    """Append text to user_input, handling str and list[dict] formats.

    For list[dict]: appends to _llm_content of the last user message.
    Follows the same pattern as agent._apply_injections().
    """
    if isinstance(user_input, str):
        return user_input + text

    # list[dict]: shallow-copy list, copy-on-write the target message
    result = copy.copy(user_input)
    for i in range(len(result) - 1, -1, -1):
        msg = result[i]
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        msg = dict(msg)
        result[i] = msg
        if not msg.get("_llm_content"):
            msg["_llm_content"] = msg.get("content", "")
        if isinstance(msg["_llm_content"], str):
            msg["_llm_content"] += text
        elif isinstance(msg["_llm_content"], list):
            msg["_llm_content"] = list(msg["_llm_content"])
            msg["_llm_content"].append({"type": "text", "text": text})
        break
    return result


class MemorySystemPlugin(TeamPlugin):
    """PantheonTeam adapter — delegates all logic to MemoryRuntime."""

    def __init__(self, runtime: "MemoryRuntime"):
        self.runtime = runtime
        self._background_tasks: set[asyncio.Task] = set()

    async def get_toolsets(self, team: "PantheonTeam") -> list:
        return []

    async def on_team_created(self, team: "PantheonTeam") -> None:
        """Inject MEMORY.md guidance (and optionally the index) into agent system prompts."""
        if not self.runtime.is_initialized:
            logger.warning("MemoryRuntime not initialized, skipping memory injection")
            return

        pantheon_dir = str(get_settings().pantheon_dir)
        guidance = MEMORY_GUIDANCE.replace(".pantheon/", f"{pantheon_dir}/")
        section = f"\n\n{guidance}"

        if self.runtime.config.get("static_index_enabled", False):
            index = self.runtime.load_bootstrap_memory()
            if index:
                section += f"\n\n### Current Memory Index\n\n{index}"

        agents = getattr(team, "team_agents", None)
        if not isinstance(agents, list):
            agents = team.agents if isinstance(team.agents, list) else list(team.agents.values())
        for agent in agents:
            if hasattr(agent, "instructions") and agent.instructions:
                agent.instructions += section
                logger.debug(f"Injected memory guidance into agent '{agent.name}'")

    async def on_run_start(
        self, team: "PantheonTeam", user_input: Any, context: dict
    ) -> Any | None:
        """Retrieve relevant memories and append to user input.

        Supports str and list[dict] (room.py frontend format).
        Returns modified input or None if nothing to inject.
        """
        if not self.runtime.is_initialized:
            return None

        query = _extract_query(user_input)
        if not query:
            return None

        memory = context.get("memory")
        session_id = getattr(memory, "id", "default") if memory else "default"

        try:
            results = await self.runtime.retrieve_relevant(query=query, session_id=session_id)
            if not results:
                return None
            inject_mode = self.runtime.config.get("inject_mode", "index")
            base_dir = get_settings().pantheon_dir if inject_mode == "index" else None
            memory_context = _format_memory_context(results, inject_mode, base_dir)
            logger.debug(f"Retrieved {len(results)} relevant memories (mode={inject_mode})")
        except Exception as e:
            logger.warning(f"Memory retrieval failed: {e}")
            return None

        return _append_to_user_input(user_input, memory_context)

    async def on_run_end(
        self, team: "PantheonTeam", result: dict
    ) -> None:
        """Post-run: fire background tasks for memory extraction, session note,
        and dream consolidation. All tasks are non-blocking — the chat loop
        returns immediately while these run in the background.

        Sub-agent runs (identified by "question" key in result) are skipped —
        their results are already captured as tool call/result pairs in the
        main agent's conversation, which gets processed on the main agent's
        on_run_end.
        """
        if not self.runtime.is_initialized:
            return

        # Sub-agent delegation results have a "question" key; skip them
        if result.get("question") is not None:
            return

        session_id = result.get("chat_id") or "default"
        messages = result.get("messages", [])
        if not messages:
            return

        memory = result.get("memory")
        all_messages = memory._messages if memory and hasattr(memory, "_messages") else messages

        # Phase 2B: Auto-extract durable memories — non-blocking.
        # MemoryExtractor has _in_progress + _pending guards; safe to fire every turn.
        self._fire(self.runtime.maybe_extract_memories(session_id, all_messages))

        # Phase 2A: Update session memory — non-blocking.
        self._fire(self._update_session_note(session_id, all_messages, memory))

        # Dream gate check — non-blocking. DreamGate has its own file-lock + time gate.
        self.runtime.increment_session()
        self._fire(self._run_dream())

    def _fire(self, coro) -> None:
        """Schedule a coroutine as a background task, keeping a strong reference."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _update_session_note(
        self, session_id: str, all_messages: list, memory: Any
    ) -> None:
        try:
            context_tokens = 0
            for msg in reversed(all_messages):
                if msg.get("role") == "assistant" and "_metadata" in msg:
                    context_tokens = msg["_metadata"].get("total_tokens", 0)
                    break
            if not context_tokens:
                context_tokens = len(str(all_messages)) // 4
            fp = memory.file_path if memory else None
            jsonl_path = str(fp) if fp else ""
            await self.runtime.maybe_update_session_note(
                session_id, all_messages, context_tokens, jsonl_path=jsonl_path
            )
        except Exception as e:
            logger.debug(f"Session note update error: {e}")

    async def _run_dream(self) -> None:
        try:
            await self.runtime.maybe_run_dream()
        except Exception as e:
            logger.error(f"Dream error in on_run_end: {e}")

    async def pre_compression(
        self, team: "PantheonTeam", session_id: str, messages: list[dict]
    ) -> "str | CompactHint | None":
        """Pre-compression hook: flush durable memories and prepare session note compact.

        1. Flush important conversation content to durable memory (str result logged).
        2. Force-update session note so it reflects current state.
        3. Return CompactHint if session note has usable content, else flush str.
        """
        from pantheon.team.plugin import CompactHint

        flush_content = await self.pre_compression_flush(session_id, messages)

        if not self.runtime.is_initialized:
            return flush_content

        # Force-update session note to current state before compact
        await self.runtime.force_update_session_note(session_id, messages)

        boundary = self.runtime.get_session_note_boundary(session_id, messages)
        if boundary and boundary > 0 and not self.runtime.is_session_note_empty(session_id):
            summary = self.runtime.get_session_note_for_compact(session_id)
            if summary:
                return CompactHint(summary=summary, boundary=boundary)

        return flush_content

    async def pre_compression_flush(
        self, session_id: str, messages: list[dict]
    ) -> str | None:
        """Flush important conversation content to durable memory before compression."""
        if not self.runtime.is_initialized:
            return None
        return await self.runtime.flush_before_compaction(session_id, messages)


# ── Singleton runtime ──

_memory_runtime = None


def _create_memory_plugin(config: dict, settings) -> MemorySystemPlugin:
    """Factory function for plugin registry."""
    global _memory_runtime
    if _memory_runtime is None:
        from .config import resolve_pantheon_dir, resolve_runtime_dir, get_memory_system_config
        from .runtime import MemoryRuntime

        _memory_runtime = MemoryRuntime(get_memory_system_config(settings))
        _memory_runtime.initialize(
            resolve_pantheon_dir(settings),
            resolve_runtime_dir(settings),
        )
    return MemorySystemPlugin(_memory_runtime)


# Register with plugin registry
from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(PluginDef(
    name="memory_system",
    config_key="memory_system",
    enabled_key="enabled",
    factory=_create_memory_plugin,
    priority=50,
))
