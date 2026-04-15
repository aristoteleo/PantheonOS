"""Tests for MemoryExtractor (auto per-turn extraction)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.extract_memories import MemoryExtractor


class TestMaybeExtract:
    @pytest.mark.asyncio
    async def test_extracts_memories(self, store):
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        messages = [
            {"role": "user", "content": "I prefer dark mode and terse responses"},
            {"role": "assistant", "content": "Noted, I'll keep that in mind."},
        ]

        # Mock agent that simulates writing a memory file via file_manager
        from pantheon.internal.memory_system.types import MemoryEntry, MemoryType
        async def fake_run(*args, **kwargs):
            store.add_memory(MemoryEntry(
                title="Dark mode preference", type=MemoryType.USER,
                summary="User prefers dark mode",
                content="User explicitly stated dark mode preference.",
            ))
            return MagicMock(content="Extracted 1 memory.")

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=fake_run)

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await extractor.maybe_extract("s1", messages)

        assert result is not None
        assert len(result) >= 1
        headers = store.scan_headers()
        assert len(headers) >= 1

    @pytest.mark.asyncio
    async def test_skips_when_agent_wrote_memory(self, store):
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        messages = [
            {"role": "user", "content": "remember this"},
            {"role": "assistant", "content": "saved", "tool_calls": [
                {"function": {"name": "file_write", "arguments": '{"path": ".pantheon/memory-store/test.md"}'}}
            ]},
        ]
        result = await extractor.maybe_extract("s1", messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_empty_messages(self, store):
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        result = await extractor.maybe_extract("s1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_in_progress(self, store):
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        extractor._in_progress["s1"] = True
        result = await extractor.maybe_extract("s1", [
            {"role": "user", "content": "test"},
        ])
        assert result is None

    @pytest.mark.asyncio
    async def test_advances_cursor(self, store):
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(content="Nothing to extract."))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            await extractor.maybe_extract("s1", messages)

        assert extractor._last_cursor["s1"] == 2

    @pytest.mark.asyncio
    async def test_handles_llm_error_trailing_run(self, store):
        """On failure, cursor should NOT advance (trailing run)."""
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        messages = [{"role": "user", "content": "test"}]

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=Exception("API err"))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await extractor.maybe_extract("s1", messages)
        assert result is None
        # Trailing run: cursor should NOT advance on failure
        assert extractor._last_cursor.get("s1", 0) == 0
        # Retry count should be incremented
        assert extractor._retry_count.get("s1", 0) == 1

    @pytest.mark.asyncio
    async def test_max_retries_advances_cursor(self, store):
        """After MAX_RETRIES failures, cursor should advance to skip the segment."""
        extractor = MemoryExtractor(store, model="gpt-4o-mini")
        messages = [{"role": "user", "content": "test"}]

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=Exception("API err"))

        # Fail MAX_RETRIES times
        for i in range(MemoryExtractor.MAX_RETRIES):
            with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
                result = await extractor.maybe_extract("s1", messages)
            assert result is None

        # After MAX_RETRIES, cursor should have advanced
        assert extractor._last_cursor.get("s1", 0) == len(messages)
        # Retry count should be reset
        assert extractor._retry_count.get("s1", 0) == 0


class TestMutualExclusion:
    def test_detects_memory_write(self, store):
        extractor = MemoryExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "file_write", "arguments": '{"path": ".pantheon/memory-store/user_prefs.md"}'}}
            ]},
        ]
        assert extractor._has_agent_memory_writes(messages, "s1") is True

    def test_detects_memory_update(self, store):
        extractor = MemoryExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "file_edit", "arguments": '{"path": ".pantheon/memory-store/feedback.md"}'}}
            ]},
        ]
        assert extractor._has_agent_memory_writes(messages, "s1") is True

    def test_no_memory_writes(self, store):
        extractor = MemoryExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "file_write", "arguments": '{"path": "src/main.py"}'}}
            ]},
        ]
        assert extractor._has_agent_memory_writes(messages, "s1") is False
