"""
Gemini adapter — handles Google Gemini models via the google-genai SDK.

Converts between OpenAI message format and Gemini's native format,
and normalizes streaming events to OpenAI-compatible chunk dicts.
"""

import json
import time
from typing import Any, Callable

from ..log import logger
from ..misc import run_func
from .base import (
    BaseAdapter,
    ServiceUnavailableError,
    InternalServerError,
    RateLimitError,
    APIConnectionError,
)


def _wrap_gemini_error(e: Exception) -> Exception:
    """Convert Gemini SDK exceptions to unified exception types."""
    error_str = str(e).lower()
    if "429" in error_str or "resource exhausted" in error_str or "rate" in error_str:
        return RateLimitError(str(e))
    elif "503" in error_str or "unavailable" in error_str:
        return ServiceUnavailableError(str(e))
    elif "500" in error_str or "internal" in error_str:
        return InternalServerError(str(e))
    elif "connect" in error_str or "timeout" in error_str:
        return APIConnectionError(str(e))
    return e


# ============ Message Format Conversion ============


def _convert_messages_to_gemini(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Convert OpenAI-format messages to Gemini format.

    Returns:
        (system_instruction, gemini_contents)
    """
    system_instruction = None
    contents = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if system_instruction is None:
                system_instruction = content if isinstance(content, str) else str(content)
            else:
                # Additional system messages as user context
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"[System]: {content}"}],
                })
            continue

        if role == "user":
            parts = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            parts.append({"text": item["text"]})
                        elif item.get("type") == "image_url":
                            # Pass image URLs through
                            url = item.get("image_url", {}).get("url", "")
                            if url.startswith("data:"):
                                # Base64 inline data
                                parts.append({"inline_data": {"mime_type": "image/png", "data": url.split(",", 1)[-1]}})
                            else:
                                parts.append({"text": f"[Image: {url}]"})
            contents.append({"role": "user", "parts": parts})
            continue

        if role == "assistant":
            parts = []
            if content:
                if isinstance(content, str):
                    parts.append({"text": content})

            # Tool calls → function_call parts
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    parts.append({
                        "function_call": {
                            "name": func.get("name", ""),
                            "args": args,
                        }
                    })

            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if role == "tool":
            # Tool results → function_response parts
            tool_call_id = msg.get("tool_call_id", "")
            # Try to find tool name from previous assistant message
            tool_name = msg.get("name", tool_call_id)
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                result = {"result": content}

            contents.append({
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": tool_name,
                        "response": result if isinstance(result, dict) else {"result": str(result)},
                    }
                }],
            })
            continue

    return system_instruction, contents


def _convert_tools_to_gemini(tools: list[dict] | None) -> list[dict] | None:
    """Convert OpenAI tool format to Gemini function declarations.

    From: {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    To:   {"name": ..., "description": ..., "parameters": {...}}
    """
    if not tools:
        return None

    declarations = []
    for tool in tools:
        func = tool.get("function", {})
        decl = {"name": func.get("name", "")}
        if "description" in func:
            decl["description"] = func["description"]
        if "parameters" in func:
            params = dict(func["parameters"])
            # Gemini doesn't support 'strict' or 'additionalProperties' at top level
            params.pop("strict", None)
            params.pop("additionalProperties", None)
            decl["parameters"] = params
        declarations.append(decl)

    return declarations


# ============ Adapter ============


class GeminiAdapter(BaseAdapter):
    """Adapter for Google Gemini API via google-genai SDK."""

    def _make_client(self, api_key: str | None = None):
        """Create a google-genai client."""
        from google import genai

        import os
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        return genai.Client(api_key=key)

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
    ):
        """Streaming chat completion using the Google GenAI SDK.

        Returns collected chunks in OpenAI-compatible format.
        """
        from google.genai import types

        client = self._make_client(api_key)

        # Convert messages and tools
        system_instruction, gemini_contents = _convert_messages_to_gemini(messages)
        gemini_tools = _convert_tools_to_gemini(tools)

        # Build config
        config_kwargs = {}

        # System instruction
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        # Tools
        if gemini_tools:
            config_kwargs["tools"] = [types.Tool(function_declarations=gemini_tools)]

        # Temperature
        temperature = kwargs.pop("temperature", None)
        if temperature is not None:
            config_kwargs["temperature"] = temperature

        # Max output tokens
        max_tokens = kwargs.pop("max_tokens", None) or kwargs.pop("max_output_tokens", None)
        if max_tokens:
            config_kwargs["max_output_tokens"] = max_tokens

        # Response modalities (for multimodal image generation)
        modalities = kwargs.pop("modalities", None)
        if modalities:
            config_kwargs["response_modalities"] = modalities

        # Reasoning / thinking config
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        thinking = kwargs.pop("thinking", None)
        if thinking and isinstance(thinking, dict):
            budget = thinking.get("budget_tokens", -1)
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=budget,
                include_thoughts=True,
            )
        elif reasoning_effort:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=-1,  # auto
                include_thoughts=True,
            )

        config = types.GenerateContentConfig(**config_kwargs)

        try:
            stream_start_time = time.time()
            first_chunk_time = None
            chunk_count = 0
            collected_chunks = []
            full_text = ""
            prompt_tokens = 0
            completion_tokens = 0

            stream_iter = await client.aio.models.generate_content_stream(
                model=model,
                contents=gemini_contents,
                config=config,
            )
            async for response in stream_iter:
                # Extract text from response candidates
                text = ""
                tool_calls_data = []

                thinking_text = ""

                if response.candidates:
                    for candidate in response.candidates:
                        if candidate.content and candidate.content.parts:
                            for part in candidate.content.parts:
                                if getattr(part, "thought", False) and part.text:
                                    # Thinking/reasoning part
                                    thinking_text += part.text
                                elif hasattr(part, "text") and part.text:
                                    text += part.text
                                elif hasattr(part, "function_call") and part.function_call:
                                    fc = part.function_call
                                    tool_calls_data.append({
                                        "index": len(tool_calls_data),
                                        "id": f"call_{fc.name}_{len(tool_calls_data)}",
                                        "type": "function",
                                        "function": {
                                            "name": fc.name,
                                            "arguments": json.dumps(dict(fc.args)) if fc.args else "{}",
                                        },
                                    })

                if text:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        ttfb = first_chunk_time - stream_start_time
                        logger.info(f"⚡ First chunk received: {ttfb:.3f}s (TTFB) [{model}]")

                    full_text += text

                    chunk_dict = {
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": text,
                            },
                            "finish_reason": None,
                        }],
                    }
                    collected_chunks.append(chunk_dict)

                    if process_chunk:
                        chunk_count += 1
                        await run_func(process_chunk, {
                            "role": "assistant",
                            "content": text,
                        })

                if thinking_text:
                    chunk_dict = {
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "reasoning_content": thinking_text,
                            },
                            "finish_reason": None,
                        }],
                    }
                    collected_chunks.append(chunk_dict)

                    if process_chunk:
                        await run_func(process_chunk, {
                            "role": "assistant",
                            "reasoning_content": thinking_text,
                        })

                if tool_calls_data:
                    chunk_dict = {
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": tool_calls_data,
                            },
                            "finish_reason": None,
                        }],
                    }
                    collected_chunks.append(chunk_dict)

                # Extract usage if available
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    um = response.usage_metadata
                    prompt_tokens = getattr(um, "prompt_token_count", 0) or 0
                    completion_tokens = getattr(um, "candidates_token_count", 0) or 0

            # Add finish chunk
            collected_chunks.append({
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
            })

            if process_chunk:
                await run_func(process_chunk, {"stop": True})

            # Add usage chunk
            total_tokens = prompt_tokens + completion_tokens
            collected_chunks.append({
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
                "choices": [],
            })

            total_time = time.time() - stream_start_time
            logger.info(f"✅ Stream completed: {total_time:.3f}s, {chunk_count} chunks [{model}]")

            return collected_chunks

        except Exception as e:
            raise _wrap_gemini_error(e) from e

    async def aembedding(
        self,
        *,
        model: str,
        input: list[str],
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> list[list[float]]:
        """Generate embeddings using Gemini API."""
        client = self._make_client(api_key)
        try:
            results = []
            for text in input:
                response = await client.aio.models.embed_content(
                    model=model,
                    contents=text,
                )
                results.append(response.embedding)
            return results
        except Exception as e:
            raise _wrap_gemini_error(e) from e
