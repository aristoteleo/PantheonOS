"""
Memory type system with YAML frontmatter support.

Defines the five-type taxonomy (user/feedback/project/reference/workflow)
and provides frontmatter parsing/writing with Phase 1 canonical schema
and legacy (worktree v0) compatibility.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import frontmatter


class MemoryType(str, Enum):
    """Five-type memory taxonomy + session_note."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"
    WORKFLOW = "workflow"
    SESSION_NOTE = "session_note"

    @classmethod
    def from_str(cls, value: str) -> MemoryType:
        """Parse a string to MemoryType.

        Legacy: 'pattern' maps to WORKFLOW.
        Unknown values default to WORKFLOW.
        """
        v = value.lower().strip()
        if v == "pattern":
            return cls.WORKFLOW
        try:
            return cls(v)
        except ValueError:
            return cls.WORKFLOW


@dataclass
class MemoryHeader:
    """Lightweight memory metadata from frontmatter scan (no full content)."""

    filename: str
    filepath: Path
    mtime: float
    summary: str
    type: MemoryType
    title: str = ""
    entry_id: str = ""

    @property
    def description(self) -> str:
        """Legacy compat."""
        return self.summary


@dataclass
class MemoryEntry:
    """Full memory entry with content.

    Phase 1 canonical fields: id, title, type, summary.
    Legacy 'name'/'description' accessible via properties.
    """

    title: str
    summary: str
    type: MemoryType
    content: str
    entry_id: str | None = None
    path: Path | None = None
    mtime: float = 0.0

    @property
    def name(self) -> str:
        """Legacy compat: name -> title."""
        return self.title

    @property
    def description(self) -> str:
        """Legacy compat: description -> summary."""
        return self.summary

    def to_frontmatter_dict(self) -> dict[str, str]:
        """Phase 1 canonical frontmatter."""
        return {
            "id": self.entry_id or _generate_id(self.title, self.type.value),
            "title": self.title,
            "type": self.type.value,
            "summary": self.summary,
        }


FRONTMATTER_MAX_LINES = 30


def _generate_id(title: str, type_value: str) -> str:
    """Generate a stable id from type and title."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().strip()).strip("-")[:50]
    return f"{type_value}-{slug}" if slug else f"{type_value}-unnamed"


def parse_memory_file(path: Path) -> MemoryEntry:
    """Parse a memory file with YAML frontmatter.

    Supports both Phase 1 canonical and legacy (v0) formats.
    """
    post = frontmatter.load(str(path))
    stat = path.stat()

    title = post.get("title") or post.get("name", path.stem)
    summary = post.get("summary") or post.get("description", "")
    entry_id = post.get("id") or _generate_id(title, post.get("type", "workflow"))

    return MemoryEntry(
        entry_id=entry_id,
        title=title,
        summary=summary,
        type=MemoryType.from_str(post.get("type", "workflow")),
        content=post.content,
        path=path,
        mtime=stat.st_mtime,
    )


def parse_frontmatter_only(path: Path, max_lines: int = FRONTMATTER_MAX_LINES) -> dict[str, Any]:
    """Read only the first N lines to extract frontmatter metadata.

    Performance-critical: never reads full file content during scanning.
    """
    lines: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
    except (OSError, UnicodeDecodeError):
        return {}

    text = "".join(lines)
    if not text.startswith("---"):
        return {}

    try:
        post = frontmatter.loads(text)
        return dict(post.metadata)
    except Exception:
        return {}


def write_memory_file(path: Path, entry: MemoryEntry) -> None:
    """Write a memory file with Phase 1 canonical YAML frontmatter.

    Uses atomic write (temp file + os.replace) to prevent corruption.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(entry.content, **entry.to_frontmatter_dict())
    content = frontmatter.dumps(post)

    # Atomic write: write to temp file, then rename
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = -1  # Mark as closed
        os.replace(tmp_path, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def make_filename(title: str, mem_type: MemoryType) -> str:
    """Generate a safe filename from memory title and type."""
    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in title)
    safe = safe.strip().replace(" ", "_").lower()
    if not safe:
        safe = "unnamed"
    safe = safe[:60]
    return f"{mem_type.value}_{safe}.md"
