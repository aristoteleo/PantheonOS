"""
Session Note Extractor — continuously updated session notes for compact shortcut.

Maintains a per-session Markdown summary that tracks current task state,
files, workflow, errors, and learnings. Directly integrates with compression
to enable zero-LLM-call compaction (Session Note Compact).

Inspired by Claude Code's SessionMemory system.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger

from .prompts import SESSION_MEMORY_UPDATE_PROMPT, SESSION_MEMORY_TEMPLATE


@dataclass
class _SessionState:
    """Per-session mutable state for extraction tracking."""

    initialized: bool = False
    tokens_at_last_extraction: int = 0
    tool_calls_since_last: int = 0
    last_message_index: int = 0
    extraction_in_progress: bool = False
    extraction_started_at: float = 0.0
    jsonl_path: str = ""  # path to raw conversation log, set on first update


class SessionNoteExtractor:
    """Continuously updated session notes — enables compact shortcut.

    Thresholds (aligned with Claude Code):
    - Init: 10,000 tokens context before first extraction
    - Update: 5,000 tokens growth OR 3 tool calls since last extraction
    - Budget: 12,000 tokens total session note
    """

    INIT_TOKEN_THRESHOLD = 10_000
    UPDATE_TOKEN_THRESHOLD = 5_000
    TOOL_CALL_THRESHOLD = 3
    MAX_TOTAL_TOKENS = 12_000
    EXTRACTION_TIMEOUT = 15.0

    def __init__(self, runtime_dir: Path, model: str, config: dict | None = None):
        self.notes_dir = runtime_dir / "session-notes"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self._states: dict[str, _SessionState] = {}
        cfg = config or {}
        self.INIT_TOKEN_THRESHOLD = cfg.get("session_note_init_tokens", 10_000)
        self.UPDATE_TOKEN_THRESHOLD = cfg.get("session_note_update_tokens", 5_000)
        self.TOOL_CALL_THRESHOLD = cfg.get("session_note_tool_calls", 3)

    def _state(self, session_id: str) -> _SessionState:
        if session_id not in self._states:
            self._states[session_id] = _SessionState()
        return self._states[session_id]

    # ── Public API ──

    async def maybe_update(
        self,
        session_id: str,
        messages: list[dict],
        context_tokens: int,
        jsonl_path: str = "",
    ) -> bool:
        """Check thresholds and update session note if needed.

        Called in on_run_end. Returns True if extraction ran.
        """
        state = self._state(session_id)
        if jsonl_path and not state.jsonl_path:
            state.jsonl_path = jsonl_path

        # Init gate
        if not state.initialized:
            if context_tokens >= self.INIT_TOKEN_THRESHOLD:
                state.initialized = True
                logger.debug(f"Session memory initialized for {session_id} at {context_tokens} tokens")
            else:
                return False

        # Token growth gate
        token_growth = context_tokens - state.tokens_at_last_extraction
        has_met_token_threshold = token_growth >= self.UPDATE_TOKEN_THRESHOLD

        if not has_met_token_threshold:
            return False

        # Tool call gate
        tool_calls = self._count_tool_calls_since(messages, state.last_message_index)
        has_met_tool_threshold = tool_calls >= self.TOOL_CALL_THRESHOLD
        last_turn_has_tools = self._last_turn_has_tools(messages)

        should_extract = (
            has_met_token_threshold
            and (has_met_tool_threshold or not last_turn_has_tools)
        )

        if not should_extract:
            return False

        # Execute extraction
        state.extraction_in_progress = True
        state.extraction_started_at = time.time()
        try:
            await self._extract(session_id, messages)
            state.tokens_at_last_extraction = context_tokens
            state.tool_calls_since_last = 0
            state.last_message_index = len(messages)
            return True
        except Exception as e:
            logger.warning(f"Session note extraction failed: {e}")
            return False
        finally:
            state.extraction_in_progress = False

    def read(self, session_id: str) -> str:
        """Read session note content. Used by compact shortcut."""
        path = self._note_path(session_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def is_empty_template(self, session_id: str) -> bool:
        """Check if session note is only the template (no real content)."""
        content = self.read(session_id)
        if not content:
            return True
        # Strip template headers and check for real content
        lines = [l.strip() for l in content.split("\n")
                 if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("_")]
        return len(lines) == 0

    def get_last_summarized_index(
        self, session_id: str, messages: list[dict]
    ) -> int | None:
        """Return the message index up to which session note has summarized.

        Used by compact to determine which messages to keep.
        """
        state = self._state(session_id)
        if not state.initialized or state.last_message_index == 0:
            return None
        if state.last_message_index > len(messages):
            return None
        return state.last_message_index

    async def wait_for_extraction(self, session_id: str) -> None:
        """Wait for in-flight extraction to complete. Called before compact."""
        state = self._state(session_id)
        if not state.extraction_in_progress:
            return

        deadline = time.time() + self.EXTRACTION_TIMEOUT
        while state.extraction_in_progress and time.time() < deadline:
            await asyncio.sleep(0.5)

        if state.extraction_in_progress:
            # Timed out — force-clear the flag so future operations aren't blocked
            state.extraction_in_progress = False
            logger.warning(f"Session note extraction timed out for {session_id}, clearing flag")

    # ── Internal ──

    async def _extract(self, session_id: str, messages: list[dict]) -> None:
        """Run LLM to update session note."""
        from pantheon.utils.llm import acompletion

        current_notes = self.read(session_id)
        if not current_notes:
            current_notes = SESSION_MEMORY_TEMPLATE

        state = self._state(session_id)
        new_messages = messages[state.last_message_index:]
        formatted = self._format_messages(new_messages)

        prompt = SESSION_MEMORY_UPDATE_PROMPT.format(
            current_notes=current_notes,
            new_messages=formatted,
        )

        response = await acompletion(
            model=str(self.model),
            messages=[{"role": "user", "content": prompt}],
            model_params={"temperature": 0.0, "max_tokens": self.MAX_TOTAL_TOKENS},
        )
        updated = response.choices[0].message.content or ""
        if updated.strip():
            self._write(session_id, updated)
            logger.info(f"Session note updated for {session_id}")

    def _write(self, session_id: str, content: str) -> None:
        """Write session note, appending/updating the Metadata section."""
        state = self._state(session_id)
        # Strip any existing Metadata section before rewriting
        meta_marker = "\n# Metadata\n"
        if meta_marker in content:
            content = content[:content.index(meta_marker)]
        # Append metadata block (system-managed, LLM must not modify)
        from datetime import datetime
        meta_lines = [
            "",
            "# Metadata",
            f"- session_id: {session_id}",
            f"- updated_at: {datetime.now().isoformat(timespec='seconds')}",
        ]
        if state.jsonl_path:
            meta_lines.append(f"- jsonl_path: {state.jsonl_path}")
        content = content.rstrip() + "\n" + "\n".join(meta_lines) + "\n"
        path = self._note_path(session_id)
        path.write_text(content, encoding="utf-8")

    def _note_path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:80]
        return self.notes_dir / f"{safe}.md"

    def note_path(self, session_id: str) -> Path:
        """Return the path to the session note file (public API)."""
        return self._note_path(session_id)

    @staticmethod
    def _count_tool_calls_since(messages: list[dict], since_index: int) -> int:
        count = 0
        for msg in messages[since_index:]:
            if msg.get("role") == "assistant":
                count += len(msg.get("tool_calls") or [])
        return count

    @staticmethod
    def _last_turn_has_tools(messages: list[dict]) -> bool:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                return bool(msg.get("tool_calls"))
        return False

    @staticmethod
    def _format_messages(messages: list[dict], max_chars: int = 10000) -> str:
        lines: list[str] = []
        total = 0
        for msg in messages:
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
        return "\n".join(lines)
