"""
Codex adapter — calls OpenAI ChatGPT backend-api via OAuth tokens.

Uses the Responses API format at https://chatgpt.com/backend-api/codex/responses.
Requires OAuth tokens from CodexOAuthManager.
"""

import json
import time
import platform
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

CODEX_BASE_URL = "https://chatgpt.com/backend-api"


def _build_headers(access_token: str, account_id: str | None = None) -> dict:
    """Build request headers for Codex backend-api."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
        "originator": "pi",
        "User-Agent": f"pi ({platform.system()} {platform.release()}; {platform.machine()})",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _convert_messages_to_responses_input(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Convert Chat Completions messages to Responses API input format."""
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

    return instructions, input_items


def _convert_tools(tools: list[dict] | None) -> list[dict] | None:
    """Convert Chat Completions tool format to Responses API format."""
    if not tools:
        return None
    converted = []
    for tool in tools:
        func = tool.get("function", {})
        item = {"type": "function", "name": func.get("name", "")}
        if "description" in func:
            item["description"] = func["description"]
        if "parameters" in func:
            item["parameters"] = func["parameters"]
        if "strict" in func:
            item["strict"] = func["strict"]
        converted.append(item)
    return converted


class CodexAdapter(BaseAdapter):
    """Adapter for OpenAI Codex via ChatGPT backend-api OAuth."""

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
        api_key: str | None = None,  # This is the OAuth access_token
        num_retries: int = 3,
        **kwargs,
    ):
        """Call Codex backend-api with Responses API format.

        api_key should be the OAuth access_token.
        kwargs may contain 'account_id' for the chatgpt-account-id header.
        """
        import httpx

        access_token = api_key
        if not access_token:
            raise APIConnectionError("No Codex OAuth access token provided")

        account_id = kwargs.pop("account_id", None)
        headers = _build_headers(access_token, account_id)
        endpoint = f"{base_url or CODEX_BASE_URL}/codex/responses"

        # Convert messages
        instructions, input_items = _convert_messages_to_responses_input(messages)
        converted_tools = _convert_tools(tools)

        # Build request body
        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "instructions": instructions or "You are a helpful assistant.",
            "stream": True,
            "store": False,
            "parallel_tool_calls": True,
            "include": ["reasoning.encrypted_content"],
        }
        if converted_tools:
            body["tools"] = converted_tools
        if response_format:
            body["text"] = response_format

        # Map model_params (Codex backend-api has limited parameter support)
        kwargs.pop("max_tokens", None)
        kwargs.pop("max_completion_tokens", None)
        kwargs.pop("max_output_tokens", None)
        reasoning_effort = kwargs.pop("reasoning_effort", None)
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}

        # Stream response
        text_parts = []
        tool_calls_by_id = {}
        _item_to_call = {}
        usage_dict = {}
        cost = 0.0

        try:
            stream_start_time = time.time()
            first_chunk_time = None

            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", endpoint, headers=headers, json=body) as resp:
                    if resp.status_code == 401:
                        raise APIConnectionError(
                            "[OAUTH_REQUIRED] Codex OAuth token expired. "
                            "Please re-login in Settings → API Keys → OAuth."
                        )
                    elif resp.status_code == 429:
                        raise RateLimitError(f"Codex rate limited (429)")
                    elif resp.status_code >= 500:
                        raise ServiceUnavailableError(f"Codex server error ({resp.status_code})")
                    elif resp.status_code >= 400:
                        body_text = ""
                        async for chunk in resp.aiter_text():
                            body_text += chunk
                        raise APIConnectionError(f"Codex error {resp.status_code}: {body_text[:300]}")

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        event_type = event.get("type", "")

                        if event_type == "response.output_text.delta":
                            delta_text = event.get("delta", "")
                            text_parts.append(delta_text)
                            if first_chunk_time is None:
                                first_chunk_time = time.time()
                                ttfb = first_chunk_time - stream_start_time
                                logger.info(f"⚡ First chunk received: {ttfb:.3f}s (TTFB) [{model}]")
                            if process_chunk:
                                await run_func(process_chunk, {"content": delta_text, "role": "assistant"})

                        elif event_type == "response.output_item.added":
                            item = event.get("item", {})
                            if item.get("type") == "function_call":
                                call_id = item.get("call_id", "")
                                item_id = item.get("id", "")
                                _item_to_call[item_id] = call_id
                                tool_calls_by_id[call_id] = {
                                    "name": item.get("name", ""),
                                    "arguments": "",
                                }

                        elif event_type == "response.function_call_arguments.done":
                            item_id = event.get("item_id", "")
                            call_id = _item_to_call.get(item_id, "")
                            if call_id and call_id in tool_calls_by_id:
                                tool_calls_by_id[call_id]["arguments"] = event.get("arguments", "")
                                if event.get("name"):
                                    tool_calls_by_id[call_id]["name"] = event["name"]

                        elif event_type == "response.completed":
                            if process_chunk:
                                await run_func(process_chunk, {"stop": True})
                            # Extract usage
                            resp_obj = event.get("response", {})
                            usage = resp_obj.get("usage", {})
                            if usage:
                                input_tokens = usage.get("input_tokens", 0)
                                output_tokens = usage.get("output_tokens", 0)
                                usage_dict = {
                                    "prompt_tokens": input_tokens,
                                    "completion_tokens": output_tokens,
                                    "total_tokens": input_tokens + output_tokens,
                                }

                        elif event_type == "response.failed":
                            error_info = event.get("response", {}).get("error", {})
                            raise RuntimeError(f"Codex call failed: {error_info}")

            total_time = time.time() - stream_start_time
            logger.info(f"✅ Codex stream completed: {total_time:.3f}s [{model}]")

        except (APIConnectionError, RateLimitError, ServiceUnavailableError):
            raise
        except Exception as e:
            err_str = str(e).lower()
            if "401" in err_str or "unauthorized" in err_str:
                raise APIConnectionError(f"Codex OAuth token invalid: {e}") from e
            elif "429" in err_str or "rate" in err_str:
                raise RateLimitError(str(e)) from e
            raise

        # Build output message (same format as acompletion_responses in llm.py)
        aggregated_text = "".join(text_parts) if text_parts else None
        final_tool_calls = None
        if tool_calls_by_id:
            final_tool_calls = [
                {"id": cid, "type": "function", "function": {"name": info["name"], "arguments": info["arguments"]}}
                for cid, info in tool_calls_by_id.items()
            ]

        # Cost estimation from catalog
        try:
            from ..provider_registry import completion_cost as calc_cost
            cost = calc_cost(model=model, **usage_dict) if usage_dict else 0.0
        except Exception:
            pass

        return {
            "role": "assistant",
            "content": aggregated_text,
            "tool_calls": final_tool_calls,
            "_metadata": {"_debug_cost": cost, "_debug_usage": usage_dict},
        }
