"""Tests for OpenAI Responses API support (codex models)."""

import json
import os
from unittest.mock import MagicMock

import pytest

from pantheon.utils.llm_providers import (
    ProviderConfig,
    ProviderType,
    is_responses_api_model,
)
from pantheon.utils.llm import (
    _convert_content_to_responses_blocks,
    _convert_messages_to_responses_input,
    _convert_tools_for_responses,
    _convert_model_params_for_responses,
)

HAS_OPENAI_KEY = bool(os.environ.get("OPENAI_API_KEY"))
CODEX_MODEL = "gpt-5.1-codex-mini"


# ============ is_responses_api_model ============


class TestIsResponsesApiModel:
    def test_codex_model_openai(self):
        config = ProviderConfig(provider_type=ProviderType.OPENAI, model_name="codex-mini-latest")
        assert is_responses_api_model(config) is True

    def test_codex_model_with_prefix(self):
        config = ProviderConfig(provider_type=ProviderType.OPENAI, model_name="openai/codex-mini-latest")
        assert is_responses_api_model(config) is True

    def test_codex_model_case_insensitive(self):
        config = ProviderConfig(provider_type=ProviderType.OPENAI, model_name="Codex-Mini")
        assert is_responses_api_model(config) is True

    def test_non_codex_openai(self):
        config = ProviderConfig(provider_type=ProviderType.OPENAI, model_name="gpt-4o")
        assert is_responses_api_model(config) is False

    def test_codex_model_native_provider(self):
        """Codex model but via native provider should NOT use Responses API."""
        config = ProviderConfig(provider_type=ProviderType.NATIVE, model_name="codex-mini-latest")
        assert is_responses_api_model(config) is False

    def test_non_codex_native(self):
        config = ProviderConfig(provider_type=ProviderType.NATIVE, model_name="anthropic/claude-3-opus")
        assert is_responses_api_model(config) is False

    def test_o1_model_not_codex(self):
        config = ProviderConfig(provider_type=ProviderType.OPENAI, model_name="o1-mini")
        assert is_responses_api_model(config) is False


# ============ _convert_messages_to_responses_input ============


class TestConvertMessagesToResponsesInput:
    def test_system_user_assistant(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions == "You are helpful."
        assert len(items) == 2
        assert items[0] == {"role": "user", "content": "Hello"}
        assert items[1] == {"role": "assistant", "content": "Hi there!"}

    def test_multiple_system_messages(self):
        messages = [
            {"role": "system", "content": "First system"},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Second system"},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions == "First system"
        assert len(items) == 2
        assert items[0] == {"role": "user", "content": "Hi"}
        assert items[1] == {"role": "developer", "content": "Second system"}

    def test_no_system_message(self):
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions is None
        assert len(items) == 1
        assert items[0] == {"role": "user", "content": "Hello"}

    def test_assistant_with_tool_calls(self):
        messages = [
            {"role": "assistant", "content": "Let me check.", "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                }
            ]},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions is None
        assert len(items) == 2
        assert items[0] == {"role": "assistant", "content": "Let me check."}
        assert items[1] == {
            "type": "function_call",
            "call_id": "call_123",
            "name": "get_weather",
            "arguments": '{"city": "NYC"}',
        }

    def test_assistant_with_tool_calls_no_content(self):
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {
                    "id": "call_456",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q": "test"}'},
                }
            ]},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        # No text content → no assistant text item
        assert len(items) == 1
        assert items[0]["type"] == "function_call"

    def test_tool_message(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_123", "content": "Sunny, 72F"},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions is None
        assert len(items) == 1
        assert items[0] == {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "Sunny, 72F",
        }

    def test_full_conversation(self):
        """Test a realistic multi-turn conversation with tool calls."""
        messages = [
            {"role": "system", "content": "You are a weather assistant."},
            {"role": "user", "content": "What's the weather in NYC?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                }
            ]},
            {"role": "tool", "tool_call_id": "call_abc", "content": "Sunny, 72F"},
            {"role": "assistant", "content": "It's sunny and 72F in NYC!"},
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions == "You are a weather assistant."
        assert len(items) == 4
        assert items[0] == {"role": "user", "content": "What's the weather in NYC?"}
        assert items[1] == {
            "type": "function_call",
            "call_id": "call_abc",
            "name": "get_weather",
            "arguments": '{"city": "NYC"}',
        }
        assert items[2] == {
            "type": "function_call_output",
            "call_id": "call_abc",
            "output": "Sunny, 72F",
        }
        assert items[3] == {"role": "assistant", "content": "It's sunny and 72F in NYC!"}

    def test_user_multimodal_content_blocks_are_converted(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAA", "detail": "high"},
                    },
                ],
            }
        ]
        instructions, items = _convert_messages_to_responses_input(messages)
        assert instructions is None
        assert items == [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe this image"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,AAA",
                        "detail": "high",
                    },
                ],
            }
        ]

    def test_assistant_content_blocks_use_output_text(self):
        assert _convert_content_to_responses_blocks(
            "assistant",
            [{"type": "text", "text": "Done"}],
        ) == [{"type": "output_text", "text": "Done"}]


# ============ _convert_tools_for_responses ============


class TestConvertToolsForResponses:
    def test_basic_tool(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]
        result = _convert_tools_for_responses(tools)
        assert result is not None
        assert len(result) == 1
        assert result[0] == {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }

    def test_tool_with_strict(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search",
                    "parameters": {"type": "object", "properties": {}},
                    "strict": True,
                },
            }
        ]
        result = _convert_tools_for_responses(tools)
        assert result[0]["strict"] is True

    def test_none_tools(self):
        assert _convert_tools_for_responses(None) is None

    def test_empty_tools(self):
        assert _convert_tools_for_responses([]) is None

    def test_multiple_tools(self):
        tools = [
            {"type": "function", "function": {"name": "tool_a", "description": "A"}},
            {"type": "function", "function": {"name": "tool_b", "description": "B"}},
        ]
        result = _convert_tools_for_responses(tools)
        assert len(result) == 2
        assert result[0]["name"] == "tool_a"
        assert result[1]["name"] == "tool_b"


# ============ _convert_model_params_for_responses ============


class TestConvertModelParamsForResponses:
    def test_none(self):
        assert _convert_model_params_for_responses(None) == {}

    def test_empty(self):
        assert _convert_model_params_for_responses({}) == {}

    def test_max_tokens(self):
        result = _convert_model_params_for_responses({"max_tokens": 1024})
        assert result == {"max_output_tokens": 1024}
        assert "max_tokens" not in result

    def test_reasoning_effort(self):
        result = _convert_model_params_for_responses({"reasoning_effort": "high"})
        assert result == {"reasoning": {"effort": "high"}}
        assert "reasoning_effort" not in result

    def test_passthrough_params(self):
        result = _convert_model_params_for_responses({"temperature": 0.7, "top_p": 0.9})
        assert result == {"temperature": 0.7, "top_p": 0.9}

    def test_dropped_params(self):
        result = _convert_model_params_for_responses({
            "stream_options": {"include_usage": True},
            "num_retries": 3,
            "temperature": 0.5,
        })
        assert result == {"temperature": 0.5}
        assert "stream_options" not in result
        assert "num_retries" not in result

    def test_combined(self):
        result = _convert_model_params_for_responses({
            "max_tokens": 2048,
            "reasoning_effort": "medium",
            "temperature": 0.3,
            "stream_options": {"include_usage": True},
        })
        assert result == {
            "max_output_tokens": 2048,
            "reasoning": {"effort": "medium"},
            "temperature": 0.3,
        }


# ============ Real API Integration Tests ============
# These tests require OPENAI_API_KEY and make real API calls.


@pytest.mark.skipif(not HAS_OPENAI_KEY, reason="OPENAI_API_KEY not set")
class TestResponsesApiRealCalls:
    """Integration tests that call the real OpenAI Responses API with codex models."""

    @pytest.mark.asyncio
    async def test_basic_conversation(self):
        """codex model can answer a simple question via acompletion_responses."""
        from pantheon.utils.llm import acompletion_responses

        chunks_received = []

        async def collect_chunk(chunk):
            chunks_received.append(chunk)

        result = await acompletion_responses(
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Reply briefly."},
                {"role": "user", "content": "What is 2 + 3?"},
            ],
            model=CODEX_MODEL,
            process_chunk=collect_chunk,
        )

        # Verify message structure
        assert result["role"] == "assistant"
        assert result["content"] is not None
        assert "5" in result["content"]
        assert result["tool_calls"] is None
        assert "_metadata" in result
        assert "_debug_cost" in result["_metadata"]
        assert "_debug_usage" in result["_metadata"]

        # Verify streaming happened
        assert len(chunks_received) >= 2  # at least some text + stop
        # Last chunk should be the stop signal
        assert chunks_received[-1] == {"stop": True}
        # Earlier chunks should have content
        text_chunks = [c for c in chunks_received if "content" in c]
        assert len(text_chunks) > 0

    @pytest.mark.asyncio
    async def test_tool_call(self):
        """codex model can return tool calls via acompletion_responses."""
        from pantheon.utils.llm import acompletion_responses

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                        },
                        "required": ["city"],
                    },
                },
            },
        ]

        result = await acompletion_responses(
            messages=[
                {"role": "system", "content": "You are a weather assistant. Use the get_weather tool to answer weather questions."},
                {"role": "user", "content": "What's the weather in Tokyo?"},
            ],
            model=CODEX_MODEL,
            tools=tools,
        )

        # Should have tool calls
        assert result["role"] == "assistant"
        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) >= 1

        tc = result["tool_calls"][0]
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert "id" in tc

        # Arguments should be valid JSON containing the city
        args = json.loads(tc["function"]["arguments"])
        assert "city" in args

    @pytest.mark.asyncio
    async def test_multi_turn_with_tool_result(self):
        """codex model handles a full tool-call round trip."""
        from pantheon.utils.llm import acompletion_responses

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                        },
                        "required": ["city"],
                    },
                },
            },
        ]

        # Turn 1: user asks → model calls tool
        turn1 = await acompletion_responses(
            messages=[
                {"role": "system", "content": "Use the get_weather tool to answer weather questions. Be brief."},
                {"role": "user", "content": "Weather in Paris?"},
            ],
            model=CODEX_MODEL,
            tools=tools,
        )
        assert turn1["tool_calls"] is not None
        tc = turn1["tool_calls"][0]

        # Turn 2: provide tool result → model gives final answer
        turn2 = await acompletion_responses(
            messages=[
                {"role": "system", "content": "Use the get_weather tool to answer weather questions. Be brief."},
                {"role": "user", "content": "Weather in Paris?"},
                {
                    "role": "assistant",
                    "content": turn1.get("content"),
                    "tool_calls": turn1["tool_calls"],
                },
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "Sunny, 22°C",
                },
            ],
            model=CODEX_MODEL,
            tools=tools,
        )

        # Final answer should be text (no more tool calls)
        assert turn2["role"] == "assistant"
        assert turn2["content"] is not None
        assert len(turn2["content"]) > 0

    @pytest.mark.asyncio
    async def test_routing_through_call_llm_provider(self):
        """call_llm_provider correctly routes codex models to the Responses API."""
        from pantheon.utils.llm_providers import (
            call_llm_provider,
            detect_provider,
        )

        config = detect_provider(CODEX_MODEL, relaxed_schema=False)
        assert is_responses_api_model(config) is True

        result = await call_llm_provider(
            config=config,
            messages=[
                {"role": "system", "content": "Reply with one word only."},
                {"role": "user", "content": "Say hello."},
            ],
        )

        assert result["role"] == "assistant"
        assert result["content"] is not None
        assert len(result["content"]) > 0

    @pytest.mark.asyncio
    async def test_non_codex_does_not_use_responses_api(self):
        """gpt-4o-mini uses the Chat Completions path, not Responses API."""
        config = detect_provider_for_test("gpt-4o-mini")
        assert is_responses_api_model(config) is False


def detect_provider_for_test(model: str) -> ProviderConfig:
    from pantheon.utils.llm_providers import detect_provider
    return detect_provider(model, relaxed_schema=False)


# ============ Agent.run() End-to-End Tests ============


@pytest.mark.skipif(not HAS_OPENAI_KEY, reason="OPENAI_API_KEY not set")
class TestAgentRunWithCodex:
    """End-to-end tests using Agent.run() with a codex model."""

    @pytest.mark.asyncio
    async def test_agent_basic_conversation(self):
        """Agent.run() with codex model returns a proper AgentResponse."""
        from pantheon.agent import Agent

        agent = Agent(
            name="codex_test",
            instructions="You are a helpful assistant. Reply briefly.",
            model=CODEX_MODEL,
        )

        resp = await agent.run("What is 2 + 3?")
        assert resp.agent_name == "codex_test"
        assert resp.content is not None
        assert "5" in resp.content

    @pytest.mark.asyncio
    async def test_agent_tool_use(self):
        """Agent.run() with codex model can call tools and return final answer."""
        from pantheon.agent import Agent

        agent = Agent(
            name="weather_agent",
            instructions="You are a weather assistant. Always use the get_weather tool to answer weather questions. Be brief.",
            model=CODEX_MODEL,
        )

        tool_called = False

        @agent.tool
        def get_weather(city: str) -> str:
            """Get current weather for a city.

            Args:
                city: The city name

            Returns:
                Weather description
            """
            nonlocal tool_called
            tool_called = True
            return f"Sunny, 25°C in {city}"

        resp = await agent.run("What's the weather in Tokyo?")

        # Tool should have been called
        assert tool_called, "get_weather tool was not called"
        # Final response should contain weather info
        assert resp.content is not None
        assert len(resp.content) > 0

    @pytest.mark.asyncio
    async def test_agent_multiple_tool_calls(self):
        """Agent.run() with codex model handles multiple sequential tool calls."""
        from pantheon.agent import Agent

        agent = Agent(
            name="calc_agent",
            instructions=(
                "You are a calculator assistant. "
                "Use the add tool for additions and the multiply tool for multiplications. "
                "Reply with the final numeric result only."
            ),
            model=CODEX_MODEL,
        )

        calls = []

        @agent.tool
        def add(a: int, b: int) -> int:
            """Add two numbers.

            Args:
                a: First number
                b: Second number

            Returns:
                Sum of a and b
            """
            calls.append(("add", a, b))
            return a + b

        @agent.tool
        def multiply(a: int, b: int) -> int:
            """Multiply two numbers.

            Args:
                a: First number
                b: Second number

            Returns:
                Product of a and b
            """
            calls.append(("multiply", a, b))
            return a * b

        resp = await agent.run("What is 3 + 4?")

        assert len(calls) >= 1, "No tools were called"
        assert resp.content is not None

    @pytest.mark.asyncio
    async def test_agent_streaming_with_codex(self):
        """Agent.run() with codex model delivers streaming chunks."""
        from pantheon.agent import Agent

        agent = Agent(
            name="stream_test",
            instructions="Reply briefly.",
            model=CODEX_MODEL,
        )

        chunks_received = []

        async def on_chunk(chunk):
            chunks_received.append(chunk)

        resp = await agent.run("Say hello.", process_chunk=on_chunk)

        assert resp.content is not None
        # Should have received streaming chunks
        assert len(chunks_received) > 0


# ============ Default-to-Responses-API routing + fallback ============


class TestDefaultResponsesAPIRouting:
    """Non-codex/non-pro OpenAI models now default to the Responses API, with
    a runtime fallback to Chat Completions when the endpoint or model doesn't
    implement /v1/responses. These tests pin that behaviour without hitting
    a real network."""

    def setup_method(self):
        from pantheon.utils.llm_providers import reset_responses_api_cache

        reset_responses_api_cache()

    def teardown_method(self):
        from pantheon.utils.llm_providers import reset_responses_api_cache

        reset_responses_api_cache()

    def test_should_use_responses_api_defaults_true_for_openai(self):
        from pantheon.utils.llm_providers import should_use_responses_api

        cfg = ProviderConfig(provider_type=ProviderType.OPENAI, model_name="gpt-4o")
        assert should_use_responses_api(cfg) is True

    def test_should_use_responses_api_false_for_native(self):
        from pantheon.utils.llm_providers import should_use_responses_api

        cfg = ProviderConfig(
            provider_type=ProviderType.NATIVE, model_name="anthropic/claude-sonnet-4-6"
        )
        assert should_use_responses_api(cfg) is False

    def test_should_use_responses_api_false_for_codex(self):
        """codex/ models have a dedicated OAuth adapter path and must not be
        sent through the default Responses API route."""
        from pantheon.utils.llm_providers import should_use_responses_api

        cfg = ProviderConfig(
            provider_type=ProviderType.OPENAI, model_name="codex/gpt-5.4"
        )
        assert should_use_responses_api(cfg) is False

    def test_cache_key_is_per_base_url_and_model(self):
        """Marking one (base_url, model) pair unavailable must not leak into
        other pairs — a custom proxy that lacks /v1/responses should not
        disable the default OpenAI endpoint."""
        from pantheon.utils.llm_providers import (
            mark_responses_api_unavailable,
            should_use_responses_api,
        )

        proxy_cfg = ProviderConfig(
            provider_type=ProviderType.OPENAI,
            model_name="gpt-4o",
            base_url="https://proxy.example.com/v1",
        )
        default_cfg = ProviderConfig(
            provider_type=ProviderType.OPENAI, model_name="gpt-4o"
        )
        other_model_cfg = ProviderConfig(
            provider_type=ProviderType.OPENAI, model_name="gpt-5.4"
        )

        mark_responses_api_unavailable(proxy_cfg)

        assert should_use_responses_api(proxy_cfg) is False
        assert should_use_responses_api(default_cfg) is True
        assert should_use_responses_api(other_model_cfg) is True

    @pytest.mark.asyncio
    async def test_fallback_on_404_routes_to_chat_completions(self, monkeypatch):
        """A 404 from acompletion_responses must mark the cache and retry via
        acompletion (Chat Completions)."""
        import openai as openai_mod

        from pantheon.utils.llm_providers import (
            call_llm_provider,
            detect_provider,
            should_use_responses_api,
        )

        async def fake_responses(**kwargs):
            raise openai_mod.NotFoundError(
                message="Not Found",
                response=MagicMock(status_code=404, request=MagicMock()),
                body=None,
            )

        async def fake_completion(**kwargs):
            return {"role": "assistant", "content": "fallback worked"}

        monkeypatch.setattr(
            "pantheon.utils.llm.acompletion_responses", fake_responses
        )
        monkeypatch.setattr("pantheon.utils.llm.acompletion", fake_completion)
        monkeypatch.setattr(
            "pantheon.utils.llm_providers.extract_message_from_response",
            lambda msg, prefix: msg,
        )

        config = detect_provider("openai/gpt-5.4", relaxed_schema=False)
        assert should_use_responses_api(config) is True

        result = await call_llm_provider(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result == {"role": "assistant", "content": "fallback worked"}
        # After the 404 the cache should flip the routing off so subsequent
        # calls skip the probe.
        assert should_use_responses_api(config) is False

    @pytest.mark.asyncio
    async def test_404_on_responses_only_model_raises(self, monkeypatch):
        """Responses-only models (-pro, codex) have no Chat Completions route,
        so a 404 must propagate rather than silently fall back."""
        import openai as openai_mod

        from pantheon.utils.llm_providers import call_llm_provider, detect_provider

        async def fake_responses(**kwargs):
            raise openai_mod.NotFoundError(
                message="Not Found",
                response=MagicMock(status_code=404, request=MagicMock()),
                body=None,
            )

        monkeypatch.setattr(
            "pantheon.utils.llm.acompletion_responses", fake_responses
        )

        config = detect_provider("openai/gpt-5.4-pro", relaxed_schema=False)

        with pytest.raises(openai_mod.NotFoundError):
            await call_llm_provider(
                config=config,
                messages=[{"role": "user", "content": "hi"}],
            )
