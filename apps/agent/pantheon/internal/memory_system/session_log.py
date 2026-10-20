"""
Session log manager — Phase 1 minimal implementation.

Maintains per-session log files that capture delegation summaries,
flush excerpts, and stage-level context for compaction recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class SessionLogManager:
    """Phase 1 minimal session log management.

    Each session gets an append-only .md file in the logs directory.
    """

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def append(self, session_id: str, content: str) -> None:
        """Append a timestamped entry to the session log."""
        path = self._log_path(session_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"- {timestamp} {content}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)

    def read(self, session_id: str) -> str:
        """Read the full session log content."""
        path = self._log_path(session_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def exists(self, session_id: str) -> bool:
        """Check if a session log exists."""
        return self._log_path(session_id).exists()

    def _log_path(self, session_id: str) -> Path:
        safe_id = self._safe_filename(session_id)
        return self.logs_dir / f"{safe_id}.md"

    @staticmethod
    def _safe_filename(session_id: str) -> str:
        """Sanitize session_id for use as filename."""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:80]
