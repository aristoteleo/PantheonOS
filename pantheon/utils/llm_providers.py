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


# ============ Provider Detection ============


def detect_provider(model: str, relaxed_schema: bool) -> ProviderConfig:
    """Detect provider from model string.

    Model format:
    - "gpt-4" → OpenAI provider
    - "provider/model" → Native SDK (handles anthropic, gemini, etc.)
    - "custom_anthropic/model" → OpenAI-compatible with CUSTOM_ANTHROPIC_* env vars
    - "custom_openai/model" → OpenAI-compatible with CUSTOM_OPENAI_* env vars

    Args:
        model: Model identifier string
        relaxed_schema: Use relaxed (non-strict) tool schema mode

    Returns:
        ProviderConfig with detected provider and model name
    """
    from pantheon.utils.model_selector import CUSTOM_ENDPOINT_ENVS

    base_url = None
    api_key = None

    # Check for user-defined custom models (custom/model-name)
    if "/" in model:
        provider_prefix, model_name = model.split("/", 1)
        provider_lower = provider_prefix.lower()

        if provider_lower == "custom":
            from .model_selector import _load_custom_models_config
            custom_models = _load_custom_models_config()
            if model_name in custom_models:
                cfg = custom_models[model_name]
                custom_base = cfg.get("api_base", "")
                custom_key = cfg.get("api_key", "")
                ptype = cfg.get("provider_type", "openai").lower()
                if ptype == "anthropic":
                    resolved = f"anthropic/{model_name}"
                    pt = ProviderType.NATIVE
                else:
                    resolved = f"openai/{model_name}"
                    pt = ProviderType.OPENAI
                logger.debug(f"Using custom model '{model_name}' with base_url={custom_base}")
                return ProviderConfig(
                    provider_type=pt,
                    model_name=resolved,
                    base_url=custom_base or None,
                    api_key=custom_key or None,
                    relaxed_schema=relaxed_schema,
                )

        # Check for custom endpoint prefix (e.g., "custom_anthropic/glm-5")
        if provider_lower in CUSTOM_ENDPOINT_ENVS:
            config = CUSTOM_ENDPOINT_ENVS[provider_lower]
            base_url = os.environ.get(config.api_base_env, "")
            api_key = os.environ.get(config.api_key_env, "")

            # Determine the resolved model format based on endpoint type.
            # A provider prefix is needed to route correctly.
            # Explicitly passed api_key in call_llm_provider overrides env vars.
            if "anthropic" in provider_lower:
                resolved_model = f"anthropic/{model_name}"
            else:
                resolved_model = f"openai/{model_name}"

            logger.debug(f"Using custom endpoint '{provider_lower}' with base_url={base_url}, resolved_model={resolved_model}")
            return ProviderConfig(
                provider_type=ProviderType.OPENAI,
                model_name=resolved_model,
                base_url=base_url or None,
                api_key=api_key or None,
                relaxed_schema=relaxed_schema,
            )

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
    """Check if model should use the OpenAI Responses API instead of Chat Completions.

    Triggers for:
    - Models with "codex" in the name (e.g. codex-mini-latest)
    - Pro models (gpt-5.x-pro, gpt-5.2-pro) which are Responses-only
    """
    name_lower = config.model_name.lower()
    if config.provider_type != ProviderType.OPENAI:
        return False
    # Strip "openai/" prefix for matching
    bare = name_lower.split("/")[-1] if "/" in name_lower else name_lower
    return "codex" in bare or bare.endswith("-pro")


def get_base_url(provider: ProviderType) -> Optional[str]:
    """Get base URL from environment variables or settings.

    Priority:
    1. Custom endpoint: ``CUSTOM_{PROVIDER}_API_BASE`` (e.g. CUSTOM_OPENAI_API_BASE)
    2. Provider-specific: ``{PROVIDER}_API_BASE`` (e.g. OPENAI_API_BASE)
    3. Universal fallback: ``LLM_API_BASE`` (covers all providers, deprecated)

    Args:
        provider: Provider type

    Returns:
        Base URL if set, None otherwise
    """
    import os
    from pantheon.settings import get_settings
    from pantheon.utils.model_selector import CUSTOM_ENDPOINT_ENVS

    settings = get_settings()
    provider_lower = provider.value.lower()

    # 1. Check custom endpoint base URL first
    custom_key = f"custom_{provider_lower}"
    if custom_key in CUSTOM_ENDPOINT_ENVS:
        config = CUSTOM_ENDPOINT_ENVS[custom_key]
        custom_base = os.environ.get(config.api_base_env, "")
        if custom_base:
            return custom_base

    # 2. Provider-specific override
    env_var = f"{provider.value.upper()}_API_BASE"
    value = settings.get_api_key(env_var)
    if value:
        return value

    # 3. Universal fallback (deprecated)
    return settings.get_api_key("LLM_API_BASE")


def get_api_key_for_provider(provider: ProviderType) -> Optional[str]:
    """Get API key from environment variables or settings.

    Priority:
    1. Custom endpoint key: ``CUSTOM_{PROVIDER}_API_KEY`` (if custom endpoint configured)
    2. When LLM_API_BASE is set: ``LLM_API_KEY`` (unified proxy mode)
    3. Provider-specific: ``{PROVIDER}_API_KEY`` (e.g. OPENAI_API_KEY)
    4. Universal fallback: ``LLM_API_KEY``

    Args:
        provider: Provider type

    Returns:
        API key if set, None otherwise
    """
    import os
    from pantheon.settings import get_settings
    from pantheon.utils.model_selector import CUSTOM_ENDPOINT_ENVS

    settings = get_settings()
    provider_lower = provider.value.lower()

    # 1. Check custom endpoint key first
    custom_key = f"custom_{provider_lower}"
    if custom_key in CUSTOM_ENDPOINT_ENVS:
        config = CUSTOM_ENDPOINT_ENVS[custom_key]
        custom_key_value = os.environ.get(config.api_key_env, "")
        if custom_key_value:
            return custom_key_value

    # 2. When LLM_API_BASE is set, LLM_API_KEY takes priority (unified proxy mode)
    if settings.get_api_key("LLM_API_BASE"):
        llm_key = settings.get_api_key("LLM_API_KEY")
        if llm_key:
            return llm_key

    # 3. Provider-specific key
    env_var = f"{provider.value.upper()}_API_KEY"
    value = settings.get_api_key(env_var)
    if value:
        return value

    # 4. Universal fallback
    return settings.get_api_key("LLM_API_KEY")


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


def get_llm_config(provider: ProviderType) -> tuple[str, str]:
    """Return (base_url, api_key) for the given provider.

    Single entry point for all callers that need a base URL and API key.
    Handles proxy mode, provider-specific overrides, and settings.json.

    Args:
        provider: Provider type

    Returns:
        (base_url, api_key) — either may be empty string if not configured
    """
    return get_base_url(provider) or "", get_api_key_for_provider(provider) or ""


def get_llm_proxy_config() -> tuple[str, str]:
    """Return (base_url, api_key) when proxy mode is active, else ('', '').

    Proxy mode is active when LLM_API_BASE is set (env var or settings.json).
    Use this when you need to detect proxy mode and switch SDK behaviour.
    For simply fetching credentials, prefer get_llm_config(provider).
    """
    from pantheon.settings import get_settings
    settings = get_settings()
    base_url = settings.get_api_key("LLM_API_BASE") or ""
    api_key = (settings.get_api_key("LLM_API_KEY") or "") if base_url else ""
    return base_url, api_key


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

    # Route codex/pro models through the OpenAI Responses API
    if is_responses_api_model(config):
        from .llm import acompletion_responses

        model_name = config.model_name
        if model_name.startswith("openai/"):
            model_name = model_name.split("/", 1)[1]

        logger.debug(
            f"[CALL_LLM_PROVIDER] Using Responses API for model={model_name}"
        )
        # acompletion_responses returns a normalised message dict directly
        return await acompletion_responses(
            messages=clean_messages,
            model=model_name,
            tools=tools,
            response_format=response_format,
            process_chunk=process_chunk,
            base_url=config.base_url,
            model_params=model_params,
        )

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
