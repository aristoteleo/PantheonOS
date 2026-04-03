"""
Integration tests for provider adapters — verifies every model in DEFAULT_PROVIDER_MODELS works.

Requires API keys in .env file.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

from pantheon.utils.provider_registry import (
    load_catalog,
    find_provider_for_model,
    get_model_info,
    completion_cost,
    models_by_provider,
    token_counter,
)
from pantheon.utils.adapters import get_adapter
from pantheon.utils.llm import stream_chunk_builder
from pantheon.utils.model_selector import DEFAULT_PROVIDER_MODELS, PROVIDER_API_KEYS
from pantheon.utils.llm_providers import is_responses_api_model, detect_provider


# ============ provider_registry unit tests ============


class TestProviderRegistry:

    def test_load_catalog(self):
        cat = load_catalog()
        assert cat["version"] == 1
        assert len(cat["providers"]) >= 8

    def test_find_provider_with_prefix(self):
        p, m, c = find_provider_for_model("anthropic/claude-sonnet-4-6")
        assert p == "anthropic"
        assert m == "claude-sonnet-4-6"
        assert c["sdk"] == "anthropic"

    def test_find_provider_openai_compat(self):
        p, m, c = find_provider_for_model("deepseek/deepseek-chat")
        assert p == "deepseek"
        assert c["sdk"] == "openai"

    def test_find_provider_qwen(self):
        p, m, c = find_provider_for_model("qwen/qwen3-235b-a22b")
        assert p == "qwen"
        assert c["api_key_env"] == "DASHSCOPE_API_KEY"

    def test_find_provider_unknown(self):
        p, m, c = find_provider_for_model("unknown/some-model")
        assert p == "unknown"
        assert c == {}

    def test_get_model_info_known(self):
        info = get_model_info("anthropic/claude-opus-4-6")
        assert info["max_input_tokens"] == 1_000_000
        assert info["supports_vision"] is True

    def test_get_model_info_unknown_returns_defaults(self):
        info = get_model_info("fake/nonexistent-model")
        assert info["max_input_tokens"] == 200_000

    def test_completion_cost(self):
        cost = completion_cost(model="openai/gpt-5.4", prompt_tokens=1_000_000, completion_tokens=100_000)
        assert abs(cost - 2.8) < 0.01

    def test_models_by_provider(self):
        models = models_by_provider("anthropic")
        assert len(models) == 7

    def test_models_by_provider_qwen(self):
        models = models_by_provider("qwen")
        assert len(models) == 9

    def test_token_counter_basic(self):
        count = token_counter(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])
        assert count > 0

    def test_all_default_models_in_catalog(self):
        """Every model in DEFAULT_PROVIDER_MODELS should exist in the catalog."""
        cat = load_catalog()
        all_catalog_models = set()
        for prov, cfg in cat["providers"].items():
            for m in cfg.get("models", {}):
                all_catalog_models.add(f"{prov}/{m}")

        missing = []
        for provider, levels in DEFAULT_PROVIDER_MODELS.items():
            for level, models in levels.items():
                for model in models:
                    if model not in all_catalog_models:
                        missing.append(model)
        assert missing == [], f"Models in selector but not in catalog: {missing}"


# ============ stream_chunk_builder unit tests ============


class TestStreamChunkBuilder:

    def test_text_chunks(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "choices": []},
        ]
        resp = stream_chunk_builder(chunks)
        msg = resp.choices[0].message.model_dump()
        assert msg["content"] == "Hello world"
        assert resp.usage.prompt_tokens == 10

    def test_tool_call_chunks(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "test", "arguments": '{"a":'}}
            ]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ' 1}'}}
            ]}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, "choices": []},
        ]
        resp = stream_chunk_builder(chunks)
        msg = resp.choices[0].message.model_dump()
        assert msg["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'

    def test_empty_chunks(self):
        resp = stream_chunk_builder([])
        msg = resp.choices[0].message.model_dump()
        assert msg["content"] is None


# ============ Real API: test every model in DEFAULT_PROVIDER_MODELS ============

SIMPLE_MESSAGES = [{"role": "user", "content": "Say 'hello' and nothing else."}]


def _has_key(provider: str) -> bool:
    env_var = PROVIDER_API_KEYS.get(provider, "")
    return bool(os.environ.get(env_var, ""))


def _get_all_models():
    """Collect unique (provider, model) pairs from DEFAULT_PROVIDER_MODELS, excluding qwen (no valid key)."""
    seen = set()
    result = []
    for provider, levels in DEFAULT_PROVIDER_MODELS.items():
        if provider == "qwen":
            continue  # Skip: no valid API key available
        for level, models in levels.items():
            for model in models:
                if model not in seen:
                    seen.add(model)
                    result.append((provider, model))
    return result


ALL_MODELS = _get_all_models()


@pytest.mark.parametrize("provider,model", ALL_MODELS, ids=[m for _, m in ALL_MODELS])
@pytest.mark.asyncio
async def test_model_completion(provider, model):
    """Test that each model in DEFAULT_PROVIDER_MODELS can complete a simple prompt.

    Automatically detects whether to use Chat Completions or Responses API.
    """
    env_var = PROVIDER_API_KEYS.get(provider, "")
    api_key = os.environ.get(env_var, "")
    if not api_key:
        pytest.skip(f"{env_var} not set")

    provider_key, model_name, provider_config = find_provider_for_model(model)
    sdk_type = provider_config.get("sdk", "openai")
    base_url = provider_config.get("base_url")

    # Check if this model needs Responses API
    config = detect_provider(model, relaxed_schema=False)
    uses_responses_api = is_responses_api_model(config)

    adapter = get_adapter("openai" if uses_responses_api else sdk_type)

    if uses_responses_api:
        # Responses API path
        bare_model = model_name.split("/")[-1] if "/" in model_name else model_name
        msg = await adapter.acompletion_responses(
            model=bare_model,
            messages=SIMPLE_MESSAGES,
            base_url=base_url,
            api_key=api_key,
            max_output_tokens=2048,
        )
        content = msg.get("content") or ""
        assert len(content.strip()) > 0, f"{model}: got empty content, full msg={msg}"
        print(f"  [{provider}] {model} (responses): {content[:80]!r}")
    else:
        # Chat Completions path
        info = get_model_info(model)
        is_reasoning = info.get("supports_reasoning", False)

        extra_kwargs = {}
        if sdk_type == "anthropic":
            extra_kwargs["max_tokens"] = 1024 if is_reasoning else 128
        elif sdk_type == "openai" and provider_key == "openai":
            extra_kwargs["max_completion_tokens"] = 2048
        elif sdk_type != "google-genai":
            extra_kwargs["max_tokens"] = 1024 if is_reasoning else 128

        chunks = await adapter.acompletion(
            model=model_name,
            messages=SIMPLE_MESSAGES,
            base_url=base_url,
            api_key=api_key,
            num_retries=2,
            **extra_kwargs,
        )
        resp = stream_chunk_builder(chunks)
        msg = resp.choices[0].message.model_dump()

        content = msg.get("content") or ""
        assert len(content.strip()) > 0, f"{model}: got empty content, full msg={msg}"
        print(f"  [{provider}] {model}: {content[:80]!r}")
