from unittest.mock import MagicMock, patch

from pantheon.auth.auth_settings import (
    get_default_oauth_provider,
    get_provider_auth_settings,
)


def _mock_settings(values: dict):
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: values.get(key, default)
    return settings


def test_default_oauth_provider_uses_new_setting():
    with patch(
        "pantheon.auth.auth_settings.get_settings",
        return_value=_mock_settings({"auth.default_oauth_provider": "github"}),
    ):
        assert get_default_oauth_provider() == "github"


def test_provider_auth_settings_prefers_new_layout():
    with patch(
        "pantheon.auth.auth_settings.get_settings",
        return_value=_mock_settings(
            {
                "auth.providers.openai": {"mode": "prefer_oauth"},
                "auth.openai": {"mode": "auto"},
            }
        ),
    ):
        assert get_provider_auth_settings("openai") == {"mode": "prefer_oauth"}


def test_provider_auth_settings_falls_back_to_legacy_openai_layout():
    with patch(
        "pantheon.auth.auth_settings.get_settings",
        return_value=_mock_settings({"auth.openai": {"mode": "oauth_only"}}),
    ):
        assert get_provider_auth_settings("openai") == {"mode": "oauth_only"}

