"""
Anthropic adapter — handles Claude models via the native Anthropic SDK.

Converts between OpenAI message format (used internally by PantheonOS)
and Anthropic's native format, and normalizes streaming events.
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


def _wrap_anthropic_error(e: Exception) -> Exception:
    """Convert anthropic SDK exceptions to unified exception types."""
    try:
        import anthropic as anthropic_mod

        if isinstance(e, anthropic_mod.RateLimitError):
            return RateLimitError(str(e))
        elif isinstance(e, anthropic_mod.APIConnectionError):
            return APIConnectionError(str(e))
        elif isinstance(e, anthropic_mod.InternalServerError):
            return InternalServerError(str(e))
        elif isinstance(e, anthropic_mod.APIStatusError):
            status = getattr(e, "status_code", 0)
            if status == 503:
                return ServiceUnavailableError(str(e))
            elif status == 429:
                return RateLimitError(str(e))
            elif status >= 500:
                return InternalServerError(str(e))
    except ImportError:
        pass
    return e


# ============ Message Format Conversion ============


def _convert_messages_to_anthropic(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Convert OpenAI-format messages to Anthropic format.

    Key differences:
    - System messages become top-level `system` parameter
    - tool_calls in assistant messages become tool_use content blocks
    - tool role messages become tool_result content blocks in user messages

    Returns:
        (system_prompt, converted_messages)
    """
    system_prompt = None
    converted = []
    pending_tool_results = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            # First system message becomes the system parameter
            if system_prompt is None:
                system_prompt = content if isinstance(content, str) else str(content)
            else:
                # Additional system messages become user messages
                converted.append({
                    "role": "user",
                    "content": f"[System]: {content}"
                })
            continue

        if role == "tool":
            # Accumulate tool results to attach to next user message
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": content or "",
            })
            continue

        if role == "user":
            # Flush pending tool results first
            if pending_tool_results:
                # Tool results must be in a user message
                result_content = list(pending_tool_results)
                if content:
                    if isinstance(content, str):
                        result_content.append({"type": "text", "text": content})
                    elif isinstance(content, list):
                        result_content.extend(content)
                converted.append({"role": "user", "content": result_content})
                pending_tool_results = []
            else:
                converted.append({"role": "user", "content": content})
            continue

        if role == "assistant":
            # Flush any pending tool results as a separate user message
            if pending_tool_results:
                converted.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results = []

            # Build content blocks for assistant
            content_blocks = []

            # Text content
            if content:
                if isinstance(content, str):
                    content_blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    content_blocks.extend(content)

            # Tool calls → tool_use blocks
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    # Parse arguments from JSON string
                    try:
                        input_data = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        input_data = {}

                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": input_data,
                    })

            if content_blocks:
                converted.append({"role": "assistant", "content": content_blocks})
            elif not content and not tool_calls:
                # Empty assistant message — skip
                pass
            continue

    # Flush remaining tool results
    if pending_tool_results:
        converted.append({"role": "user", "content": list(pending_tool_results)})

    # Anthropic requires alternating user/assistant messages
    # Merge consecutive same-role messages
    merged = []
    for msg in converted:
        if merged and merged[-1]["role"] == msg["role"]:
            prev = merged[-1]["content"]
            curr = msg["content"]
            # Normalize both to lists
            if isinstance(prev, str):
                prev = [{"type": "text", "text": prev}]
            elif not isinstance(prev, list):
                prev = [{"type": "text", "text": str(prev)}]
            if isinstance(curr, str):
                curr = [{"type": "text", "text": curr}]
            elif not isinstance(curr, list):
                curr = [{"type": "text", "text": str(curr)}]
            merged[-1]["content"] = prev + curr
        else:
            merged.append(msg)

    return system_prompt, merged


def _convert_tools_to_anthropic(tools: list[dict] | None) -> list[dict] | None:
    """Convert OpenAI tool format to Anthropic tool format.

    From: {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    To:   {"name": ..., "description": ..., "input_schema": {...}}
    """
    if not tools:
        return None

    converted = []
    for tool in tools:
        func = tool.get("function", {})
        anthropic_tool = {
            "name": func.get("name", ""),
        }
        if "description" in func:
            anthropic_tool["description"] = func["description"]
        if "parameters" in func:
            anthropic_tool["input_schema"] = func["parameters"]
        else:
            anthropic_tool["input_schema"] = {"type": "object", "properties": {}}
        converted.append(anthropic_tool)

    return converted


# ============ Adapter ============


class AnthropicAdapter(BaseAdapter):
    """Adapter for Anthropic Claude API."""

    def _make_client(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        from anthropic import AsyncAnthropic

        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        return AsyncAnthropic(**kwargs)

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
        """Streaming chat completion using the Anthropic SDK.

        Converts OpenAI messages to Anthropic format, streams events,
        normalizes them to OpenAI-compatible chunk dicts, and returns
        collected chunks.
        """
        client = self._make_client(base_url, api_key)

        # Convert messages and tools
        system_prompt, anthropic_messages = _convert_messages_to_anthropic(messages)
        anthropic_tools = _convert_tools_to_anthropic(tools)

        # Build call kwargs (stream() method implies streaming, don't pass stream=True)
        call_kwargs = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.pop("max_tokens", None) or kwargs.pop("max_output_tokens", 8192),
        }

        if system_prompt:
            call_kwargs["system"] = system_prompt

        if anthropic_tools:
            call_kwargs["tools"] = anthropic_tools

        # Handle thinking parameter
        thinking = kwargs.pop("thinking", None)
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        if thinking:
            call_kwargs["thinking"] = thinking
        elif reasoning_effort:
            # Map reasoning_effort to Anthropic thinking
            call_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}

        # Temperature
        temperature = kwargs.pop("temperature", None)
        if temperature is not None:
            call_kwargs["temperature"] = temperature

        # Top-p
        top_p = kwargs.pop("top_p", None)
        if top_p is not None:
            call_kwargs["top_p"] = top_p

        # Extra headers
        extra_headers = kwargs.pop("extra_headers", None)

        try:
            stream_start_time = time.time()
            first_chunk_time = None
            chunk_count = 0
            collected_chunks = []

            # Track state for building OpenAI-compatible chunks
            current_text = ""
            current_tool_calls = []
            tool_call_index = -1
            tool_call_json_accum = ""
            usage_info = {}

            async with client.messages.stream(
                **call_kwargs,
                extra_headers=extra_headers or {},
            ) as stream_resp:
                async for event in stream_resp:
                    event_type = event.type

                    if event_type == "message_start":
                        # Extract initial usage
                        msg = getattr(event, "message", None)
                        if msg and hasattr(msg, "usage"):
                            usage_info["prompt_tokens"] = getattr(msg.usage, "input_tokens", 0)

                    elif event_type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_call_index += 1
                            tool_call_json_accum = ""
                            current_tool_calls.append({
                                "index": tool_call_index,
                                "id": block.id,
                                "type": "function",
                                "function": {
                                    "name": block.name,
                                    "arguments": "",
                                },
                            })
                            # Emit initial chunk with id and name
                            chunk_dict = {
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "tool_calls": [{
                                            "index": tool_call_index,
                                            "id": block.id,
                                            "type": "function",
                                            "function": {
                                                "name": block.name,
                                                "arguments": "",
                                            },
                                        }],
                                    },
                                    "finish_reason": None,
                                }],
                            }
                            collected_chunks.append(chunk_dict)

                    elif event_type == "content_block_delta":
                        delta_obj = event.delta

                        if delta_obj.type == "text_delta":
                            text = delta_obj.text
                            current_text += text

                            if first_chunk_time is None:
                                first_chunk_time = time.time()
                                ttfb = first_chunk_time - stream_start_time
                                logger.info(f"⚡ First chunk received: {ttfb:.3f}s (TTFB) [{model}]")

                            # Build OpenAI-compatible chunk
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

                        elif delta_obj.type == "input_json_delta":
                            # Accumulate tool call arguments
                            partial = delta_obj.partial_json
                            tool_call_json_accum += partial
                            if current_tool_calls:
                                current_tool_calls[-1]["function"]["arguments"] += partial

                            chunk_dict = {
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "tool_calls": [{
                                            "index": tool_call_index,
                                            "function": {
                                                "arguments": partial,
                                            },
                                        }],
                                    },
                                    "finish_reason": None,
                                }],
                            }
                            collected_chunks.append(chunk_dict)

                        elif delta_obj.type == "thinking_delta":
                            thinking_text = delta_obj.thinking

                            # Write into chunks so stream_chunk_builder captures it
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

                    elif event_type == "message_delta":
                        delta = event.delta
                        stop_reason = getattr(delta, "stop_reason", None)

                        # Extract usage from message_delta
                        usage = getattr(event, "usage", None)
                        if usage:
                            usage_info["completion_tokens"] = getattr(usage, "output_tokens", 0)

                        # Map Anthropic stop reasons to OpenAI finish reasons
                        finish_reason = None
                        if stop_reason == "end_turn":
                            finish_reason = "stop"
                        elif stop_reason == "tool_use":
                            finish_reason = "tool_calls"
                        elif stop_reason == "max_tokens":
                            finish_reason = "length"

                        if finish_reason:
                            chunk_dict = {
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": finish_reason,
                                }],
                            }
                            collected_chunks.append(chunk_dict)

                            if process_chunk and finish_reason == "stop":
                                await run_func(process_chunk, {"stop": True})

                    elif event_type == "message_stop":
                        pass

            # Add usage chunk at the end (OpenAI stream_options style)
            total_tokens = usage_info.get("prompt_tokens", 0) + usage_info.get("completion_tokens", 0)
            usage_info["total_tokens"] = total_tokens
            collected_chunks.append({
                "usage": usage_info,
                "choices": [],
            })

            total_time = time.time() - stream_start_time
            logger.info(f"✅ Stream completed: {total_time:.3f}s, {chunk_count} chunks [{model}]")

            return collected_chunks

        except Exception as e:
            raise _wrap_anthropic_error(e) from e
