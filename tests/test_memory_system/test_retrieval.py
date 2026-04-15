"""Tests for LLM-based memory retrieval."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.retrieval import MemoryRetriever, RetrievalResult


class TestFormatManifest:
    def test_formats_headers(self, populated_store):
        retriever = MemoryRetriever(populated_store, model="gpt-4o-mini")
        headers = populated_store.scan_headers()
        manifest = retriever._format_manifest(headers)
        assert "[user]" in manifest
        assert "[feedback]" in manifest
        assert "[workflow]" in manifest

    def test_empty_headers(self, store):
        retriever = MemoryRetriever(store)
        assert retriever._format_manifest([]) == ""


class TestLLMSelect:
    @pytest.mark.asyncio
    async def test_successful_selection(self, populated_store):
        retriever = MemoryRetriever(populated_store, model="gpt-4o-mini")
        headers = populated_store.scan_headers()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content=json.dumps({"selected_memories": [headers[0].filename]})))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            selected = await retriever._llm_select("test query", "manifest", 5)
            assert len(selected) == 1

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self, populated_store):
        retriever = MemoryRetriever(populated_store)
        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, side_effect=Exception("API error")):
            assert await retriever._llm_select("query", "manifest", 5) == []


class TestFindRelevant:
    @pytest.mark.asyncio
    async def test_returns_results(self, populated_store):
        retriever = MemoryRetriever(populated_store)
        headers = populated_store.scan_headers()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(
            content=json.dumps({"selected_memories": [headers[0].filename, headers[1].filename]})))]

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
