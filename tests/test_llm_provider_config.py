import os
from unittest.mock import MagicMock, patch

from pantheon.settings import Settings
from pantheon.utils.llm_providers import (
    get_openai_effective_config,
    get_openai_fallback_config,
    get_provider_api_key,
    get_provider_base_url,
    resolve_provider_base_url,
)
from pantheon.utils.model_selector import ModelSelector


def _mock_settings(values: dict[str, str | None] | None = None) -> MagicMock:
    values = values or {}
    settings = MagicMock()
    settings.get_api_key.side_effect = lambda key: os.environ.get(key) or values.get(key)
    settings.get.side_effect = lambda key, default=None: default
    return settings


def test_openai_effective_config_prefers_provider_key_over_fallback_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "fallback-key")

    with patch("pantheon.settings.get_settings", return_value=_mock_settings()):
        assert get_openai_effective_config() == (
            "https://fallback.example/v1",
            "openai-key",
        )


def test_openai_fallback_config_exposes_base_and_key_independently(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "fallback-key")

    with patch("pantheon.settings.get_settings", return_value=_mock_settings()):
        assert get_openai_fallback_config() == (
            "https://fallback.example/v1",
            "fallback-key",
        )
        assert get_openai_effective_config() == (
            "https://fallback.example/v1",
            "fallback-key",
        )


def test_openai_effective_config_uses_global_base_without_llm_key(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    with patch("pantheon.settings.get_settings", return_value=_mock_settings()):
        assert get_openai_fallback_config() == ("https://fallback.example/v1", "")
        assert get_openai_effective_config() == (
            "https://fallback.example/v1",
            "openai-key",
        )


def test_provider_specific_base_url_is_resolved_by_provider_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_BASE", "https://anthropic.example")

    with patch("pantheon.settings.get_settings", return_value=_mock_settings()):
        assert get_provider_base_url("anthropic") == "https://anthropic.example"
        assert get_provider_base_url("unknown") is None


def test_resolve_provider_base_url_falls_back_to_global_llm_base(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_BASE", raising=False)
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")

    with patch("pantheon.settings.get_settings", return_value=_mock_settings()):
        assert resolve_provider_base_url("anthropic", "https://api.anthropic.com") == "https://fallback.example/v1"
        assert resolve_provider_base_url("unknown", "https://default.example") == "https://fallback.example/v1"


def test_provider_specific_api_key_is_resolved_by_provider_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    with patch("pantheon.settings.get_settings", return_value=_mock_settings()):
        assert get_provider_api_key("gemini") == "gemini-key"
        assert get_provider_api_key("unknown") is None


def test_model_selector_does_not_expose_custom_endpoint_providers(monkeypatch):
    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "legacy-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_BASE", raising=False)

    selector = ModelSelector(_mock_settings())

    with (
        patch("pantheon.settings.get_settings", return_value=_mock_settings()),
        patch("pantheon.utils.oauth.CodexOAuthManager") as codex_mgr,
        patch("pantheon.utils.oauth.GeminiCliOAuthManager") as gemini_mgr,
        patch("pantheon.utils.model_selector.get_ollama_cached_state", return_value=(False, [])),
    ):
        codex_mgr.return_value.is_authenticated.return_value = False
        gemini_mgr.return_value.is_authenticated.return_value = False

        assert "custom_openai" not in selector._get_available_providers()


def test_settings_maps_legacy_custom_openai_envs_to_openai_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "legacy-openai-key")
    monkeypatch.setenv("CUSTOM_OPENAI_API_BASE", "https://legacy-openai.example/v1")

    settings = Settings(work_dir=tmp_path)
    settings._loaded = True
    settings._settings = {}

    assert settings.get_api_key("OPENAI_API_KEY") == "legacy-openai-key"
    assert settings.get_api_key("OPENAI_API_BASE") == "https://legacy-openai.example/v1"


def test_settings_maps_legacy_custom_anthropic_envs_to_anthropic_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_BASE", raising=False)
    monkeypatch.setenv("CUSTOM_ANTHROPIC_API_KEY", "legacy-anthropic-key")
    monkeypatch.setenv("CUSTOM_ANTHROPIC_API_BASE", "https://legacy-anthropic.example")

    settings = Settings(work_dir=tmp_path)
    settings._loaded = True
    settings._settings = {}

    assert settings.get_api_key("ANTHROPIC_API_KEY") == "legacy-anthropic-key"
    assert settings.get_api_key("ANTHROPIC_API_BASE") == "https://legacy-anthropic.example"
