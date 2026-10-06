"""Regression tests for model context/output budgeting."""

import pytest

from pantheon.internal.compression import CompressionConfig, ContextCompressor
from pantheon.utils import llm
from pantheon.utils.provider_registry import token_counter


def test_output_budget_is_clamped_to_remaining_context(monkeypatch):
    """A large input plus tools must leave room for the model response."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {
            "max_input_tokens": 204800,
            "max_output_tokens": 128000,
        },
    )
    monkeypatch.setattr(llm, "_safe_token_counter", lambda *_args, **_kwargs: 172000)

    params = llm._normalize_output_token_param(
        "openrouter/z-ai/glm-5",
        None,
        api_mode="chat",
        force_param="max_tokens",
        messages=[{"role": "user", "content": "large"}],
        tools=[{"type": "function", "function": {"name": "tool"}}],
    )

    assert params["max_tokens"] == 204800 - 172000 - llm.CONTEXT_TOKEN_SAFETY_MARGIN


def test_context_budget_raises_when_no_output_can_fit(monkeypatch):
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {"max_input_tokens": 1000, "max_output_tokens": 128},
    )
    monkeypatch.setattr(llm, "_safe_token_counter", lambda *_args, **_kwargs: 1000)

    with pytest.raises(llm.ContextWindowExceededError, match="context exhausted"):
        llm._normalize_output_token_param(
            "openrouter/test/model",
            None,
            force_param="max_tokens",
            messages=[{"role": "user", "content": "too large"}],
        )


@pytest.mark.asyncio
async def test_responses_api_sends_clamped_output_budget(monkeypatch):
    """The Responses adapter receives the same guarded budget as chat calls."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {
            "max_input_tokens": 204800,
            "max_output_tokens": 128000,
        },
    )
    monkeypatch.setattr(llm, "_safe_token_counter", lambda *_args, **_kwargs: 172000)

    captured = {}

    class EmptyStream:
        async def __aiter__(self):
            if False:
                yield None

    class FakeResponses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return EmptyStream()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)

    await llm.acompletion_responses(
        messages=[{"role": "user", "content": "large"}],
        model="openai/gpt-5.4",
        model_params={"max_output_tokens": 128000},
    )

    output_keys = [
        key for key in ("max_output_tokens", "max_completion_tokens", "max_tokens")
        if key in captured
    ]
    assert len(output_keys) == 1
    assert captured[output_keys[0]] == 204800 - 172000 - llm.CONTEXT_TOKEN_SAFETY_MARGIN


@pytest.mark.asyncio
async def test_openai_compatible_fallback_sends_clamped_max_tokens(monkeypatch):
    """The legacy OpenAI-compatible fallback cannot bypass the context guard."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {
            "max_input_tokens": 204800,
            "max_output_tokens": 128000,
        },
    )
    monkeypatch.setattr(llm, "_safe_token_counter", lambda *_args, **_kwargs: 172000)
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.find_provider_for_model",
        lambda _model: ("openai", "gpt-5.4", {"sdk": "openai"}),
    )

    captured = {}

    class FakeAdapter:
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(
        "pantheon.utils.adapters.get_adapter",
        lambda _sdk: FakeAdapter(),
    )
    monkeypatch.setenv("LLM_API_BASE", "https://mock.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    await llm.acompletion(
        messages=[{"role": "user", "content": "large"}],
        model="openai/gpt-5.4",
        model_params={"max_output_tokens": 128000},
    )

    assert captured["max_tokens"] == 204800 - 172000 - llm.CONTEXT_TOKEN_SAFETY_MARGIN
    assert "max_output_tokens" not in captured


def test_compression_considers_pending_input_and_output_reserve(monkeypatch):
    """Compression must trigger before a smaller fallback model overflows."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {
            "max_input_tokens": 204800,
            "max_output_tokens": 128000,
        },
    )
    monkeypatch.setattr(llm, "_safe_token_counter", lambda *_args, **_kwargs: 10000)

    compressor = ContextCompressor(CompressionConfig(enable=True, threshold=0.8), "normal")
    messages = [{"role": "assistant", "content": "history", "_metadata": {
        "total_tokens": 120000,
        "max_tokens": 204800,
    }}]

    assert compressor.should_compress(
        messages,
        model="openrouter/z-ai/glm-5",
        pending_messages=[{"role": "user", "content": "new request"}],
        tools=[{"type": "function", "function": {"name": "tool"}}],
    ) is True


def test_compression_does_not_double_count_tool_schema(monkeypatch):
    """Previous assistant usage already includes the stable tool definition."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {"max_input_tokens": 204800, "max_output_tokens": 32000},
    )
    monkeypatch.setattr(llm, "_safe_token_counter", lambda *_args, **kwargs: 100000 if kwargs.get("tools") else 1000)

    compressor = ContextCompressor(CompressionConfig(enable=True, threshold=0.8), "normal")
    messages = [{"role": "assistant", "content": "history", "_metadata": {
        "total_tokens": 100000,
        "max_tokens": 204800,
        "tools_definition_tokens": 100000,
    }}]

    assert compressor.should_compress(
        messages,
        model="openrouter/z-ai/glm-5",
        pending_messages=[{"role": "user", "content": "new request"}],
        tools=[{"type": "function", "function": {"name": "large_tool_schema"}}],
    ) is False


def test_legacy_compression_metadata_does_not_double_count_tools(monkeypatch):
    """Pre-upgrade metadata must not assume current tools are new tokens."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {"max_input_tokens": 204800, "max_output_tokens": 32000},
    )
    monkeypatch.setattr(
        llm,
        "_safe_token_counter",
        lambda *_args, **kwargs: 100000 if kwargs.get("tools") else 1000,
    )

    compressor = ContextCompressor(CompressionConfig(enable=True, threshold=0.8), "normal")
    messages = [{"role": "assistant", "content": "history", "_metadata": {
        "total_tokens": 100000,
        "max_tokens": 204800,
    }}]

    assert compressor.should_compress(
        messages,
        model="openrouter/z-ai/glm-5",
        pending_messages=[{"role": "user", "content": "new request"}],
        tools=[{"type": "function", "function": {"name": "large_tool_schema"}}],
    ) is False


def test_compression_accounts_for_changed_tool_schema(monkeypatch):
    """A newly active tool schema must count even when prior usage had no tools."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {"max_input_tokens": 204800, "max_output_tokens": 32000},
    )

    def count_tokens(_model, messages=None, tools=None):
        message_tokens = 1_000 if messages else 0
        tool_tokens = 70_000 if tools else 0
        return message_tokens + tool_tokens

    monkeypatch.setattr(llm, "_safe_token_counter", count_tokens)

    compressor = ContextCompressor(CompressionConfig(enable=True, threshold=0.8), "normal")
    messages = [{"role": "assistant", "content": "history", "_metadata": {
        "total_tokens": 110_000,
        "max_tokens": 204800,
        "tools_definition_tokens": 0,
    }}]
    tools = [{"type": "function", "function": {"name": "new_tool"}}]

    assert compressor.should_compress(
        messages,
        model="openrouter/z-ai/glm-5",
        pending_messages=[{"role": "user", "content": "new request"}],
        tools=tools,
    ) is True

    assert compressor.should_compress(
        messages,
        model="openrouter/z-ai/glm-5",
        pending_messages=[{"role": "user", "content": "new request"}],
        tools=None,
    ) is False


def test_token_counter_includes_tool_call_arguments():
    plain = token_counter(
        "openrouter/z-ai/glm-5",
        messages=[{"role": "assistant", "content": "ok"}],
    )
    with_call = token_counter(
        "openrouter/z-ai/glm-5",
        messages=[
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"content":"' + ("x" * 4000) + '"}',
                        },
                    }
                ],
            }
        ],
    )

    assert with_call > plain


def test_safe_token_counter_fallback_includes_tool_call_arguments(monkeypatch):
    """Fallback estimation must protect context budgets when tiktoken fails."""
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("counter unavailable")

    monkeypatch.setattr("pantheon.utils.provider_registry.token_counter", unavailable)

    tokens = llm._safe_token_counter(
        "unsupported/model",
        messages=[{
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1",
                "function": {
                    "name": "write_file",
                    "arguments": '{"content":"' + ("x" * 4000) + '"}',
                },
            }],
        }],
    )

    assert tokens > 0


def test_message_stats_records_tool_definition_tokens(monkeypatch):
    """Subsequent compression checks can compare the active tool schema delta."""
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda _model: {"max_input_tokens": 204800, "max_output_tokens": 32000},
    )
    monkeypatch.setattr(
        llm,
        "_safe_token_counter",
        lambda *_args, **kwargs: 700 if kwargs.get("tools") else 100,
    )

    message = {"_metadata": {"_debug_usage": {"total_tokens": 500}}}
    llm.collect_message_stats_lightweight(
        message=message,
        messages=[],
        model="openrouter/z-ai/glm-5",
        tools=[{"type": "function", "function": {"name": "tool"}}],
    )

    assert message["_metadata"]["tools_definition_tokens"] == 700


def test_compression_plugin_normalizes_structured_pending_input():
    """List/BaseModel AgentInput values must reach the preflight as messages."""
    import asyncio
    from pantheon.internal.compression.plugin import CompressionPlugin

    captured = {}

    class Compressor:
        def should_compress(self, messages, model, **kwargs):
            captured.update(kwargs)
            return False

    class Agent:
        models = ["openrouter/z-ai/glm-5"]

        async def _input_to_openai_messages(self, _value):
            return [{"role": "user", "content": "normalized"}]

        async def get_tools_for_llm(self):
            return [{"type": "function", "function": {"name": "tool"}}]

    class Team:
        def get_active_agent(self, _memory):
            return Agent()

    class Memory:
        _messages = [{"role": "assistant", "_metadata": {"total_tokens": 1, "max_tokens": 204800}}]

    plugin = CompressionPlugin({"enable": True})
    plugin.compressor = Compressor()
    asyncio.run(plugin.on_run_start(Team(), ["structured"], {"memory": Memory()}))

    assert captured["pending_messages"] == [{"role": "user", "content": "normalized"}]
    assert captured["tools"] == [{"type": "function", "function": {"name": "tool"}}]
