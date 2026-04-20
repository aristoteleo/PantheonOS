from __future__ import annotations

from typing import Any

import httpx


SUPPORTED_DISCOVERY_PROVIDERS = {"openai", "anthropic", "gemini"}
DEFAULT_PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}
_GENERATION_METHODS = {"generateContent", "streamGenerateContent"}


def normalize_provider_name(provider: str) -> str:
    return provider.strip().lower().replace("google", "gemini")


def error_response(
    provider: str,
    code: str,
    message: str,
    *,
    status_code: int | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "success": False,
        "provider": provider,
        "error_code": code,
        "message": message,
    }
    if status_code is not None:
        response["status_code"] = status_code
    return response


def _join_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def build_models_endpoint(provider: str, api_base: str | None = None) -> str:
    provider = normalize_provider_name(provider)
    base_url = (api_base or DEFAULT_PROVIDER_BASE_URLS[provider]).rstrip("/")

    if provider == "gemini":
        suffix = "models" if base_url.endswith(("/v1", "/v1beta")) else "v1beta/models"
        return _join_url(base_url, suffix)

    suffix = "models" if base_url.endswith("/v1") else "v1/models"
    return _join_url(base_url, suffix)


def _auth_config(provider: str, api_key: str) -> tuple[dict[str, str], dict[str, str] | None]:
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] | None = None

    if provider == "openai":
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "gemini":
        params = {"key": api_key}

    return headers, params


def _has_non_ascii(text: str) -> bool:
    return any(not char.isascii() for char in text)


def resolve_provider_credentials(
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> tuple[str, str]:
    from pantheon.utils.llm_providers import get_provider_api_key, resolve_provider_base_url

    provider = normalize_provider_name(provider)
    effective_key = (api_key or "").strip() or (get_provider_api_key(provider) or "").strip()
    effective_base = (api_base or "").strip() or (resolve_provider_base_url(provider) or "").strip()
    return effective_key, effective_base


def _parse_openai_like_models(payload: dict[str, Any]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()

    for entry in payload.get("data", []):
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append(model_id)

    return models


def _parse_gemini_models(payload: dict[str, Any]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()

    for entry in payload.get("models", []):
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods", [])
        if isinstance(methods, list) and methods:
            if not any(method in _GENERATION_METHODS for method in methods if isinstance(method, str)):
                continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        model_id = name.split("/", 1)[-1].strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append(model_id)

    return models


def parse_discovered_models(provider: str, payload: dict[str, Any]) -> list[str]:
    provider = normalize_provider_name(provider)
    if provider in {"openai", "anthropic"}:
        return _parse_openai_like_models(payload)
    if provider == "gemini":
        return _parse_gemini_models(payload)
    return []


async def discover_provider_models(
    provider: str,
    api_key: str,
    api_base: str | None = None,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    provider = normalize_provider_name(provider)
    if provider not in SUPPORTED_DISCOVERY_PROVIDERS:
        return error_response(
            provider,
            "unsupported_provider",
            "Model discovery is only supported for OpenAI, Anthropic, and Gemini.",
        )

    if not api_key.strip():
        return error_response(
            provider,
            "missing_api_key",
            f"{provider.upper()} API key is required before testing or discovering models.",
        )

    if _has_non_ascii(api_key):
        return error_response(
            provider,
            "invalid_api_key",
            "API key contains unsupported characters. Check for extra text, full-width punctuation, or non-ASCII characters.",
        )

    endpoint = build_models_endpoint(provider, api_base)
    headers, params = _auth_config(provider, api_key)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.get(endpoint, headers=headers, params=params)
    except httpx.TimeoutException:
        return error_response(
            provider,
            "timeout",
            f"Timed out while connecting to {endpoint}.",
        )
    except httpx.HTTPError as exc:
        return error_response(
            provider,
            "network_error",
            f"Failed to reach {endpoint}: {exc}",
        )

    if response.status_code in {401, 403}:
        return error_response(
            provider,
            "auth_error",
            "Authentication failed. Check the API key and Base URL.",
            status_code=response.status_code,
        )

    if response.status_code in {404, 405, 501}:
        return error_response(
            provider,
            "discovery_unsupported",
            "The endpoint does not expose a compatible model discovery API. You can still add models manually.",
            status_code=response.status_code,
        )

    if not response.is_success:
        return error_response(
            provider,
            "http_error",
            f"Model discovery failed with HTTP {response.status_code}.",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError:
        return error_response(
            provider,
            "invalid_response",
            "The endpoint returned a non-JSON response.",
            status_code=response.status_code,
        )

    if not isinstance(payload, dict):
        return error_response(
            provider,
            "invalid_response",
            "The endpoint returned an unexpected response shape.",
            status_code=response.status_code,
        )

    models = parse_discovered_models(provider, payload)
    tested_base_url = (api_base or DEFAULT_PROVIDER_BASE_URLS[provider]).rstrip("/")
    return {
        "success": True,
        "provider": provider,
        "models": models,
        "tested_base_url": tested_base_url,
        "message": f"Discovered {len(models)} model(s).",
    }


async def discover_provider_models_with_fallback(
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    effective_key, effective_base = resolve_provider_credentials(provider, api_key, api_base)
    return await discover_provider_models(
        provider=provider,
        api_key=effective_key,
        api_base=effective_base or None,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "DEFAULT_PROVIDER_BASE_URLS",
    "SUPPORTED_DISCOVERY_PROVIDERS",
    "build_models_endpoint",
    "discover_provider_models",
    "discover_provider_models_with_fallback",
    "error_response",
    "normalize_provider_name",
    "parse_discovered_models",
    "resolve_provider_credentials",
]
