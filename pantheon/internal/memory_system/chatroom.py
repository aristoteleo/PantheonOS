"""
ChatRoom adapter for the shared MemoryRuntime.

Provides the same memory capabilities as PantheonTeam
through a ChatRoom-friendly interface.
"""

from __future__ import annotations

from typing import Any

from .runtime import MemoryRuntime
from .types import MemoryEntry, MemoryType


class ChatRoomMemoryAdapter:
    """ChatRoom integration — uses the same MemoryRuntime as PantheonTeam."""

    def __init__(self, runtime: MemoryRuntime):
        self.runtime = runtime

    def load_bootstrap_memory(self) -> str:
        """Load MEMORY.md index for system prompt injection."""
        return self.runtime.load_bootstrap_memory()

    async def retrieve_relevant(
        self,
        query: str,
        session_id: str,
        already_shown: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant memories, returned as dicts for ChatRoom."""
        results = await self.runtime.retrieve_relevant(query, session_id, already_shown)
        return [
            {
                "title": r.entry.title,
                "type": r.entry.type.value,
                "age": r.age_text,
                "content": r.content,
            }
            for r in results
        ]

    async def flush_before_compaction(
        self, session_id: str, messages: list[dict]
    ) -> str | None:
        """Pre-compression flush."""
        return await self.runtime.flush_before_compaction(session_id, messages)

    def update_session_log(self, session_id: str, content: str) -> None:
        """Update session log (delegation summary, etc.)."""
        self.runtime.update_session_log(session_id, content)

    async def maybe_run_dream(self) -> dict[str, Any] | None:
        """Try to trigger dream consolidation."""
        result = await self.runtime.maybe_run_dream()
        if result is None:
            return None
        return {"success": result.success, "summary": result.summary}

    def write_memory(
        self,
        content: str,
        title: str,
        memory_type: str = "workflow",
        summary: str = "",
    ) -> dict[str, Any]:
        """Write a durable memory entry."""
        entry = MemoryEntry(
            title=title,
            summary=summary or title,
            type=MemoryType.from_str(memory_type),
            content=content,
        )
        path = self.runtime.write_memory(entry)
        return {"success": True, "path": str(path.name)}
