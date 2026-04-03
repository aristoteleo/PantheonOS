"""
OpenAI adapter — handles OpenAI and all OpenAI-compatible providers.

Covers: openai, deepseek, moonshot, minimax, zai (zhipu), and any
provider with openai_compatible=true in the catalog.
"""

import os
import time
from typing import Any, Callable

from openai import NOT_GIVEN, AsyncOpenAI

from ..log import logger
from ..misc import run_func
from .base import (
    BaseAdapter,
    ServiceUnavailableError,
    InternalServerError,
    RateLimitError,
    APIConnectionError,
)


def _wrap_openai_error(e: Exception) -> Exception:
    """Convert openai SDK exceptions to unified exception types."""
    import openai as openai_mod

    if isinstance(e, openai_mod.RateLimitError):
        return RateLimitError(str(e))
    elif isinstance(e, openai_mod.APIConnectionError):
        return APIConnectionError(str(e))
    elif isinstance(e, openai_mod.InternalServerError):
        return InternalServerError(str(e))
    elif isinstance(e, openai_mod.APIStatusError):
        status = getattr(e, "status_code", 0)
        if status == 503:
            return ServiceUnavailableError(str(e))
        elif status == 429:
            return RateLimitError(str(e))
        elif status >= 500:
            return InternalServerError(str(e))
    return e


class OpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI and OpenAI-compatible APIs."""

    def _make_client(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> AsyncOpenAI:
        """Create an AsyncOpenAI client with optional overrides."""
        kwargs = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        return AsyncOpenAI(**kwargs)

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
        """Streaming chat completion using the OpenAI SDK.

        Returns an async iterator that yields raw chunk dicts.
        The caller is responsible for assembling chunks (via stream_chunk_builder).
        """
        client = self._make_client(base_url, api_key)

        _tools = tools or NOT_GIVEN
        _pcall = (tools is not None) or NOT_GIVEN

        # Build call kwargs
        call_kwargs = {
            "model": model,
            "messages": messages,
            "tools": _tools,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if response_format:
            call_kwargs["response_format"] = response_format

        # reasoning models (o1, o3, o4 series) don't support parallel_tool_calls
        if not model.startswith("o"):
            call_kwargs["parallel_tool_calls"] = _pcall

        # Merge extra kwargs (reasoning_effort, temperature, etc.)
        call_kwargs.update(kwargs)

        retry_count = num_retries
        while retry_count > 0:
            try:
                stream_start_time = time.time()
                first_chunk_time = None
                chunk_count = 0

                response = await client.chat.completions.create(**call_kwargs)

                collected_chunks = []
                try:
                    async for chunk in response:
                        chunk_dict = chunk.model_dump()
                        collected_chunks.append(chunk_dict)

                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                            ttfb = first_chunk_time - stream_start_time
                            logger.info(f"⚡ First chunk received: {ttfb:.3f}s (TTFB) [{model}]")

                        if (
                            process_chunk
                            and chunk.choices
                            and len(chunk.choices) > 0
                        ):
                            choice = chunk.choices[0]
                            if hasattr(choice, "delta") and choice.delta:
                                delta = choice.delta.model_dump()
                                chunk_count += 1
                                await run_func(process_chunk, delta)
                            if hasattr(choice, "finish_reason") and choice.finish_reason == "stop":
                                await run_func(process_chunk, {"stop": True})
                except Exception as stream_err:
                    # Some providers (e.g. Groq) validate tool calls server-side
                    # and abort the stream mid-way with errors like:
                    # - "tool call validation failed: attempted to call tool X not in request.tools"
                    # - "Failed to parse tool call arguments as JSON"
                    # If we already collected text chunks, return them as a partial response
                    # instead of crashing the entire request.
                    err_str = str(stream_err).lower()
                    is_tool_error = "tool call" in err_str or "tool_call" in err_str
                    if is_tool_error and collected_chunks:
                        logger.warning(
                            f"⚠ Stream interrupted by tool call error, "
                            f"returning {len(collected_chunks)} partial chunks [{model}]: {stream_err}"
                        )
                        # Strip tool_call deltas from partial chunks — they are incomplete
                        # and will cause downstream errors. Only keep text content.
                        cleaned_chunks = []
                        for c in collected_chunks:
                            choices = c.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                # Remove tool_calls from delta, keep only text content
                                delta.pop("tool_calls", None)
                            cleaned_chunks.append(c)
                        # Add a stop chunk
                        cleaned_chunks.append({
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        })
                        collected_chunks = cleaned_chunks
                        if process_chunk:
                            await run_func(process_chunk, {"stop": True})
                    else:
                        raise

                total_time = time.time() - stream_start_time
                logger.info(f"✅ Stream completed: {total_time:.3f}s, {chunk_count} chunks [{model}]")
                return collected_chunks

            except Exception as e:
                wrapped = _wrap_openai_error(e)
                if isinstance(wrapped, APIConnectionError):
                    retry_count -= 1
                    logger.warning(f"Connection error, retrying ({num_retries - retry_count}/{num_retries}): {e}")
                    if retry_count <= 0:
                        raise wrapped from e
                else:
                    raise wrapped from e

        # Should not reach here, but just in case
        raise APIConnectionError(f"Failed after {num_retries} retries")

    async def acompletion_responses(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: Any | None = None,
        process_chunk: Callable | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> dict:
        """Call OpenAI Responses API with streaming.

        Used for models that require the Responses API (gpt-5.x-pro, codex, etc.).
        Returns a normalized message dict (not chunks).
        """
        client = self._make_client(base_url, api_key)

        # Convert messages to Responses API format
        instructions = None
        input_items = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                if instructions is None:
                    instructions = content
                else:
                    input_items.append({"role": "developer", "content": content})
            elif role == "user":
                input_items.append({"role": "user", "content": content})
            elif role == "assistant":
                if content:
                    input_items.append({"role": "assistant", "content": content})
                for tc in msg.get("tool_calls") or []:
                    func = tc.get("function", {})
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc["id"],
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                    })
            elif role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": content or "",
                })

        # Convert tools
        converted_tools = None
        if tools:
            converted_tools = []
            for tool in tools:
                func = tool.get("function", {})
                item = {"type": "function", "name": func.get("name", "")}
                if "description" in func:
                    item["description"] = func["description"]
                if "parameters" in func:
                    item["parameters"] = func["parameters"]
                if "strict" in func:
                    item["strict"] = func["strict"]
                converted_tools.append(item)

        # Build kwargs
        call_kwargs = {"model": model, "input": input_items, "stream": True}
        if instructions is not None:
            call_kwargs["instructions"] = instructions
        if converted_tools is not None:
            call_kwargs["tools"] = converted_tools
        if response_format is not None:
            call_kwargs["text"] = response_format

        # Map model_params
        if kwargs.get("max_tokens"):
            call_kwargs["max_output_tokens"] = kwargs.pop("max_tokens")
        if kwargs.get("max_completion_tokens"):
            call_kwargs["max_output_tokens"] = kwargs.pop("max_completion_tokens")
        if kwargs.get("max_output_tokens"):
            call_kwargs["max_output_tokens"] = kwargs.pop("max_output_tokens")
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        if reasoning_effort:
            call_kwargs["reasoning"] = {"effort": reasoning_effort}

        # Stream
        text_parts = []
        tool_calls_by_id = {}
        _item_to_call = {}
        response_obj = None

        try:
            stream = await client.responses.create(**call_kwargs)
            async for event in stream:
                event_type = event.type

                if event_type == "response.output_text.delta":
                    text_parts.append(event.delta)
                    if process_chunk:
                        await run_func(process_chunk, {"content": event.delta, "role": "assistant"})

                elif event_type == "response.output_item.added":
                    item = event.item
                    if getattr(item, "type", None) == "function_call":
                        call_id = getattr(item, "call_id", "") or ""
                        item_id = getattr(item, "id", "") or ""
                        _item_to_call[item_id] = call_id
                        tool_calls_by_id[call_id] = {
                            "name": getattr(item, "name", "") or "",
                            "arguments": "",
                        }

                elif event_type == "response.function_call_arguments.done":
                    item_id = getattr(event, "item_id", "") or ""
                    call_id = _item_to_call.get(item_id, "")
                    if call_id and call_id in tool_calls_by_id:
                        tool_calls_by_id[call_id]["arguments"] = event.arguments
                        if event.name:
                            tool_calls_by_id[call_id]["name"] = event.name

                elif event_type == "response.completed":
                    response_obj = event.response
                    if process_chunk:
                        await run_func(process_chunk, {"stop": True})

                elif event_type == "response.failed":
                    error_msg = ""
                    if hasattr(event, "response") and hasattr(event.response, "error"):
                        error_msg = str(event.response.error)
                    raise RuntimeError(f"Responses API call failed: {error_msg}")

        except Exception as e:
            wrapped = _wrap_openai_error(e)
            if wrapped is not e:
                raise wrapped from e
            raise

        # Build output
        aggregated_text = "".join(text_parts) if text_parts else None
        final_tool_calls = None
        if tool_calls_by_id:
            final_tool_calls = [
                {"id": cid, "type": "function", "function": {"name": info["name"], "arguments": info["arguments"]}}
                for cid, info in tool_calls_by_id.items()
            ]

        # Cost
        cost = 0.0
        usage_dict = {}
        if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
            usage = response_obj.usage
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
            usage_dict = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
            try:
                from ..provider_registry import completion_cost as calc_cost
                cost = calc_cost(model=model, prompt_tokens=input_tokens, completion_tokens=output_tokens) or 0.0
            except Exception:
                pass
            if cost == 0.0 and (input_tokens or output_tokens):
                cost = (input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000

        return {
            "role": "assistant",
            "content": aggregated_text,
            "tool_calls": final_tool_calls,
            "_metadata": {"_debug_cost": cost, "_debug_usage": usage_dict},
        }

    async def aembedding(
        self,
        *,
        model: str,
        input: list[str],
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> list[list[float]]:
        """Generate embeddings using OpenAI API."""
        client = self._make_client(base_url, api_key)
        try:
            response = await client.embeddings.create(model=model, input=input)
            return [d.embedding for d in response.data]
        except Exception as e:
            raise _wrap_openai_error(e) from e

    async def aimage_generation(
        self,
        *,
        model: str,
        prompt: str,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> Any:
        """Generate images using OpenAI API (DALL-E, gpt-image)."""
        client = self._make_client(base_url, api_key)
        try:
            response = await client.images.generate(
                model=model,
                prompt=prompt,
                **kwargs,
            )
            return response
        except Exception as e:
            raise _wrap_openai_error(e) from e

    async def aimage_edit(
        self,
        *,
        model: str,
        image: Any,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> Any:
        """Edit images using OpenAI API."""
        client = self._make_client(base_url, api_key)
        try:
            response = await client.images.edit(
                model=model,
                image=image,
                **kwargs,
            )
            return response
        except Exception as e:
            raise _wrap_openai_error(e) from e

    async def atranscription(
        self,
        *,
        model: str,
        file: Any,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs,
    ) -> Any:
        """Transcribe audio using OpenAI Whisper API."""
        client = self._make_client(base_url, api_key)
        try:
            response = await client.audio.transcriptions.create(
                model=model,
                file=file,
                **kwargs,
            )
            return response
        except Exception as e:
            raise _wrap_openai_error(e) from e
