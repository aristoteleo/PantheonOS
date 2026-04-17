"""
Dream memory consolidation system.

Provides:
- DreamGate: 5-layer gating to decide when to consolidate
- ConsolidationLock: File-based lock preventing concurrent dreams
- DreamConsolidator: 4-phase consolidation via LLM
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from pantheon.utils.log import logger

from .prompts import DREAM_CONSOLIDATION
from .store import MemoryStore


@dataclass
class DreamResult:
    """Result of a dream consolidation."""

    success: bool
    summary: str
    files_updated: int = 0
    files_created: int = 0


class ConsolidationLock:
    """File-based lock for dream consolidation.

    The lock file's mtime serves as the 'last consolidated at' timestamp.
    The file body contains the PID of the holder.
    """

    LOCK_FILE = ".consolidate-lock"
    STALE_TIMEOUT = 3600  # 60 minutes

    def __init__(self, memory_dir: Path):
        self.lock_path = memory_dir / self.LOCK_FILE

    def read_last_consolidated_at(self) -> float:
        """Read the last consolidation timestamp from lock file mtime."""
        if not self.lock_path.exists():
            return 0.0
        return self.lock_path.stat().st_mtime

    def try_acquire(self) -> float | None:
        """Try to acquire the consolidation lock.

        Returns the prior mtime (for rollback) or None if lock is held.
        Uses O_CREAT|O_EXCL for atomic creation when no lock exists,
        and PID + stale check for existing locks.
        """
        prior_mtime = 0.0

        if self.lock_path.exists():
            holder_pid: int | None = None
            try:
                stat = self.lock_path.stat()
                prior_mtime = stat.st_mtime
                raw = self.lock_path.read_text().strip()
                holder_pid = int(raw) if raw else None
            except (OSError, ValueError):
                pass

            # Check if lock is still valid
            if time.time() - prior_mtime < self.STALE_TIMEOUT:
                if holder_pid is not None and self._is_process_alive(holder_pid):
                    return None  # Lock held by live process

            # Stale lock — reclaim by overwriting
            try:
                self.lock_path.write_text(str(os.getpid()))
            except OSError:
                return None
        else:
            # No lock file — atomic create with O_CREAT|O_EXCL
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                try:
                    os.write(fd, str(os.getpid()).encode())
                finally:
                    os.close(fd)
            except FileExistsError:
                return None  # Another process won the race

        return prior_mtime

    def release(self) -> None:
        """Release the lock (update mtime to now = mark consolidation complete)."""
        if self.lock_path.exists():
            now = time.time()
            os.utime(self.lock_path, (now, now))

    def rollback(self, prior_mtime: float) -> None:
        """Rollback lock state on failure."""
        if prior_mtime == 0.0:
            if self.lock_path.exists():
                self.lock_path.unlink()
        else:
            if self.lock_path.exists():
                self.lock_path.write_text("")
                os.utime(self.lock_path, (prior_mtime, prior_mtime))

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process is running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


class DreamGate:
    """5-layer gating system for dream consolidation."""

    SCAN_INTERVAL = 600  # 10 minutes between scans

    def __init__(self, store: MemoryStore, config: dict):
        self.store = store
        self.lock = ConsolidationLock(store.durable_dir)
        self.min_hours: float = config.get("dream_min_hours", 24)
        self.min_sessions: int = config.get("dream_min_sessions", 5)
        self._last_scan_time = 0.0
        self._session_counter = 0  # Approximate session count

    def increment_session(self) -> None:
        """Called at end of each run to track sessions."""
        self._session_counter += 1

    async def should_dream(self, force: bool = False) -> float | None:
        """Check all gates. Returns prior_mtime if should dream, None otherwise."""
        if force:
            return self.lock.read_last_consolidated_at()

        # Gate 1: Time gate
        last_at = self.lock.read_last_consolidated_at()
        hours_since = (time.time() - last_at) / 3600 if last_at else float("inf")
        if hours_since < self.min_hours:
            return None

        # Gate 2: Scan throttle
        now = time.time()
        if now - self._last_scan_time < self.SCAN_INTERVAL:
            return None
        self._last_scan_time = now

        # Gate 3: Session count gate
        if self._session_counter < self.min_sessions:
            return None

        # Gate 4: Lock acquisition
        prior_mtime = self.lock.try_acquire()
        if prior_mtime is None:
            return None

        return prior_mtime


class DreamConsolidator:
    """Executes dream consolidation via multi-turn Agent with file_manager.

    The Agent uses file_manager to read/write memory files directly,
    guided by DREAM_CONSOLIDATION prompt. Same pattern as main agents
    (Claude Code zero-tool model).
    """

    def __init__(self, store: MemoryStore, model: str | None = None):
        self.store = store
        self.model = model or "low"

    async def consolidate(self) -> DreamResult:
        """Execute dream consolidation with Agent-based multi-turn reasoning."""
        from pantheon.internal.background_agent import create_background_agent

        # Snapshot before consolidation
        headers_before = self.store.scan_headers()
        names_before = {h.filename: h.mtime for h in headers_before}

        # Workspace is the .pantheon/ parent directory (file_manager root)
        workspace = self.store.durable_dir.parent.parent

        instructions = DREAM_CONSOLIDATION.format(
            memory_dir=str(self.store.durable_dir),
            max_index_lines=MemoryStore.MAX_INDEX_LINES,
        )

        agent = await create_background_agent(
            name="dream-consolidator",
            instructions=instructions,
            model=str(self.model),
            workspace_path=workspace,
        )

        user_prompt = self._build_prompt()

        try:
            resp = await agent.run(user_prompt, use_memory=False)
            response = getattr(resp, "content", "") or "" if resp else ""
        except Exception as e:
            logger.error(f"Dream consolidation failed: {e}")
            return DreamResult(success=False, summary=f"Error: {e}")

        # Count what the agent did by comparing before/after
        headers_after = self.store.scan_headers()
        names_after = {h.filename: h.mtime for h in headers_after}

        created = sum(1 for f in names_after if f not in names_before)
        updated = sum(
            1 for f, mt in names_after.items()
            if f in names_before and mt > names_before[f]
        )

        summary = response[:500] if response else "Consolidation complete"
        logger.info(f"Dream consolidation complete: created={created}, updated={updated}")

        return DreamResult(
            success=True,
            summary=summary,
            files_created=created,
            files_updated=updated,
        )

    def _build_prompt(self) -> str:
        """Build user prompt with current state overview."""
        index_content = self.store.read_index()
        headers = self.store.scan_headers()
        file_list = "\n".join(f"- {h.filename} [{h.type.value}]: {h.summary}" for h in headers)

        from datetime import datetime, timedelta, timezone
        since = datetime.now(timezone.utc) - timedelta(days=7)
        logs = self.store.list_daily_logs(since=since)
        log_list = "\n".join(f"- {l.name}" for l in logs) if logs else "(no recent logs)"

        return (
            f"## Current State\n\n"
            f"### MEMORY.md index:\n```\n{index_content}\n```\n\n"
            f"### Memory files ({len(headers)} total):\n{file_list}\n\n"
            f"### Recent daily logs:\n{log_list}\n\n"
            f"Read the memory files and daily logs you need to inspect using file_manager, "
            f"then consolidate: merge duplicates, update stale info, delete obsolete entries, "
            f"and update MEMORY.md index. Write changes directly using file_manager."
        )
