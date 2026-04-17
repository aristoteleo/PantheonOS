"""
Pre-compression flush: extract important info before context compression.

Runs a fast LLM call to identify information worth preserving from the
conversation, then appends it to the daily log file.
"""

from __future__ import annotations

from pantheon.utils.log import logger

from .prompts import FLUSH_SYSTEM, FLUSH_USER
from .store import MemoryStore


class MemoryFlusher:
    """Extracts important information before context compression."""

    NOTHING_MARKER = "[nothing_to_save]"

    def __init__(self, store: MemoryStore, model: str | None = None):
        self.store = store
        self.model = model or "low"

    async def flush(self, messages: list[dict]) -> str | None:
        """Extract important info from messages, append to daily log.

        Returns the extracted content, or None if nothing worth saving.
        """
        if not messages:
            return None

        messages_text = self._format_messages(messages)
        prompt = FLUSH_USER.format(messages_text=messages_text)

        try:
            content = await self._run_llm(prompt)
        except Exception as e:
            logger.warning(f"Memory flush LLM call failed: {e}")
            return None

        if not content or self.NOTHING_MARKER in content:
            return None

        self.store.append_daily_log(content)
        logger.info("Memory flush: saved to daily log")
        return content

    def _format_messages(self, messages: list[dict], max_chars: int = 8000) -> str:
        """Format messages for the flush prompt."""
        lines: list[str] = []
        total = 0
        for msg in reversed(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            line = f"[{role}] {content}"
            total += len(line)
            if total > max_chars:
                break
            lines.append(line)
        lines.reverse()
        return "\n".join(lines)

    async def _run_llm(self, user_prompt: str) -> str:
        """Call LLM for flush extraction."""
        from pantheon.utils.llm import acompletion

        response = await acompletion(
            model=str(self.model),
            messages=[
                {"role": "system", "content": FLUSH_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            model_params={"temperature": 0.0, "max_tokens": 1000},
        )
        return response.choices[0].message.content or ""
