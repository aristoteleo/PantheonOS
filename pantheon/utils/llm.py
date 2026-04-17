import json
import re
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Callable

from .log import logger
from .misc import run_func

_PATTERN_BASE64_DATA_URI = re.compile(
    r"data:image/([a-zA-Z0-9+-]+);base64,([A-Za-z0-9+/=]+)"
)
_PATTERN_BASE64_MAGIC_PNG = re.compile(
    r'(iVBORw0KGgo[A-Za-z0-9+/=]{50,}?)(?:["\'\s]|$)', re.MULTILINE
)
_PATTERN_BASE64_MAGIC_JPEG = re.compile(
    r'(/9j/[A-Za-z0-9+/=]{50,}?)(?:["\'\s]|$)', re.MULTILINE
)
_PATTERN_BASE64_MAGIC_GIF = re.compile(
    r'(R0lGODlh[A-Za-z0-9+/=]{50,}?)(?:["\'\s]|$)', re.MULTILINE
)

_PATTERN_BASE64_MAGIC = re.compile(
    r"(?:"
    + _PATTERN_BASE64_MAGIC_PNG.pattern
    + "|"
    + _PATTERN_BASE64_MAGIC_JPEG.pattern
    + "|"
    + _PATTERN_BASE64_MAGIC_GIF.pattern
    + r")"
)


async def acompletion_openai(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    retry_times: int = 3,
    base_url: str | None = None,
    model_params: dict | None = None,
):
    from openai import NOT_GIVEN, APIConnectionError, AsyncOpenAI

    # Create client with custom base_url if provided
    if base_url:
        client = AsyncOpenAI(base_url=base_url)
    else:
        client = AsyncOpenAI()
    chunks = []
    _tools = tools or NOT_GIVEN
    _pcall = (tools is not None) or NOT_GIVEN

    # Use beta API only for OpenAI reasoning models (o1, o3 series)
    if model.startswith("o"):
        stream_manager = client.beta.chat.completions.stream(
            model=model,
            messages=messages,
            tools=_tools,
            response_format=response_format or {"type": "text"},
            **model_params,
        )
    else:
        stream_manager = client.beta.chat.completions.stream(
            model=model,
            messages=messages,
            tools=_tools,
            parallel_tool_calls=_pcall,
            response_format=response_format or {"type": "text"},
            **model_params,
        )

    while retry_times > 0:
        try:
            import time

            from .log import logger

            stream_start_time = time.time()
            first_chunk_time = None
            chunk_count = 0

            async with stream_manager as stream:
                logger.info(f"🔗 OpenAI stream connection established ({model})")
                async for event in stream:
                    if event.type == "chunk":
                        chunk = event.chunk
                        chunks.append(chunk.model_dump())

                        # Track first chunk timing
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                            ttfb = first_chunk_time - stream_start_time
                            logger.info(
                                f"⚡ OpenAI first chunk received: {ttfb:.3f}s (TTFB)"
                            )

                        if (
                            process_chunk
                            and hasattr(chunk, "choices")
                            and chunk.choices
                            and len(chunk.choices) > 0
                        ):
                            choice = chunk.choices[0]
                            if hasattr(choice, "delta"):
                                delta = choice.delta.model_dump()
                                chunk_count += 1
                                await run_func(process_chunk, delta)
                            if (
                                hasattr(choice, "finish_reason")
                                and choice.finish_reason == "stop"
                            ):
                                await run_func(process_chunk, {"stop": True})

                final_message = await stream.get_final_completion()
                total_stream_time = time.time() - stream_start_time
                logger.info(
                    f"✅ OpenAI stream completed: {total_stream_time:.3f}s, {chunk_count} chunks"
                )
                break
        except APIConnectionError as e:
            logger.error(f"OpenAI API connection error: {e}")
            retry_times -= 1
    return final_message


def _convert_content_to_responses_blocks(
    role: str,
    content: Any,
) -> Any:
    """Convert Chat Completions-style content blocks to Responses API blocks."""
    if content is None or isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content

    converted: list[dict] = []
    for item in content:
        if isinstance(item, str):
            text_key = "text"
            text_type = "output_text" if role == "assistant" else "input_text"
            converted.append({"type": text_type, text_key: item})
            continue

        if not isinstance(item, dict):
            text_type = "output_text" if role == "assistant" else "input_text"
            converted.append({"type": text_type, "text": str(item)})
            continue

        item_type = item.get("type")

        if role == "assistant":
            if item_type == "text":
                converted.append({"type": "output_text", "text": item.get("text", "")})
            elif item_type in {"output_text", "refusal", "summary_text"}:
                converted.append(item)
            else:
                # Best effort: preserve unsupported assistant blocks as text.
                converted.append({"type": "output_text", "text": str(item)})
            continue

        if item_type == "text":
            converted.append({"type": "input_text", "text": item.get("text", "")})
        elif item_type == "image_url":
            image_url = item.get("image_url", {})
            converted_item = {
                "type": "input_image",
                "image_url": image_url.get("url", ""),
            }
            if image_url.get("detail"):
                converted_item["detail"] = image_url["detail"]
            converted.append(converted_item)
        elif item_type in {"input_text", "input_image", "input_file"}:
            converted.append(item)
        else:
            converted.append({"type": "input_text", "text": str(item)})

    return converted


def _convert_messages_to_responses_input(
    messages: list[dict],
) -> tuple[str | None, list[dict]]:
    """Convert Chat Completions messages to Responses API input format.

    Returns:
        (instructions, input_items) — the first system message is extracted as
        ``instructions``; everything else becomes ``input_items``.
    """
    instructions: str | None = None
    input_items: list[dict] = []
    first_system_seen = False

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if not first_system_seen:
                instructions = content
                first_system_seen = True
            else:
                input_items.append({
                    "role": "developer",
                    "content": _convert_content_to_responses_blocks("developer", content),
                })

        elif role == "user":
            input_items.append({
                "role": "user",
                "content": _convert_content_to_responses_blocks("user", content),
            })

        elif role == "assistant":
            # Text part
            if content:
                input_items.append({
                    "role": "assistant",
                    "content": _convert_content_to_responses_blocks("assistant", content),
                })
            # Tool calls → function_call items
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

    return instructions, input_items


def _convert_tools_for_responses(tools: list[dict] | None) -> list[dict] | None:
    """Flatten Chat Completions tool format to Responses API format.

    From: {"type": "function", "function": {"name": ..., "description": ..., ...}}
    To:   {"type": "function", "name": ..., "description": ..., ...}
    """
    if not tools:
        return None
    converted = []
    for tool in tools:
        func = tool.get("function", {})
        item: dict = {"type": "function", "name": func.get("name", "")}
        if "description" in func:
            item["description"] = func["description"]
        if "parameters" in func:
            item["parameters"] = func["parameters"]
        if "strict" in func:
            item["strict"] = func["strict"]
        converted.append(item)
    return converted


def _convert_model_params_for_responses(model_params: dict | None) -> dict:
    """Map model_params to Responses API compatible kwargs.

    Conversions:
    - max_tokens → max_output_tokens
    - reasoning_effort → {"reasoning": {"effort": value}}
    - temperature, top_p → pass through
    - stream_options, num_retries → dropped
    """
    if not model_params:
        return {}
    result: dict = {}
    drop_keys = {"stream_options", "num_retries"}
    for key, value in model_params.items():
        if key in drop_keys:
            continue
        if key == "max_tokens":
            result["max_output_tokens"] = value
        elif key == "reasoning_effort":
            result["reasoning"] = {"effort": value}
        else:
            result[key] = value
    return result


async def acompletion_responses(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    base_url: str | None = None,
    model_params: dict | None = None,
    num_retries: int = 3,
) -> dict:
    """Call OpenAI Responses API with streaming.

    Used for models that require the Responses API (e.g. codex-mini-latest).
    Returns a normalised message dict compatible with ``extract_message_from_response``.
    """
    from openai import AsyncOpenAI
    from .provider_registry import get_model_info, get_output_token_param
    from .llm_providers import get_llm_proxy_config

    # ========== Build client ==========
    _proxy_base, _proxy_key = get_llm_proxy_config()
    if _proxy_base:
        # Hub-deployed mode: route through LiteLLM proxy using virtual key.
        # The proxy exposes /responses natively for OpenAI models.
        client = AsyncOpenAI(base_url=_proxy_base, api_key=_proxy_key)
    elif base_url:
        client = AsyncOpenAI(base_url=base_url)
    else:
        client = AsyncOpenAI()

    # ========== Convert inputs ==========
    instructions, input_items = _convert_messages_to_responses_input(messages)
    converted_tools = _convert_tools_for_responses(tools)
    response_model_params = dict(model_params or {})
    if not any(
        key in response_model_params
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
    ):
        try:
            max_out = get_model_info(model).get("max_output_tokens")
            token_param = get_output_token_param(model, api_mode="responses")
            if token_param and max_out and max_out > 0:
                response_model_params[token_param] = max_out
        except Exception:
            pass
    extra_params = _convert_model_params_for_responses(response_model_params)

    # ========== Build kwargs ==========
    kwargs: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "stream": True,
    }
    if instructions is not None:
        kwargs["instructions"] = instructions
    if converted_tools is not None:
        kwargs["tools"] = converted_tools
    if response_format is not None:
        # Responses API uses a "text" parameter for format control
        kwargs["text"] = response_format
    kwargs.update(extra_params)

    logger.debug(f"[RESPONSES_API] Calling responses.create | model={model}")

    # ========== Stream ==========
    text_parts: list[str] = []
    tool_calls_by_id: dict[str, dict] = {}  # call_id → {name, arguments}
    # item_id → call_id mapping (arguments.done events only carry item_id)
    _item_to_call: dict[str, str] = {}
    response_obj = None

    from pantheon.agent import StopRunning

    stream = await client.responses.create(**kwargs)
    try:
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
                # This event carries item_id, not call_id
                item_id = getattr(event, "item_id", "") or ""
                call_id = _item_to_call.get(item_id, "")
                if call_id and call_id in tool_calls_by_id:
                    tool_calls_by_id[call_id]["arguments"] = event.arguments
                    # name may be available here; prefer the one from output_item.added
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

            else:
                logger.debug(f"[RESPONSES_API] Skipping event: {event_type}")
    except StopRunning:
        # Build partial message from text collected so far
        partial_text = "".join(text_parts) if text_parts else None
        partial_msg = None
        if partial_text and partial_text.strip():
            partial_msg = {
                "role": "assistant",
                "content": partial_text,
                "tool_calls": None,
            }
        raise StopRunning(partial_message=partial_msg)

    # ========== Build output message ==========
    aggregated_text = "".join(text_parts) if text_parts else None
    final_tool_calls = None
    if tool_calls_by_id:
        final_tool_calls = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": info["name"],
                    "arguments": info["arguments"],
                },
            }
            for call_id, info in tool_calls_by_id.items()
        ]

    # ========== Cost estimation ==========
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
            from pantheon.utils.provider_registry import completion_cost
            cost = completion_cost(model=model, prompt_tokens=input_tokens, completion_tokens=output_tokens) or 0.0
        except Exception:
            pass
        if cost == 0.0 and (input_tokens or output_tokens):
            cost = (input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000

    message: dict[str, Any] = {
        "role": "assistant",
        "content": aggregated_text,
        "tool_calls": final_tool_calls,
        "_metadata": {
            "_debug_cost": cost,
            "_debug_usage": usage_dict,
        },
    }
    return message


def stream_chunk_builder(chunks: list[dict]) -> Any:
    """Assemble streaming chunks into a complete response object.

    Aggregates content deltas, tool_call deltas, and usage from collected chunks
    into a SimpleNamespace that mimics the shape of a chat completion response.

    Replaces the stream_chunk_builder from external dependencies.
    """
    from types import SimpleNamespace

    full_content = ""
    full_reasoning = ""
    tool_calls_map: dict[int, dict] = {}  # index → tool_call dict
    finish_reason = None
    usage = {}
    model = ""
    role = "assistant"

    for chunk in chunks:
        # Handle dict chunks (from adapters)
        if isinstance(chunk, dict):
            # Extract usage from usage-only chunks
            if "usage" in chunk and chunk["usage"]:
                usage = chunk["usage"]
            if "model" in chunk:
                model = chunk["model"]

            choices = chunk.get("choices", [])
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta", {})

            # Accumulate content
            if "content" in delta and delta["content"]:
                full_content += delta["content"]

            # Accumulate reasoning (various field names across providers)
            # - reasoning_content: DeepSeek, Zhipu, Kimi, Anthropic adapter, Gemini adapter
            # - reasoning: Groq gpt-oss models
            if "reasoning_content" in delta and delta["reasoning_content"]:
                full_reasoning += delta["reasoning_content"]
            elif "reasoning" in delta and delta["reasoning"]:
                full_reasoning += delta["reasoning"]

            # Accumulate role
            if "role" in delta and delta["role"]:
                role = delta["role"]

            # Accumulate tool calls
            if "tool_calls" in delta and delta["tool_calls"]:
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc.get("id", ""),
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": "",
                            },
                        }
                        # Preserve thought_signature for Gemini 3 thinking models
                        if "thought_signature" in tc:
                            tool_calls_map[idx]["thought_signature"] = tc["thought_signature"]
                    else:
                        # Merge
                        if tc.get("id"):
                            tool_calls_map[idx]["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            tool_calls_map[idx]["function"]["name"] = func["name"]

                    # Always append arguments
                    args = tc.get("function", {}).get("arguments", "")
                    if args:
                        tool_calls_map[idx]["function"]["arguments"] += args

            # Track finish reason
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = fr

        else:
            # Handle object-style chunks (from OpenAI SDK directly)
            if hasattr(chunk, "model_dump"):
                chunk_dict = chunk.model_dump()
                # Recursively process as dict
                result = stream_chunk_builder([chunk_dict])
                return result

    # Build final tool_calls list
    final_tool_calls = None
    if tool_calls_map:
        final_tool_calls = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]

    # Build message
    # Keep content and reasoning_content separate so the frontend can
    # render reasoning in a dedicated "Thinking" section instead of
    # mixing it into the main response text.
    effective_content = full_content or None

    message = SimpleNamespace(
        role=role,
        content=effective_content,
        tool_calls=final_tool_calls,
        reasoning_content=full_reasoning or None,
    )

    def message_model_dump():
        d = {"role": message.role, "content": message.content, "tool_calls": message.tool_calls}
        if message.reasoning_content:
            d["reasoning_content"] = message.reasoning_content
        return d
    message.model_dump = message_model_dump

    # Build choice
    choice = SimpleNamespace(
        message=message,
        finish_reason=finish_reason,
    )

    # Build usage
    usage_ns = SimpleNamespace(
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
    )

    # Build response
    resp = SimpleNamespace(
        choices=[choice],
        model=model,
        usage=usage_ns,
        _hidden_params={},
    )

    return resp


async def acompletion(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model_params: dict | None = None,
    num_retries: int = 3,
):
    """Call LLM via provider adapters.

    Two modes of operation:

    1. PROXY MODE (Hub-launched agents):
       - LLM_API_BASE is set to the LiteLLM proxy URL
       - Provider API keys are set to the user's virtual key
       - All calls route through the OpenAI-compatible proxy

    2. STANDALONE MODE (agents running independently):
       - LLM_API_BASE not set
       - Falls back to reading real API keys from environment variables
       - Uses native SDK adapters (openai, anthropic, google-genai)
    """
    from .provider_registry import (
        find_provider_for_model,
        get_provider_config,
        completion_cost,
        get_model_info,
        get_output_token_param,
    )
    from .adapters import get_adapter
    from .llm_providers import get_llm_proxy_config

    logger.debug(f"[ACOMPLETION] Starting LLM call | Model={model}")

    # ========== Resolve provider and adapter ==========
    provider_key, model_name, provider_config = find_provider_for_model(model)
    sdk_type = provider_config.get("sdk", "openai")

    # ========== Ensure output token limit is set from the catalog ==========
    # Different vendors use different parameter names for the same concept.
    # The catalog records the preferred parameter name; we use it here so the
    # first request is correct for known providers/models.
    model_params = dict(model_params or {})
    if not any(
        key in model_params
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
    ):
        try:
            max_out = get_model_info(model).get("max_output_tokens")
            token_param = get_output_token_param(model, api_mode="chat")
            if token_param and max_out and max_out > 0:
                model_params[token_param] = max_out
        except Exception:
            pass  # Fall through to provider default

    # ========== Mode Detection & Configuration ==========
    _proxy_base, _proxy_key = get_llm_proxy_config()
    oauth_client_kwargs = None
    if _proxy_base:
        # Proxy mode: LLM_API_BASE is set — all calls go through the
        # OpenAI-compatible LiteLLM proxy.  Force OpenAI SDK for every
        # provider (Anthropic, Gemini, etc.) so the proxy receives a
        # standard /v1/chat/completions request with the full model string.
        effective_base_url = _proxy_base
        effective_api_key = _proxy_key
        sdk_type = "openai"
        effective_model = model  # pass full "provider/model" string to proxy
        # Normalize provider-specific token param names to OpenAI-compatible.
        # e.g. Gemini uses max_output_tokens, but the OpenAI SDK only accepts
        # max_tokens / max_completion_tokens.
        if "max_output_tokens" in model_params:
            model_params["max_tokens"] = model_params.pop("max_output_tokens")
    elif sdk_type == "codex":
        # Codex OAuth: get access token from OAuth manager
        from .oauth import CodexOAuthManager
        oauth = CodexOAuthManager()
        effective_api_key = oauth.get_access_token(auto_refresh=True)
        if not effective_api_key:
            raise RuntimeError(
                "[OAUTH_REQUIRED] Codex OAuth session expired or not configured. "
                "Please re-login in Settings → API Keys → OAuth."
            )
        effective_base_url = provider_config.get("base_url")
        effective_model = model_name
    elif provider_key == "gemini-cli":
        sdk_type = "gemini-cli"
        effective_base_url = base_url or "https://cloudcode-pa.googleapis.com"
        try:
            from .oauth import GeminiCliOAuthError, GeminiCliOAuthManager

            gemini_oauth = GeminiCliOAuthManager()
            effective_api_key = gemini_oauth.build_api_key_payload(
                refresh_if_needed=True,
                import_if_missing=True,
            )
            if not effective_api_key:
                raise GeminiCliOAuthError(
                    "Gemini CLI OAuth session expired or not configured. "
                    "Please re-login in Settings → API Keys → OAuth."
                )
        except GeminiCliOAuthError as e:
            raise RuntimeError(f"[OAUTH_PROJECT_REQUIRED] {e}") from e
        except Exception as e:
            raise RuntimeError(
                "[OAUTH_REQUIRED] Gemini CLI OAuth session expired or not configured. "
                "Please re-login in Settings → API Keys → OAuth."
            ) from e
        effective_model = model_name
    elif provider_key == "gemini" or sdk_type == "google-genai":
        effective_base_url = base_url or provider_config.get("base_url")
        effective_api_key = api_key
        if not effective_api_key:
            import os
            api_key_env = provider_config.get("api_key_env", "")
            if api_key_env:
                effective_api_key = os.environ.get(api_key_env, "")
        effective_model = model_name
    else:
        effective_base_url = base_url or provider_config.get("base_url")
        effective_api_key = api_key
        if not effective_api_key:
            import os
            api_key_env = provider_config.get("api_key_env", "")
            if api_key_env:
                effective_api_key = os.environ.get(api_key_env, "")
        # Local providers (Ollama) don't need a real API key
        if not effective_api_key and provider_config.get("local"):
            effective_api_key = "ollama"
        effective_model = model_name  # use bare model name with native SDK

    adapter = get_adapter(sdk_type)

    # ========== Prepare adapter kwargs ==========
    adapter_kwargs = dict(model_params or {})
    if oauth_client_kwargs:
        adapter_kwargs["oauth_client_kwargs"] = oauth_client_kwargs

    # Codex OAuth: pass account_id for chatgpt-account-id header
    if sdk_type == "codex":
        from .oauth import CodexOAuthManager
        account_id = CodexOAuthManager().get_account_id()
        if account_id:
            adapter_kwargs["account_id"] = account_id

    # Kimi Coding API gates access by User-Agent header
    if "kimi-for-coding" in model:
        adapter_kwargs.setdefault("extra_headers", {})
        adapter_kwargs["extra_headers"].setdefault("User-Agent", "claude-code/0.1.0")

    # ========== Execute Call ==========
    from pantheon.agent import StopRunning

    try:
        logger.debug(f"[ACOMPLETION] Calling {sdk_type} adapter for model={effective_model}")
        collected_chunks = await adapter.acompletion(
            model=effective_model,
            messages=messages,
            tools=tools,
            response_format=response_format,
            stream=True,
            process_chunk=process_chunk,
            base_url=effective_base_url,
            api_key=effective_api_key,
            num_retries=num_retries,
            **adapter_kwargs,
        )
        logger.debug(f"[ACOMPLETION] ✓ Call succeeded for model={effective_model}")
    except StopRunning:
        raise
    except Exception as e:
        logger.error(
            f"[ACOMPLETION] ✗ Call failed | "
            f"Model={effective_model} | Error={type(e).__name__}: {str(e)[:200]}"
        )
        raise

    # ========== Build complete response ==========
    # Codex adapter returns a message dict directly (not chunks)
    if sdk_type == "codex" and isinstance(collected_chunks, dict):
        return collected_chunks  # Already a normalized message dict

    complete_resp = stream_chunk_builder(collected_chunks)

    # Calculate and attach cost information
    try:
        cost = completion_cost(completion_response=complete_resp)
        if cost and cost > 0:
            if not hasattr(complete_resp, "_hidden_params"):
                complete_resp._hidden_params = {}
            complete_resp._hidden_params["response_cost"] = cost
    except Exception:
        pass

    return complete_resp


def remove_parsed(messages: list[dict]) -> list[dict]:
    for message in messages:
        if "parsed" in message:
            del message["parsed"]
    return messages


def remove_reasoning_content(messages: list[dict]) -> list[dict]:
    """Remove reasoning fields from messages (prevent context pollution).

    Removes both reasoning_content (unified field) and thinking_blocks (Claude-specific).
    """
    for message in messages:
        if "reasoning_content" in message:
            del message["reasoning_content"]
        # Claude-specific: also remove thinking_blocks
        if "thinking_blocks" in message:
            del message["thinking_blocks"]
    return messages


def convert_tool_message(messages: list[dict]) -> list[dict]:
    new_messages = []
    for msg in messages:
        if msg["role"] == "tool":
            resp_prompt = (
                f"Tool `{msg['tool_name']}` called with id `{msg['tool_call_id']}` "
                f"got result:\n{msg['content']}"
            )
            new_msg = {
                "role": "user",
                "content": resp_prompt,
            }
            new_messages.append(new_msg)
        elif msg.get("tool_calls"):
            tool_call_str = str(msg["tool_calls"])
            msg["content"] += f"\nTool calls:\n{tool_call_str}"
            del msg["tool_calls"]
            new_messages.append(msg)
        else:
            new_messages.append(msg)
    return new_messages


def remove_raw_content(messages: list[dict]) -> list[dict]:
    for msg in messages:
        if "raw_content" in msg:
            del msg["raw_content"]
    return messages


def filter_base64_in_tool_result(result: dict) -> dict:
    """
    Filter base64 data from a dict.
    """
    if not isinstance(result, dict):
        return result
    # iterate over dict values
    for key, value in result.items():
        if isinstance(value, str):
            result[key] = _replace_base64_with_placeholder(value)
        elif isinstance(value, dict):
            result[key] = filter_base64_in_tool_result(value)

    return result


def _replace_base64_with_placeholder(content: str, log=False) -> str:
    """
    Replace all base64 data in a string with placeholders.

    Supported formats:
    1. data URI: data:image/png;base64,iVBORw0KGgo...
    2. Magic numbers: iVBORw0KGgo..., /9j/..., R0lGODlh...

    Args:
        content: String containing base64 data

    Returns:
        String with base64 replaced by [Image: TYPE (SIZEkB)] placeholders
    """
    if not any(
        marker in content
        for marker in [
            "data:image/",
            "image/png",
            "image/jpeg",
            "image/gif",
            "iVBORw0KGgo",
            "/9j/",
            "R0lGODlh",
        ]
    ):
        return content

    modified = content
    original_size = len(content)
    replacements = []

    # Pattern 1: data:image/...;base64,{base64_data}
    def replace_data_uri(match):
        image_type = match.group(1)
        base64_data = match.group(2)
        size_kb = len(base64_data) * 3 / 4 / 1024
        placeholder = f"[Image: {image_type.upper()} ({size_kb:.1f}KB)]"
        replacements.append(
            {
                "type": f"image/{image_type}",
                "size_kb": round(size_kb, 2),
                "format": "data_uri",
            }
        )

        return placeholder

    data_uri_pattern = _PATTERN_BASE64_DATA_URI
    modified = re.sub(data_uri_pattern, replace_data_uri, modified)

    # 2. Pattern 2: Magic numbers: iVBORw0KGgo..., /9j/..., R0lGODlh...
    def replace_magic(match):
        magic = match.group(0)
        placeholder = f"[Image: {magic[:6]}... ({len(magic) * 3 / 4 / 1024:.1f}KB)]"
        replacements.append(
            {
                "type": "magic",
                "size_kb": round(len(magic) * 3 / 4 / 1024, 2),
                "format": "magic",
            }
        )

        return placeholder

    modified = re.sub(_PATTERN_BASE64_MAGIC, replace_magic, modified)

    # Log summary if replacements were made
    if replacements and log:
        filtered_size = len(modified)
        total_saved_kb = sum(r["size_kb"] for r in replacements)
        logger.info(
            f"📊 Base64 Filtering Summary: {len(replacements)} image(s) replaced, "
            f"content size reduced from {original_size / 1024:.1f}KB to {filtered_size / 1024:.1f}KB "
            f"(saved ~{total_saved_kb:.1f}KB, compression ratio ~{original_size / filtered_size:.1f}x)"
        )

    return modified


def _remove_ansi_escape_sequences(text):
    """Remove ANSI escape sequences from text."""
    # Regex pattern to match ANSI escape sequences
    ansi_escape = re.compile(r"(?:\x1b|u001b|x1b)\[[0-9;]*[a-zA-Z]")
    return ansi_escape.sub("", text)


def filter_tool_messages(messages: list[dict]) -> list[dict]:
    """
    Filter tool-returned messages
    """
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        # Only process tool messages
        if msg.get("role") != "tool":
            continue

        content = msg.get("content")
        if not isinstance(content, str):
            continue

        # Filter ANSI escape sequences
        filtered_content = _replace_base64_with_placeholder(content)
        filtered_content = _remove_ansi_escape_sequences(filtered_content)

        msg["content"] = filtered_content

    return messages


def remove_unjsonifiable_raw_content(messages: list[dict]) -> list[dict]:
    for msg in messages:
        if "raw_content" in msg:
            try:
                json.dumps(msg["raw_content"])
            except Exception:
                del msg["raw_content"]
    return messages


def remove_extra_fields(messages: list[dict]) -> list[dict]:
    for msg in messages:
        if "agent_name" in msg:
            del msg["agent_name"]
        if "tool_name" in msg:
            del msg["tool_name"]
    return messages


def remove_ui_fields(messages: list[dict]) -> list[dict]:
    """
    Remove UI-only fields that should not be sent to LLM.

    These fields are added for frontend display/processing and would waste tokens
    if sent to the LLM. They can also confuse the LLM model.

    Fields removed:
    - Attachment metadata (detected_attachments - only kept for UI/frontend)
    - Timing information (timestamp, start_timestamp, end_timestamp, generation_duration, execution_duration)
    - Internal IDs (id, message_id, chunk_index, transfer)
    """
    UI_ONLY_FIELDS = {
        # Attachment field (UI-only)
        "detected_attachments",
        # Timing fields
        "timestamp",
        "start_timestamp",
        "end_timestamp",
        "generation_duration",
        "execution_duration",
        # Internal IDs and metadata
        "id",
        "message_id",
        "chunk_index",
        "transfer",
        # NOTE: _metadata is intentionally preserved here for cost tracking
        # It must be removed explicitly by remove_metadata before sending to LLM
    }

    for msg in messages:
        for field in UI_ONLY_FIELDS:
            if field in msg:
                del msg[field]

    return messages


_ALLOWED_MESSAGE_FIELDS = {
    "role", "content", "name", "tool_calls", "tool_call_id",
    "refusal", "function_call",  # OpenAI standard fields
}


def remove_metadata(messages: list[dict]) -> list[dict]:
    """
    Strip messages down to only standard OpenAI fields before sending to LLM.

    Strict providers like Groq reject ANY unknown field (chat_id, _metadata,
    _llm_content, _user_metadata, detected_attachments, etc.) and also
    reject null values for optional fields like tool_calls.
    """
    for msg in messages:
        # Remove non-standard fields
        extra_keys = [k for k in msg if k not in _ALLOWED_MESSAGE_FIELDS]
        for k in extra_keys:
            del msg[k]
        # Remove fields with None/null values (Groq rejects "tool_calls": null)
        null_keys = [k for k in ("tool_calls", "tool_call_id", "name", "function_call", "refusal")
                     if k in msg and msg[k] is None]
        for k in null_keys:
            del msg[k]
    return messages


def process_messages_for_model(messages: list[dict], model: str) -> list[dict]:
    """
    Process messages for model consumption.

    Processing steps (order matters):
    1. remove_parsed - Remove parsed fields
    2. remove_raw_content - Remove raw_content (structured tool outputs)
    3. filter_base64_in_tool_messages - Filter base64 in tool messages
    4. remove_extra_fields - Remove agent_name, tool_name
    5. remove_ui_fields - Remove UI-only fields
    """
    messages = deepcopy(messages)
    messages = remove_parsed(messages)
    messages = remove_reasoning_content(messages)
    messages = remove_raw_content(messages)
    messages = filter_tool_messages(messages)
    messages = remove_extra_fields(messages)
    messages = remove_ui_fields(messages)  # Remove UI-only fields
    return messages


def process_messages_for_store(messages: list[dict]) -> list[dict]:
    """Process messages before storing in memory.
    
    Ensures all messages have a unique ID for later reference (e.g., revert operations).
    """
    from uuid import uuid4
    
    messages = deepcopy(messages)
    messages = remove_parsed(messages)
    messages = remove_unjsonifiable_raw_content(messages)
    
    # Ensure all messages have an ID
    for msg in messages:
        if "id" not in msg or not msg["id"]:
            msg["id"] = str(uuid4())
    
    return messages


def process_messages_for_hook_func(messages: list[dict]) -> list[dict]:
    messages = deepcopy(messages)
    messages = remove_unjsonifiable_raw_content(messages)
    return messages


async def openai_embedding(
    texts: list[str], model: str = "text-embedding-3-large"
) -> list[list[float]]:
    """Get embeddings (with proxy support).

    Args:
        texts: List of texts to embed
        model: Embedding model to use

    Returns:
        List of embedding vectors
    """
    from .adapters import get_adapter
    from .llm_providers import get_llm_proxy_config

    _proxy_base, _proxy_key = get_llm_proxy_config()
    adapter = get_adapter("openai")

    return await adapter.aembedding(
        model=model,
        input=texts,
        base_url=_proxy_base or None,
        api_key=_proxy_key or None,
    )


def remove_hidden_fields(content: dict) -> dict:
    """Remove hidden fields from dict content.
    
    If content is not a dict, return as-is.
    """
    if not isinstance(content, dict):
        return content
    
    content = deepcopy(content)
    if "hidden_to_model" in content:
        hidden_fields = content.pop("hidden_to_model")
        for field in hidden_fields:
            if field in content:
                content.pop(field)
    return content


def process_tool_result(
    result: Any,
    max_length: int | None = None,
    tool_name: str | None = None,
) -> Any:
    """Process tool result with optional truncation.

    Args:
        result: Raw tool result
        max_length: Global max length for truncation (fallback)
        tool_name: Tool name for per-tool threshold lookup

    Returns:
        Processed result
    """
    # Remove hidden fields
    result = remove_hidden_fields(result)

    # Determine effective limit: per-tool threshold takes priority over global
    effective_limit = max_length
    if max_length is not None:
        try:
            from pantheon.utils.token_optimization import (
                get_per_tool_limit,
            )
            effective_limit = int(get_per_tool_limit(tool_name, max_length))
        except Exception as e:
            logger.debug(f"get_per_tool_limit failed for {tool_name}: {e}")

    # Apply smart truncation if limit specified
    if effective_limit is not None:
        try:
            from pantheon.utils.truncate import smart_truncate_result
            return smart_truncate_result(result, effective_limit, filter_base64=True)
        except Exception as e:
            # Fallback to simple string conversion if truncation fails
            logger.warning(f"Smart truncation failed: {e}, falling back to simple conversion")
            content = str(result) if not isinstance(result, str) else result
            if len(content) > effective_limit:
                # Simple truncation: head + tail
                half = effective_limit // 2
                return f"{content[:half]}\n...[truncated]...\n{content[-half:]}"
            return content

    return result


# ============ Timing Tracker ============


class TimingTracker:
    """Track execution time for different phases.

    Provides:
    - Manual start/end tracking
    - Context manager for automatic timing
    - Aggregate timing report

    Examples:
        >>> tracker = TimingTracker()
        >>> tracker.start("phase1")
        >>> time.sleep(0.1)
        >>> duration = tracker.end("phase1")
        >>> print(tracker.get_all())
        {'phase1': 0.10...}

        >>> async with tracker.measure("phase2"):
        ...     await asyncio.sleep(0.1)
        >>> tracker.get_all()
        {'phase1': 0.10..., 'phase2': 0.10...}
    """

    def __init__(self):
        """Initialize timing tracker."""
        self.timings: dict[str, float] = {}
        self._start_times: dict[str, float] = {}

    def start(self, phase: str) -> None:
        """Mark the start of a phase.

        Args:
            phase: Phase name

        Raises:
            ValueError: If phase already started
        """
        if phase in self._start_times:
            raise ValueError(f"Phase '{phase}' already started")
        self._start_times[phase] = time.time()

    def end(self, phase: str) -> float:
        """End a phase and record duration.

        Args:
            phase: Phase name

        Returns:
            Duration in seconds

        Raises:
            ValueError: If phase not started
        """
        if phase not in self._start_times:
            raise ValueError(f"Phase '{phase}' not started")
        duration = time.time() - self._start_times[phase]
        self.timings[phase] = duration
        del self._start_times[phase]
        return duration

    def get_all(self) -> dict[str, float]:
        """Get all recorded timings.

        Returns:
            Dictionary of phase -> duration
        """
        return self.timings.copy()

    @asynccontextmanager
    async def measure(self, phase: str):
        """Measure timing for a phase using context manager.

        Args:
            phase: Phase name

        Examples:
            >>> async with tracker.measure("api_call"):
            ...     await some_async_function()
        """
        self.start(phase)
        try:
            yield
        finally:
            self.end(phase)

def _fallback_token_count(text: str) -> int:
    """Fallback token counter with language awareness.
    
    Attempts tiktoken first, then falls back to language-aware estimation.
    
    Token estimation ratios:
    - CJK (Chinese/Japanese/Korean): ~1.5 tokens/char
    - ASCII (English/code): ~0.25 tokens/char (4 chars/token)
    - Other Unicode: ~0.5 tokens/char (2 chars/token)
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Enhanced fallback with CJK detection
        if not text:
            return 0
        
        cjk_chars = 0
        ascii_chars = 0
        other_chars = 0
        
        for char in text:
            code = ord(char)
            # CJK Unified Ideographs and common CJK ranges
            if (0x4E00 <= code <= 0x9FFF or      # CJK Unified Ideographs
                0x3400 <= code <= 0x4DBF or      # CJK Extension A
                0x3000 <= code <= 0x303F or      # CJK Punctuation
                0xFF00 <= code <= 0xFFEF or      # Fullwidth Forms
                0xAC00 <= code <= 0xD7AF or      # Korean Hangul
                0x3040 <= code <= 0x309F or      # Japanese Hiragana
                0x30A0 <= code <= 0x30FF):       # Japanese Katakana
                cjk_chars += 1
            elif code < 128:  # ASCII
                ascii_chars += 1
            else:  # Other Unicode (emojis, symbols, etc.)
                other_chars += 1
        
        # Calculate tokens
        tokens = (
            cjk_chars * 1.5 +       # CJK: ~1.5 tokens per char
            ascii_chars * 0.25 +    # ASCII: ~4 chars per token
            other_chars * 0.5       # Other: ~2 chars per token
        )
        
        return max(1, int(tokens))


def _safe_token_counter(
    model: str, messages: list[dict] = None, tools: list[dict] = None
) -> int:
    """Token counter with fallback for unsupported models."""
    try:
        from pantheon.utils.provider_registry import token_counter

        return token_counter(model=model, messages=messages or [], tools=tools)
    except Exception:
        # Fallback: count tokens from message content
        total = 0
        for msg in messages or []:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += _fallback_token_count(content)
            elif isinstance(content, list):
                # Handle multimodal content
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += _fallback_token_count(part["text"])
        # Estimate tools tokens
        if tools:
            import json

            total += _fallback_token_count(json.dumps(tools))
        return total


def calculate_total_cost_from_messages(messages: list[dict]) -> float:
    """Calculate total cost from message list
    
    Uniformly processes all message types, calculating current_cost only once per message.
    
    Note: Should pass in the full message list (for_llm=False), including compressed messages.
    The system naturally calculates only once because:
    - Compressed assistant messages have their own current_cost
    - Compression messages only record the cost of compression itself
    - No duplicate calculations
    
    Args:
        messages: Message list (recommended to use memory.get_messages(for_llm=False))
    
    Returns:
        Total cost (rounded to 4 decimals)
    """
    total = 0.0
    
    for msg in messages:
        # Unified processing: read current_cost from all messages
        total += msg.get("_metadata", {}).get("current_cost", 0)
    
    return round(total, 4)


def collect_message_stats_lightweight(
    message: dict,
    messages: list[dict],
    model: str,
) -> None:
    """Lightweight statistics collection - read usage from _debug fields
    
    Only collects essential fields:
    - total_tokens: actual size of current context
    - current_cost: current message cost
    - max_tokens: model's maximum context
    
    Data source priority:
    1. Read from _debug_usage/_debug_cost (populated by call_llm_provider)
    2. Fallback: manually calculate new messages
    """
    meta = message.setdefault("_metadata", {})
    
    # ========== 1. Read from _debug fields ==========
    # call_llm_provider has already stored usage info in _debug_usage
    if "_debug_usage" in meta:
        usage = meta["_debug_usage"]
        
        # ✅ Read total_tokens (includes prompt + completion)
        # This is the complete context size for the next call
        meta["total_tokens"] = usage.get("total_tokens", 0)
        
        # ✅ Read current_cost (write if exists, including 0.0)
        # _debug_cost is calculated by _extract_cost_and_usage, already includes fallback logic
        if "_debug_cost" in meta:
            meta["current_cost"] = meta["_debug_cost"]
        
        # ✅ Clean up temporary debug fields
        meta.pop("_debug_cost", None)
        meta.pop("_debug_usage", None)
        
    # ========== 2. Fallback: manually calculate new messages ==========
    else:
        # Find the previous assistant message
        last_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") == "assistant" and "_metadata" in msg:
                last_assistant_idx = i
                break
        
        # Calculate new messages
        if last_assistant_idx >= 0:
            # All messages after last assistant + current assistant
            new_messages = messages[last_assistant_idx + 1:] + [message]
            
            # Get previous total_tokens
            prev_meta = messages[last_assistant_idx].get("_metadata", {})
            prev_total = prev_meta.get("total_tokens", 0)
        else:
            # First message, calculate all
            new_messages = messages + [message]
            prev_total = 0
        
        try:
            # Calculate tokens for new portion
            new_tokens = _safe_token_counter(model, messages=new_messages)
            
            # Accumulate
            meta["total_tokens"] = prev_total + new_tokens
            
        except Exception as e:
            logger.warning(f"Failed to calculate tokens: {e}")
            meta["total_tokens"] = 0
    
    # ========== 3. Max tokens ==========
    try:
        from pantheon.utils.provider_registry import get_model_info
        model_info = get_model_info(model)
        meta["max_tokens"] = model_info.get("max_input_tokens", 200000)
    except Exception:
        meta["max_tokens"] = 200000


def count_tokens_in_messages(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    assistant_message: dict | None = None,
) -> dict:
    """Count tokens with per-role breakdown and context usage metrics.

    Separates system prompt (first system message) and tools definition from other roles.
    """
    try:
        from pantheon.utils.provider_registry import get_model_info

        total_tokens = 0
        tokens_by_role = {}
        message_counts = {}
        system_prompt_tokens = 0
        tools_definition_tokens = 0

        # Create counting list
        msgs_to_count = list(messages)
        if assistant_message:
            msgs_to_count.append(assistant_message)

        # 1. Count tokens for messages
        for i, msg in enumerate(msgs_to_count):
            role = msg.get("role", "unknown")
            msg_tokens = _safe_token_counter(model=model, messages=[msg])

            # Check if this is the system prompt (first system message)
            if role == "system" and i == 0:
                system_prompt_tokens = msg_tokens
                # Don't add to tokens_by_role["system"], kept separate
            else:
                tokens_by_role[role] = tokens_by_role.get(role, 0) + msg_tokens

            total_tokens += msg_tokens
            message_counts[role] = message_counts.get(role, 0) + 1

        # 2. Count tokens for tools definition
        if tools:
            # token_counter handles tools definition specifically
            tools_definition_tokens = _safe_token_counter(model=model, tools=tools)
            total_tokens += tools_definition_tokens

        # Try to get model info, fallback to defaults for unsupported models
        try:
            model_info = get_model_info(model)
        except Exception:
            model_info = {}  # Will use fallback defaults below

        # Calculate usage metrics (the context window usually refers to input tokens)
        max_input_tokens = model_info.get("max_input_tokens") or 200_000  # 200K default
        max_output_tokens = model_info.get("max_output_tokens") or 32_000  # 32K default

        # User concern: Max should reflect context window (input), not input + output
        max_tokens = max_input_tokens
        remaining = max(0, max_tokens - total_tokens)
        usage_percent = (
            round((total_tokens / max_tokens * 100), 1) if max_tokens > 0 else 0
        )

        # calculate estimated cost for the current model
        input_cost_per_token = model_info.get("input_cost_per_token", 0) or 0
        output_cost_per_token = model_info.get("output_cost_per_token", 0) or 0

        # Determine target message for cost tracking
        # Use explicit assistant_message if provided (Write Mode),
        # otherwise find last assistant message in history (Read Mode)
        target_message = assistant_message or next(
            (m for m in reversed(msgs_to_count) if m.get("role") == "assistant"), None
        )

        # Estimate input/output split
        # msg_tokens is currently holding the token count of the last processed message
        # If we have a target message (assistant response), its tokens are output tokens
        # Everything else is considered input tokens
        output_tokens = 0
        if target_message:
            # Recalculate/Get target message tokens
            # Note: We don't have per-message tokens stored easily unless we re-count or captured it loop
            # But we can approximate.
            # Better approach: We know total_tokens. We can subtract target_message tokens if we knew them.
            # Actually, let's just use the simple heuristic:
            # If target_message is assistant, its content is output.
            output_tokens = _safe_token_counter(model=model, messages=[target_message])

        input_tokens = max(0, total_tokens - output_tokens)
        current_cost = round(
            (input_tokens * input_cost_per_token)
            + (output_tokens * output_cost_per_token),
            6,
        )

        total_session_cost = None

        # Process cost tracking if we have a target message
        if target_message:
            meta = target_message.get("_metadata", {})

            # Scenario A: Legacy - Convert _debug_cost to current_cost
            # (Rarely triggers - collect_message_stats_lightweight handles most cases)
            if "_debug_cost" in meta:
                current_cost = meta.pop("_debug_cost", 0.0)
                meta.pop("_debug_usage", None)
                
                # Store as current_cost if not already set
                if "current_cost" not in meta:
                    meta["current_cost"] = current_cost
                
                # Don't calculate total_session_cost here - let Scenario C handle it

            # Scenario A': Write Mode (No provider cost, store estimated cost)
            elif "current_cost" not in meta and current_cost > 0:
                meta["current_cost"] = current_cost

            # Note: total_tokens is now written by collect_message_stats_lightweight
            # (removed redundant write here to avoid overwriting)

            # Ensure metadata dict is attached to message
            if "_metadata" not in target_message:
                target_message["_metadata"] = meta

            # Scenario B: Read Mode (Historical data exists)
            if "current_cost" in meta and total_session_cost is None:
                current_cost = meta["current_cost"]
                # ✅ Fixed: Calculate total_cost dynamically, don't read from metadata
                # (it was never stored there anyway)

        # Scenario C: Fallback - calculate total cost from all assistant messages
        if total_session_cost is None:
            # ✅ Use common utility function
            total_session_cost = calculate_total_cost_from_messages(msgs_to_count)

        return {
            "total": int(total_tokens),
            "by_role": tokens_by_role,
            "system_prompt": system_prompt_tokens,
            "tools_definition": tools_definition_tokens,
            "tools_count": len(tools) if tools else 0,
            "message_counts": message_counts,
            "max_tokens": max_tokens,
            "remaining": remaining,
            "usage_percent": usage_percent,
            "warning_90": usage_percent >= 90,
            "critical_95": usage_percent >= 95,
            "current_cost": current_cost,
            "total_cost": total_session_cost,
            "error": None,
        }

    except Exception as e:
        logger.debug(f"Token counting skipped for unsupported model: {e}")
        return {
            "total": 0,
            "by_role": {},
            "system_prompt": 0,
            "tools_definition": 0,
            "message_counts": {},
            "max_tokens": 0,
            "remaining": 0,
            "usage_percent": 0,
            "warning_90": False,
            "critical_95": False,
            "current_cost": 0,
            "error": str(e),
        }


def format_token_visualization(
    token_info: dict, bar_width: int = 50
) -> tuple[str, str, str]:
    """Format token distribution bar with per-role breakdown and warning if usage >= 90%."""
    total_tokens = token_info.get("total", 0)
    by_role = token_info.get("by_role", {})
    system_prompt_tokens = token_info.get("system_prompt", 0)
    tools_definition_tokens = token_info.get("tools_definition", 0)
    message_counts = token_info.get("message_counts", {})
    max_tokens = token_info.get("max_tokens", 0)
    remaining_tokens = token_info.get("remaining", 0)
    usage_percent = token_info.get("usage_percent", 0)
    warning_90 = token_info.get("warning_90", False)
    critical_95 = token_info.get("critical_95", False)

    if total_tokens == 0:
        return "💾 Token Distribution: [No tokens]", "", ""

    # Color codes for different roles (ANSI 256-color)
    role_colors = {
        "system_prompt": "\033[38;5;103m",  # Dusty blue (for system prompt)
        "tools_definition": "\033[38;5;37m",  # Cyan (for tools definition)
        "system": "\033[38;5;103m",  # Dusty blue (kept for other system msgs)
        "user": "\033[38;5;108m",  # Dusty green
        "assistant": "\033[38;5;137m",  # Dusty brown
        "tool": "\033[38;5;94m",  # Dark magenta/wine
    }
    reset_color = "\033[0m"

    # Build the stacked bar showing used vs remaining
    used_ratio = total_tokens / max_tokens if max_tokens > 0 else 0
    used_width = max(1, round(used_ratio * bar_width))
    remaining_width = bar_width - used_width

    # Define segment order: System Prompt -> Tools Def -> System(rest) -> User -> Assistant -> Tool
    segments_data = [
        ("system_prompt", system_prompt_tokens),
        ("tools_definition", tools_definition_tokens),
        ("system", by_role.get("system", 0)),
        ("user", by_role.get("user", 0)),
        ("assistant", by_role.get("assistant", 0)),
        ("tool", by_role.get("tool", 0)),
    ]

    used_bar_segments = []
    # Accumulate used segments
    for name, count in segments_data:
        if count == 0:
            continue
        segment_width = (
            max(1, round((count / total_tokens) * used_width))
            if total_tokens > 0
            else 0
        )

        color = role_colors.get(name, "")
        segment = color + "█" * segment_width + reset_color
        used_bar_segments.append(segment)

    # Combine used (colored) + remaining (gray)
    bar = "".join(used_bar_segments)
    if remaining_width > 0:
        bar += "░" * remaining_width

    # Format bar line: [bar] Used: X (Y%) | Max: Z | Remaining: W
    bar_line = (
        f"💾 Token Distribution: [{bar}] "
        f"Used: {total_tokens:,} ({usage_percent}%) | "
        f"Max: {max_tokens:,} | "
        f"Remaining: {remaining_tokens:,}"
    )

    # Build summary line with detailed information
    summary_parts = []

    # helper for summary item
    def add_summary_item(name, label, count, msg_count=None):
        if count == 0:
            return
        percentage = (count / total_tokens) * 100
        color = role_colors.get(name, "")
        count_part = f"msg{'s' if msg_count != 1 else ''}"
        msg_info = f", {msg_count} {count_part}" if msg_count is not None else ""

        summary_parts.append(
            f"{color}{label}{reset_color}: {count} ({percentage:.0f}%{msg_info})"
        )

    add_summary_item("system_prompt", "SysPrompt", system_prompt_tokens)
    add_summary_item("tools_definition", "ToolsDef", tools_definition_tokens)
    add_summary_item(
        "system", "System", by_role.get("system", 0), message_counts.get("system", 0)
    )
    add_summary_item(
        "user", "User", by_role.get("user", 0), message_counts.get("user", 0)
    )
    add_summary_item(
        "assistant",
        "Assistant",
        by_role.get("assistant", 0),
        message_counts.get("assistant", 0),
    )
    add_summary_item(
        "tool", "Tool", by_role.get("tool", 0), message_counts.get("tool", 0)
    )

    summary_line = "📊 " + " | ".join(summary_parts)
    # Add current cost to summary line
    current_cost = token_info.get("current_cost", 0)
    summary_line += f" | Cost: ${current_cost:.4f}"

    # Add total cost to summary line if available
    if (total_cost := token_info.get("total_cost")) is not None:
        summary_line += f" | Total: ${total_cost:.4f}"

    # Generate warning line if usage >= 90%
    warning_line = ""
    if warning_90:
        warning_icon = "🔴 CRITICAL" if critical_95 else "⚠️ WARNING"
        warning_line = (
            f"{warning_icon}: Context usage at {usage_percent}% "
            f"({total_tokens:,} / {max_tokens:,} tokens). "
            f"Only {remaining_tokens:,} tokens remaining!"
        )

    return bar_line, summary_line, warning_line
