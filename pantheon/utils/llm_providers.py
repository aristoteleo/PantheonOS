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
    """Supported LLM providers"""
    OPENAI = "openai"
    LITELLM = "litellm"


@dataclass
class ProviderConfig:
    """Provider configuration"""
    provider_type: ProviderType
    model_name: str
    base_url: Optional[str] = None
    force_litellm: bool = False


# ============ Provider Detection ============

def detect_provider(model: str, force_litellm: bool) -> ProviderConfig:
    """Detect provider from model string.

    Model format:
    - "gpt-4" → OpenAI (via LiteLLM)
    - "provider/model" → LiteLLM (handles zhipu, anthropic, etc. natively)

    Args:
        model: Model identifier string
        force_litellm: Force using LiteLLM backend

    Returns:
        ProviderConfig with detected provider and model name
    """
    if "/" in model:
        provider_str, model_name = model.split("/", 1)
        # Check if it's explicitly openai provider
        if provider_str.lower() == "openai":
            provider_type = ProviderType.OPENAI
        else:
            # All other prefixed models go through LiteLLM (zhipu, anthropic, etc.)
            provider_type = ProviderType.LITELLM
            model_name = model  # Keep full model string for LiteLLM
    else:
        provider_type = ProviderType.OPENAI
        model_name = model

    # Override with LiteLLM if forced
    if force_litellm and provider_type != ProviderType.LITELLM:
        provider_type = ProviderType.LITELLM

    return ProviderConfig(
        provider_type=provider_type,
        model_name=model_name,
        force_litellm=force_litellm
    )


def get_base_url(provider: ProviderType) -> Optional[str]:
    """Get base URL from environment variables or settings.

    Args:
        provider: Provider type

    Returns:
        Base URL if set, None otherwise
    """
    from pantheon.settings import get_settings
    env_var = f"{provider.value.upper()}_API_BASE"
    return get_settings().get_api_key(env_var)


# ============ Response Extraction ============

def _create_error_message(content: str) -> dict:
    """Create standardized error message.

    Args:
        content: Error description

    Returns:
        Error message dictionary
    """
    return {
        "role": "assistant",
        "content": f"Error: {content}"
    }


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
    """Calculate cost and extract usage from response."""
    try:
        from litellm import completion_cost
        cost = completion_cost(completion_response=complete_resp)
        usage = getattr(complete_resp, "usage", None)
        
        usage_dict = {}
        if usage:
            if hasattr(usage, "model_dump"):
                usage_dict = usage.model_dump()
            elif hasattr(usage, "to_dict"):
                usage_dict = usage.to_dict()
            else:
                try:
                    usage_dict = dict(usage)
                except Exception:
                    pass
                    
        return cost or 0.0, usage_dict
    except Exception as e:
        logger.warning(f"Failed to calculate completion cost: {e}")
        return 0.0, {}



def extract_message_from_response(
    complete_resp: Any,
    error_prefix: str = "API"
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
) -> Callable | None:
    """Create enhanced chunk processor with metadata.

    Injects into each chunk:
    - message_id: For correlating chunks with messages
    - chunk_index: Sequential index of chunks
    - timestamp: When the chunk was processed

    Args:
        base_process_chunk: Original chunk processor (can be None)
        message_id: Message identifier for this completion

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

    Returns:
        Extracted and cleaned message dictionary
    """
    # Import here to avoid circular imports
    from .llm import (
        acompletion_litellm,
        remove_metadata,
    )

    # Remove metadata before sending to LLM
    clean_messages = [m.copy() for m in messages]
    clean_messages = remove_metadata(clean_messages)

    # Call appropriate provider
    if config.provider_type == ProviderType.OPENAI:
        # LiteLLM requires explicit provider prefixes for models it cannot auto-detect.
        # Ensure OpenAI models include the provider namespace to avoid BadRequestError.
        model_name = config.model_name
        if "/" not in model_name:
            model_name = f"{config.provider_type.value}/{model_name}"
        complete_resp = await acompletion_litellm(
            messages=clean_messages,
            model=model_name,
            tools=tools,
            response_format=response_format,
            process_chunk=process_chunk,
            base_url=config.base_url,
            model_params=model_params,
        )
        error_prefix = "OpenAI"

    else:  # LITELLM
        complete_resp = await acompletion_litellm(
            messages=clean_messages,
            model=config.model_name,
            tools=tools,
            response_format=response_format,
            process_chunk=process_chunk,
            base_url=config.base_url,
            model_params=model_params,
        )
        error_prefix = "LiteLLM"

    # Extract and clean message
    return extract_message_from_response(complete_resp, error_prefix)
