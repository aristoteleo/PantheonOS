from unittest.mock import patch

from pantheon.auth.oauth_manager import OAuthManager


class _DummyProvider:
    def __init__(self, name: str, display_name: str):
        self._name = name
        self._display_name = display_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    def login(self, *, open_browser: bool = True, timeout_seconds: int = 300) -> bool:
        return True

    def get_status(self):
        from pantheon.auth.oauth_manager import OAuthStatus

        return OAuthStatus(authenticated=False)

    def logout(self) -> None:
        return None

    def ensure_access_token(self, refresh_if_needed: bool = True):
        return None


def test_default_provider_comes_from_settings():
    with patch("pantheon.auth.oauth_manager.get_default_oauth_provider", return_value="github"):
        manager = OAuthManager()
        manager.register(_DummyProvider("openai", "OpenAI"))
        manager.register(_DummyProvider("github", "GitHub"))
        assert manager.default_provider == "github"


def test_first_registered_provider_becomes_default_if_configured_default_missing():
    with patch("pantheon.auth.oauth_manager.get_default_oauth_provider", return_value="github"):
        manager = OAuthManager()
        manager.register(_DummyProvider("openai", "OpenAI"))
        assert manager.default_provider == "openai"
