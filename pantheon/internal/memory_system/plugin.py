"""
MemorySystemPlugin — PantheonTeam adapter for the shared MemoryRuntime.

This plugin does NOT own memory logic; it delegates everything to MemoryRuntime.
ChatRoom uses the same runtime through ChatRoomMemoryAdapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pantheon.settings import get_settings
from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger

from .prompts import MEMORY_GUIDANCE

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam

    from .runtime import MemoryRuntime


class MemorySystemPlugin(TeamPlugin):
    """PantheonTeam adapter — delegates all logic to MemoryRuntime."""

    def __init__(self, runtime: "MemoryRuntime"):
        self.runtime = runtime

    async def on_team_created(self, team: "PantheonTeam") -> None:
        """Inject MEMORY.md into agent system prompt."""
        if not self.runtime.is_initialized:
            logger.warning("MemoryRuntime not initialized, skipping memory injection")
            return

        index = self.runtime.load_bootstrap_memory()
        pantheon_dir = str(get_settings().pantheon_dir)
        guidance = MEMORY_GUIDANCE.replace(".pantheon/", f"{pantheon_dir}/")
        section = f"\n\n{guidance}"
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
    ) -> None:
        """Retrieve relevant memories and inject into context."""
        if not self.runtime.is_initialized:
            return

        query = str(user_input) if user_input else ""
        if not query:
            return

        memory = context.get("memory")
        session_id = getattr(memory, "id", "default") if memory else "default"

        try:
            results = await self.runtime.retrieve_relevant(query, session_id)
            if results:
                parts = [
                    f"### {r.entry.title} ({r.age_text})\n{r.content}"
                    for r in results
                ]
                context["memory_context"] = (
                    "\n\n## Relevant Memories\n\n"
                    + "\n\n---\n\n".join(parts)
                )
                logger.debug(f"Retrieved {len(results)} relevant memories")
        except Exception as e:
            logger.warning(f"Memory retrieval failed: {e}")

    async def on_run_end(
        self, team: "PantheonTeam", result: dict
    ) -> None:
        """Post-run: extract memories, update session memory, check dream.

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

        # Phase 2B: Auto-extract durable memories (needs full history for cursor tracking)
        try:
            await self.runtime.maybe_extract_memories(session_id, all_messages)
        except Exception as e:
            logger.debug(f"Memory extraction error: {e}")

        # Phase 2A: Update session memory (for compact shortcut)
        try:
            # Read real token count from last assistant message's _metadata, fallback to char estimate
            context_tokens = 0
            for msg in reversed(all_messages):
                if msg.get("role") == "assistant" and "_metadata" in msg:
                    context_tokens = msg["_metadata"].get("total_tokens", 0)
                    break
            if not context_tokens:
                context_tokens = len(str(all_messages)) // 4
            # Use memory.file_path (public property) — returns .jsonl path for JSONL backend
            fp = memory.file_path if memory else None
            jsonl_path = str(fp) if fp else ""
            await self.runtime.maybe_update_session_note(
                session_id, all_messages, context_tokens, jsonl_path=jsonl_path
            )
        except Exception as e:
            logger.debug(f"Session note update error: {e}")

        # Dream gate check (only main agent increments)
        self.runtime.increment_session()
        try:
            await self.runtime.maybe_run_dream()
        except Exception as e:
            logger.error(f"Dream error in on_run_end: {e}")

    async def pre_compression(
        self, team: "PantheonTeam", session_id: str, messages: list[dict]
    ) -> str | None:
        """Called by CompressionPlugin before compression via hook system."""
        return await self.pre_compression_flush(session_id, messages)

    async def pre_compression_flush(
        self, session_id: str, messages: list[dict]
    ) -> str | None:
        """Called by CompressionPlugin before compression."""
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
