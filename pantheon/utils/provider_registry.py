"""
Provider registry — loads the catalog and exposes model metadata helpers.

Provides get_model_info, completion_cost, token_counter,
and models_by_provider from the local LLM catalog.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .log import logger

# ============ Catalog Loading ============

_CATALOG_PATH = Path(__file__).parent / "llm_catalog.json"

# Default metadata for unknown models
_DEFAULT_MODEL_INFO = {
    "max_input_tokens": 200_000,
    "max_output_tokens": 32_000,
    "input_cost_per_million": 1.0,
    "output_cost_per_million": 5.0,
    "supports_vision": False,
    "supports_function_calling": True,
    "supports_response_schema": False,
    "supports_reasoning": False,
    "supports_audio_input": False,
    "supports_audio_output": False,
    "supports_web_search": False,
    "supports_pdf_input": False,
    "supports_computer_use": False,
    "supports_assistant_prefill": False,
}


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """Load and cache the provider catalog from llm_catalog.json."""
    try:
        with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load LLM catalog: {e}")
        return {"version": 1, "providers": {}}


def reload_catalog() -> dict:
    """Force-reload the catalog (clears cache). For testing."""
    load_catalog.cache_clear()
    return load_catalog()


# ============ Provider Resolution ============


def _parse_model_string(model: str) -> tuple[str | None, str]:
    """Parse 'provider/model_name' into (provider, model_name).

    Returns (None, model) if no provider prefix.
    """
    if "/" in model:
        provider, model_name = model.split("/", 1)
        return provider.lower(), model_name
    return None, model


def find_provider_for_model(model: str) -> tuple[str, str, dict]:
    """Given a model string, return (provider_key, model_name, provider_config).

    Tries:
    1. Explicit prefix: 'anthropic/claude-sonnet-4-6' → provider='anthropic'
    2. Search all providers for a matching model name

    Returns ('unknown', model, {}) if not found.
    """
    catalog = load_catalog()
    providers = catalog.get("providers", {})

    # 1. Explicit prefix
    prefix, model_name = _parse_model_string(model)
    if prefix and prefix in providers:
        return prefix, model_name, providers[prefix]

    # 2. Search all providers for bare model name
    for pkey, pconfig in providers.items():
        if model_name in pconfig.get("models", {}):
            return pkey, model_name, pconfig

    # 3. Not found — return with empty config
    return prefix or "unknown", model_name, {}


def get_provider_config(provider: str) -> dict:
    """Get provider configuration from catalog."""
    catalog = load_catalog()
    return catalog.get("providers", {}).get(provider, {})


# ============ Model Metadata ============


def get_model_info(model: str) -> dict:
    """Get model metadata from the catalog.

    Retrieves model metadata from the local catalog.

    Args:
        model: Model string, e.g. 'anthropic/claude-sonnet-4-6' or 'gpt-5.4'

    Returns:
        Dict with max_input_tokens, max_output_tokens, pricing, supports_*, etc.
        Returns defaults for unknown models.
    """
    provider_key, model_name, provider_config = find_provider_for_model(model)
    models = provider_config.get("models", {})

    if model_name in models:
        info = {**_DEFAULT_MODEL_INFO, **models[model_name]}
        # Ensure per-token fields exist for backward compat
        if "input_cost_per_token" not in info:
            info["input_cost_per_token"] = info.get("input_cost_per_million", 1.0) / 1_000_000
        if "output_cost_per_token" not in info:
            info["output_cost_per_token"] = info.get("output_cost_per_million", 5.0) / 1_000_000
        return info

    logger.debug(f"Model '{model}' not found in catalog, using defaults")
    info = dict(_DEFAULT_MODEL_INFO)
    info["input_cost_per_token"] = info["input_cost_per_million"] / 1_000_000
    info["output_cost_per_token"] = info["output_cost_per_million"] / 1_000_000
    return info


# ============ Cost Calculation ============


def completion_cost(
    completion_response: Any = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> float:
    """Calculate completion cost from response or explicit token counts.

    Calculates completion cost from the local catalog pricing.
    """
    # Extract from response object if provided
    if completion_response is not None:
        usage = getattr(completion_response, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Try to get model from response
        if model is None:
            model = getattr(completion_response, "model", None) or ""

    if not model:
        # Fallback pricing: $1/1M input, $5/1M output
        return (prompt_tokens * 1.0 + completion_tokens * 5.0) / 1_000_000

    info = get_model_info(model)
    input_cost = info.get("input_cost_per_token", 1.0 / 1_000_000)
    output_cost = info.get("output_cost_per_token", 5.0 / 1_000_000)

    return prompt_tokens * input_cost + completion_tokens * output_cost


# ============ Model Listing ============


def models_by_provider(provider: str) -> list[str]:
    """List all model names for a provider.

    Lists all model names for a given provider from the catalog.
    """
    catalog = load_catalog()
    provider_config = catalog.get("providers", {}).get(provider, {})
    models = provider_config.get("models", {})

    # Return as 'provider/model_name' format
    return [f"{provider}/{name}" for name in models]


# ============ Token Counting ============


def token_counter(
    model: str,
    messages: list[dict] | None = None,
    tools: list[dict] | None = None,
) -> int:
    """Count tokens for messages and tools.

    Uses tiktoken when available, falls back to heuristic estimation.
    """
    total = 0

    # Try tiktoken first (works for OpenAI models)
    try:
        import tiktoken

        # Map model to encoding
        try:
            encoding = tiktoken.encoding_for_model(model.split("/")[-1])
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        for msg in messages or []:
            # Per-message overhead
            total += 4  # role + content framing
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(encoding.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text", "")
                        if text:
                            total += len(encoding.encode(text))
                        # Image tokens: rough estimate
                        if part.get("type") == "image_url":
                            total += 765  # ~average image token cost

        if tools:
            total += len(encoding.encode(json.dumps(tools)))

        return total

    except (ImportError, Exception):
        pass

    # Fallback: heuristic estimation
    for msg in messages or []:
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _heuristic_token_count(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += _heuristic_token_count(part["text"])

    if tools:
        total += _heuristic_token_count(json.dumps(tools))

    return total


def _heuristic_token_count(text: str) -> int:
    """Estimate token count with language-aware heuristics."""
    if not text:
        return 0

    cjk_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3040' <= c <= '\u30ff'
                    or '\uac00' <= c <= '\ud7af')
    ascii_chars = sum(1 for c in text if c.isascii())
    other_chars = len(text) - cjk_chars - ascii_chars

    tokens = (
        cjk_chars * 0.6 +      # CJK: ~1.7 chars per token
        ascii_chars * 0.25 +    # ASCII: ~4 chars per token
        other_chars * 0.5       # Other: ~2 chars per token
    )

    return max(1, int(tokens))
