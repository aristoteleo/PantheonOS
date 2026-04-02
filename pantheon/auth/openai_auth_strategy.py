"""
OpenAI authentication strategy helpers.

This module centralizes how Pantheon decides between OpenAI API key auth
and Codex OAuth auth when both are present.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import os

from pantheon.auth.auth_settings import get_provider_auth_settings
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


@dataclass(frozen=True)
class OpenAIAuthDecision:
    model_name: str
    selected_auth: str
    reason: str
    oauth_transport: bool
    fallback_used: bool = False
    api_key_present: bool = False
    oauth_authenticated: bool = False
    effective_api_key_enabled: bool = False
    effective_oauth_enabled: bool = False
    mode: str = "auto"
    standard_openai_api: bool = False
    codex_model: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_openai_auth_settings() -> OpenAIAuthSettings:
    raw = get_provider_auth_settings("openai") or {}
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

    lower = (model_name or "").strip().lower()
    if lower.startswith("codex/"):
        return True
    if "codex" in lower and prefs.mode in {"prefer_oauth", "oauth_only"}:
        return True
    return False


def should_treat_openai_api_key_as_available() -> bool:
    return is_api_key_auth_enabled()


def get_openai_auth_runtime_state() -> dict[str, Any]:
    oauth_authenticated = False
    try:
        from pantheon.auth.oauth_manager import get_oauth_manager

        oauth_status = get_oauth_manager().get_status("openai")
        oauth_authenticated = bool(oauth_status and oauth_status.authenticated)
    except Exception:
        oauth_authenticated = False

    return summarize_openai_auth_state(
        api_key_present=bool(os.environ.get("OPENAI_API_KEY")),
        oauth_authenticated=oauth_authenticated,
    )


def decide_openai_auth(
    model_name: str,
    *,
    api_key_present: bool,
    oauth_authenticated: bool,
) -> OpenAIAuthDecision:
    prefs = get_openai_auth_settings()
    effective_api_key_enabled = is_api_key_auth_enabled()
    effective_oauth_enabled = is_oauth_auth_enabled()

    lower = (model_name or "").strip().lower()
    codex_model = lower.startswith("codex/") or "codex" in lower
    standard_openai_api = not codex_model

    base_kwargs = {
        "model_name": model_name,
        "api_key_present": bool(api_key_present),
        "oauth_authenticated": bool(oauth_authenticated),
        "effective_api_key_enabled": effective_api_key_enabled,
        "effective_oauth_enabled": effective_oauth_enabled,
        "mode": prefs.mode,
        "standard_openai_api": standard_openai_api,
        "codex_model": codex_model,
    }

    if codex_model:
        if effective_oauth_enabled and oauth_authenticated:
            return OpenAIAuthDecision(
                selected_auth="oauth",
                reason="codex_models_use_oauth_transport",
                oauth_transport=True,
                **base_kwargs,
            )
        if effective_api_key_enabled and api_key_present and prefs.allow_codex_fallback_to_api_key:
            return OpenAIAuthDecision(
                selected_auth="api_key",
                reason="codex_oauth_unavailable_fell_back_to_api_key",
                oauth_transport=False,
                fallback_used=True,
                **base_kwargs,
            )
        if not effective_oauth_enabled:
            return OpenAIAuthDecision(
                selected_auth="unavailable",
                reason="oauth_disabled_for_codex_models",
                oauth_transport=False,
                **base_kwargs,
            )
        return OpenAIAuthDecision(
            selected_auth="unavailable",
            reason="oauth_required_for_codex_models",
            oauth_transport=False,
            **base_kwargs,
        )

    if effective_api_key_enabled and api_key_present:
        return OpenAIAuthDecision(
            selected_auth="api_key",
            reason="standard_openai_api_uses_api_key",
            oauth_transport=False,
            **base_kwargs,
        )
    if (
        effective_oauth_enabled
        and oauth_authenticated
        and prefs.allow_openai_api_fallback_to_oauth
    ):
        return OpenAIAuthDecision(
            selected_auth="oauth",
            reason="standard_openai_api_fell_back_to_oauth",
            oauth_transport=False,
            fallback_used=True,
            **base_kwargs,
        )
    if oauth_authenticated and effective_oauth_enabled:
        return OpenAIAuthDecision(
            selected_auth="unavailable",
            reason="oauth_does_not_replace_standard_openai_api_key",
            oauth_transport=False,
            **base_kwargs,
        )
    if not effective_api_key_enabled:
        return OpenAIAuthDecision(
            selected_auth="unavailable",
            reason="api_key_routing_disabled_for_standard_openai_api",
            oauth_transport=False,
            **base_kwargs,
        )
    return OpenAIAuthDecision(
        selected_auth="unavailable",
        reason="missing_openai_api_key_for_standard_openai_api",
        oauth_transport=False,
        **base_kwargs,
    )


def resolve_openai_auth_decision(
    model_name: str,
    *,
    api_key_present: bool | None = None,
    oauth_authenticated: bool | None = None,
) -> OpenAIAuthDecision:
    if api_key_present is None or oauth_authenticated is None:
        runtime = get_openai_auth_runtime_state()
        if api_key_present is None:
            api_key_present = bool(runtime["api_key_present"])
        if oauth_authenticated is None:
            oauth_authenticated = bool(runtime["oauth_authenticated"])

    return decide_openai_auth(
        model_name,
        api_key_present=bool(api_key_present),
        oauth_authenticated=bool(oauth_authenticated),
    )


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
