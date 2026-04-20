"""
Gemini adapter — handles Google Gemini models via the REST API.

Uses the Gemini REST API (generativelanguage.googleapis.com) directly
instead of the google-genai SDK, following the approach used by litellm.
This avoids SDK-specific issues with tool call parameter serialization.

Converts between OpenAI message format and Gemini's native format,
and normalizes streaming events to OpenAI-compatible chunk dicts.
"""

import json
import time
import uuid
from typing import Any, Callable

import httpx

from ..log import logger
from ..misc import run_func
from .base import (
    BaseAdapter,
    ServiceUnavailableError,
    InternalServerError,
    RateLimitError,
    APIConnectionError,
)
from .image_blocks import has_image_content, split_text_and_images


def _wrap_gemini_error(e: Exception) -> Exception:
    """Convert Gemini API exceptions to unified exception types."""
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


# ============ API URL ============

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com"

# Models gemini-3 and newer use v1alpha; older models use v1beta
_V1ALPHA_PREFIXES = ("gemini-3", "gemini-4")


def _api_version(model: str) -> str:
    """Select API version based on model name."""
    bare = model.split("/")[-1] if "/" in model else model
    for prefix in _V1ALPHA_PREFIXES:
        if bare.startswith(prefix):
            return "v1alpha"
    return "v1beta"


def _get_gemini_api_base(base_url: str | None = None) -> str:
    """Resolve the Gemini API base URL from call args, settings, or default."""
    if base_url:
        return base_url.rstrip("/")

    try:
        from pantheon.settings import get_settings

        configured = get_settings().get_api_key("GEMINI_API_BASE")
        if configured:
            return configured.rstrip("/")
    except Exception:
        pass

    return _GEMINI_API_BASE


def _build_url(
    model: str, api_key: str, *, stream: bool = True, base_url: str | None = None
) -> str:
    """Build the Gemini REST API URL."""
    bare = model.split("/")[-1] if "/" in model else model
    version = _api_version(model)
    endpoint = "streamGenerateContent" if stream else "generateContent"
    url = f"{_get_gemini_api_base(base_url)}/{version}/models/{bare}:{endpoint}?key={api_key}"
    if stream:
        url += "&alt=sse"
    return url


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
                    fc_part: dict[str, Any] = {
                        "functionCall": {
                            "name": func.get("name", ""),
                            "args": args,
                        }
                    }
                    # Replay thoughtSignature for Gemini 3 thinking models
                    thought_sig = tc.get("thought_signature")
                    if thought_sig:
                        fc_part["thoughtSignature"] = thought_sig
                    parts.append(fc_part)

            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if role == "tool":
            # Tool results → function_response parts.
            # When the tool returns images (OpenAI-style image_url blocks),
            # emit a functionResponse carrying the text summary PLUS additional
            # inline_data parts in the same user message so Gemini can see
            # both the structured result and the pixels.
            tool_call_id = msg.get("tool_call_id", "")
            tool_name = msg.get("name", tool_call_id)

            if has_image_content(content):
                text, inline_images, http_urls = split_text_and_images(content)
                parts: list[dict[str, Any]] = [
                    {
                        "functionResponse": {
                            "name": tool_name,
                            "response": {"result": text or "(see attached image)"},
                        }
                    }
                ]
                for mime, data in inline_images:
                    parts.append({"inline_data": {"mime_type": mime, "data": data}})
                for url in http_urls:
                    # Gemini has no direct URL image type in functionResponse;
                    # fall back to a text note so the model knows about the URL.
                    parts.append({"text": f"[Referenced image URL: {url}]"})
                contents.append({"role": "user", "parts": parts})
                continue

            try:
                result = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                result = {"result": content}

            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
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
            params = _sanitize_schema_for_gemini(dict(func["parameters"]))
            decl["parameters"] = params
        declarations.append(decl)

    return declarations


def _sanitize_schema_for_gemini(value: Any) -> Any:
    """Recursively drop OpenAI-specific JSON-schema fields Gemini rejects."""
    if isinstance(value, list):
        return [_sanitize_schema_for_gemini(item) for item in value]

    if not isinstance(value, dict):
        return value

    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"strict", "additionalProperties", "additional_properties"}:
            continue
        sanitized[key] = _sanitize_schema_for_gemini(item)
    return sanitized


# ============ SSE Streaming Parser ============


def _parse_sse_line(line: str) -> dict | None:
    """Parse a single SSE data line into a JSON dict."""
    line = line.strip()
    if not line or not line.startswith("data: "):
        return None
    payload = line[6:]  # strip "data: "
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


# ============ Adapter ============


class GeminiAdapter(BaseAdapter):
    """Adapter for Google Gemini API via REST API (httpx)."""

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
        """Streaming chat completion using the Gemini REST API.

        Returns collected chunks in OpenAI-compatible format.
        """
        import os
        kwargs.pop("oauth_client_kwargs", None)
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ValueError("GEMINI_API_KEY is required")

        # Convert messages and tools
        system_instruction, gemini_contents = _convert_messages_to_gemini(messages)
        gemini_tools = _convert_tools_to_gemini(tools)

        # Build request body
        request_body: dict[str, Any] = {
            "contents": gemini_contents,
        }

        if system_instruction:
            request_body["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        if gemini_tools:
            request_body["tools"] = [{"functionDeclarations": gemini_tools}]

        # Generation config
        gen_config: dict[str, Any] = {}
        temperature = kwargs.pop("temperature", None)
        if temperature is not None:
            gen_config["temperature"] = temperature

        max_tokens = kwargs.pop("max_tokens", None) or kwargs.pop("max_output_tokens", None)
        if max_tokens:
            gen_config["maxOutputTokens"] = max_tokens

        # Reasoning / thinking config
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        thinking = kwargs.pop("thinking", None)
        if thinking and isinstance(thinking, dict):
            budget = thinking.get("budget_tokens", -1)
            gen_config["thinkingConfig"] = {
                "thinkingBudget": budget,
                "includeThoughts": True,
            }
        elif reasoning_effort:
            gen_config["thinkingConfig"] = {
                "thinkingBudget": -1,
                "includeThoughts": True,
            }

        if gen_config:
            request_body["generationConfig"] = gen_config

        # response_format → responseMimeType for JSON mode
        if response_format and isinstance(response_format, dict):
            if response_format.get("type") == "json_object":
                request_body.setdefault("generationConfig", {})["responseMimeType"] = "application/json"

        # Response modalities
        modalities = kwargs.pop("modalities", None)
        if modalities:
            request_body.setdefault("generationConfig", {})["responseModalities"] = modalities

        url = _build_url(model, key, stream=True, base_url=base_url)

        try:
            stream_start_time = time.time()
            first_chunk_time = None
            chunk_count = 0
            collected_chunks = []
            full_text = ""
            prompt_tokens = 0
            completion_tokens = 0
            tool_call_idx = 0

            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=request_body,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        error_text = body.decode("utf-8", errors="replace")
                        raise InternalServerError(
                            f"Gemini API error {response.status_code}: {error_text[:500]}"
                        )

                    async for line in response.aiter_lines():
                        data = _parse_sse_line(line)
                        if data is None:
                            continue

                        # Extract parts from candidates
                        text = ""
                        thinking_text = ""
                        tool_calls_data = []
                        inline_images: list[dict] = []

                        for candidate in data.get("candidates", []):
                            # Log non-normal finish reasons (SAFETY, RECITATION,
                            # IMAGE_SAFETY, OTHER) so empty responses from blocked
                            # generation don't look like silent adapter failures.
                            fr = candidate.get("finishReason")
                            if fr and fr not in ("STOP", "MAX_TOKENS", None):
                                logger.warning(
                                    f"[gemini] candidate finishReason={fr!r} model={model} — "
                                    f"response may be truncated or blocked"
                                )
                            for part in candidate.get("content", {}).get("parts", []):
                                if part.get("thought") and part.get("text"):
                                    thinking_text += part["text"]
                                elif "text" in part and part["text"]:
                                    text += part["text"]
                                elif "inlineData" in part or "inline_data" in part:
                                    # Image (or other binary) output — common for
                                    # image-generation models (gemini-*-image-*).
                                    # Wrap as a data URI so downstream code sees
                                    # the same shape as OpenAI-style image output.
                                    inline = part.get("inlineData") or part.get("inline_data") or {}
                                    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                                    b64 = inline.get("data", "")
                                    if b64:
                                        inline_images.append({
                                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                                        })
                                elif "functionCall" in part:
                                    fc = part["functionCall"]
                                    tc_data = {
                                        "index": tool_call_idx,
                                        "id": f"call_{uuid.uuid4().hex[:24]}",
                                        "type": "function",
                                        "function": {
                                            "name": fc.get("name", ""),
                                            "arguments": json.dumps(
                                                fc.get("args", {}), ensure_ascii=False
                                            ),
                                        },
                                    }
                                    # Preserve thoughtSignature for Gemini 3 thinking models
                                    if "thoughtSignature" in part:
                                        tc_data["thought_signature"] = part["thoughtSignature"]
                                    tool_calls_data.append(tc_data)
                                    tool_call_idx += 1

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

                        if inline_images:
                            collected_chunks.append({
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "images": inline_images,
                                    },
                                    "finish_reason": None,
                                }],
                            })

                        # Extract usage if available
                        usage = data.get("usageMetadata", {})
                        if usage:
                            prompt_tokens = usage.get("promptTokenCount", 0) or 0
                            completion_tokens = usage.get("candidatesTokenCount", 0) or 0

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

        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
            raise _wrap_gemini_error(e) from e
        except Exception as e:
            if isinstance(e, (RateLimitError, ServiceUnavailableError, InternalServerError, APIConnectionError)):
                raise
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
        """Generate embeddings using Gemini REST API."""
        import os
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ValueError("GEMINI_API_KEY is required")

        bare = model.split("/")[-1] if "/" in model else model
        base = _get_gemini_api_base(base_url)
        url = f"{base}/v1beta/models/{bare}:embedContent?key={key}"

        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            for text in input:
                resp = await client.post(url, json={
                    "content": {"parts": [{"text": text}]},
                })
                if resp.status_code != 200:
                    raise InternalServerError(f"Gemini embedding error: {resp.text[:300]}")
                data = resp.json()
                embedding = data.get("embedding", {}).get("values", [])
                results.append(embedding)
        return results
