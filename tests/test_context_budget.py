"""Regression tests for model context/output budgeting.

The output budget of every request is clamped to what the model's window
leaves after the prompt (messages + tool definitions). Catalog models without
an explicit ``context_window`` use ``max_input_tokens``, the same window the
agent's compaction uses (apps/agent/pantheon/agent_context.py).
"""

import pytest

from pantheonos.utils import llm, provider_registry
from pantheonos.utils.provider_registry import token_counter

WINDOW = 204_800
PROMPT = 172_000


def _expected(prompt: int = PROMPT, window: int = WINDOW) -> int:
    return window - prompt - max(llm.CONTEXT_TOKEN_SAFETY_MARGIN, int(prompt * 0.05))


@pytest.fixture
def catalog_model(monkeypatch):
    """A catalog model that advertises max_input_tokens but no context_window."""
    monkeypatch.setattr(
        provider_registry,
        "get_model_info",
        lambda _model, **_kwargs: {"max_input_tokens": WINDOW, "max_output_tokens": 128_000},
    )
    monkeypatch.setattr(provider_registry, "token_counter", lambda **_kwargs: PROMPT)


def test_output_budget_is_clamped_to_remaining_context(catalog_model):
    """A large input plus tools must leave room for the model response."""
    params = llm._normalize_output_token_param(
        "anthropic/claude-sonnet-4-6",
        None,
        api_mode="chat",
        force_param="max_tokens",
        messages=[{"role": "user", "content": "large"}],
        tools=[{"type": "function", "function": {"name": "tool"}}],
    )

    assert params["max_tokens"] == _expected()


def test_tools_alone_are_budgeted(catalog_model):
    params = llm._normalize_output_token_param(
        "anthropic/claude-sonnet-4-6",
        None,
        force_param="max_tokens",
        tools=[{"type": "function", "function": {"name": "tool"}}],
    )

    assert params["max_tokens"] == _expected()


def test_context_budget_raises_when_no_output_can_fit(monkeypatch):
    from pantheon.agent_context import is_context_overflow

    monkeypatch.setattr(
        provider_registry,
        "get_model_info",
        lambda _model, **_kwargs: {"max_input_tokens": 1000, "max_output_tokens": 128},
    )
    monkeypatch.setattr(provider_registry, "token_counter", lambda **_kwargs: 1000)

    with pytest.raises(llm.ContextWindowExceededError, match="exceeds the context window") as raised:
        llm._normalize_output_token_param(
            "openrouter/test/model",
            None,
            force_param="max_tokens",
            messages=[{"role": "user", "content": "too large"}],
        )
    # The agent loop compacts and retries on this error.
    assert isinstance(raised.value, ValueError)
    assert is_context_overflow(raised.value)


@pytest.mark.asyncio
async def test_responses_api_sends_clamped_output_budget(catalog_model, monkeypatch):
    """The Responses adapter receives the same guarded budget as chat calls."""
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
        model_params={"max_output_tokens": 128_000},
    )

    output_keys = [
        key for key in ("max_output_tokens", "max_completion_tokens", "max_tokens")
        if key in captured
    ]
    assert len(output_keys) == 1
    assert captured[output_keys[0]] == _expected()


@pytest.mark.asyncio
async def test_openai_compatible_fallback_sends_clamped_max_tokens(catalog_model, monkeypatch):
    """The OpenAI-compatible fallback route cannot bypass the context guard."""
    monkeypatch.setattr(
        provider_registry,
        "find_provider_for_model",
        lambda _model, **_kwargs: ("openai", "gpt-5.4", {"sdk": "openai"}),
    )

    captured = {}

    class FakeAdapter:
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(
        "pantheonos.utils.adapters.get_adapter",
        lambda _sdk: FakeAdapter(),
    )
    monkeypatch.setenv("LLM_API_BASE", "https://mock.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    await llm.acompletion(
        messages=[{"role": "user", "content": "large"}],
        model="openai/gpt-5.4",
        model_params={"max_output_tokens": 128_000},
    )

    assert captured["max_tokens"] == _expected()
    assert "max_output_tokens" not in captured


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
    """Fallback estimation must protect context budgets when the counter fails."""
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("counter unavailable")

    monkeypatch.setattr(provider_registry, "token_counter", unavailable)

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

    assert tokens > 100  # the arguments count; an empty assistant message counts 0
