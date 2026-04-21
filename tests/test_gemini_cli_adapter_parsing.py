"""Regression tests for Gemini CLI adapter parsing.

Protects against the bug where multiple ``functionCall`` parts in a single
Gemini response got merged into one tool_call with concatenated ``arguments``
strings (``{a}{b}``), producing invalid JSON at call time. Root cause was
the missing ``index`` field on emitted tool_calls — ``stream_chunk_builder``
keys tool_calls by ``index`` and, when it's missing, every entry defaults to
``0`` and their ``function.arguments`` get ``+=`` concatenated.
"""

from __future__ import annotations

import json

import pytest

from pantheon.utils.adapters.gemini_cli_adapter import _extract_gemini_text_and_tool_calls
from pantheon.utils.llm import stream_chunk_builder


def test_multiple_function_calls_get_distinct_indices():
    payload = {
        "candidates": [{
            "content": {
                "parts": [
                    {"functionCall": {"name": "write_file", "args": {"path": "/a", "content": "foo"}}},
                    {"functionCall": {"name": "write_file", "args": {"path": "/b", "content": "bar"}}},
                ],
            },
            "finishReason": "STOP",
        }],
    }
    _text, tool_calls, _raw, _stop = _extract_gemini_text_and_tool_calls(payload)

    assert len(tool_calls) == 2
    indices = [tc.get("index") for tc in tool_calls]
    assert indices == [0, 1], f"expected sequential indices, got {indices}"


def test_single_function_call_still_has_index_zero():
    payload = {
        "candidates": [{
            "content": {
                "parts": [
                    {"functionCall": {"name": "search", "args": {"q": "hello"}}},
                ],
            },
            "finishReason": "STOP",
        }],
    }
    _text, tool_calls, _raw, _stop = _extract_gemini_text_and_tool_calls(payload)
    assert len(tool_calls) == 1
    assert tool_calls[0].get("index") == 0


def test_builder_keeps_parallel_tool_calls_separate():
    """End-to-end: parsed parts → stream_chunk_builder → assembled message.
    Before the fix this test would produce a single tool_call whose
    ``function.arguments`` is ``{...}{...}`` (invalid JSON).
    """
    payload = {
        "candidates": [{
            "content": {
                "parts": [
                    {"functionCall": {"name": "write_file", "args": {"path": "/a", "content": "foo"}}},
                    {"functionCall": {"name": "write_file", "args": {"path": "/b", "content": "bar"}}},
                ],
            },
            "finishReason": "STOP",
        }],
    }
    _text, tool_calls, _raw, _stop = _extract_gemini_text_and_tool_calls(payload)
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": tool_calls}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    resp = stream_chunk_builder(chunks)
    built = resp.choices[0].message.tool_calls

    assert built is not None and len(built) == 2
    # Both must have valid JSON arguments (the bug made them invalid)
    for tc in built:
        args = json.loads(tc["function"]["arguments"])
        assert "path" in args and "content" in args


def test_text_only_response_has_no_tool_calls():
    payload = {
        "candidates": [{
            "content": {"parts": [{"text": "hello"}]},
            "finishReason": "STOP",
        }],
    }
    text, tool_calls, _raw, _stop = _extract_gemini_text_and_tool_calls(payload)
    assert text == "hello"
    assert tool_calls == []


# ============================================================================
# _messages_to_gemini_rest_contents — parallel tool-response coalescing
# ============================================================================

from pantheon.utils.adapters.gemini_cli_adapter import _messages_to_gemini_rest_contents


def test_parallel_tool_responses_coalesce_into_one_user_content():
    """Gemini requires the number of functionResponse parts in the user
    turn to equal the number of functionCall parts in the preceding model
    turn. Our OpenAI-style history has one tool message per tool_call_id,
    so two parallel calls end up as two separate tool messages. The
    adapter must merge them into a single user content.
    """
    messages = [
        {"role": "user", "content": "do two things"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"/a"}'}},
                {"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"/b"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "write_file", "content": '{"ok":true,"path":"/a"}'},
        {"role": "tool", "tool_call_id": "c2", "name": "write_file", "content": '{"ok":true,"path":"/b"}'},
        {"role": "user", "content": "thanks"},
    ]

    contents = _messages_to_gemini_rest_contents(messages)

    # Expect 4 contents: user(text), model(2 functionCalls), user(2 functionResponses), user(text)
    roles = [c["role"] for c in contents]
    assert roles == ["user", "model", "user", "user"], roles

    model_parts = contents[1]["parts"]
    function_calls = [p for p in model_parts if "functionCall" in p]
    assert len(function_calls) == 2

    tool_response_content = contents[2]
    function_responses = [p for p in tool_response_content["parts"] if "functionResponse" in p]
    assert len(function_responses) == 2, (
        f"Expected 2 functionResponse parts coalesced, got {len(function_responses)}"
    )
    assert len(function_responses) == len(function_calls), (
        "Gemini requires matching counts of functionCall ↔ functionResponse"
    )


def test_single_tool_response_still_emits_single_user_content():
    """Single tool call path: no coalescing regression."""
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": '{"p":"/a"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": '{"ok":true}'},
    ]
    contents = _messages_to_gemini_rest_contents(messages)
    assert [c["role"] for c in contents] == ["model", "user"]
    assert len(contents[1]["parts"]) == 1
    assert "functionResponse" in contents[1]["parts"][0]


def test_tool_messages_at_end_still_get_flushed():
    """A trailing run of tool messages must still be emitted."""
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "read", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "ok"},
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": "ok"},
    ]
    contents = _messages_to_gemini_rest_contents(messages)
    assert [c["role"] for c in contents] == ["model", "user"]
    assert sum(1 for p in contents[1]["parts"] if "functionResponse" in p) == 2
