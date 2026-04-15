"""Tests for ChatRoomMemoryAdapter."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.chatroom import ChatRoomMemoryAdapter
from pantheon.internal.memory_system.types import MemoryEntry, MemoryType


class TestChatRoomAdapter:
    @pytest.fixture
    def adapter(self, runtime):
        return ChatRoomMemoryAdapter(runtime)

    def test_load_bootstrap_empty(self, adapter):
        assert adapter.load_bootstrap_memory() == ""

    def test_load_bootstrap_with_content(self, adapter, runtime):
        runtime.store.write_index("- [Test](memory/test.md) — hello")
        assert "Test" in adapter.load_bootstrap_memory()

    @pytest.mark.asyncio
    async def test_retrieve_relevant(self, adapter, runtime, sample_user_entry):
        runtime.store.add_memory(sample_user_entry)
        headers = runtime.store.scan_headers()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content=json.dumps({"selected_memories": [headers[0].filename]})))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            results = await adapter.retrieve_relevant("query", "sess-1")
            assert len(results) == 1
            assert "title" in results[0]
            assert "content" in results[0]
            assert "type" in results[0]
            assert "age" in results[0]

    @pytest.mark.asyncio
    async def test_flush(self, adapter):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Important info"))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.flush_before_compaction("sess-1", [
                {"role": "user", "content": "test"}
            ])
        assert result == "Important info"

    def test_update_session_log(self, adapter, runtime):
        adapter.update_session_log("sess-1", "delegation done")
        assert "delegation done" in runtime.session_log.read("sess-1")

    @pytest.mark.asyncio
    async def test_maybe_run_dream(self, adapter):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Consolidated."))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await adapter.maybe_run_dream()
        # Gate not met (fresh runtime) → None
        assert result is None

    def test_write_memory(self, adapter, runtime):
        result = adapter.write_memory(
            content="Test content", title="Test", memory_type="feedback", summary="Test summary"
        )
        assert result["success"]
        assert result["path"].endswith(".md")
        # Verify file exists
        assert runtime.store.find_memory_by_name(result["path"]) is not None
