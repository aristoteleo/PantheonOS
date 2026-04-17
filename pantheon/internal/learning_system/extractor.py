"""SkillExtractor — background auto-extraction of skills from conversations."""

from __future__ import annotations

import asyncio
from typing import Any

from pantheon.utils.log import logger

from .store import SkillStore
from .prompts import SKILL_EXTRACTION_SYSTEM, SKILL_EXTRACTION_USER

# Tool name for the consolidated skill_manage tool
_SKILL_MANAGE_TOOL = "skill_manage"
# Actions that count as skill writes
_SKILL_WRITE_ACTIONS = {"create", "update", "patch"}


class SkillExtractor:
    """Auto-extract skills from conversations after N runs.

    Concurrency model:
    - _locks prevents concurrent extraction for the same session.
    - When the lock is held and a new call arrives, _pending is set so the
      waiting call is not silently dropped — the next increment_run will
      re-evaluate the counter and trigger extraction if still warranted.
    - increment_run always increments, regardless of whether extraction is
      running, so the counter is never stalled by an in-flight task.
    """

    def __init__(self, store: SkillStore, model: str, nudge_interval: int = 5):
        self.store = store
        self.model = model
        self.nudge_interval = nudge_interval

        self._run_counter: dict[str, int] = {}  # session_id → run count
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, bool] = {}     # extraction requested while lock held

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def maybe_extract(
        self, session_id: str, messages: list[dict[str, Any]],
        session_note_path: str = "",
    ) -> list[str] | None:
        """Check if extraction should run and execute if so.

        Returns list of created/updated skill names, or None if skipped.
        """
        if not messages:
            logger.info(f"[Learning] Skipping extraction: no messages (session={session_id})")
            return None

        # Gate: counter — checked before acquiring the lock so we don't
        # queue up behind a running extraction unnecessarily.
        counter = self._run_counter.get(session_id, 0)
        logger.info(f"[Learning] Counter check: {counter}/{self.nudge_interval} (session={session_id})")
        if counter < self.nudge_interval:
            return None

        # Gate: agent already wrote skills this session
        if self._has_agent_skill_writes(messages):
            logger.info(f"[Learning] Skipping extraction: agent already wrote skills (session={session_id})")
            self._run_counter[session_id] = 0
            return None

        # Non-blocking lock check — if already running, mark pending and return.
        # increment_run keeps counting, so the next call will re-evaluate.
        lock = self._get_lock(session_id)
        if lock.locked():
            self._pending[session_id] = True
            logger.info(f"[Learning] Extraction deferred (in-progress): {session_id}")
            return None

        logger.info(f"[Learning] Starting skill extraction (session={session_id}, counter={counter})")
        self._pending[session_id] = False
        async with lock:
            try:
                result = await self._extract(messages, session_note_path=session_note_path)
                return result
            except Exception as e:
                logger.warning(f"Skill extraction failed: {e}")
                return None
            finally:
                self._run_counter[session_id] = 0

    async def _extract(
        self, messages: list[dict[str, Any]], session_note_path: str = ""
    ) -> list[str]:
        """Run multi-turn Agent extraction with file_manager."""
        from pantheon.internal.background_agent import create_background_agent

        # Snapshot existing skills before extraction
        headers_before = self.store.scan_headers()
        names_before = {h.name for h in headers_before}

        manifest = self._build_skill_manifest()
        formatted = self._format_messages(messages)

        # Build session context block — pass path so agent reads it via file_manager
        session_context = ""
        if session_note_path:
            session_context = f"\n## Session Context\nsession_note_path: {session_note_path}\n(Read this file via file_manager for structured session context before extracting skills.)\n"

        user_prompt = SKILL_EXTRACTION_USER.format(
            skill_manifest=manifest,
            session_context=session_context,
            messages=formatted,
        )

        # Workspace is the .pantheon/ parent directory
        pantheon_dir = self.store.skills_dir.parent
        workspace = pantheon_dir.parent

        agent = await create_background_agent(
            name="skill-extractor",
            instructions=SKILL_EXTRACTION_SYSTEM,
            model=str(self.model),
            workspace_path=workspace,
        )

        await agent.run(user_prompt, use_memory=False)

        # Detect new/updated skills by comparing before/after
        headers_after = self.store.scan_headers()
        changed = []
        for h in headers_after:
            if h.name not in names_before:
                changed.append(h.name)  # New skill
            else:
                # Check if mtime changed (updated)
                before_h = next((b for b in headers_before if b.name == h.name), None)
                if before_h and h.mtime > before_h.mtime:
                    changed.append(h.name)

        return changed

    def _has_agent_skill_writes(
        self, messages: list[dict[str, Any]]
    ) -> bool:
        """Check if agent has called skill_manage with a write action in recent messages."""
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                if fn.get("name") == _SKILL_MANAGE_TOOL:
                    args_str = fn.get("arguments", "")
                    try:
                        import json
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        if isinstance(args, dict) and args.get("action") in _SKILL_WRITE_ACTIONS:
                            return True
                    except (json.JSONDecodeError, TypeError):
                        # Fallback: stricter substring match
                        for action in _SKILL_WRITE_ACTIONS:
                            if f'"action": "{action}"' in args_str or f'"action":"{action}"' in args_str:
                                return True
        return False

    def _build_skill_manifest(self) -> str:
        """Build a manifest of existing skills for the LLM."""
        headers = self.store.scan_headers()
        if not headers:
            return "(no existing skills)"
        lines = [f"- {h.name}: {h.description}" for h in headers]
        return "\n".join(lines)

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Format messages for the extraction prompt, including tool calls."""
        from pantheon.utils.message_formatter import format_messages_to_text
        result = format_messages_to_text(
            messages[-50:],
            max_arg_length=500,
            max_output_length=1000,
            extract_files=True,
            use_smart_truncate=True,
        )
        return result.text

    def increment_run(self, session_id: str, by: int = 1) -> None:
        """Increment run counter for a session.

        Always increments regardless of whether extraction is currently running,
        so the counter is never stalled by an in-flight task.

        Args:
            by: Amount to increment (default 1). Pass the number of tool calls
                in the run so complex runs reach the threshold faster.
        """
        self._run_counter[session_id] = self._run_counter.get(session_id, 0) + by
        logger.info(f"[Learning] Counter incremented by {by}: {self._run_counter[session_id]}/{self.nudge_interval} (session={session_id})")

    def reset_counter(self, session_id: str) -> None:
        """Reset counter when agent manually uses skill tools."""
        self._run_counter[session_id] = 0
