"""
Vision capability detection for LLM providers.

Determines which providers/models support embedding images directly in
tool result messages (native mode) vs. requiring a sub-agent to view
images and return a text summary (fallback mode).

Native support map (as of 2026-04):
- Anthropic Messages API: tool_result.content can contain image blocks
- Gemini API: functionResponse parts can carry inline_data (image)
- OpenAI Responses API: function_call_output can include input_image items
- OpenAI Chat Completions: NOT supported (tool role cannot carry image_url)
- Other OpenAI-compatible providers via Chat Completions: NOT supported
"""

from __future__ import annotations

from .llm_providers import ProviderType, detect_provider, should_use_responses_api


def supports_tool_result_image(model: str | None) -> bool:
    """Return True when the provider can accept image content in tool messages.

    Args:
        model: Model string (e.g. "anthropic/claude-sonnet-4-6", "gpt-4o",
               "gemini/gemini-2.5-pro"). None → False (conservative).

    Returns:
        True  → tool can return {type:"image_url", ...} and it will reach
                the model natively.
        False → images in tool messages will be stripped. Caller should
                fall back to sub-agent pattern (observe-style).
    """
    if not model:
        return False

    bare = model.lower()
    # Strip provider prefix for matching (e.g. "anthropic/claude" → "claude")
    if "/" in bare:
        prefix, tail = bare.split("/", 1)
    else:
        prefix, tail = "", bare

    # Explicit native SDK providers that support image in tool result.
    if prefix in {"anthropic", "claude"}:
        return True
    if prefix in {"gemini", "google"}:
        return True
    # Codex OAuth (codex/gpt-5.x) routes through the backend-api Responses
    # endpoint which supports input_image in function_call_output.
    if prefix == "codex":
        return True
    # Bare model names without provider prefix.
    if not prefix:
        if tail.startswith("claude") or tail.startswith("gemini"):
            return True

    # OpenAI: by default we route via Responses API, which supports
    # input_image in function_call_output. If a previous call on this
    # (base_url, model) pair proved /v1/responses is unavailable, the cache
    # in should_use_responses_api flips to False and we report no native
    # tool-image support — matching the Chat Completions fallback behaviour.
    try:
        config = detect_provider(model, relaxed_schema=False)
        if config.provider_type == ProviderType.OPENAI:
            return should_use_responses_api(config)
        if config.provider_type == ProviderType.NATIVE:
            bare_model = config.model_name.lower()
            if bare_model.startswith(("anthropic/", "claude", "gemini", "google/", "codex/")):
                return True
            return False
    except Exception:
        # Fall through to False on any detection failure.
        pass

    return False


__all__ = ["supports_tool_result_image"]
