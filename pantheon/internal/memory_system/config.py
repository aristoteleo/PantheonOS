"""
Configuration for the memory system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "selection_model": "low",
    "flush_enabled": True,
    "flush_model": None,
    "dream_enabled": True,
    "dream_model": None,
    "dream_min_hours": 24,
    "dream_min_sessions": 5,
    # Session note thresholds
    "session_note_init_tokens": 10_000,
    "session_note_update_tokens": 5_000,
    "session_note_tool_calls": 3,
    # Retrieval settings
    "selection_max_memories": 15,
    "selection_max_chats": 5,
    "session_notes_retrieval_limit": 15,
    # Static index injection (disabled by default — dynamic retrieve handles context)
    "static_index_enabled": False,
    # Dynamic retrieval injection mode:
    # - "index": inject title, summary, and file path only (default, low token cost)
    # - "full": inject complete file content
    "inject_mode": "index",
}


def resolve_model(model_tag: str) -> str:
    """Resolve a quality tag ('low', 'normal', 'high') or model name to a concrete model."""
    try:
        from pantheon.agent import _is_model_tag, _resolve_model_tag

        if _is_model_tag(model_tag):
            models = _resolve_model_tag(model_tag)
            if models:
                return models[0]
    except ImportError:
        pass
    return model_tag


class LazyModel:
    """Lazy-resolved model — resolves quality tag to concrete model on each use.

    Stores the original tag (e.g. "low") and re-resolves via ModelSelector
    every time str() is called, so provider changes (API key add/remove)
    are picked up without restarting.
    """

    def __init__(self, tag: str):
        self._tag = tag

    def resolve(self) -> str:
        return resolve_model(self._tag)

    def __str__(self) -> str:
        return self.resolve()

    def __repr__(self) -> str:
        return f"LazyModel({self._tag!r})"


def resolve_pantheon_dir(settings: Any) -> Path:
    """Resolve .pantheon/ directory — all memory paths live inside here."""
    return Path(settings.pantheon_dir)


def resolve_durable_memory_dir(settings: Any) -> Path:
    """.pantheon/memory-store/ — durable memory files."""
    return resolve_pantheon_dir(settings) / "memory-store"


def resolve_memory_index_path(settings: Any) -> Path:
    """.pantheon/MEMORY.md — durable memory index."""
    return resolve_pantheon_dir(settings) / "MEMORY.md"


def resolve_runtime_dir(settings: Any) -> Path:
    """.pantheon/memory-store/memory-runtime/ — runtime state (session notes, logs, locks)."""
    return resolve_durable_memory_dir(settings) / "memory-runtime"


def get_memory_system_config(settings: Any) -> dict[str, Any]:
    """Extract memory_system config from Settings, with defaults."""
    raw = settings.get_section("memory_system")
    config = {**DEFAULT_CONFIG, **raw}

    # Resolve None models to selection_model
    base_model = config["selection_model"]
    if config["flush_model"] is None:
        config["flush_model"] = base_model
    if config["dream_model"] is None:
        config["dream_model"] = base_model

    # Wrap models as LazyModel for dynamic resolution
    config["selection_model"] = LazyModel(config["selection_model"])
    config["flush_model"] = LazyModel(config["flush_model"])
    config["dream_model"] = LazyModel(config["dream_model"])

    return config
