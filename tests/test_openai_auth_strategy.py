from unittest.mock import patch

import asyncio
import pytest

from pantheon.auth.openai_auth_strategy import (
    decide_openai_auth,
    get_openai_auth_settings,
    is_api_key_auth_enabled,
    is_oauth_auth_enabled,
    resolve_openai_auth_decision,
    should_use_codex_oauth_transport,
)


def _mock_provider_settings(auth_openai: dict):
    def _get_provider_auth_settings(provider: str):
        if provider == "openai":
            return auth_openai
        return {}
    return _get_provider_auth_settings


def test_api_key_can_be_disabled_by_settings():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "auto",
        "enable_api_key": False,
        "enable_oauth": True,
    })):
        prefs = get_openai_auth_settings()
        assert prefs.mode == "auto"
        assert is_api_key_auth_enabled() is False
        assert is_oauth_auth_enabled() is True


def test_oauth_only_disables_api_key_routing():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "oauth_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        assert is_api_key_auth_enabled() is False
        assert is_oauth_auth_enabled() is True
        assert should_use_codex_oauth_transport("codex/gpt-5.4") is True


def test_api_key_only_disables_codex_oauth_transport():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "api_key_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        assert is_api_key_auth_enabled() is True
        assert is_oauth_auth_enabled() is False
        assert should_use_codex_oauth_transport("codex/gpt-5.4") is False


def test_codex_model_respects_disabled_oauth():
    from pantheon.utils.llm_providers import ProviderConfig, ProviderType, call_llm_provider

    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "api_key_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        with pytest.raises(RuntimeError, match="Codex OAuth transport is unavailable"):
            asyncio.run(
                call_llm_provider(
                    config=ProviderConfig(
                        provider_type=ProviderType.OPENAI,
                        model_name="codex/gpt-5.4",
                    ),
                    messages=[{"role": "user", "content": "hi"}],
                )
            )


def test_standard_openai_prefers_api_key_when_both_exist():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "auto",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        decision = resolve_openai_auth_decision(
            "openai/gpt-5.4",
            api_key_present=True,
            oauth_authenticated=True,
        )
        assert decision.selected_auth == "api_key"
        assert decision.reason == "standard_openai_api_uses_api_key"


def test_codex_prefers_oauth_when_both_exist():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "auto",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        decision = resolve_openai_auth_decision(
            "codex/gpt-5.4",
            api_key_present=True,
            oauth_authenticated=True,
        )
        assert decision.selected_auth == "oauth"
        assert decision.oauth_transport is True


def test_decide_openai_auth_is_pure_given_explicit_runtime_inputs():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "auto",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        decision = decide_openai_auth(
            "codex/gpt-5.4",
            api_key_present=False,
            oauth_authenticated=True,
        )
        assert decision.selected_auth == "oauth"
        assert decision.reason == "codex_models_use_oauth_transport"


def test_standard_openai_rejects_oauth_only_without_api_key():
    with patch("pantheon.auth.openai_auth_strategy.get_provider_auth_settings", side_effect=_mock_provider_settings({
        "mode": "oauth_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        decision = resolve_openai_auth_decision(
            "openai/gpt-5.4",
            api_key_present=False,
            oauth_authenticated=True,
        )
        assert decision.selected_auth == "unavailable"
        assert decision.reason in {
            "oauth_does_not_replace_standard_openai_api_key",
            "api_key_routing_disabled_for_standard_openai_api",
        }
