import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "api_key_env", "base_env", "expected_base"),
    [
        ("openai/gpt-5.4", "OPENAI_API_KEY", "OPENAI_API_BASE", "https://openai-proxy.example/v1"),
        ("anthropic/claude-sonnet-4-6", "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE", "https://anthropic-proxy.example"),
        ("gemini/gemini-2.5-flash", "GEMINI_API_KEY", "GEMINI_API_BASE", "https://gemini-proxy.example"),
    ],
)
async def test_acompletion_uses_provider_specific_base_urls(
    monkeypatch, model, api_key_env, base_env, expected_base
):
    from pantheon.utils import adapters as adapters_module
    from pantheon.utils import llm as llm_module

    captured: dict[str, str] = {}

    class DummyAdapter:
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "model": kwargs["model"],
                },
                {
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                    "choices": [],
                },
            ]

    monkeypatch.setattr(adapters_module, "get_adapter", lambda _sdk: DummyAdapter())
    monkeypatch.setenv(api_key_env, "provider-key")
    monkeypatch.setenv(base_env, expected_base)
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    resp = await llm_module.acompletion(
        messages=[{"role": "user", "content": "hello"}],
        model=model,
        model_params={},
    )

    assert resp.choices[0].message.content == "ok"
    assert captured["base_url"] == expected_base
    assert captured["api_key"] == "provider-key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "api_key_env"),
    [
        ("anthropic/claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
        ("gemini/gemini-2.5-flash", "GEMINI_API_KEY"),
    ],
)
async def test_acompletion_uses_global_llm_base_as_provider_base_fallback(
    monkeypatch, model, api_key_env
):
    from pantheon.utils import adapters as adapters_module
    from pantheon.utils import llm as llm_module

    captured: dict[str, str] = {}

    class DummyAdapter:
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "model": kwargs["model"],
                },
                {
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                    "choices": [],
                },
            ]

    monkeypatch.setattr(adapters_module, "get_adapter", lambda _sdk: DummyAdapter())
    monkeypatch.setenv(api_key_env, "provider-key")
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_BASE", raising=False)
    monkeypatch.delenv("GEMINI_API_BASE", raising=False)

    resp = await llm_module.acompletion(
        messages=[{"role": "user", "content": "hello"}],
        model=model,
        model_params={},
    )

    assert resp.choices[0].message.content == "ok"
    assert captured["base_url"] == "https://fallback.example/v1"
    assert captured["api_key"] == "provider-key"
