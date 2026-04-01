"""
OpenAI authentication strategy helpers.

This module centralizes how Pantheon decides between OpenAI API key auth
and Codex OAuth auth when both are present.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pantheon.settings import get_settings
from pantheon.utils.log import logger


VALID_OPENAI_AUTH_MODES = {
    "auto",
    "prefer_api_key",
    "prefer_oauth",
    "api_key_only",
    "oauth_only",
}


@dataclass(frozen=True)
class OpenAIAuthSettings:
    mode: str = "auto"
    enable_api_key: bool = True
    enable_oauth: bool = True
    allow_codex_fallback_to_api_key: bool = False
    allow_openai_api_fallback_to_oauth: bool = False

    def normalized(self) -> "OpenAIAuthSettings":
        mode = str(self.mode or "auto").strip().lower()
        if mode not in VALID_OPENAI_AUTH_MODES:
            logger.warning(f"Unknown auth.openai.mode '{self.mode}', falling back to 'auto'")
            mode = "auto"
        return OpenAIAuthSettings(
            mode=mode,
            enable_api_key=bool(self.enable_api_key),
            enable_oauth=bool(self.enable_oauth),
            allow_codex_fallback_to_api_key=bool(self.allow_codex_fallback_to_api_key),
            allow_openai_api_fallback_to_oauth=bool(self.allow_openai_api_fallback_to_oauth),
        )


def get_openai_auth_settings() -> OpenAIAuthSettings:
    settings = get_settings()
    raw = settings.get("auth.openai", {}) or {}
    return OpenAIAuthSettings(
        mode=raw.get("mode", "auto"),
        enable_api_key=raw.get("enable_api_key", True),
        enable_oauth=raw.get("enable_oauth", True),
        allow_codex_fallback_to_api_key=raw.get("allow_codex_fallback_to_api_key", False),
        allow_openai_api_fallback_to_oauth=raw.get("allow_openai_api_fallback_to_oauth", False),
    ).normalized()


def get_openai_auth_settings_dict() -> dict[str, Any]:
    return asdict(get_openai_auth_settings())


def is_api_key_auth_enabled() -> bool:
    prefs = get_openai_auth_settings()
    return prefs.enable_api_key and prefs.mode != "oauth_only"


def is_oauth_auth_enabled() -> bool:
    prefs = get_openai_auth_settings()
    return prefs.enable_oauth and prefs.mode != "api_key_only"


def should_use_codex_oauth_transport(model_name: str) -> bool:
    prefs = get_openai_auth_settings()
    if not is_oauth_auth_enabled():
        return False

    lower = (model_name or "").lower()
    if lower.startswith("codex/"):
        return True
    if "codex" in lower and prefs.mode in {"prefer_oauth", "oauth_only"}:
        return True
    return False


def should_treat_openai_api_key_as_available() -> bool:
    return is_api_key_auth_enabled()


def summarize_openai_auth_state(
    *,
    api_key_present: bool,
    oauth_authenticated: bool,
) -> dict[str, Any]:
    prefs = get_openai_auth_settings()
    return {
        "mode": prefs.mode,
        "enable_api_key": prefs.enable_api_key,
        "enable_oauth": prefs.enable_oauth,
        "allow_codex_fallback_to_api_key": prefs.allow_codex_fallback_to_api_key,
        "allow_openai_api_fallback_to_oauth": prefs.allow_openai_api_fallback_to_oauth,
        "api_key_present": bool(api_key_present),
        "oauth_authenticated": bool(oauth_authenticated),
        "effective_api_key_enabled": is_api_key_auth_enabled(),
        "effective_oauth_enabled": is_oauth_auth_enabled(),
    }
