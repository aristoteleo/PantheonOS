"""
File-based memory storage with MEMORY.md index management.

Durable memory files live at <workspace>/.pantheon/memory-store/.
MEMORY.md index lives at <workspace>/.pantheon/MEMORY.md (separate from durable dir).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from pantheon.utils.log import logger

from .types import (
    MemoryEntry,
    MemoryHeader,
    MemoryType,
    make_filename,
    parse_frontmatter_only,
    parse_memory_file,
    write_memory_file,
)


class MemoryStore:
    """File-based memory storage with separated durable dir and index path."""

    MAX_INDEX_LINES = 200
    MAX_INDEX_BYTES = 25_000
    MAX_MEMORY_FILES = 200

    def __init__(self, durable_dir: Path, index_path: Path):
        """Initialize store with separated paths.

        Args:
            durable_dir: <workspace>/memory/ — durable memory files
            index_path: <workspace>/MEMORY.md — index at workspace root
        """
        self.durable_dir = durable_dir
        self.index_path = index_path
        self.durable_dir.mkdir(parents=True, exist_ok=True)

    # ── Scanning ──

    def scan_headers(self) -> list[MemoryHeader]:
        """Scan all .md files for frontmatter metadata.

        Only reads first 30 lines per file (no full content).
        Returns sorted by mtime descending, capped at MAX_MEMORY_FILES.
        """
        headers: list[MemoryHeader] = []

        for md_path in self._iter_memory_files():
            try:
                meta = parse_frontmatter_only(md_path)
                stat = md_path.stat()
                title = meta.get("title") or meta.get("name", md_path.stem)
                summary = meta.get("summary") or meta.get("description", "")
                entry_id = meta.get("id", "")
                headers.append(
                    MemoryHeader(
                        filename=str(md_path.relative_to(self.durable_dir)),
                        filepath=md_path,
                        mtime=stat.st_mtime,
                        summary=summary,
                        type=MemoryType.from_str(meta.get("type", "workflow")),
                        title=title,
                        entry_id=entry_id,
                    )
                )
            except (OSError, ValueError) as e:
                logger.debug(f"Skipping {md_path}: {e}")

        headers.sort(key=lambda h: h.mtime, reverse=True)
        return headers[: self.MAX_MEMORY_FILES]

    def _iter_memory_files(self):
        """Iterate .md files in durable_dir, excluding logs/."""
        for root, dirs, files in os.walk(self.durable_dir):
            rel = Path(root).relative_to(self.durable_dir)
            if str(rel).startswith("logs"):
                continue
            for fname in files:
                if fname.endswith(".md"):
                    yield Path(root) / fname

    # ── Index (MEMORY.md at workspace root) ──

    def read_index(self) -> str:
        """Read MEMORY.md content, truncated to limits."""
        if not self.index_path.exists():
            return ""
        content = self.index_path.read_text(encoding="utf-8")
        return self._truncate_index(content)

    def write_index(self, content: str) -> None:
        """Write MEMORY.md with truncation enforcement."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        truncated = self._truncate_index(content)
        self.index_path.write_text(truncated, encoding="utf-8")

    def _truncate_index(self, content: str) -> str:
        """Enforce 200 lines / 25KB limits on index content."""
        lines = content.split("\n")
        if len(lines) > self.MAX_INDEX_LINES:
            lines = lines[: self.MAX_INDEX_LINES]
            logger.debug(f"MEMORY.md truncated to {self.MAX_INDEX_LINES} lines")

        result = "\n".join(lines)
        if len(result.encode("utf-8")) > self.MAX_INDEX_BYTES:
            while len(result.encode("utf-8")) > self.MAX_INDEX_BYTES and lines:
                lines.pop()
                result = "\n".join(lines)
            logger.debug(f"MEMORY.md truncated to {self.MAX_INDEX_BYTES} bytes")

        return result

    def _add_index_entry(self, entry: MemoryEntry, filename: str) -> None:
        """Append a one-line pointer to MEMORY.md."""
        line = f"- [{entry.title}](memory-store/{filename}) — {entry.summary}"
        if len(line) > 150:
            line = line[:147] + "..."

        if self.index_path.exists():
            current = self.index_path.read_text(encoding="utf-8").rstrip()
            new_content = f"{current}\n{line}\n" if current else f"{line}\n"
        else:
            new_content = f"{line}\n"

        self.write_index(new_content)

    def _remove_index_entry(self, filename: str) -> None:
        """Remove a pointer from MEMORY.md by filename."""
        if not self.index_path.exists():
            return
        lines = self.index_path.read_text(encoding="utf-8").split("\n")
        filtered = [l for l in lines if filename not in l]
        self.write_index("\n".join(filtered))

    # ── CRUD ──

    def add_memory(self, entry: MemoryEntry) -> Path:
        """Two-step save: write file + update index."""
        filename = make_filename(entry.title, entry.type)
        path = self.durable_dir / filename

        counter = 1
        while path.exists():
            stem = filename.rsplit(".", 1)[0]
            path = self.durable_dir / f"{stem}_{counter}.md"
            filename = path.name
            counter += 1

        write_memory_file(path, entry)
        self._add_index_entry(entry, filename)
        logger.info(f"Memory saved: {filename}")
        return path

    def update_memory(self, path: Path, entry: MemoryEntry) -> None:
        """Update an existing memory file."""
        if not path.exists():
            raise FileNotFoundError(f"Memory file not found: {path}")
        write_memory_file(path, entry)
        logger.debug(f"Memory updated: {path.name}")

    def delete_memory(self, path: Path) -> None:
        """Delete a memory file and its index entry."""
        filename = path.name
        if path.exists():
            path.unlink()
        self._remove_index_entry(filename)
        logger.info(f"Memory deleted: {filename}")

    def read_memory(self, path: Path) -> MemoryEntry:
        """Read a memory file fully."""
        if not path.exists():
            raise FileNotFoundError(f"Memory file not found: {path}")
        return parse_memory_file(path)

    def find_memory_by_name(self, filename: str) -> Path | None:
        """Find a memory file by filename."""
        path = (self.durable_dir / filename).resolve()
        if not path.is_relative_to(self.durable_dir.resolve()):
            return None  # Path traversal attempt
        if path.exists() and path.suffix == ".md":
            return path
        for md_path in self._iter_memory_files():
            if md_path.name == filename:
                return md_path
        return None

    # ── Daily Logs ──

    def append_daily_log(self, content: str, date: datetime | None = None) -> Path:
        """Append content to today's daily log: logs/YYYY/MM/YYYY-MM-DD.md"""
        if date is None:
            date = datetime.now(timezone.utc)

        log_dir = self.durable_dir / "logs" / date.strftime("%Y") / date.strftime("%m")
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / f"{date.strftime('%Y-%m-%d')}.md"
        timestamp = date.strftime("%H:%M")

        if log_path.exists():
            existing = log_path.read_text(encoding="utf-8").rstrip()
            new_content = f"{existing}\n- {timestamp} {content}\n"
        else:
            header = f"# {date.strftime('%Y-%m-%d')}\n\n"
            new_content = f"{header}- {timestamp} {content}\n"

        log_path.write_text(new_content, encoding="utf-8")
        return log_path

    def list_daily_logs(self, since: datetime | None = None) -> list[Path]:
        """List daily log files, optionally filtered by date."""
        logs_dir = self.durable_dir / "logs"
        if not logs_dir.exists():
            return []

        logs: list[Path] = []
        for root, _, files in os.walk(logs_dir):
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                path = Path(root) / fname
                if since is not None:
                    try:
                        log_date = datetime.strptime(fname.replace(".md", ""), "%Y-%m-%d")
                        log_date = log_date.replace(tzinfo=timezone.utc)
                        if log_date < since:
                            continue
                    except ValueError:
                        continue
                logs.append(path)

        return sorted(logs)
