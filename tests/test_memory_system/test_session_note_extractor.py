"""Tests for SessionNoteExtractor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.memory_system.session_note import SessionNoteExtractor


@pytest.fixture
def extractor(tmp_path):
    return SessionNoteExtractor(tmp_path / "runtime", model="gpt-4o-mini")


class TestThresholds:
    @pytest.mark.asyncio
    async def test_no_update_below_init_threshold(self, extractor):
        messages = [{"role": "user", "content": "hi"}]
        result = await extractor.maybe_update("s1", messages, context_tokens=5000)
        assert result is False

    @pytest.mark.asyncio
    async def test_initializes_at_threshold(self, extractor):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="# Session\nUpdated"))]

        with patch("pantheon.utils.llm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            result = await extractor.maybe_update("s1", [
                {"role": "user", "content": "x" * 1000},
                {"role": "assistant", "content": "y" * 1000},
            ], context_tokens=12000)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_update_if_token_growth_insufficient(self, extractor):
        # Force initialization
        state = extractor._state("s1")
        state.initialized = True
        state.tokens_at_last_extraction = 10000

        result = await extractor.maybe_update("s1", [], context_tokens=12000)
        assert result is False  # growth=2000 < 5000


class TestRead:
    def test_read_empty(self, extractor):
        assert extractor.read("nonexistent") == ""

    def test_read_after_write(self, extractor):
        frontmatter = {
            "title": "Test Session",
            "summary": "Test summary",
            "type": "session_note",
            "session_id": "s1",
            "updated": "2026-04-15T10:00:00Z",
        }
        extractor._write("s1", frontmatter, "# Session\nContent here")
        content = extractor.read("s1")
        assert "Content here" in content
        assert "---" not in content  # Frontmatter should be stripped by read()


class TestIsEmptyTemplate:
    def test_empty_is_template(self, extractor):
        assert extractor.is_empty_template("nonexistent") is True

    def test_template_only_is_template(self, extractor):
        from pantheon.internal.memory_system.prompts import SESSION_MEMORY_TEMPLATE
        # Write template directly to file (bypassing _write which adds Metadata)
        path = extractor._note_path("s1")
        path.write_text(SESSION_MEMORY_TEMPLATE, encoding="utf-8")
        assert extractor.is_empty_template("s1") is True

    def test_content_is_not_template(self, extractor):
        frontmatter = {
            "title": "Test Session",
            "summary": "Test summary",
            "type": "session_note",
            "session_id": "s1",
            "updated": "2026-04-15T10:00:00Z",
        }
        extractor._write("s1", frontmatter, "# Session\n_desc_\nActual content here")
        assert extractor.is_empty_template("s1") is False


class TestBoundary:
    def test_no_boundary_before_init(self, extractor):
        assert extractor.get_last_summarized_index("s1", []) is None

    def test_boundary_after_extraction(self, extractor):
        state = extractor._state("s1")
        state.initialized = True
        state.last_message_index = 10
        messages = [{}] * 20
        assert extractor.get_last_summarized_index("s1", messages) == 10


class TestPendingDrain:
    @pytest.mark.asyncio
    async def test_sets_pending_when_in_progress(self, extractor):
        """When in-flight, a new call records pending state instead of dropping."""
        state = extractor._state("s1")
        state.initialized = True
        state.tokens_at_last_extraction = 0
        state.extraction_in_progress = True

        await extractor.maybe_update("s1", [{"role": "user", "content": "x"}], context_tokens=15000)

        assert state.pending_messages is not None
        assert state.pending_tokens == 15000

    @pytest.mark.asyncio
    async def test_pending_overwrites_with_latest(self, extractor):
        """Multiple calls while in-flight keep only the latest pending state."""
        state = extractor._state("s1")
        state.initialized = True
        state.tokens_at_last_extraction = 0
        state.extraction_in_progress = True

        msgs_a = [{"role": "user", "content": "a"}]
        msgs_b = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]

        await extractor.maybe_update("s1", msgs_a, context_tokens=12000)
        await extractor.maybe_update("s1", msgs_b, context_tokens=16000)

        assert state.pending_tokens == 16000
        assert state.pending_messages is msgs_b


class TestWaitForExtraction:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_idle(self, extractor):
        await extractor.wait_for_extraction("s1")  # should not hang


class TestToolCounting:
    def test_count_tool_calls(self, extractor):
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"function": {"name": "read"}},
                {"function": {"name": "write"}},
            ]},
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "search"}},
            ]},
        ]
        assert extractor._count_tool_calls_since(messages, 0) == 3
        assert extractor._count_tool_calls_since(messages, 2) == 1

    def test_last_turn_has_tools(self, extractor):
        with_tools = [
            {"role": "assistant", "content": "x", "tool_calls": [{"function": {"name": "y"}}]},
        ]
        assert extractor._last_turn_has_tools(with_tools) is True

        without_tools = [
            {"role": "assistant", "content": "x"},
        ]
        assert extractor._last_turn_has_tools(without_tools) is False
