"""
Unit tests for native tool-result image routing.

Covers:
- vision_capability.supports_tool_result_image() returns the right provider verdict
- image_blocks helpers split content correctly
- Anthropic adapter translates OpenAI image_url in tool messages → image blocks
- Gemini adapter emits functionResponse + inline_data parts when tool returns images
- OpenAI Chat Completions adapter sanitises tool-message images to a text placeholder
- Responses API path emits input_image items in function_call_output
- observe_images tool picks native vs sub-agent mode based on active model
- Notebook execute summary helper produces useful text for image blocks
- Agent-layer opt-in path preserves list content instead of JSON-stringifying
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from pantheon.utils.vision_capability import supports_tool_result_image
from pantheon.utils.adapters.image_blocks import (
    has_image_content,
    resolve_image_url,
    split_text_and_images,
)
from pantheon.utils.adapters.anthropic_adapter import (
    _convert_messages_to_anthropic,
    _content_to_anthropic_tool_result,
)
from pantheon.utils.adapters.gemini_adapter import _convert_messages_to_gemini
from pantheon.utils.adapters.openai_adapter import (
    _sanitize_tool_messages_for_chat_completions,
)
from pantheon.utils.llm import (
    _convert_messages_to_responses_input,
    _tool_output_for_responses,
)


# Tiny 1×1 PNG (base64). Enough for tests without external files.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABXvMqOgAAAABJRU5ErkJggg=="
)
_TINY_PNG_DATA_URI = f"data:image/png;base64,{_TINY_PNG_B64}"


# ============ capability detection ============


class TestCapabilityDetection:

    def test_none_returns_false(self):
        assert supports_tool_result_image(None) is False

    def test_anthropic_supported(self):
        assert supports_tool_result_image("anthropic/claude-sonnet-4-6") is True
        assert supports_tool_result_image("claude-opus-4") is True

    def test_gemini_supported(self):
        assert supports_tool_result_image("gemini/gemini-2.5-pro") is True
        assert supports_tool_result_image("google/gemini-2.0-flash") is True

    def test_openai_defaults_to_responses_api_supported(self):
        """OpenAI models now default to the Responses API, which supports
        input_image in function_call_output, so tool-result images work
        natively on the first call."""
        from pantheon.utils.llm_providers import reset_responses_api_cache

        reset_responses_api_cache()
        assert supports_tool_result_image("gpt-4o") is True
        assert supports_tool_result_image("openai/gpt-4o") is True
        assert supports_tool_result_image("gpt-5") is True
        assert supports_tool_result_image("openai/gpt-5.4") is True

    def test_openai_responses_api_supported(self):
        # codex and *-pro models go through Responses API which supports images
        assert supports_tool_result_image("codex-mini-latest") is True

    def test_codex_oauth_supported(self):
        # Codex OAuth (codex/gpt-5.x) uses the backend-api Responses endpoint
        # which supports input_image in function_call_output.
        assert supports_tool_result_image("codex/gpt-5.4") is True
        assert supports_tool_result_image("codex/gpt-5.4-mini") is True
        assert supports_tool_result_image("codex/gpt-5.2-codex") is True

    def test_cached_unavailable_flips_openai_to_false(self):
        """Once the Responses API probe has failed for a (base_url, model)
        pair, supports_tool_result_image must flip to False so observe_images
        falls back to the sub-agent path instead of sending image blocks that
        would be stripped by the Chat Completions sanitiser."""
        from pantheon.utils.llm_providers import (
            ProviderConfig,
            ProviderType,
            mark_responses_api_unavailable,
            reset_responses_api_cache,
        )

        reset_responses_api_cache()
        assert supports_tool_result_image("openai/gpt-5.4") is True
        mark_responses_api_unavailable(
            ProviderConfig(provider_type=ProviderType.OPENAI, model_name="gpt-5.4")
        )
        assert supports_tool_result_image("openai/gpt-5.4") is False
        # Other OpenAI models are independent.
        assert supports_tool_result_image("openai/gpt-4o") is True
        reset_responses_api_cache()

    def test_anthropic_and_gemini_unaffected_by_openai_cache(self):
        """Native providers (Anthropic/Gemini) report support regardless of
        the OpenAI Responses cache state."""
        from pantheon.utils.llm_providers import reset_responses_api_cache

        reset_responses_api_cache()
        assert supports_tool_result_image("anthropic/claude-sonnet-4-6") is True
        assert supports_tool_result_image("gemini/gemini-2.5-pro") is True


# ============ image_blocks helpers ============


class TestImageBlocksHelpers:

    def test_has_image_content_true(self):
        assert (
            has_image_content(
                [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "x"}},
                ]
            )
            is True
        )

    def test_has_image_content_string(self):
        assert has_image_content("plain string") is False

    def test_has_image_content_text_only(self):
        assert has_image_content([{"type": "text", "text": "hi"}]) is False

    def test_resolve_data_uri(self):
        mime, data = resolve_image_url(_TINY_PNG_DATA_URI)
        assert mime == "image/png"
        assert data == _TINY_PNG_B64

    def test_resolve_jpg_normalized_to_jpeg(self):
        uri = "data:image/jpg;base64,AAAA"
        mime, data = resolve_image_url(uri)
        assert mime == "image/jpeg"
        assert data == "AAAA"

    def test_resolve_http_returns_none(self):
        assert resolve_image_url("https://example.com/a.png") is None

    def test_split_content(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            {"type": "image_url", "image_url": {"url": "https://e.com/x.png"}},
        ]
        text, inline, http = split_text_and_images(content)
        assert text == "hello"
        assert len(inline) == 1
        assert inline[0] == ("image/png", _TINY_PNG_B64)
        assert http == ["https://e.com/x.png"]


# ============ Anthropic adapter ============


class TestAnthropicAdapter:

    def test_tool_result_plain_string(self):
        out = _content_to_anthropic_tool_result("plain text")
        assert out == "plain text"

    def test_tool_result_text_only_list(self):
        out = _content_to_anthropic_tool_result(
            [{"type": "text", "text": "hello"}]
        )
        assert out == "hello"

    def test_tool_result_with_image(self):
        blocks = _content_to_anthropic_tool_result(
            [
                {"type": "text", "text": "see this"},
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            ]
        )
        assert isinstance(blocks, list)
        assert blocks[0] == {"type": "text", "text": "see this"}
        img = blocks[1]
        assert img["type"] == "image"
        assert img["source"]["type"] == "base64"
        assert img["source"]["media_type"] == "image/png"
        assert img["source"]["data"] == _TINY_PNG_B64

    def test_convert_messages_tool_image(self):
        messages = [
            {"role": "user", "content": "look at this"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "observe_images", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "text", "text": "here's the image"},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                ],
            },
        ]
        _system, converted = _convert_messages_to_anthropic(messages)
        # Find the tool_result block
        tool_result_msg = None
        for m in converted:
            for block in m.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_result_msg = block
                    break
        assert tool_result_msg is not None
        inner = tool_result_msg["content"]
        assert isinstance(inner, list)
        assert any(b.get("type") == "image" for b in inner)
        img_block = [b for b in inner if b["type"] == "image"][0]
        assert img_block["source"]["media_type"] == "image/png"
        assert img_block["source"]["data"] == _TINY_PNG_B64


# ============ Gemini adapter ============


class TestGeminiAdapter:

    def test_tool_with_image_emits_inline_data(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "observe_images", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "observe_images",
                "content": [
                    {"type": "text", "text": "saw cat"},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                ],
            },
        ]
        _sys, contents = _convert_messages_to_gemini(messages)
        # Find the user message carrying the functionResponse
        fn_response_msg = None
        for m in contents:
            parts = m.get("parts") or []
            if any("functionResponse" in p for p in parts):
                fn_response_msg = m
                break
        assert fn_response_msg is not None
        parts = fn_response_msg["parts"]
        # First part = functionResponse with the text summary
        assert "functionResponse" in parts[0]
        assert parts[0]["functionResponse"]["response"]["result"] == "saw cat"
        # Subsequent parts carry the actual image bytes via inline_data
        inline_parts = [p for p in parts if "inline_data" in p]
        assert len(inline_parts) == 1
        assert inline_parts[0]["inline_data"]["mime_type"] == "image/png"
        assert inline_parts[0]["inline_data"]["data"] == _TINY_PNG_B64


# ============ OpenAI Chat Completions sanitiser ============


class TestOpenAIChatCompletionsSanitiser:

    def test_strip_image_from_tool_message(self):
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "text", "text": "here"},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                ],
            },
        ]
        out = _sanitize_tool_messages_for_chat_completions(messages)
        tool_msg = out[1]
        content = tool_msg["content"]
        assert isinstance(content, str)
        assert "here" in content
        assert "1 image(s)" in content

    def test_passthrough_non_image_tool(self):
        messages = [
            {"role": "tool", "tool_call_id": "c", "content": "ok"},
        ]
        out = _sanitize_tool_messages_for_chat_completions(messages)
        assert out[0]["content"] == "ok"


# ============ Responses API conversion ============


class TestResponsesAPIConversion:

    def test_tool_output_string_unchanged(self):
        messages = [
            {"role": "tool", "tool_call_id": "c1", "content": "hello"},
        ]
        _instructions, items = _convert_messages_to_responses_input(messages)
        fco = next(i for i in items if i.get("type") == "function_call_output")
        assert fco["output"] == "hello"

    def test_tool_output_with_image_emits_items(self):
        messages = [
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": [
                    {"type": "text", "text": "see"},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                ],
            },
        ]
        _instructions, items = _convert_messages_to_responses_input(messages)
        fco = next(i for i in items if i.get("type") == "function_call_output")
        output = fco["output"]
        assert isinstance(output, list)
        types = [it.get("type") for it in output]
        assert "input_text" in types
        assert "input_image" in types
        img_item = next(it for it in output if it["type"] == "input_image")
        assert img_item["image_url"].startswith("data:image/png;base64,")


# ============ more: image_blocks edge cases ============


class TestImageBlocksEdgeCases:

    def test_invalid_data_uri_returns_none(self):
        assert resolve_image_url("data:text/plain;base64,AA") is None

    def test_malformed_data_uri_returns_none(self):
        assert resolve_image_url("data:image/png;something_wrong") is None

    def test_empty_url(self):
        assert resolve_image_url("") is None

    def test_non_file_path_not_leading_slash(self):
        # "foo/bar.png" — no leading slash, no scheme → skipped
        assert resolve_image_url("foo/bar.png") is None

    def test_split_text_empty_list(self):
        text, inline, http = split_text_and_images([])
        assert text == ""
        assert inline == []
        assert http == []

    def test_split_text_preserves_multiple_text_blocks(self):
        text, inline, http = split_text_and_images([
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            {"type": "text", "text": "second"},
        ])
        # Both text pieces survive (joined with double newline).
        assert "first" in text
        assert "second" in text
        assert len(inline) == 1

    def test_split_skips_empty_url(self):
        text, inline, http = split_text_and_images([
            {"type": "image_url", "image_url": {"url": ""}},
        ])
        assert text == ""
        assert inline == []
        assert http == []

    def test_split_unknown_item_stringified(self):
        text, inline, http = split_text_and_images([
            "raw string item",  # not a dict
            {"type": "text", "text": "real text"},
        ])
        assert "raw string item" in text
        assert "real text" in text


# ============ file:// resolution via PIL ============


@pytest.fixture
def tiny_png_file(tmp_path):
    """Write a real 2×2 PNG to disk and return its absolute path."""
    path = tmp_path / "tiny.png"
    img = Image.new("RGB", (2, 2), color=(255, 0, 0))
    img.save(path, format="PNG")
    return path


class TestFilePathResolution:

    def test_resolve_file_uri(self, tiny_png_file):
        mime, data = resolve_image_url(f"file://{tiny_png_file}")
        assert mime.startswith("image/")
        # Should decode without error
        decoded = base64.b64decode(data)
        assert len(decoded) > 0

    def test_resolve_absolute_path(self, tiny_png_file):
        mime, data = resolve_image_url(str(tiny_png_file))
        assert mime.startswith("image/")
        assert len(base64.b64decode(data)) > 0

    def test_resolve_missing_file(self, tmp_path):
        missing = tmp_path / "does-not-exist.png"
        # Should gracefully return None, not raise
        assert resolve_image_url(str(missing)) is None


# ============ Anthropic: multiple images + HTTP URL ============


class TestAnthropicMultipleImages:

    def test_two_inline_images(self):
        second_data = "iVBORw0KGgoSECOND"
        blocks = _content_to_anthropic_tool_result([
            {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{second_data}"}},
        ])
        assert isinstance(blocks, list)
        image_blocks = [b for b in blocks if b["type"] == "image"]
        assert len(image_blocks) == 2
        assert image_blocks[0]["source"]["media_type"] == "image/png"
        assert image_blocks[1]["source"]["media_type"] == "image/jpeg"
        assert image_blocks[1]["source"]["data"] == second_data

    def test_http_url_becomes_url_source(self):
        blocks = _content_to_anthropic_tool_result([
            {"type": "text", "text": "see hosted img"},
            {"type": "image_url", "image_url": {"url": "https://example.com/pic.png"}},
        ])
        assert isinstance(blocks, list)
        url_blocks = [b for b in blocks if b.get("type") == "image" and b["source"].get("type") == "url"]
        assert len(url_blocks) == 1
        assert url_blocks[0]["source"]["url"] == "https://example.com/pic.png"

    def test_empty_tool_content_returns_empty_string(self):
        assert _content_to_anthropic_tool_result("") == ""
        assert _content_to_anthropic_tool_result([]) == ""


# ============ Gemini: multiple images + HTTP fallback ============


class TestGeminiMultipleImages:

    def test_multiple_inline_parts(self):
        second_data = "iVBORw0KGgoSECOND"
        messages = [
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "observe_images",
                "content": [
                    {"type": "text", "text": "two images"},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{second_data}"}},
                ],
            },
        ]
        _sys, contents = _convert_messages_to_gemini(messages)
        parts = contents[0]["parts"]
        inline_parts = [p for p in parts if "inline_data" in p]
        assert len(inline_parts) == 2
        mimes = {p["inline_data"]["mime_type"] for p in inline_parts}
        assert mimes == {"image/png", "image/jpeg"}

    def test_http_url_becomes_text_fallback(self):
        messages = [
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "observe_images",
                "content": [
                    {"type": "text", "text": "hosted"},
                    {"type": "image_url", "image_url": {"url": "https://e.com/x.png"}},
                ],
            },
        ]
        _sys, contents = _convert_messages_to_gemini(messages)
        parts = contents[0]["parts"]
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        assert any("https://e.com/x.png" in t for t in text_parts)


# ============ OpenAI sanitiser edge cases ============


class TestOpenAISanitiserEdgeCases:

    def test_multiple_images_counted(self):
        messages = [{
            "role": "tool",
            "tool_call_id": "c",
            "content": [
                {"type": "text", "text": "three pics"},
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                {"type": "image_url", "image_url": {"url": "https://e.com/a.png"}},
            ],
        }]
        out = _sanitize_tool_messages_for_chat_completions(messages)
        assert "3 image(s)" in out[0]["content"]
        assert "three pics" in out[0]["content"]

    def test_does_not_mutate_input(self):
        original = [{
            "role": "tool",
            "tool_call_id": "c",
            "content": [
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            ],
        }]
        _out = _sanitize_tool_messages_for_chat_completions(original)
        # Original content unchanged
        assert isinstance(original[0]["content"], list)
        assert original[0]["content"][0]["type"] == "image_url"

    def test_empty_images_only(self):
        messages = [{
            "role": "tool",
            "tool_call_id": "c",
            "content": [
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            ],
        }]
        out = _sanitize_tool_messages_for_chat_completions(messages)
        # No text, just the placeholder
        assert "1 image(s)" in out[0]["content"]


# ============ Responses API: full round trip ============


class TestResponsesAPIRoundTrip:

    def test_tool_output_helper_directly(self):
        """The helper used by _convert_messages_to_responses_input."""
        # String passthrough
        assert _tool_output_for_responses("hi") == "hi"
        assert _tool_output_for_responses(None) == ""

        # Text-only list collapses to string
        out = _tool_output_for_responses([{"type": "text", "text": "only text"}])
        assert out == "only text"

        # With images → structured list
        result = _tool_output_for_responses([
            {"type": "text", "text": "caption"},
            {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
        ])
        assert isinstance(result, list)
        assert result[0]["type"] == "input_text"
        assert result[1]["type"] == "input_image"
        assert "detail" in result[1]

    def test_multiple_images_in_function_call_output(self):
        messages = [{
            "role": "tool",
            "tool_call_id": "call_x",
            "content": [
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                {"type": "image_url", "image_url": {"url": "https://e.com/x.png"}},
            ],
        }]
        _instructions, items = _convert_messages_to_responses_input(messages)
        fco = next(i for i in items if i.get("type") == "function_call_output")
        output = fco["output"]
        assert isinstance(output, list)
        image_items = [it for it in output if it.get("type") == "input_image"]
        assert len(image_items) == 2
        # Data URI is embedded verbatim
        assert any(i["image_url"].startswith("data:image/png") for i in image_items)
        # HTTP URL is passed through
        assert any(i["image_url"] == "https://e.com/x.png" for i in image_items)


# ============ Agent-layer content_blocks auto-merge ============


class TestAgentContentBlocksMerge:
    """Verify the agent framework's opt-in logic for content_blocks.

    We inline the critical detection logic so the test doesn't need a full
    Agent instance.
    """

    def _detect_native(self, result) -> list | None:
        """Mimic agent.py::_run_single_tool_call native-blocks detection."""
        if not isinstance(result, dict):
            return None
        maybe_blocks = result.get("content_blocks")
        if isinstance(maybe_blocks, list) and any(
            isinstance(b, dict) and b.get("type") == "image_url"
            for b in maybe_blocks
        ):
            return maybe_blocks
        return None

    def test_content_blocks_with_image_detected(self):
        result = {
            "success": True,
            "cell_id": "abc",
            "content_blocks": [
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            ],
        }
        assert self._detect_native(result) is not None

    def test_no_content_blocks_falls_through(self):
        result = {"success": True, "content": "plain text"}
        assert self._detect_native(result) is None

    def test_content_blocks_without_image_ignored(self):
        result = {
            "success": True,
            "content_blocks": [{"type": "text", "text": "no images here"}],
        }
        assert self._detect_native(result) is None

    def test_string_content_not_mistaken_for_blocks(self):
        # Old-style tool results with content: str must NOT trigger native path.
        result = {"success": True, "content": "plain text"}
        assert self._detect_native(result) is None

    def test_guard_empty_tool_results_preserves_content_blocks(self):
        """guard_empty_tool_results must NOT replace a list of content blocks
        with '[No output]' — that path silently drops image payloads."""
        from pantheon.utils.token_optimization import (
            guard_empty_tool_results,
            EMPTY_TOOL_RESULT_PLACEHOLDER,
        )

        messages = [
            {
                "role": "tool",
                "tool_call_id": "c",
                "content": [
                    {"type": "text", "text": "summary"},
                    {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
                ],
            },
        ]
        out = guard_empty_tool_results(messages)
        assert out[0]["content"] != EMPTY_TOOL_RESULT_PLACEHOLDER
        assert isinstance(out[0]["content"], list)
        assert any(b.get("type") == "image_url" for b in out[0]["content"])

    def test_guard_empty_tool_results_still_replaces_truly_empty(self):
        from pantheon.utils.token_optimization import (
            guard_empty_tool_results,
            EMPTY_TOOL_RESULT_PLACEHOLDER,
        )

        # Plain empty string → gets placeholder
        messages = [{"role": "tool", "tool_call_id": "c", "content": ""}]
        out = guard_empty_tool_results(messages)
        assert out[0]["content"] == EMPTY_TOOL_RESULT_PLACEHOLDER

        # Empty list → also placeholder
        messages = [{"role": "tool", "tool_call_id": "c", "content": []}]
        out = guard_empty_tool_results(messages)
        assert out[0]["content"] == EMPTY_TOOL_RESULT_PLACEHOLDER

    def test_structured_data_preserved_when_peeled(self):
        """After extracting content_blocks, remaining dict keeps other fields."""
        result = {
            "success": True,
            "cell_id": "abc",
            "notebook_path": "foo.ipynb",
            "execution_count": 3,
            "content_blocks": [
                {"type": "image_url", "image_url": {"url": _TINY_PNG_DATA_URI}},
            ],
        }
        structured = {k: v for k, v in result.items() if k != "content_blocks"}
        assert structured["cell_id"] == "abc"
        assert structured["notebook_path"] == "foo.ipynb"
        assert structured["execution_count"] == 3
        assert "content_blocks" not in structured
        assert structured["success"] is True


# ============ observe_images routing (mocked) ============


class TestObserveImagesRouting:
    """Verify observe_images picks native vs sub-agent path based on active model."""

    @pytest.fixture
    def sample_image(self, tmp_path):
        p = tmp_path / "test.png"
        img = Image.new("RGB", (4, 4), (0, 128, 255))
        img.save(p, format="PNG")
        return str(p)

    @pytest.mark.asyncio
    async def test_native_mode_returns_content_blocks(self, sample_image, monkeypatch):
        from pantheon.toolsets.file.file_manager import FileManagerToolSet

        fm = FileManagerToolSet(name="test_fm")

        # Patch the execution context so get_context() returns a stub.
        mock_ctx = MagicMock()
        mock_ctx.call_agent = AsyncMock(return_value={"success": True, "response": "fallback"})
        monkeypatch.setattr(fm, "get_context", lambda: mock_ctx)

        # Force capability detection to return True (simulate Anthropic/Gemini).
        with patch(
            "pantheon.agent.get_current_run_model",
            return_value="anthropic/claude-sonnet-4-6",
        ):
            result = await fm.observe_images.__wrapped__(
                fm, question="what is this?", image_paths=[sample_image]
            )

        assert result["success"] is True
        # Native mode uses the content_blocks opt-in field, leaving other
        # structured metadata (question, image_count) at the top level.
        assert "content_blocks" in result
        assert isinstance(result["content_blocks"], list)
        assert result.get("question") == "what is this?"
        assert result.get("image_count") == 1
        assert result.get("mode") == "native"
        # Sub-agent should NOT have been invoked
        mock_ctx.call_agent.assert_not_called()
        # Every block is an image_url (no text block — framework adds one)
        types = [b.get("type") for b in result["content_blocks"]]
        assert all(t == "image_url" for t in types)

    @pytest.mark.asyncio
    async def test_subagent_mode_calls_call_agent(self, sample_image, monkeypatch):
        from pantheon.toolsets.file.file_manager import FileManagerToolSet
        from pantheon.utils.llm_providers import (
            ProviderConfig,
            ProviderType,
            mark_responses_api_unavailable,
            reset_responses_api_cache,
        )

        fm = FileManagerToolSet(name="test_fm")

        mock_ctx = MagicMock()
        mock_ctx.call_agent = AsyncMock(
            return_value={"success": True, "response": "summary text"}
        )
        monkeypatch.setattr(fm, "get_context", lambda: mock_ctx)

        # Simulate the Responses API being unavailable for this model
        # (e.g., proxy endpoint that doesn't implement /v1/responses), which
        # flips supports_tool_result_image to False and forces the sub-agent
        # path — the same fallback we use on a 404 in production.
        reset_responses_api_cache()
        mark_responses_api_unavailable(
            ProviderConfig(provider_type=ProviderType.OPENAI, model_name="gpt-4o")
        )
        try:
            with patch(
                "pantheon.agent.get_current_run_model",
                return_value="gpt-4o",
            ):
                result = await fm.observe_images.__wrapped__(
                    fm, question="what is this?", image_paths=[sample_image]
                )
        finally:
            reset_responses_api_cache()

        assert result["success"] is True
        # Sub-agent SHOULD have been invoked
        mock_ctx.call_agent.assert_called_once()
        # Content is a plain text string from the sub-agent response
        assert isinstance(result["content"], str)
        assert "summary text" in result["content"]

    @pytest.mark.asyncio
    async def test_missing_image_returns_error(self, tmp_path, monkeypatch):
        from pantheon.toolsets.file.file_manager import FileManagerToolSet

        fm = FileManagerToolSet(name="test_fm")
        monkeypatch.setattr(fm, "get_context", lambda: MagicMock())

        result = await fm.observe_images.__wrapped__(
            fm, question="x", image_paths=[str(tmp_path / "missing.png")]
        )
        assert result["success"] is False
        assert "does not exist" in result["error"]
