"""
Long-term memory system for Pantheon Agents.

Provides cross-session persistent memory with:
- Five-type taxonomy (user/feedback/project/reference/workflow)
- Markdown file storage with YAML frontmatter
- LLM-based memory selection (no embedding required)
- Freshness tracking with staleness warnings
- Pre-compression flush to daily logs
- Dream consolidation (4-phase distillation)
- Shared runtime for both ChatRoom and PantheonTeam

No specialized agent tools — agents use file_manager to read/write memory files
directly, guided by system prompt instructions (Claude Code pattern).
"""

from .types import MemoryType, MemoryEntry, MemoryHeader
from .store import MemoryStore
from .freshness import memory_age_days, memory_age_text, staleness_warning
from .retrieval import MemoryRetriever, RetrievalResult
from .flush import MemoryFlusher
from .dream import DreamGate, DreamConsolidator, DreamResult
from .session_note import SessionNoteExtractor
from .extract_memories import MemoryExtractor
from .runtime import MemoryRuntime
from .session_log import SessionLogManager
from .plugin import MemorySystemPlugin
from .chatroom import ChatRoomMemoryAdapter
from .config import DEFAULT_CONFIG, get_memory_system_config

__all__ = [
    "MemoryType",
    "MemoryEntry",
    "MemoryHeader",
    "MemoryStore",
    "MemoryRetriever",
    "RetrievalResult",
    "MemoryFlusher",
    "DreamGate",
    "DreamConsolidator",
    "DreamResult",
    "MemoryRuntime",
    "SessionLogManager",
    "SessionNoteExtractor",
    "MemorySystemPlugin",
    "ChatRoomMemoryAdapter",
    "DEFAULT_CONFIG",
    "get_memory_system_config",
    "memory_age_days",
    "memory_age_text",
    "staleness_warning",
]
