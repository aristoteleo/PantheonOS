"""
LLM-based memory retrieval.

Selects relevant memories by having an LLM scan frontmatter descriptions
and judge relevance — no embedding or vector database required.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pantheon.utils.log import logger

from .freshness import annotate_with_freshness, memory_age_text
from .prompts import LLM_SELECTION_SYSTEM, LLM_SELECTION_USER
from .store import MemoryStore
from .types import MemoryEntry, MemoryHeader

if TYPE_CHECKING:
    from .runtime import MemoryRuntime


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
        runtime: "MemoryRuntime | None" = None,
        selection_timeout_seconds: float = 3.0,
    ):
        self.store = store
        self.model = model
        self.runtime = runtime
        self.runtime_dir = runtime_dir
        self.max_memories = max_memories
        self.max_chats = max_chats
        self.session_notes_limit = session_notes_limit
        self.selection_timeout_seconds = selection_timeout_seconds

    def _resolved_model(self) -> str:
        return self.runtime.resolve_model(self.model) if self.runtime else self.model

    async def find_relevant(
        self,
        query: str,
        already_shown: set[str] | None = None,
    ) -> list[RetrievalResult]:
        """Find memories and session notes relevant to the query using LLM selection."""
        memory_headers, session_headers = await asyncio.to_thread(self._scan_all_headers)

        if not memory_headers and not session_headers:
            return []

        # Filter out already-shown items
        if already_shown:
            memory_headers = [h for h in memory_headers if h.filename not in already_shown]
            session_headers = [h for h in session_headers if h.filename not in already_shown]

        if not memory_headers and not session_headers:
            return []

        manifest = self._build_manifest(memory_headers, session_headers)
        try:
            selected_memories, selected_chats = await asyncio.wait_for(
                self._llm_select(query, manifest, self.max_memories, self.max_chats),
                timeout=self.selection_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("Memory selection timed out; using local header matches")
            selected_memories = self._local_select(query, memory_headers, self.max_memories)
            selected_chats = self._local_select(query, session_headers, self.max_chats)

        # Only load entries from this request's filtered manifest. Model output
        # must not re-inject already shown entries or name arbitrary files.
        allowed_memories = {h.filename for h in memory_headers}
        allowed_chats = {h.filename for h in session_headers}
        selected_memories = list(dict.fromkeys(
            name for name in selected_memories if isinstance(name, str) and name in allowed_memories
        ))
        selected_chats = list(dict.fromkeys(
            name for name in selected_chats if isinstance(name, str) and name in allowed_chats
        ))

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

    @staticmethod
    def _local_select(query: str, headers: list[MemoryHeader], limit: int) -> list[str]:
        """Cheap fallback over titles/summaries, including Chinese word fragments."""
        def tokens(text: str) -> set[str]:
            words = set(re.findall(r"[a-z0-9_]{2,}", text.casefold()))
            for phrase in re.findall(r"[\u3400-\u9fff]+", text):
                words.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
            return words - {"the", "and", "for", "with", "this", "that", "from", "what", "about"}

        query_tokens = tokens(query)
        ranked = []
        for header in headers:
            overlap = query_tokens & tokens(f"{header.title} {header.summary} {header.filename}")
            if overlap:
                ranked.append((len(overlap), header.mtime, header.filename))
        ranked.sort(reverse=True)
        return [name for _, _, name in ranked[:limit]]

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
                model=str(self._resolved_model()),
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                model_params={"temperature": 0.0, "max_tokens": 1000},
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
