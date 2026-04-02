"""
Generic OAuth/auth settings helpers.

This module provides a provider-aware configuration layer while preserving
backward compatibility with the older ``auth.openai`` layout.
"""
from __future__ import annotations

from typing import Any

from pantheon.settings import get_settings


def _settings_get(key: str, default=None):
    settings = get_settings()
    return settings.get(key, default)


def get_default_oauth_provider() -> str:
    provider = str(_settings_get("auth.default_oauth_provider", "openai") or "openai").strip().lower()
    return provider or "openai"


def get_provider_auth_settings(provider: str) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        return {}

    raw = _settings_get(f"auth.providers.{provider_key}", None)
    if isinstance(raw, dict) and raw:
        return raw

    # Backward compatibility for the legacy auth.openai layout.
    if provider_key == "openai":
        legacy = _settings_get("auth.openai", None)
        if isinstance(legacy, dict):
            return legacy
    return {}

