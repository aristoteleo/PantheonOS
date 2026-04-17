"""
Extract Memories — automatic per-turn long-term memory extraction.

After each conversation turn, checks if there's information worth preserving
as durable memory. Mutually exclusive with agent's manual memory_write.

Inspired by Claude Code's extractMemories system.
"""

from __future__ import annotations

from pantheon.utils.log import logger

from .prompts import EXTRACT_MEMORIES_SYSTEM, EXTRACT_MEMORIES_USER
from .store import MemoryStore


class MemoryExtractor:
    """Automatic per-turn extraction of long-term memories.

    After each turn, if the agent didn't manually write memories,
    this extracts durable memories from new conversation content.

    Concurrency model:
    - _in_progress guards against concurrent extraction for the same session.
    - _pending is set when a call arrives while extraction is running; the
      caller that clears _in_progress is responsible for re-running so that
      messages accumulated during the in-flight extraction are not lost.
    """

    MAX_TURNS = 5  # Budget for extraction LLM (aligned with Claude Code)
    MAX_RETRIES = 3  # Max consecutive failures before skipping a segment

    def __init__(self, store: MemoryStore, model: str):
        self.store = store
        self.model = model
        self._in_progress: dict[str, bool] = {}
        self._pending: dict[str, bool] = {}       # new messages arrived while in-flight
        self._last_cursor: dict[str, int] = {}
        self._retry_count: dict[str, int] = {}  # session_id → consecutive failures

    async def maybe_extract(
        self,
        session_id: str,
        messages: list[dict],
    ) -> list[str] | None:
        """Check conditions and extract memories if appropriate.

        Returns list of written filenames, or None if skipped.
        """
        # Mutual exclusion: skip if already in progress, but mark pending so
        # the in-flight task will re-run after it finishes.
        if self._in_progress.get(session_id):
            self._pending[session_id] = True
            logger.debug(f"Extract memories deferred (in-progress): {session_id}")
            return None

        # Mutual exclusion: skip if agent already wrote memory this turn
        if self._has_agent_memory_writes(messages, session_id):
            self._advance_cursor(session_id, messages)
            logger.debug(f"Extract memories skipped: agent already wrote memory in {session_id}")
            return None

        # Count new messages since last extraction
        cursor = self._last_cursor.get(session_id, 0)
        new_count = self._count_visible_messages(messages, cursor)
        if new_count == 0:
            return None

        self._in_progress[session_id] = True
        self._pending[session_id] = False
        all_results: list[str] = []
        try:
            # Snapshot the end position at the start of this run so cursor
            # advances only to what was actually processed.
            end_pos = len(messages)
            result = await self._extract(session_id, messages, new_count)
            self._last_cursor[session_id] = end_pos   # advance to snapshot, not live len
            self._retry_count[session_id] = 0
            if result:
                all_results.extend(result)
        except Exception as e:
            # Trailing run: don't advance cursor on failure → retry next run
            retries = self._retry_count.get(session_id, 0) + 1
            self._retry_count[session_id] = retries
            if retries >= self.MAX_RETRIES:
                logger.warning(
                    f"Memory extraction failed {retries} times for {session_id}, "
                    f"skipping segment: {e}"
                )
                self._advance_cursor(session_id, messages)
                self._retry_count[session_id] = 0
            else:
                logger.warning(f"Memory extraction failed for {session_id} (retry {retries}/{self.MAX_RETRIES}): {e}")
            return None
        finally:
            self._in_progress[session_id] = False

        # If new messages arrived while we were running, process them now.
        # This is the _pending drain loop — at most one extra pass per trigger.
        if self._pending.get(session_id):
            self._pending[session_id] = False
            extra = await self.maybe_extract(session_id, messages)
            if extra:
                all_results.extend(extra)

        return all_results or None

    async def _extract(
        self, session_id: str, messages: list[dict], new_count: int
    ) -> list[str]:
        """Execute extraction via multi-turn Agent with file_manager."""
        from pantheon.internal.background_agent import create_background_agent

        # Pre-inject memory manifest
        headers = self.store.scan_headers()
        manifest = "\n".join(
            f"- [{h.type.value}] {h.filename}: {h.summary}" for h in headers
        ) if headers else "(no existing memories)"

        # Format recent messages
        cursor = self._last_cursor.get(session_id, 0)
        recent = messages[cursor:]
        formatted = self._format_messages(recent)

        user_prompt = EXTRACT_MEMORIES_USER.format(
            new_message_count=new_count,
            existing_memories=manifest,
            messages=formatted,
        )

        # Workspace is the .pantheon/ parent directory
        workspace = self.store.durable_dir.parent.parent

        agent = await create_background_agent(
            name="memory-extractor",
            instructions=EXTRACT_MEMORIES_SYSTEM,
            model=str(self.model),
            workspace_path=workspace,
        )

        await agent.run(user_prompt, use_memory=False)
        # Trailing run: if agent.run raises, caller won't advance cursor

        # Count written files by scanning store
        headers_after = self.store.scan_headers()
        new_files = [
            h.filename for h in headers_after
            if h.filename not in {h2.filename for h2 in headers}
        ]

        if new_files:
            logger.info(f"Extracted {len(new_files)} memories for {session_id}: {new_files}")

        return new_files

    def _has_agent_memory_writes(self, messages: list[dict], session_id: str) -> bool:
        """Check if the agent wrote to memory-store/ this turn via file tools."""
        cursor = self._last_cursor.get(session_id, 0)
        for msg in messages[cursor:]:
            if msg.get("role") == "assistant":
                for tc in (msg.get("tool_calls") or []):
                    func = tc.get("function", {}) if isinstance(tc, dict) else {}
                    fn_name = func.get("name", "")
                    # Detect file_manager write/edit operations targeting memory-store
                    if fn_name in ("file_write", "file_edit", "file_create"):
                        args_str = func.get("arguments", "")
                        if "memory-store" in args_str or "memory_store" in args_str:
                            return True
        return False

    def _advance_cursor(self, session_id: str, messages: list[dict]) -> None:
        self._last_cursor[session_id] = len(messages)
    @staticmethod
    def _count_visible_messages(messages: list[dict], since: int) -> int:
        """Count user/assistant messages (not tool results or system)."""
        count = 0
        for msg in messages[since:]:
            if msg.get("role") in ("user", "assistant"):
                count += 1
        return count

    @staticmethod
    def _format_messages(messages: list[dict], max_chars: int = 8000) -> str:
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
