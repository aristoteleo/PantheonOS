"""Tests for pre-compression flush."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.flush import MemoryFlusher


class TestMemoryFlusher:
    @pytest.mark.asyncio
    async def test_flush_saves_to_daily_log(self, store):
        flusher = MemoryFlusher(store, model="gpt-4o-mini")
        messages = [
            {"role": "user", "content": "Analyze this dataset"},
            {"role": "assistant", "content": "The mito ratio is high at 20%"},
        ]
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content="- Dataset has high mito ratio (20%)"))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await flusher.flush(messages)
            assert result is not None
            assert "mito" in result
        assert len(store.list_daily_logs()) == 1

    @pytest.mark.asyncio
    async def test_flush_nothing_to_save(self, store):
        flusher = MemoryFlusher(store)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="[nothing_to_save]"))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            assert await flusher.flush([{"role": "user", "content": "Hi"}]) is None
        assert len(store.list_daily_logs()) == 0

    @pytest.mark.asyncio
    async def test_flush_empty_messages(self, store):
        flusher = MemoryFlusher(store)
        assert await flusher.flush([]) is None

    @pytest.mark.asyncio
    async def test_flush_llm_error(self, store):
        flusher = MemoryFlusher(store)
        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, side_effect=Exception("err")):
            assert await flusher.flush([{"role": "user", "content": "test"}]) is None

    def test_format_messages(self, store):
        flusher = MemoryFlusher(store)
        text = flusher._format_messages([
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ])
        assert "[user] Hello" in text
        assert "[assistant] World" in text

    def test_format_messages_list_content(self, store):
        flusher = MemoryFlusher(store)
        text = flusher._format_messages([
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        ])
        assert "Hello" in text
