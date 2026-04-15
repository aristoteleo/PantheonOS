"""Tests for MemoryRuntime shared runtime."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.runtime import MemoryRuntime
from pantheon.internal.memory_system.types import MemoryEntry, MemoryType


class TestInitialization:
    def test_initialize(self, runtime):
        assert runtime.is_initialized
        assert runtime.store is not None
        assert runtime.retriever is not None
        assert runtime.flusher is not None
        assert runtime.dream_gate is not None
        assert runtime.consolidator is not None
        assert runtime.session_log is not None

    def test_not_initialized(self, runtime_config):
        rt = MemoryRuntime(runtime_config)
        assert not rt.is_initialized
        assert rt.load_bootstrap_memory() == ""

    def test_directories_created(self, runtime, tmp_pantheon_dir, tmp_runtime_dir):
        assert (tmp_pantheon_dir / "memory-store").exists()
        assert (tmp_runtime_dir / "session-logs").exists()


class TestLoadBootstrap:
    def test_empty(self, runtime):
        assert runtime.load_bootstrap_memory() == ""

    def test_with_content(self, runtime):
        runtime.store.write_index("- [Test](memory/test.md) — hello")
        content = runtime.load_bootstrap_memory()
        assert "Test" in content


class TestRetrieveRelevant:
    @pytest.mark.asyncio
    async def test_retrieve_and_deduplicate(self, runtime, sample_user_entry):
        runtime.store.add_memory(sample_user_entry)
        headers = runtime.store.scan_headers()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content=json.dumps({"selected_memories": [headers[0].filename]})))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            r1 = await runtime.retrieve_relevant("query", "sess-1")
            assert len(r1) == 1

        # Second call with same session → already_shown filters it out
        r2 = await runtime.retrieve_relevant("query", "sess-1")
        assert r2 == []


class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_writes_log_and_session_note(self, runtime):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Important finding"))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await runtime.flush_before_compaction("sess-1", [
                {"role": "user", "content": "test"}
            ])
        assert result == "Important finding"
        assert runtime.session_log.exists("sess-1")
        log = runtime.session_log.read("sess-1")
        assert "flush" in log


class TestSessionNote:
    def test_update_session_log(self, runtime):
        runtime.update_session_log("sess-1", "delegation summary: completed task X")
        log = runtime.session_log.read("sess-1")
        assert "delegation summary" in log


class TestDream:
    @pytest.mark.asyncio
    async def test_maybe_run_dream_force(self, runtime):
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(content="Consolidated."))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await runtime.maybe_run_dream(force=True)
        assert result is not None
        assert result.success

    @pytest.mark.asyncio
    async def test_maybe_run_dream_gate_not_met(self, runtime):
        # Default config: 24h min, 5 sessions min. Fresh runtime → gate fails.
        result = await runtime.maybe_run_dream()
        assert result is None


class TestWriteMemory:
    def test_write_creates_file(self, runtime):
        entry = MemoryEntry(title="Test", summary="Test memory",
                           type=MemoryType.WORKFLOW, content="Content")
        path = runtime.write_memory(entry)
        assert path.exists()

    def test_write_updates_index(self, runtime):
        entry = MemoryEntry(title="Test", summary="Test memory",
                           type=MemoryType.USER, content="Content")
        runtime.write_memory(entry)
        index = runtime.load_bootstrap_memory()
        assert "Test" in index


class TestIncrementSession:
    def test_increments(self, runtime):
        assert runtime.dream_gate._session_counter == 0
        runtime.increment_session()
        runtime.increment_session()
        assert runtime.dream_gate._session_counter == 2
