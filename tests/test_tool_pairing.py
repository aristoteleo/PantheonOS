import pytest

from pantheon.utils.tool_pairing import (
    INCOMPLETE_TOOL_RESULT_PLACEHOLDER,
    ensure_tool_result_pairing,
    ensure_tool_result_pairing_with_stats,
)


def test_ensure_tool_result_pairing_inserts_placeholder_for_missing_response():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "agent_name": "tester",
            "execution_context_id": "ctx-1",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "next"},
    ]

    repaired, stats = ensure_tool_result_pairing_with_stats(messages)

    assert stats.inserted_placeholder_tool_messages == 1
    assert repaired[1]["tool_calls"][0]["id"] == "call_1"
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "call_1"
    assert repaired[2]["tool_name"] == "shell"
    assert repaired[2]["content"] == INCOMPLETE_TOOL_RESULT_PLACEHOLDER
    assert repaired[2]["execution_context_id"] == "ctx-1"
    assert repaired[2]["agent_name"] == "tester"
    assert repaired[3] == {"role": "user", "content": "next"}


def test_ensure_tool_result_pairing_dedupes_duplicate_calls_and_results():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                },
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "grep", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "tool_name": "shell", "content": "first"},
        {"role": "tool", "tool_call_id": "call_1", "tool_name": "shell", "content": "duplicate"},
        {"role": "tool", "tool_call_id": "call_2", "tool_name": "grep", "content": "second"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "grep", "arguments": "{}"},
                }
            ],
        },
    ]

    repaired, stats = ensure_tool_result_pairing_with_stats(messages)

    assert [tc["id"] for tc in repaired[0]["tool_calls"]] == ["call_1", "call_2"]
    assert [msg["tool_call_id"] for msg in repaired[1:3]] == ["call_1", "call_2"]
    assert len(repaired) == 3
    assert stats.dropped_duplicate_tool_calls == 2
    assert stats.dropped_duplicate_tool_messages == 1
    assert stats.dropped_empty_assistant_messages == 1


def test_ensure_tool_result_pairing_strict_mode_raises_on_missing_response():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        }
    ]

    with pytest.raises(ValueError, match="call_1"):
        ensure_tool_result_pairing(messages, strict=True)
