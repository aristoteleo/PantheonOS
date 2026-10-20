"""
Base adapter — ABC for all provider adapters + unified exception types.
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable


# ============ Unified Exception Types ============
# Unified exception types caught in agent.py _is_retryable_error()


class LLMError(Exception):
    """Base exception for LLM provider errors."""
    pass


class ServiceUnavailableError(LLMError):
    """Provider service is temporarily unavailable (503)."""
    pass


class InternalServerError(LLMError):
    """Provider encountered an internal error (500)."""
    pass


class RateLimitError(LLMError):
    """Request was rate-limited (429)."""
    pass


class APIConnectionError(LLMError):
    """Failed to connect to the provider API."""
    pass


# ============ Base Adapter ============


class BaseAdapter(ABC):
    """Abstract base class for LLM provider adapters.

    Each adapter wraps a specific SDK and normalizes responses to
    a common format compatible with the existing codebase.

    Streaming responses yield dicts with OpenAI-compatible chunk format.
    Complete responses are SimpleNamespace objects with .choices and .usage.
    """

    @abstractmethod
    async def acompletion(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: Any | None = None,
        stream: bool = True,
        process_chunk: Callable | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        num_retries: int = 3,
        **kwargs,
    ) -> AsyncIterator:
        """Async chat completion with streaming.

        Args:
            model: Model name (without provider prefix)
            messages: Chat messages in OpenAI format
            tools: Tool definitions in OpenAI format
            response_format: Response format specification
            stream: Whether to stream (always True for now)
            process_chunk: Callback for processing stream chunks
            base_url: Override API base URL
            api_key: Override API key
            num_retries: Number of retries on transient errors
            **kwargs: Additional provider-specific parameters

        Yields:
            Stream chunks (provider-specific format, collected by caller)

        Returns:
            The async iterator of chunks
        """
        ...

    async def aembedding(
        self,
        *,
        model: str,
        input: list[str],
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> list[list[float]]:
        """Generate embeddings.

        Returns:
            List of embedding vectors
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support embeddings"
        )

    async def aimage_generation(
        self,
        *,
        model: str,
        prompt: str,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> Any:
        """Generate images from text prompt.

        Returns:
            Provider-specific image response
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support image generation"
        )

    async def aimage_edit(
        self,
        *,
        model: str,
        image: Any,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> Any:
        """Edit an image.

        Returns:
            Provider-specific image response
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support image editing"
        )

    async def atranscription(
        self,
        *,
        model: str,
        file: Any,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> Any:
        """Transcribe audio to text.

        Returns:
            Transcription response
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support transcription"
        )
