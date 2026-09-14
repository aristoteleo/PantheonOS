import pytest

from pantheon.utils import provider_registry
from pantheon.utils.llm import _normalize_output_token_param
from pantheon.utils.openrouter_catalog import _derive


@pytest.fixture
def routed_model(monkeypatch):
    info = _derive({
        "id": "z-ai/glm-5.3", "context_length": 1_310_720,
        "top_provider": {"context_length": 1_048_576, "max_completion_tokens": 943_718},
    })
    monkeypatch.setattr(provider_registry, "get_model_info", lambda model: info)
    monkeypatch.setattr(provider_registry, "get_output_token_param", lambda *a, **k: "max_tokens")
    monkeypatch.setattr(provider_registry, "token_counter", lambda **k: 205_844)
    return info


def test_provider_window_not_larger_advertised_model_window(routed_model):
    assert routed_model["max_input_tokens"] == 1_048_576
    assert routed_model["max_output_tokens"] == 943_718


def test_catalog_ceiling_is_not_default_completion_budget(routed_model):
    params = _normalize_output_token_param("openrouter/z-ai/glm-5.3", {}, messages=[])
    assert params == {"max_tokens": 32_000}
    assert 205_844 + params["max_tokens"] < routed_model["context_window"]


def test_explicit_output_budget_is_preserved_when_it_fits(routed_model):
    original = {"max_output_tokens": 100_000, "temperature": 0.2}
    params = _normalize_output_token_param("openrouter/z-ai/glm-5.3", original, messages=[])
    assert params == {"max_tokens": 100_000, "temperature": 0.2}
    assert original["max_output_tokens"] == 100_000


def test_oversized_explicit_budget_leaves_room_for_input_and_tools(routed_model, monkeypatch):
    captured = {}
    def count(**kwargs):
        captured.update(kwargs)
        return 205_844
    monkeypatch.setattr(provider_registry, "token_counter", count)
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "read"}}]
    params = _normalize_output_token_param("openrouter/z-ai/glm-5.3", {"max_tokens": 943_717}, messages=messages, tools=tools)
    assert 205_844 + params["max_tokens"] < routed_model["context_window"]
    assert captured["messages"] == messages
    assert captured["tools"] == tools


def test_conflicting_token_aliases_emit_one_parameter(routed_model):
    params = _normalize_output_token_param("openrouter/z-ai/glm-5.3", {
        "max_output_tokens": 4000, "max_completion_tokens": 5000, "max_tokens": 6000,
    })
    assert params == {"max_tokens": 4000}


def test_full_prompt_fails_before_provider_request(routed_model, monkeypatch):
    monkeypatch.setattr(provider_registry, "token_counter", lambda **k: 1_048_576)
    with pytest.raises(ValueError, match="Prompt exceeds"):
        _normalize_output_token_param("openrouter/z-ai/glm-5.3", {}, messages=[])
