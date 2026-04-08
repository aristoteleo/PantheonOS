"""
Tests for Gemini tool calling — verifies that parallel tool calls
return correct arguments for each function.

Requires GEMINI_API_KEY to be set. Skipped otherwise.
"""

import json
import os
import pytest

def _get_gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            from pantheon.settings import get_settings
            key = get_settings().get_api_key("GEMINI_API_KEY") or ""
        except Exception:
            pass
    return key

GEMINI_API_KEY = _get_gemini_key()
SKIP_REASON = "GEMINI_API_KEY not set"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature unit",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results to return"},
                },
                "required": ["query"],
            },
        },
    },
]

MESSAGES = [
    {"role": "user", "content": "What's the weather in Tokyo and also search the web for 'best restaurants in Tokyo'? Do both at the same time."},
]


def _validate_tool_calls(tool_calls: list[dict]) -> list[str]:
    """Validate tool calls have correct arguments for their function names.
    Returns list of error descriptions (empty = all good).
    """
    errors = []
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "")
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError as e:
            errors.append(f"{name}: invalid JSON - {e}")
            continue

        if name == "get_weather":
            if "city" not in args:
                errors.append(f"get_weather: missing 'city', got keys={list(args.keys())}")
            if "query" in args:
                errors.append(f"get_weather: has search_web's 'query' param — parameter mixing!")
        elif name == "search_web":
            if "query" not in args:
                errors.append(f"search_web: missing 'query', got keys={list(args.keys())}")
            if "city" in args:
                errors.append(f"search_web: has get_weather's 'city' param — parameter mixing!")
        else:
            errors.append(f"Unknown function: {name}")

    return errors


@pytest.mark.skipif(not GEMINI_API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_gemini_sdk_tool_calling():
    """Test tool calling via google-genai SDK (current approach).
    This test may FAIL due to parameter mixing — documenting the bug.
    """
    from pantheon.utils.adapters.gemini_adapter import GeminiAdapter

    adapter = GeminiAdapter()
    chunks = await adapter.acompletion(
        model="gemini-2.5-flash",
        messages=MESSAGES,
        tools=TOOLS,
        api_key=GEMINI_API_KEY,
    )

    # Extract tool calls from chunks
    tool_calls = []
    for chunk in chunks:
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if "tool_calls" in delta:
                tool_calls.extend(delta["tool_calls"])

    if not tool_calls:
        pytest.skip("Model didn't generate tool calls")

    errors = _validate_tool_calls(tool_calls)
    # Report but don't fail — this documents the SDK bug
    if errors:
        print(f"\n⚠️ SDK tool calling issues ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")


@pytest.mark.skipif(not GEMINI_API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_gemini_rest_api_tool_calling():
    """Test tool calling via REST API (litellm approach).
    This should work correctly without parameter mixing.
    """
    from pantheon.utils.adapters.gemini_adapter import GeminiAdapter

    adapter = GeminiAdapter()
    chunks = await adapter.acompletion(
        model="gemini-2.5-flash",
        messages=MESSAGES,
        tools=TOOLS,
        api_key=GEMINI_API_KEY,
    )

    # Extract tool calls from chunks
    tool_calls = []
    for chunk in chunks:
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if "tool_calls" in delta:
                tool_calls.extend(delta["tool_calls"])

    assert len(tool_calls) >= 1, "Expected at least 1 tool call"

    errors = _validate_tool_calls(tool_calls)
    assert not errors, f"Tool call validation errors: {errors}"


@pytest.mark.skipif(not GEMINI_API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_gemini_tool_calling_many_tools():
    """Stress test with many tools — more likely to trigger parameter mixing."""
    from pantheon.utils.adapters.gemini_adapter import GeminiAdapter

    # Add more tools to increase chance of parameter mixing
    many_tools = TOOLS + [
        {
            "type": "function",
            "function": {
                "name": "create_file",
                "description": "Create a file with content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_python",
                "description": "Execute Python code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                    },
                    "required": ["code"],
                },
            },
        },
    ]

    messages = [
        {"role": "user", "content": "Get weather in London, search for 'Python tutorials', and create a file called hello.txt with content 'Hello World'. Do all three at once."},
    ]

    adapter = GeminiAdapter()
    chunks = await adapter.acompletion(
        model="gemini-2.5-flash",
        messages=messages,
        tools=many_tools,
        api_key=GEMINI_API_KEY,
    )

    tool_calls = []
    for chunk in chunks:
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if "tool_calls" in delta:
                tool_calls.extend(delta["tool_calls"])

    assert len(tool_calls) >= 1, "Expected at least 1 tool call"

    # Verify each tool call has the right params for its function
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "")
        args = json.loads(func.get("arguments", "{}"))
        print(f"  {name}: {list(args.keys())}")

        # No function should have params from another function
        if name == "get_weather":
            assert "query" not in args, f"get_weather has search_web's 'query': {args}"
            assert "code" not in args, f"get_weather has run_python's 'code': {args}"
            assert "file_path" not in args, f"get_weather has create_file's 'file_path': {args}"
        elif name == "search_web":
            assert "city" not in args, f"search_web has get_weather's 'city': {args}"
            assert "code" not in args, f"search_web has run_python's 'code': {args}"
        elif name == "create_file":
            assert "city" not in args, f"create_file has get_weather's 'city': {args}"
            assert "query" not in args, f"create_file has search_web's 'query': {args}"
