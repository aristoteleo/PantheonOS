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
    # Pending state: set when a call arrives while extraction is in-flight.
    # Stores the latest messages/tokens so the drain pass uses fresh data.
    pending_messages: list | None = None
    pending_tokens: int = 0


class SessionNoteExtractor:
    """Continuously updated session notes — enables compact shortcut.

    Thresholds (aligned with Claude Code):
    - Init: 10,000 tokens context before first extraction
    - Update: 5,000 tokens growth OR 3 tool calls since last extraction
    - Budget: 12,000 tokens total session note

    Concurrency model (asyncio single-threaded):
    - extraction_in_progress is set synchronously before the first await, so
      the check+set pair is atomic within the event loop — no two tasks can
      both pass the guard.
    - When a call arrives while extraction is in-flight, pending_messages /
      pending_tokens are updated to the latest values.  After the in-flight
      extraction finishes, a drain pass runs immediately so that messages
      accumulated during the LLM call are not silently dropped.
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

        # Concurrency guard — check+set is atomic (no await between them).
        # If already running, record the latest state for a drain pass later.
        if state.extraction_in_progress:
            state.pending_messages = messages
            state.pending_tokens = context_tokens
            logger.debug(f"Session note deferred (in-progress): {session_id}")
            return False

        state.extraction_in_progress = True
        state.extraction_started_at = time.time()
        state.pending_messages = None
        state.pending_tokens = 0
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
            # Drain pass: if new messages arrived while we were running, process them now.
            if state.pending_messages is not None:
                pending_msgs = state.pending_messages
                pending_tokens = state.pending_tokens
                state.pending_messages = None
                state.pending_tokens = 0
                logger.debug(f"Session note drain pass for {session_id}")
                await self.maybe_update(session_id, pending_msgs, pending_tokens, jsonl_path)

    async def force_update(self, session_id: str, messages: list[dict]) -> bool:
        """Force a session note update, bypassing all thresholds.

        Called before compression to ensure the note reflects current state.
        """
        state = self._state(session_id)
        if state.extraction_in_progress:
            await self.wait_for_extraction(session_id)
            return False  # in-flight extraction just finished, use its result

        state.extraction_in_progress = True
        state.extraction_started_at = time.time()
        try:
            await self._extract(session_id, messages)
            state.initialized = True
            state.last_message_index = len(messages)
            return True
        except Exception as e:
            logger.warning(f"Session note force update failed: {e}")
            return False
        finally:
            state.extraction_in_progress = False

    def read(self, session_id: str) -> str:
        """Read session note content (without frontmatter). Used by compact shortcut."""
        import frontmatter

        path = self._note_path(session_id)
        if not path.exists():
            return ""

        try:
            # Try to parse as frontmatter file
            post = frontmatter.load(str(path))
            return post.content
        except Exception:
            # Fallback: read as plain text (old format)
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
        """Run LLM to update session note with YAML frontmatter."""
        from datetime import datetime
        from pantheon.utils.llm import acompletion

        current_notes = self.read(session_id)
        if not current_notes:
            current_notes = SESSION_MEMORY_TEMPLATE

        state = self._state(session_id)
        new_messages = messages[state.last_message_index:]
        formatted = self._format_messages(new_messages)

        prompt = SESSION_MEMORY_UPDATE_PROMPT.format(
            session_id=session_id,
            current_timestamp=datetime.now().isoformat(),
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
            try:
                # Parse LLM response with frontmatter
                frontmatter_dict, content = self._parse_response(updated, session_id, len(messages))
                self._write(session_id, frontmatter_dict, content)
                logger.info(f"Session note updated for {session_id}")
            except ValueError as e:
                # Fallback: LLM format error, use system-generated frontmatter
                logger.warning(f"Session note frontmatter parse failed: {e}, using fallback")
                title = self._extract_title(updated)
                summary = self._extract_summary(updated)
                frontmatter_dict = {
                    "title": title,
                    "summary": summary,
                    "type": "session_note",
                    "session_id": session_id,
                    "updated": datetime.now().isoformat(),
                    "last_message_index": len(messages),
                }
                self._write(session_id, frontmatter_dict, updated)
                logger.info(f"Session note updated for {session_id} (fallback)")

    def _parse_response(self, response: str, session_id: str, message_count: int) -> tuple[dict, str]:
        """Parse LLM response with YAML frontmatter.

        Returns (frontmatter_dict, content).
        Raises ValueError if format is invalid.
        """
        import re
        import yaml
        from datetime import datetime

        if not response.startswith("---"):
            raise ValueError("Missing frontmatter")

        # Find closing ---
        match = re.search(r'^---\s*\n(.*?)\n---\s*\n', response, re.DOTALL | re.MULTILINE)
        if not match:
            raise ValueError("Invalid frontmatter format")

        yaml_str = match.group(1)
        content = response[match.end():]

        try:
            frontmatter = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML parse error: {e}")

        if not isinstance(frontmatter, dict):
            raise ValueError("Frontmatter is not a dict")

        # Validate required fields
        if not frontmatter.get("title"):
            raise ValueError("Missing 'title' in frontmatter")
        if not frontmatter.get("summary"):
            raise ValueError("Missing 'summary' in frontmatter")

        # System-managed fields (override LLM values)
        frontmatter["type"] = "session_note"
        frontmatter["session_id"] = session_id
        frontmatter["updated"] = datetime.now().isoformat()
        frontmatter["last_message_index"] = message_count

        return frontmatter, content

    def _extract_title(self, content: str) -> str:
        """Fallback: extract title from first heading."""
        import re
        from datetime import datetime

        lines = content.split('\n')
        for line in lines:
            if line.startswith('##'):
                title = line.strip('#').strip()
                return title[:100] if title else f"Session {datetime.now().strftime('%Y-%m-%d')}"
        return f"Session {datetime.now().strftime('%Y-%m-%d')}"

    def _extract_summary(self, content: str) -> str:
        """Fallback: extract summary from Task State section."""
        import re

        match = re.search(r'## Task State\s*\n(.*?)(?:\n##|\Z)', content, re.DOTALL)
        if match:
            text = match.group(1).strip()
            # Remove markdown formatting
            text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # Remove links
            text = re.sub(r'[*_`]', '', text)  # Remove emphasis
            return text[:200] if text else "Session in progress"
        return "Session in progress"

    def _write(self, session_id: str, frontmatter: dict, content: str) -> None:
        """Write session note with YAML frontmatter."""
        import yaml

        path = self._note_path(session_id)

        # Generate YAML frontmatter
        yaml_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)

        # Write file
        full_content = f"---\n{yaml_str}---\n\n{content}"
        path.write_text(full_content, encoding="utf-8")

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
