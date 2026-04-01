from unittest.mock import MagicMock, patch

import asyncio
import pytest

from pantheon.auth.openai_auth_strategy import (
    get_openai_auth_settings,
    is_api_key_auth_enabled,
    is_oauth_auth_enabled,
    should_use_codex_oauth_transport,
)


def _mock_settings(auth_openai: dict):
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: (
        auth_openai if key == "auth.openai" else default
    )
    return settings


def test_api_key_can_be_disabled_by_settings():
    with patch("pantheon.auth.openai_auth_strategy.get_settings", return_value=_mock_settings({
        "mode": "auto",
        "enable_api_key": False,
        "enable_oauth": True,
    })):
        prefs = get_openai_auth_settings()
        assert prefs.mode == "auto"
        assert is_api_key_auth_enabled() is False
        assert is_oauth_auth_enabled() is True


def test_oauth_only_disables_api_key_routing():
    with patch("pantheon.auth.openai_auth_strategy.get_settings", return_value=_mock_settings({
        "mode": "oauth_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        assert is_api_key_auth_enabled() is False
        assert is_oauth_auth_enabled() is True
        assert should_use_codex_oauth_transport("codex/gpt-5.4") is True


def test_api_key_only_disables_codex_oauth_transport():
    with patch("pantheon.auth.openai_auth_strategy.get_settings", return_value=_mock_settings({
        "mode": "api_key_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        assert is_api_key_auth_enabled() is True
        assert is_oauth_auth_enabled() is False
        assert should_use_codex_oauth_transport("codex/gpt-5.4") is False


def test_codex_model_respects_disabled_oauth():
    from pantheon.utils.llm_providers import ProviderConfig, ProviderType, call_llm_provider

    with patch("pantheon.auth.openai_auth_strategy.get_settings", return_value=_mock_settings({
        "mode": "api_key_only",
        "enable_api_key": True,
        "enable_oauth": True,
    })):
        with pytest.raises(RuntimeError, match="Codex OAuth transport is disabled"):
            asyncio.run(
                call_llm_provider(
                    config=ProviderConfig(
                        provider_type=ProviderType.OPENAI,
                        model_name="codex/gpt-5.4",
                    ),
                    messages=[{"role": "user", "content": "hi"}],
                )
            )
