"""
Provider abstraction and utilities for LLM API calls.

This module encapsulates:
1. Provider detection and configuration
2. Response extraction and normalization
3. Provider selection logic
"""

import os
import time
from enum import Enum
from typing import Any, Callable, Optional, NamedTuple
from dataclasses import dataclass

from .misc import run_func
from .log import logger


# ============ Enums and Data Classes ============


class ProviderType(Enum):
    """Supported LLM providers.

    OPENAI: Direct OpenAI or OpenAI-compatible providers
    NATIVE: Non-OpenAI providers using native SDKs (anthropic, gemini, etc.)
    """

    OPENAI = "openai"
    NATIVE = "native"


@dataclass
class ProviderConfig:
    """Provider configuration"""

    provider_type: ProviderType
    model_name: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    relaxed_schema: bool = False


# OpenAI-compatible providers that need custom base_url.
# Maps provider prefix → (api_base_url, api_key_env_var)
OPENAI_COMPATIBLE_PROVIDERS: dict[str, tuple[str, str]] = {}

# Provider-specific Base URL env vars supported by the backend.
PROVIDER_BASE_ENV_MAP: dict[str, str] = {
    "openai": "OPENAI_API_BASE",
    "anthropic": "ANTHROPIC_API_BASE",
    "gemini": "GEMINI_API_BASE",
}

# Provider-specific API key env vars for first-class providers.
PROVIDER_API_KEY_ENV_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


# ============ Provider Detection ============


def detect_provider(model: str, relaxed_schema: bool) -> ProviderConfig:
    """Detect provider from model string.

    Model format:
    - "gpt-4" → OpenAI provider
    - "provider/model" → Native SDK (handles anthropic, gemini, etc.)

    Args:
        model: Model identifier string
        relaxed_schema: Use relaxed (non-strict) tool schema mode

    Returns:
        ProviderConfig with detected provider and model name
    """
    base_url = None
    api_key = None

    if "/" in model:
        provider_str, model_name = model.split("/", 1)
        provider_lower = provider_str.lower()

        # Check if it's an OpenAI-compatible provider (e.g. minimax)
        if provider_lower in OPENAI_COMPATIBLE_PROVIDERS:
            provider_type = ProviderType.OPENAI
            compat_base, compat_key_env = OPENAI_COMPATIBLE_PROVIDERS[provider_lower]
            base_url = os.environ.get(f"{provider_lower.upper()}_API_BASE", compat_base)
            api_key = os.environ.get(compat_key_env, "")
        # Check if it's explicitly openai provider
        elif provider_lower == "openai":
            provider_type = ProviderType.OPENAI
        else:
            # All other prefixed models use native SDK adapters (anthropic, gemini, etc.)
            provider_type = ProviderType.NATIVE
            model_name = model  # Keep full model string for native adapter
    else:
        provider_type = ProviderType.OPENAI
        model_name = model

    # Override with NATIVE if relaxed_schema is forced
    if relaxed_schema and provider_type != ProviderType.NATIVE:
        provider_type = ProviderType.NATIVE

    return ProviderConfig(
        provider_type=provider_type,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key or None,
        relaxed_schema=relaxed_schema,
    )


def is_responses_api_model(config: ProviderConfig) -> bool:
    """Check if the model is Responses-API-only (no Chat Completions fallback).

    Triggers for:
    - Models with "codex" in the name (e.g. codex-mini-latest)
    - Pro models (gpt-5.x-pro, gpt-5.2-pro) which are Responses-only

    For the broader "should I try Responses API" question (which covers all
    OpenAI models by default), use ``should_use_responses_api`` instead.
    """
    name_lower = config.model_name.lower()
    if config.provider_type != ProviderType.OPENAI:
        return False
    # Strip "openai/" prefix for matching
    bare = name_lower.split("/")[-1] if "/" in name_lower else name_lower
    return "codex" in bare or bare.endswith("-pro")


# Per-process cache of (base_url, model_name) combinations where the
# /v1/responses endpoint is known to be unavailable. Populated at runtime
# when a Responses API call hits a 404 — we then fall back to Chat Completions
# and skip the probe on subsequent calls.
_RESPONSES_API_UNAVAILABLE: set[tuple[str, str]] = set()


def _responses_cache_key(config: ProviderConfig) -> tuple[str, str]:
    return (config.base_url or "", config.model_name.lower())


def should_use_responses_api(config: ProviderConfig) -> bool:
    """Return True if this OpenAI call should try the Responses API first.

    Default behaviour: all OPENAI-routed models attempt /v1/responses, because
    Responses API supports native image input in function_call_output and has
    superseded Chat Completions for most modern OpenAI models.

    Exceptions:
    - Non-OPENAI provider types (anthropic, gemini, etc.) use their native SDKs.
    - Codex OAuth models (``codex/``) have a dedicated adapter.
    - (base_url, model) pairs where a prior call recorded that the endpoint
      does not implement /v1/responses fall back to Chat Completions.
    """
    if config.provider_type != ProviderType.OPENAI:
        return False
    if "codex/" in config.model_name.lower():
        return False
    if _responses_cache_key(config) in _RESPONSES_API_UNAVAILABLE:
        return False
    return True


def mark_responses_api_unavailable(config: ProviderConfig) -> None:
    """Record that /v1/responses is not available for a (base_url, model) pair.

    Called after a 404 from the Responses API so subsequent calls skip the probe
    and go straight to Chat Completions.
    """
    _RESPONSES_API_UNAVAILABLE.add(_responses_cache_key(config))


def reset_responses_api_cache() -> None:
    """Clear the unavailability cache. Used in tests."""
    _RESPONSES_API_UNAVAILABLE.clear()


def get_provider_base_url(provider_key: str) -> Optional[str]:
    """Get a provider-specific Base URL override without applying global fallback."""
    from pantheon.settings import get_settings

    env_key = PROVIDER_BASE_ENV_MAP.get(provider_key)
    if not env_key:
        return None
    return get_settings().get_api_key(env_key)


def get_global_fallback_base_url() -> str:
    """Return the global fallback Base URL from ``LLM_API_BASE`` if configured."""
    from pantheon.settings import get_settings

    return get_settings().get_api_key("LLM_API_BASE") or ""


def resolve_provider_base_url(
    provider_key: str,
    default_base_url: str | None = None,
) -> Optional[str]:
    """Resolve the effective Base URL for a provider.

    Priority:
    1. Provider-specific ``*_API_BASE``
    2. Global ``LLM_API_BASE``
    3. Catalog/default base URL
    """
    provider_base = get_provider_base_url(provider_key)
    if provider_base:
        return provider_base

    fallback_base = get_global_fallback_base_url()
    if fallback_base:
        return fallback_base

    return default_base_url


def get_provider_api_key(
    provider_key: str,
    api_key_env: str | None = None,
) -> Optional[str]:
    """Get a provider-specific API key without applying global fallback."""
    from pantheon.settings import get_settings

    env_key = api_key_env or PROVIDER_API_KEY_ENV_MAP.get(provider_key)
    if not env_key:
        return None
    return get_settings().get_api_key(env_key)


def get_openai_fallback_config() -> tuple[str, str]:
    """Return the global fallback ``(base_url, api_key)`` values.

    Each field is resolved independently from ``LLM_API_BASE`` / ``LLM_API_KEY``.
    """
    from pantheon.settings import get_settings

    settings = get_settings()
    base_url = settings.get_api_key("LLM_API_BASE") or ""
    api_key = settings.get_api_key("LLM_API_KEY") or ""
    return base_url, api_key


def get_openai_effective_config() -> tuple[str, str]:
    """Return the effective OpenAI-routed ``(base_url, api_key)`` pair.

    Resolution is field-wise:
    1. ``OPENAI_API_BASE`` / ``OPENAI_API_KEY``
    2. ``LLM_API_BASE`` / ``LLM_API_KEY`` fallback

    This allows a provider-specific key to coexist with a global fallback base
    when users want OpenAI requests to route through one shared endpoint.
    """
    provider_base = resolve_provider_base_url("openai") or ""
    provider_key = get_provider_api_key("openai") or ""
    fallback_base, fallback_key = get_openai_fallback_config()

    return provider_base or fallback_base, provider_key or fallback_key


# ============ Response Extraction ============


def _create_error_message(content: str) -> dict:
    """Create standardized error message.

    Args:
        content: Error description

    Returns:
        Error message dictionary
    """
    return {"role": "assistant", "content": f"Error: {content}"}


def _clean_message_fields(message: dict) -> None:
    """Clean message fields in place.

    Removes:
    - 'parsed' field (only for structured outputs)
    - Empty 'tool_calls' lists → converted to None

    Args:
        message: Message dictionary to clean
    """
    # Remove parsed field
    message.pop("parsed", None)

    # Convert empty tool_calls to None
    if "tool_calls" in message and message["tool_calls"] == []:
        message["tool_calls"] = None


def _extract_cost_and_usage(complete_resp: Any) -> tuple[float, dict]:
    """Calculate cost and extract usage from response.

    Cost and usage are extracted independently - cost calculation failures
    (e.g., for new models not yet in the price catalog) should not prevent
    usage data from being captured.
    """
    cost = 0.0
    usage_dict = {}

    # Extract usage first (independent of cost calculation)
    usage = getattr(complete_resp, "usage", None)
    if usage:
        if hasattr(usage, "model_dump"):
            usage_dict = usage.model_dump()
        elif hasattr(usage, "to_dict"):
            usage_dict = usage.to_dict()
        else:
            try:
                # vars() works on SimpleNamespace (from stream_chunk_builder)
                # while dict() does not
                usage_dict = vars(usage)
            except Exception:
                try:
                    usage_dict = dict(usage)
                except Exception:
                    pass

    # Calculate cost from catalog pricing
    try:
        from pantheon.utils.provider_registry import completion_cost

        cost = completion_cost(completion_response=complete_resp) or 0.0
    except Exception as e:
        logger.debug(f"Cost calculation unavailable: {e}")

    # Fallback: estimate cost from usage if catalog lookup failed but we have token counts
    if cost == 0.0 and usage_dict:
        input_tokens = usage_dict.get("prompt_tokens", 0)
        output_tokens = usage_dict.get("completion_tokens", 0)
        if input_tokens or output_tokens:
            # Conservative estimate using GPT-4o pricing as reference
            # $1/1M input tokens, $5/1M output tokens
            cost = (input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000
            logger.debug(
                f"Using estimated cost: ${cost:.6f} ({input_tokens} in, {output_tokens} out)"
            )

    return cost, usage_dict


def extract_message_from_response(
    complete_resp: Any, error_prefix: str = "API"
) -> dict:
    """Extract message from API response.

    Handles:
    - Missing or empty responses
    - Extracting first choice
    - Cleaning unwanted fields

    Args:
        complete_resp: API response object
        error_prefix: Prefix for error messages (e.g., "Zhipu AI")

    Returns:
        Cleaned message dictionary
    """
    # Validate response structure
    if not complete_resp:
        return _create_error_message(f"{error_prefix}: None response")

    if not hasattr(complete_resp, "choices"):
        return _create_error_message(f"{error_prefix}: No 'choices' attribute")

    if not complete_resp.choices or len(complete_resp.choices) == 0:
        return _create_error_message(f"{error_prefix}: Empty choices")

    # Extract message
    try:
        message = complete_resp.choices[0].message.model_dump()

        # Calculate cost and usage
        cost, usage = _extract_cost_and_usage(complete_resp)

        # Attach debug info (hidden fields)
        if "_metadata" not in message:
            message["_metadata"] = {}
        message["_metadata"]["_debug_cost"] = cost
        message["_metadata"]["_debug_usage"] = usage

    except (AttributeError, IndexError, TypeError) as e:
        return _create_error_message(
            f"{error_prefix}: Failed to extract message - {type(e).__name__}"
        )

    # Clean fields
    _clean_message_fields(message)
    return message


# ============ Enhanced Chunk Processing ============


def create_enhanced_process_chunk(
    base_process_chunk: Callable | None,
    message_id: str,
    agent_name: str = "",
) -> Callable | None:
    """Create enhanced chunk processor with metadata.

    Injects into each chunk:
    - message_id: For correlating chunks with messages
    - chunk_index: Sequential index of chunks
    - timestamp: When the chunk was processed
    - agent_name: Name of the agent producing this chunk

    Args:
        base_process_chunk: Original chunk processor (can be None)
        message_id: Message identifier for this completion
        agent_name: Name of the agent emitting chunks

    Returns:
        Enhanced async function, or None if base_process_chunk is None
    """
    if not base_process_chunk:
        return None

    chunk_index = 0

    async def enhanced_process_chunk(chunk: dict) -> None:
        """Wrapper that adds metadata to chunks."""
        nonlocal chunk_index
        enhanced_chunk = {
            **chunk,
            "message_id": message_id,
            "chunk_index": chunk_index,
            "timestamp": time.time(),
            "agent_name": agent_name,
        }
        chunk_index += 1
        await run_func(base_process_chunk, enhanced_chunk)

    return enhanced_process_chunk


# ============ Unified LLM Provider Call ============


async def call_llm_provider(
    config: ProviderConfig,
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    model_params: dict | None = None,
) -> dict:
    """Call LLM provider with unified interface.

    Abstracts away provider-specific details:
    - Provider selection
    - API call formatting
    - Response extraction

    Args:
        config: Provider configuration
        messages: Chat messages
        tools: Tool/function definitions
        response_format: Response format specification
        process_chunk: Optional chunk processor
        model_params: Additional parameters for calling the LLM provider
                      (Contains 'thinking' shorthand if provided)

    Returns:
        Extracted and cleaned message dictionary
    """
    from .llm import (
        acompletion,
        remove_metadata,
    )

    logger.debug(
        f"[CALL_LLM_PROVIDER] Starting LLM call | "
        f"Provider={config.provider_type.value} | "
        f"Model={config.model_name} | "
        f"BaseUrl={config.base_url}"
    )

    # Initialize model_params if None
    model_params = model_params or {}

    # Resolve 'thinking' parameter from runtime model_params
    thinking_param = model_params.pop("thinking", None)

    if thinking_param is not None:
        if thinking_param is True:
            model_params["reasoning_effort"] = "medium"
        elif thinking_param is False:
            pass  # Don't set any parameter
        elif isinstance(thinking_param, str):
            # Direct effort level: "low", "medium", "high"
            model_params["reasoning_effort"] = thinking_param
        elif isinstance(thinking_param, dict):
            model_params["thinking"] = thinking_param
        else:
            logger.warning(
                f"Invalid thinking parameter type: {type(thinking_param)}. Disabling thinking."
            )

    # Remove metadata before sending to LLM
    clean_messages = [m.copy() for m in messages]
    clean_messages = remove_metadata(clean_messages)

    # Call appropriate provider
    # Route Codex OAuth models through their dedicated adapter
    if "codex/" in config.model_name.lower() or config.model_name.startswith("codex/"):
        from .llm import acompletion
        logger.debug(f"[CALL_LLM_PROVIDER] Using Codex OAuth for model={config.model_name}")
        # acompletion handles codex specially — returns message dict directly
        return await acompletion(
            messages=clean_messages,
            model=config.model_name,
            tools=tools,
            response_format=response_format,
            process_chunk=process_chunk,
            model_params=model_params,
        )

    # Default OpenAI routing: prefer /v1/responses for everything, fall back
    # to /v1/chat/completions only if the endpoint doesn't implement it OR the
    # model is rejected as unsupported. Responses-API-only models (codex,
    # *-pro) never fall back — they raise on failure.
    if should_use_responses_api(config):
        import openai as _openai_mod
        from .llm import acompletion_responses

        model_name = config.model_name
        if model_name.startswith("openai/"):
            model_name = model_name.split("/", 1)[1]

        logger.debug(
            f"[CALL_LLM_PROVIDER] Using Responses API for model={model_name}"
        )
        try:
            return await acompletion_responses(
                messages=clean_messages,
                model=model_name,
                tools=tools,
                response_format=response_format,
                process_chunk=process_chunk,
                base_url=config.base_url,
                model_params=model_params,
            )
        except _openai_mod.NotFoundError as e:
            # Endpoint has no /v1/responses (custom proxy, older gateway) or
            # the model isn't recognised on that endpoint. Mark unavailable
            # so we don't probe again, and fall through to Chat Completions
            # unless the model REQUIRES Responses API.
            if is_responses_api_model(config):
                raise
            logger.info(
                f"Responses API unavailable (base_url={config.base_url!r}, "
                f"model={config.model_name!r}): {e}. "
                f"Falling back to Chat Completions."
            )
            mark_responses_api_unavailable(config)
            # Fall through to Chat Completions below

    if config.provider_type == ProviderType.OPENAI:
        # Provider adapters require explicit provider prefixes for models they cannot auto-detect.
        # Ensure OpenAI models include the provider namespace to avoid BadRequestError.
        model_name = config.model_name

        if "/" not in model_name:
            model_name = f"{config.provider_type.value}/{model_name}"

        logger.debug(
            f"[CALL_LLM_PROVIDER] Using OpenAI provider with model={model_name}, base_url={config.base_url}"
        )
        complete_resp = await acompletion(
            messages=clean_messages,
            model=model_name,
            tools=tools,
            response_format=response_format,
            process_chunk=process_chunk,
            base_url=config.base_url,
            api_key=config.api_key,
            model_params=model_params,
        )
        error_prefix = "OpenAI"

    else:  # NATIVE
        logger.debug(
            f"[CALL_LLM_PROVIDER] Using native provider with model={config.model_name}"
        )
        complete_resp = await acompletion(
            messages=clean_messages,
            model=config.model_name,
            tools=tools,
            response_format=response_format,
            process_chunk=process_chunk,
            base_url=config.base_url,
            api_key=config.api_key,
            model_params=model_params,
        )
        error_prefix = "Native"

    # Extract and clean message
    return extract_message_from_response(complete_resp, error_prefix)
