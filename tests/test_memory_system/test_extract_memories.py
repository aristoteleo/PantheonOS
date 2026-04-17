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


class TestPendingAndCursorSnapshot:
    @pytest.mark.asyncio
    async def test_sets_pending_when_in_progress(self, store):
        """When in-flight, a new call sets _pending instead of silently dropping."""
        extractor = MemoryExtractor(store, model="test")
        extractor._in_progress["s1"] = True
        await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])
        assert extractor._pending["s1"] is True

    @pytest.mark.asyncio
    async def test_drain_pass_processes_accumulated_messages(self, store):
        """After in-flight extraction, pending messages are processed in a drain pass."""
        call_count = 0

        async def fake_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(content="ok")

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=fake_run)

        extractor = MemoryExtractor(store, model="test")
        messages = [{"role": "user", "content": f"msg-{i}"} for i in range(4)]

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            # First call: processes messages[0:2]
            await extractor.maybe_extract("s1", messages[:2])
            assert extractor._last_cursor["s1"] == 2

            # Simulate: while first was running, two more messages arrived.
            # Mark pending manually (as the non-blocking path would).
            extractor._pending["s1"] = True

            # Second call: should trigger drain pass for messages[2:4]
            await extractor.maybe_extract("s1", messages)

        assert extractor._last_cursor["s1"] == 4
        assert call_count == 2  # main pass + drain pass

    @pytest.mark.asyncio
    async def test_cursor_snapshots_start_position(self, store):
        """Cursor advances to the snapshot taken at extraction start, not live len."""
        messages = [{"role": "user", "content": f"msg-{i}"} for i in range(3)]
        extra = [{"role": "user", "content": "late-arrival"}]

        async def fake_run(*args, **kwargs):
            # Simulate messages growing during extraction
            messages.extend(extra)
            return MagicMock(content="ok")

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=fake_run)

        extractor = MemoryExtractor(store, model="test")
        snapshot_len = len(messages)  # 3 at start

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            await extractor.maybe_extract("s1", messages)

        # Cursor should be 3 (snapshot), not 4 (live len after fake_run appended)
        assert extractor._last_cursor["s1"] == snapshot_len


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
