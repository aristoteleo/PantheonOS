"""Shared fixtures for memory system tests."""

from pathlib import Path

import pytest

from pantheon.internal.memory_system.runtime import MemoryRuntime
from pantheon.internal.memory_system.store import MemoryStore
from pantheon.internal.memory_system.types import MemoryEntry, MemoryType


@pytest.fixture
def tmp_pantheon_dir(tmp_path):
    """Create a temporary .pantheon/ directory."""
    pd = tmp_path / ".pantheon"
    pd.mkdir()
    return pd


@pytest.fixture
def tmp_runtime_dir(tmp_pantheon_dir):
    """Create a temporary runtime directory inside .pantheon/."""
    rd = tmp_pantheon_dir / "memory-runtime"
    rd.mkdir()
    return rd


@pytest.fixture
def store(tmp_pantheon_dir):
    """Create a MemoryStore with .pantheon/ paths."""
    durable_dir = tmp_pantheon_dir / "memory-store"
    index_path = tmp_pantheon_dir / "MEMORY.md"
    return MemoryStore(durable_dir, index_path)


@pytest.fixture
def sample_user_entry():
    return MemoryEntry(
        title="Senior Go engineer",
        summary="Deep Go expertise, new to React frontend",
        type=MemoryType.USER,
        content="User has 10 years Go experience but first time with React.",
    )


@pytest.fixture
def sample_feedback_entry():
    return MemoryEntry(
        title="Testing policy—use real databases",
        summary="Integration tests must hit real DB, not mocks",
        type=MemoryType.FEEDBACK,
        content="Do not mock databases in integration tests.\n\n**Why:** Prior incident.",
    )


@pytest.fixture
def sample_workflow_entry():
    return MemoryEntry(
        title="High mito ratio QC",
        summary="Dataset has high mitochondrial gene ratio, needs strict QC",
        type=MemoryType.WORKFLOW,
        content="This dataset has mitochondrial gene ratio >15%.",
    )


@pytest.fixture
def populated_store(store, sample_user_entry, sample_feedback_entry, sample_workflow_entry):
    """A store with 3 sample memories already saved."""
    store.add_memory(sample_user_entry)
    store.add_memory(sample_feedback_entry)
    store.add_memory(sample_workflow_entry)
    return store


@pytest.fixture
def runtime_config():
    return {
        "enabled": True,
        "selection_model": "gpt-4o-mini",
        "flush_enabled": True,
        "flush_model": "gpt-4o-mini",
        "dream_enabled": True,
        "dream_model": "gpt-4o-mini",
        "dream_min_hours": 24,
        "dream_min_sessions": 5,
    }


@pytest.fixture
def runtime(runtime_config, tmp_pantheon_dir, tmp_runtime_dir):
    """Create and initialize a MemoryRuntime."""
    rt = MemoryRuntime(runtime_config)
    rt.initialize(tmp_pantheon_dir, tmp_runtime_dir)
    return rt
