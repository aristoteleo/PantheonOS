"""
LLM Provider Adapters — unified async interface for different SDK types.

Each adapter wraps a specific SDK (openai, anthropic, google-genai) and
exposes a common interface: acompletion, aembedding, aimage_generation, etc.
"""

from functools import lru_cache

from .base import (
    BaseAdapter,
    LLMError,
    ServiceUnavailableError,
    InternalServerError,
    RateLimitError,
    APIConnectionError,
)


@lru_cache(maxsize=8)
def get_adapter(sdk_type: str) -> BaseAdapter:
    """Get an adapter singleton for the given SDK type.

    Args:
        sdk_type: One of 'openai', 'anthropic', 'google-genai'

    Returns:
        BaseAdapter instance
    """
    if sdk_type == "openai":
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter()
    elif sdk_type == "anthropic":
        from .anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter()
    elif sdk_type == "google-genai":
        from .gemini_adapter import GeminiAdapter
        return GeminiAdapter()
    elif sdk_type == "codex":
        from .codex_adapter import CodexAdapter
        return CodexAdapter()
    else:
        # Default to OpenAI adapter for unknown SDK types
        # (many providers are OpenAI-compatible)
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter()


__all__ = [
    "get_adapter",
    "BaseAdapter",
    "LLMError",
    "ServiceUnavailableError",
    "InternalServerError",
    "RateLimitError",
    "APIConnectionError",
]
