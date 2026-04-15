"""
LLM-based memory retrieval.

Selects relevant memories by having an LLM scan frontmatter descriptions
and judge relevance — no embedding or vector database required.
"""

from __future__ import annotations

import json
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
    1. Scan all memory files for frontmatter (filename + description + type + age)
    2. Format as a text manifest
    3. Call a fast LLM to select the most relevant memories
    4. Load and return selected memories with freshness annotations
    """

    def __init__(self, store: MemoryStore, model: str = "low"):
        self.store = store
        self.model = model

    async def find_relevant(
        self,
        query: str,
        max_results: int = 5,
        already_shown: set[str] | None = None,
    ) -> list[RetrievalResult]:
        """Find memories relevant to the query using LLM selection."""
        headers = self.store.scan_headers()
        if not headers:
            return []

        # Filter out already-shown memories
        if already_shown:
            headers = [h for h in headers if h.filename not in already_shown]
        if not headers:
            return []

        manifest = self._format_manifest(headers)
        selected_filenames = await self._llm_select(query, manifest, max_results)

        # Load full content for selected memories
        results: list[RetrievalResult] = []
        for filename in selected_filenames:
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

        return results[: max_results]

    def _format_manifest(self, headers: list[MemoryHeader]) -> str:
        """Format memory headers as a text manifest for LLM selection.

        Format: [type] filename (age): description
        """
        lines: list[str] = []
        for h in headers:
            age = memory_age_text(h.mtime)
            desc = h.description or "(no description)"
            lines.append(f"[{h.type.value}] {h.filename} ({age}): {desc}")
        return "\n".join(lines)

    async def _llm_select(
        self, query: str, manifest: str, max_results: int
    ) -> list[str]:
        """Call LLM to select relevant memories from the manifest."""
        from pantheon.utils.llm import acompletion

        system_msg = LLM_SELECTION_SYSTEM.format(max_results=max_results)
        user_msg = LLM_SELECTION_USER.format(query=query, manifest=manifest)

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
