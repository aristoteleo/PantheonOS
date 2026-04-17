"""
Shared memory runtime — core of the unified memory system.

MemoryRuntime is used by both ChatRoom and PantheonTeam via adapters.
It owns all memory components and provides 6 core methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pantheon.utils.log import logger

from .dream import DreamConsolidator, DreamGate, DreamResult
from .extract_memories import MemoryExtractor
from .flush import MemoryFlusher
from .retrieval import MemoryRetriever, RetrievalResult
from .session_note import SessionNoteExtractor
from .session_log import SessionLogManager
from .store import MemoryStore
from .types import MemoryEntry


class MemoryRuntime:
    """Shared memory runtime, used by both ChatRoom and PantheonTeam."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.store: MemoryStore | None = None
        self.retriever: MemoryRetriever | None = None
        self.flusher: MemoryFlusher | None = None
        self.dream_gate: DreamGate | None = None
        self.consolidator: DreamConsolidator | None = None
        self.session_log: SessionLogManager | None = None
        self.session_note: SessionNoteExtractor | None = None
        self.memory_extractor: MemoryExtractor | None = None
        self._shown_memories: dict[str, set[str]] = {}  # session_id → shown set
        self._initialized = False

    def initialize(self, pantheon_dir: Path, runtime_dir: Path) -> None:
        """Initialize all components, bound to a .pantheon/ directory.

        Args:
            pantheon_dir: The .pantheon/ directory (all durable memory lives here)
            runtime_dir: .pantheon/memory-store/memory-runtime/ (for session notes, logs, locks)
        """
        durable_dir = pantheon_dir / "memory-store"
        index_path = pantheon_dir / "MEMORY.md"

        self.store = MemoryStore(durable_dir, index_path)
        self.retriever = MemoryRetriever(
            self.store,
            model=self.config["selection_model"],
            runtime_dir=runtime_dir,
            max_memories=self.config.get("selection_max_memories", 5),
            max_chats=self.config.get("selection_max_chats", 3),
            session_notes_limit=self.config.get("session_notes_retrieval_limit", 10),
        )

        if self.config.get("flush_enabled"):
            self.flusher = MemoryFlusher(
                self.store, model=self.config["flush_model"]
            )

        if self.config.get("dream_enabled"):
            self.dream_gate = DreamGate(self.store, self.config)
            self.consolidator = DreamConsolidator(
                self.store, model=self.config["dream_model"]
            )

        self.session_log = SessionLogManager(runtime_dir / "session-logs")

        # Phase 2A: Session Note Extractor (for compact shortcut)
        model = self.config["selection_model"]
        self.session_note = SessionNoteExtractor(runtime_dir, model, self.config)

        # Phase 2B: Extract Memories (per-turn auto extraction)
        self.memory_extractor = MemoryExtractor(self.store, model)

        self._initialized = True

        logger.info(
            f"MemoryRuntime initialized: "
            f"pantheon_dir={pantheon_dir}, "
            f"model={self.config['selection_model']}, "
            f"flush={'on' if self.flusher else 'off'}, "
            f"dream={'on' if self.dream_gate else 'off'}"
        )

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # ── 6 core methods shared by ChatRoom and PantheonTeam ──

    def load_bootstrap_memory(self) -> str:
        """Read MEMORY.md index content for system prompt injection."""
        if self.store is None:
            return ""
        return self.store.read_index()

    async def retrieve_relevant(
        self,
        query: str,
        session_id: str,
        already_shown: set[str] | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve memories relevant to the query."""
        if self.retriever is None:
            return []
        shown = self._shown_memories.setdefault(session_id, set())
        if already_shown:
            shown.update(already_shown)
        results = await self.retriever.find_relevant(
            query, already_shown=shown
        )
        for r in results:
            shown.add(r.path.name)
        return results

    async def flush_before_compaction(
        self, session_id: str, messages: list[dict]
    ) -> str | None:
        """Pre-compression flush: extract info → daily log + session log."""
        if self.flusher is None:
            return None
        content = await self.flusher.flush(messages)
        if content and self.session_log:
            self.session_log.append(session_id, f"[flush] {content}")
        return content

    def update_session_log(self, session_id: str, content: str) -> None:
        """Update session log (delegation summary, etc.)."""
        if self.session_log:
            self.session_log.append(session_id, content)

    async def maybe_run_dream(self, force: bool = False) -> DreamResult | None:
        """Check dream gate and run consolidation if conditions met."""
        if not self.dream_gate or not self.consolidator:
            return None
        prior = await self.dream_gate.should_dream(force=force)
        if prior is None:
            return None
        logger.info("Dream gate passed — starting consolidation")
        try:
            result = await self.consolidator.consolidate()
            if result.success:
                self.dream_gate.lock.release()
                logger.info(f"Dream consolidation complete: {result.summary}")
            else:
                self.dream_gate.lock.rollback(prior)
                logger.warning(f"Dream consolidation failed: {result.summary}")
            return result
        except Exception as e:
            self.dream_gate.lock.rollback(prior)
            logger.error(f"Dream consolidation error: {e}")
            raise

    def write_memory(self, entry: MemoryEntry) -> Path:
        """Write a durable memory entry (two-step save)."""
        if self.store is None:
            raise RuntimeError("MemoryRuntime not initialized")
        return self.store.add_memory(entry)

    def increment_session(self) -> None:
        """Called at end of each run to track session count for dream gate."""
        if self.dream_gate:
            self.dream_gate.increment_session()

    # ── Phase 2A: Session Memory ──

    async def maybe_update_session_note(
        self, session_id: str, messages: list[dict], context_tokens: int,
        jsonl_path: str = "",
    ) -> bool:
        """Update session note if thresholds met. Called in on_run_end."""
        if not self.session_note:
            return False
        return await self.session_note.maybe_update(
            session_id, messages, context_tokens, jsonl_path=jsonl_path
        )

    async def force_update_session_note(
        self, session_id: str, messages: list[dict]
    ) -> bool:
        """Force session note update before compression."""
        if not self.session_note:
            return False
        return await self.session_note.force_update(session_id, messages)

    def get_session_note_for_compact(self, session_id: str) -> str:
        """Get session note content for compact shortcut."""
        if not self.session_note:
            return ""
        return self.session_note.read(session_id)

    def is_session_note_empty(self, session_id: str) -> bool:
        """Check if session note is only template (no real content)."""
        if not self.session_note:
            return True
        return self.session_note.is_empty_template(session_id)

    async def wait_for_session_note(self, session_id: str) -> None:
        """Wait for in-flight session note extraction. Called before compact."""
        if self.session_note:
            await self.session_note.wait_for_extraction(session_id)

    def get_session_note_boundary(
        self, session_id: str, messages: list[dict]
    ) -> int | None:
        """Get the message index up to which session note covers."""
        if not self.session_note:
            return None
        return self.session_note.get_last_summarized_index(session_id, messages)

    # ── Phase 2B: Extract Memories ──

    async def maybe_extract_memories(
        self, session_id: str, messages: list[dict]
    ) -> list[str] | None:
        """Auto-extract durable memories from this turn. Called in on_run_end."""
        if not self.memory_extractor:
            return None
        return await self.memory_extractor.maybe_extract(session_id, messages)
