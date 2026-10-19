"""Test GLM-5.1 model support in pantheon-agents."""

import pytest
from pantheonos.utils.provider_registry import get_provider_config


def test_glm_5_1_model_exists():
    """Test that GLM-5.1 model is registered in the catalog."""
    provider_config = get_provider_config("zai")
    assert provider_config is not None
    assert "glm-5.1" in provider_config["models"]


def test_glm_5_1_model_specs():
    """Test GLM-5.1 model specifications."""
    provider_config = get_provider_config("zai")
    glm_5_1 = provider_config["models"]["glm-5.1"]

    # Check token limits (200K input, 128K output)
    assert glm_5_1["max_input_tokens"] == 200000
    assert glm_5_1["max_output_tokens"] == 128000

    # Check pricing (updated 2026-04-27)
    assert glm_5_1["input_cost_per_million"] == 1.4
    assert glm_5_1["output_cost_per_million"] == 4.4

    # Check capabilities
    assert glm_5_1["supports_vision"] is True
    assert glm_5_1["supports_function_calling"] is True
    assert glm_5_1["supports_response_schema"] is True
    assert glm_5_1["supports_reasoning"] is True
    assert glm_5_1["supports_web_search"] is True


def test_glm_5_deprecated():
    """Test that GLM-5 is marked as deprecated."""
    provider_config = get_provider_config("zai")
    glm_5 = provider_config["models"]["glm-5"]

    assert glm_5.get("deprecated") is True
    assert glm_5.get("deprecation_date") == "2026-04-20"
    assert glm_5.get("replacement") == "glm-5.1"


def test_glm_5_updated_specs():
    """Test that GLM-5 specs are updated to match GLM-5.1."""
    provider_config = get_provider_config("zai")
    glm_5 = provider_config["models"]["glm-5"]

    # GLM-5 should have same token limits as GLM-5.1
    assert glm_5["max_input_tokens"] == 200000
    assert glm_5["max_output_tokens"] == 128000


def test_zai_provider_config():
    """Test ZAI provider configuration."""
    provider_config = get_provider_config("zai")

    assert provider_config["display_name"] == "Z.ai (Zhipu)"
    assert provider_config["sdk"] == "openai"
    assert provider_config["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert provider_config["api_key_env"] == "ZAI_API_KEY"
    assert provider_config["api_base_env"] == "ZAI_API_BASE"
    assert provider_config["openai_compatible"] is True


@pytest.mark.asyncio
async def test_glm_5_1_provider_detection(monkeypatch):
    """Test that GLM-5.1 is correctly detected as OpenAI-compatible provider."""
    from pantheonos.utils.llm_providers import ProviderType, detect_provider

    monkeypatch.setenv("ZAI_API_KEY", "test-key")
    monkeypatch.setenv("ZAI_API_BASE", "https://test.example.com/v1")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    config = detect_provider("zai/glm-5.1", relaxed_schema=False)

    assert config.provider_type == ProviderType.OPENAI
    assert config.model_name == "glm-5.1"
    assert config.base_url == "https://test.example.com/v1"
    assert config.api_key == "test-key"


@pytest.mark.asyncio
async def test_glm_5_1_model_call(monkeypatch):
    """Test that GLM-5.1 can be called through the LLM interface."""
    from pantheonos.utils import adapters as adapters_module
    from pantheonos.utils import llm as llm_module

    captured: dict[str, str] = {}

    class DummyAdapter:
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "Hello from GLM-5.1"},
                            "finish_reason": "stop",
                        }
                    ],
                    "model": kwargs["model"],
                },
                {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                    "choices": [],
                },
            ]

    monkeypatch.setattr(adapters_module, "get_adapter", lambda _sdk: DummyAdapter())
    monkeypatch.setenv("ZAI_API_KEY", "test-key")
    monkeypatch.setenv("ZAI_API_BASE", "https://test.example.com/v1")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    resp = await llm_module.acompletion(
        messages=[{"role": "user", "content": "hello"}],
        model="zai/glm-5.1",
        model_params={},
    )

    assert resp.choices[0].message.content == "Hello from GLM-5.1"
    assert captured["model"] == "glm-5.1"
    assert captured["base_url"] == "https://test.example.com/v1"
    assert captured["api_key"] == "test-key"
