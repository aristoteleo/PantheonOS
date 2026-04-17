"""
LLM-based memory retrieval.

Selects relevant memories by having an LLM scan frontmatter descriptions
and judge relevance — no embedding or vector database required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pantheon.utils.log import logger

from .freshness import annotate_with_freshness, memory_age_text
from .prompts import LLM_SELECTION_SYSTEM, LLM_SELECTION_USER
from .store import MemoryStore
from .types import MemoryEntry, MemoryHeader


@dataclass
class RetrievalResult:
    """A retrieved memory with content and metadata."""

    path: Path
    content: str  # Full content + freshness annotation
    entry: MemoryEntry
    age_text: str


class MemoryRetriever:
    """LLM-based memory selection (no embedding required).

    Workflow:
    1. Scan memory files + recent session notes for frontmatter
    2. Format as a text manifest with two sections (Memories / Recent Chats)
    3. Call a fast LLM to select the most relevant memories and chats
    4. Load and return selected items with freshness annotations
    """

    def __init__(
        self,
        store: MemoryStore,
        model: str = "low",
        runtime_dir: Path | None = None,
        max_memories: int = 5,
        max_chats: int = 3,
        session_notes_limit: int = 10,
    ):
        self.store = store
        self.model = model
        self.runtime_dir = runtime_dir
        self.max_memories = max_memories
        self.max_chats = max_chats
        self.session_notes_limit = session_notes_limit

    async def find_relevant(
        self,
        query: str,
        already_shown: set[str] | None = None,
    ) -> list[RetrievalResult]:
        """Find memories and session notes relevant to the query using LLM selection."""
        memory_headers, session_headers = self._scan_all_headers()

        if not memory_headers and not session_headers:
            return []

        # Filter out already-shown items
        if already_shown:
            memory_headers = [h for h in memory_headers if h.filename not in already_shown]
            session_headers = [h for h in session_headers if h.filename not in already_shown]

        if not memory_headers and not session_headers:
            return []

        manifest = self._build_manifest(memory_headers, session_headers)
        selected_memories, selected_chats = await self._llm_select(
            query, manifest, self.max_memories, self.max_chats
        )

        # Load full content for selected items
        results: list[RetrievalResult] = []

        # Load memories
        for filename in selected_memories:
            path = self.store.find_memory_by_name(filename)
            if path is None:
                logger.debug(f"LLM selected non-existent memory: {filename}")
                continue
            try:
                entry = self.store.read_memory(path)
                content = annotate_with_freshness(entry.content, entry.mtime)
                results.append(
                    RetrievalResult(
                        path=path,
                        content=content,
                        entry=entry,
                        age_text=memory_age_text(entry.mtime),
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to load memory {filename}: {e}")

        # Load session notes
        for filename in selected_chats:
            path = self._find_session_note(filename)
            if path is None:
                logger.debug(f"LLM selected non-existent session note: {filename}")
                continue
            try:
                entry = self._read_session_note(path)
                content = annotate_with_freshness(entry.content, entry.mtime)
                results.append(
                    RetrievalResult(
                        path=path,
                        content=content,
                        entry=entry,
                        age_text=memory_age_text(entry.mtime),
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to load session note {filename}: {e}")

        return results

    def _scan_all_headers(self) -> tuple[list[MemoryHeader], list[MemoryHeader]]:
        """Scan and return (memory_headers, session_note_headers) separately."""
        from .types import parse_frontmatter_only

        # Scan memory-store
        memory_headers = self.store.scan_headers()

        # Scan session-notes (recent N only)
        session_headers: list[MemoryHeader] = []
        if self.runtime_dir:
            session_notes_dir = self.runtime_dir / "session-notes"
            if session_notes_dir.exists():
                notes = sorted(
                    session_notes_dir.glob("*.md"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                for note_path in notes[: self.session_notes_limit]:
                    frontmatter = parse_frontmatter_only(note_path)
                    if frontmatter:  # Skip old notes without frontmatter
                        from .types import MemoryType
                        header = MemoryHeader(
                            filename=note_path.name,
                            filepath=note_path,
                            title=frontmatter.get("title", ""),
                            summary=frontmatter.get("summary", ""),
                            type=MemoryType.SESSION_NOTE,
                            mtime=note_path.stat().st_mtime,
                        )
                        session_headers.append(header)

        return memory_headers[:200], session_headers

    def _build_manifest(
        self, memory_headers: list[MemoryHeader], session_headers: list[MemoryHeader]
    ) -> str:
        """Build manifest with two sections: Memories and Recent Chats."""
        lines: list[str] = []

        # Section 1: Memories
        if memory_headers:
            lines.append("## Memories\n")
            for h in memory_headers:
                age = memory_age_text(h.mtime)
                desc = h.summary or "(no description)"
                lines.append(f"[{h.type.value}] {h.filename} ({age}): {desc}")
            lines.append("")  # Blank line between sections

        # Section 2: Recent Chats
        if session_headers:
            lines.append("## Recent Chats\n")
            for h in session_headers:
                age = memory_age_text(h.mtime)
                desc = h.summary or "(no description)"
                lines.append(f"[session] {h.filename} ({age}): {desc}")

        return "\n".join(lines)

    def _find_session_note(self, filename: str) -> Path | None:
        """Find session note by filename."""
        if not self.runtime_dir:
            return None
        session_notes_dir = self.runtime_dir / "session-notes"
        path = session_notes_dir / filename
        return path if path.exists() else None

    def _read_session_note(self, path: Path) -> MemoryEntry:
        """Read session note as MemoryEntry."""
        import frontmatter
        from .types import MemoryType

        post = frontmatter.load(str(path))
        stat = path.stat()

        return MemoryEntry(
            entry_id=post.get("session_id", path.stem),
            title=post.get("title", path.stem),
            summary=post.get("summary", ""),
            type=MemoryType.SESSION_NOTE,
            content=post.content,
            path=path,
            mtime=stat.st_mtime,
        )

    async def _llm_select(
        self, query: str, manifest: str, max_memories: int, max_chats: int
    ) -> tuple[list[str], list[str]]:
        """Call LLM to select relevant memories and chats from the manifest.

        Returns (selected_memory_filenames, selected_chat_filenames).
        """
        from pantheon.utils.llm import acompletion

        system_msg = LLM_SELECTION_SYSTEM.format(
            max_memories=max_memories, max_chats=max_chats
        )
        user_msg = LLM_SELECTION_USER.format(query=query, manifest=manifest)

        try:
            response = await acompletion(
                model=str(self.model),
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                model_params={"temperature": 0.0},
            )
            content = response.choices[0].message.content or "{}"
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', content, re.DOTALL)
                data = json.loads(match.group()) if match else {}

            selected_memories = data.get("selected_memories", [])
            selected_chats = data.get("selected_chats", [])

            if not isinstance(selected_memories, list):
                selected_memories = []
            if not isinstance(selected_chats, list):
                selected_chats = []

            return selected_memories[:max_memories], selected_chats[:max_chats]

        except Exception as e:
            logger.warning(f"LLM memory selection failed: {e}")
            return [], []

        try:
            response = await acompletion(
                model=str(self.model),
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                model_params={"temperature": 0.0, "max_tokens": 1000},
            )
            content = response.choices[0].message.content
            if not content:
                return []

            # Strip markdown code fences if present
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            selected = result.get("selected_memories", [])
            if isinstance(selected, list):
                return [s for s in selected if isinstance(s, str)]
            return []
        except json.JSONDecodeError as e:
            logger.warning(
                f"LLM memory selection failed - invalid JSON: {e}\n"
                f"LLM response: {content[:500] if content else '(empty)'}"
            )
            return []
        except Exception as e:
            logger.warning(f"LLM memory selection failed: {e}")
            return []
