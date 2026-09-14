"""Tests for LLM-based memory retrieval."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.retrieval import MemoryRetriever, RetrievalResult


class TestBuildManifest:
    def test_formats_headers(self, populated_store):
        retriever = MemoryRetriever(populated_store, model="gpt-4o-mini")
        headers = populated_store.scan_headers()
        manifest = retriever._build_manifest(headers, [])
        assert "## Memories" in manifest
        assert "[user]" in manifest
        assert "[feedback]" in manifest
        assert "[workflow]" in manifest

    def test_empty_headers(self, store):
        retriever = MemoryRetriever(store)
        assert retriever._build_manifest([], []) == ""

    def test_two_sections(self, populated_store, tmp_path):
        # Create a mock session note
        from pantheon.internal.memory_system.types import MemoryHeader, MemoryType
        session_header = MemoryHeader(
            filename="chat-1.md",
            filepath=tmp_path / "chat-1.md",
            title="Test Session",
            summary="Test summary",
            type=MemoryType.SESSION_NOTE,
            mtime=1234567890.0,
        )

        retriever = MemoryRetriever(populated_store, model="gpt-4o-mini")
        headers = populated_store.scan_headers()
        manifest = retriever._build_manifest(headers, [session_header])

        assert "## Memories" in manifest
        assert "## Recent Chats" in manifest
        assert "[session]" in manifest
        assert "chat-1.md" in manifest


class TestLLMSelect:
    @pytest.mark.asyncio
    async def test_successful_selection(self, populated_store):
        retriever = MemoryRetriever(populated_store, model="gpt-4o-mini")
        headers = populated_store.scan_headers()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content=json.dumps({
                "selected_memories": [headers[0].filename],
                "selected_chats": []
            })))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            memories, chats = await retriever._llm_select("test query", "manifest", 5, 3)
            assert len(memories) == 1
            assert len(chats) == 0

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self, populated_store):
        retriever = MemoryRetriever(populated_store)
        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, side_effect=Exception("API error")):
            memories, chats = await retriever._llm_select("query", "manifest", 5, 3)
            assert memories == []
            assert chats == []


class TestFindRelevant:
    @pytest.mark.asyncio
    async def test_slow_selector_is_cancelled_and_uses_matching_headers(self, populated_store):
        retriever = MemoryRetriever(populated_store, selection_timeout_seconds=0.01)
        cancelled = asyncio.Event()

        async def slow_model(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch("pantheon.utils.llm.acompletion", side_effect=slow_model):
            results = await asyncio.wait_for(retriever.find_relevant("React frontend"), timeout=1)
        assert cancelled.is_set()
        assert [r.entry.title for r in results] == ["Senior Go engineer"]

    @pytest.mark.asyncio
    async def test_model_selection_cannot_restore_filtered_or_unknown_entries(self, populated_store):
        retriever = MemoryRetriever(populated_store)
        headers = populated_store.scan_headers()
        retriever._llm_select = AsyncMock(return_value=(
            [headers[0].filename, headers[1].filename, headers[1].filename, "../secret.md", {}], []
        ))
        results = await retriever.find_relevant("query", already_shown={headers[0].filename})
        assert [r.path.name for r in results] == [headers[1].filename]

    def test_local_fallback_supports_chinese_and_skips_unrelated_memories(self, populated_store):
        from dataclasses import replace
        headers = populated_store.scan_headers()
        header = replace(headers[0], title="默认数据集设置", summary="修改 Volume3D 的默认数据")
        assert MemoryRetriever._local_select("请修改默认数据", [header], 1) == [header.filename]
        assert MemoryRetriever._local_select("unrelated crocodile", [header], 1) == []

    @pytest.mark.asyncio
    async def test_returns_results(self, populated_store):
        retriever = MemoryRetriever(populated_store)
        headers = populated_store.scan_headers()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content=json.dumps({
                "selected_memories": [headers[0].filename, headers[1].filename],
                "selected_chats": []
            })))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            results = await retriever.find_relevant("test query")
            assert len(results) == 2
            assert all(isinstance(r, RetrievalResult) for r in results)
            assert all(r.content for r in results)

    @pytest.mark.asyncio
    async def test_empty_store(self, store):
        retriever = MemoryRetriever(store)
        assert await retriever.find_relevant("anything") == []

    @pytest.mark.asyncio
    async def test_already_shown_filtered(self, populated_store):
        retriever = MemoryRetriever(populated_store)
        headers = populated_store.scan_headers()
        already_shown = {h.filename for h in headers}
        # All shown → empty manifest → empty results
        results = await retriever.find_relevant("query", already_shown=already_shown)
        assert results == []
