"""
Gemini CLI adapter — follows omicclaw's Gemini CLI REST implementation.

Uses the internal Code Assist generateContent endpoint with the same OAuth
JSON payload shape omicclaw uses: {"token":"...","projectId":"..."}.
"""

from __future__ import annotations

import json
import ssl
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib import request as urllib_request
from urllib.error import HTTPError

from ..log import logger
from ..misc import run_func
from .base import (
    APIConnectionError,
    BaseAdapter,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
)

_GOOGLE_GEMINI_CLI_BASE_URL = "https://cloudcode-pa.googleapis.com"
_GOOGLE_GEMINI_CLI_UNSUPPORTED_SCHEMA_KEYS = {
    "default",
    "patternProperties",
    "additionalProperties",
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "definitions",
    "examples",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "multipleOf",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
}


def _gemini_tool_call_id(name: str) -> str:
    """Generate a collision-safe Gemini tool-call ID with a non-numeric suffix."""
    safe_name = str(name or "unknown").strip() or "unknown"
    return f"gemini_{safe_name}_id{uuid.uuid4().hex[:12]}"


def _gemini_oauth_payload(api_key: str | None) -> dict[str, str] | None:
    """Extract OAuth token and project ID from a JSON API key payload."""
    text = str(api_key or "")
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.debug("Gemini OAuth payload JSON parse failed: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    token = str(payload.get("token") or "").strip()
    project_id = str(payload.get("projectId") or payload.get("project_id") or "").strip()
    if not token:
        return None
    return {"token": token, "projectId": project_id}


def _clean_schema_for_gemini_cli(schema: Any) -> Any:
    """Strip unsupported JSON Schema keys for the Gemini CLI REST API.

    Follows the same logic as omicclaw's ``_clean_schema_for_gemini_cli`` in
    ``omicverse.utils.agent_backend_gemini``, with an additional fix to
    preserve property names inside ``"properties"`` dicts (property names are
    user-defined parameter identifiers, not JSON Schema keywords, so they
    must never be stripped even when they collide with unsupported keys like
    ``"pattern"`` or ``"format"``).
    """
    if isinstance(schema, list):
        return [_clean_schema_for_gemini_cli(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in _GOOGLE_GEMINI_CLI_UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key in {"anyOf", "oneOf"} and isinstance(value, list):
            # Prefer array variant if present; otherwise drop the union
            preferred = None
            for item in value:
                if isinstance(item, dict) and item.get("type") == "array":
                    preferred = item
                    break
            if preferred is not None:
                merged = dict(preferred)
                if schema.get("description") and not merged.get("description"):
                    merged["description"] = schema.get("description")
                return _clean_schema_for_gemini_cli(merged)
            # Fallback: try to pick a non-null type from the union so the
            # property at least has a type after the anyOf is dropped.
            for item in value:
                if isinstance(item, dict) and item.get("type") and item.get("type") != "null":
                    non_null = dict(item)
                    if schema.get("description") and not non_null.get("description"):
                        non_null["description"] = schema.get("description")
                    return _clean_schema_for_gemini_cli(non_null)
            continue
        # Preserve user-defined property names inside "properties" dicts —
        # these are parameter identifiers, not JSON Schema keywords.
        if key == "properties" and isinstance(value, dict):
            cleaned_value = {
                prop_name: _clean_schema_for_gemini_cli(prop_schema)
                for prop_name, prop_schema in value.items()
            }
        else:
            cleaned_value = _clean_schema_for_gemini_cli(value)
        if key == "type" and isinstance(cleaned_value, str):
            cleaned_value = cleaned_value.upper()
        cleaned[key] = cleaned_value

    # Ensure required only references properties that actually exist.
    if isinstance(cleaned.get("properties"), dict) and isinstance(cleaned.get("required"), list):
        valid_props = set(cleaned["properties"].keys())
        cleaned["required"] = [
            r for r in cleaned["required"] if isinstance(r, str) and r in valid_props
        ]

    # Ensure every property in "properties" has a "type" field — the Gemini
    # API may ignore/reject typeless property definitions, causing them to
    # "disappear" and then fail required-field validation.
    if isinstance(cleaned.get("properties"), dict):
        for _pname, pschema in cleaned["properties"].items():
            if isinstance(pschema, dict) and "type" not in pschema:
                pschema["type"] = "STRING"

    return cleaned


def _messages_to_gemini_rest_contents(messages: List[Dict]) -> List[Dict[str, Any]]:
    """Convert OpenAI-style messages to Gemini REST JSON contents.

    Gemini requires that in a "function call turn", the number of
    ``functionResponse`` parts in the user's reply equals the number of
    ``functionCall`` parts in the preceding model message. OpenAI-style
    histories put each tool response in its own ``role:"tool"`` message, so
    two parallel tool calls produce two separate tool messages. We coalesce
    runs of consecutive ``role:"tool"`` messages into a single Gemini
    user content with N ``functionResponse`` parts to satisfy that
    invariant — otherwise the API rejects with HTTP 400 ``Please ensure
    that the number of function response parts is equal to the number of
    function call parts of the function call turn.``
    """
    contents: List[Dict[str, Any]] = []
    pending_tool_parts: List[Dict[str, Any]] = []

    def _flush_pending() -> None:
        if pending_tool_parts:
            contents.append({"role": "user", "parts": list(pending_tool_parts)})
            pending_tool_parts.clear()

    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            continue

        if role == "tool":
            # Accumulate — emit as a single user content once the next
            # non-tool message arrives (or at end of iteration).
            pending_tool_parts.append({
                "functionResponse": {
                    "name": message.get("name", "unknown"),
                    "response": _gemini_function_response_payload(message.get("content", "")),
                }
            })
            continue

        # About to emit a non-tool message — first close out any pending
        # parallel tool responses.
        _flush_pending()

        parts: List[Dict[str, Any]] = []
        content = message.get("content", "")
        if isinstance(content, str) and content:
            parts.append({"text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    parts.append({"text": block.get("text", "")})
                elif block.get("type") == "tool_result":
                    parts.append({
                        "functionResponse": {
                            "name": block.get("name", "unknown"),
                            "response": _gemini_function_response_payload(block.get("content", "")),
                        }
                    })
        elif isinstance(content, dict) and content:
            parts.append({"text": json.dumps(content, ensure_ascii=False)})

        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = dict(tool_call.get("function") or {})
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, ValueError, TypeError) as exc:
                        logger.debug("Gemini REST tool-call argument parse failed, wrapping raw: %s", exc)
                        arguments = {"raw": arguments}
                if not isinstance(arguments, dict):
                    arguments = {}
                parts.append({
                    "functionCall": {
                        "name": function.get("name", tool_call.get("name", "unknown")),
                        "args": arguments,
                    }
                })

        if parts:
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": parts,
            })

    _flush_pending()
    return contents


def _gemini_function_response_payload(result: Any) -> Dict[str, Any]:
    """Normalize a tool result into a Gemini-compatible function response dict."""
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return {"output": result}
    if isinstance(result, str):
        stripped = result.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"output": parsed}
        return {"output": result}
    return {"output": result}


def _gemini_rest_generation_config(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Build Gemini generationConfig from adapter kwargs."""
    config = {
        "temperature": kwargs.pop("temperature", 0),
        "maxOutputTokens": (
            kwargs.pop("max_tokens", None)
            or kwargs.pop("max_output_tokens", None)
            or kwargs.pop("max_completion_tokens", None)
        ),
    }
    return {key: value for key, value in config.items() if value is not None}


def _extract_gemini_text_and_tool_calls(payload: Dict[str, Any]):
    """Extract text, tool calls, raw_message, and stop reason from a Gemini REST response."""
    response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    candidates = response_payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        logger.warning("No Gemini candidates returned: %s", response_payload)
        return None, [], None, "end_turn"

    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    content = candidate.get("content")
    parts = list((content or {}).get("parts") or []) if isinstance(content, dict) else []
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    tool_call_idx = 0
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
        function_call = part.get("functionCall")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or "unknown").strip() or "unknown"
            args = function_call.get("args")
            if not isinstance(args, dict):
                args = {}
            # ``index`` MUST be set — ``stream_chunk_builder`` uses it to
            # distinguish concurrent tool calls. Without it, the builder
            # defaults every entry to 0 and concatenates their
            # ``function.arguments`` strings, producing invalid JSON like
            # ``{a:1}{b:2}`` that then fails to parse at call time.
            tool_calls.append({
                "index": tool_call_idx,
                "id": _gemini_tool_call_id(name),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            })
            tool_call_idx += 1

    stop_reason = "tool_use" if tool_calls else "end_turn"
    finish_reason = str(candidate.get("finishReason") or "").upper()
    if finish_reason == "MAX_TOKENS":
        stop_reason = "max_tokens"
    content_text = "\n".join(text_parts).strip() or None
    raw_message = {
        "role": "assistant",
        "content": content_text,
        "tool_calls": tool_calls,
    } if (content_text or tool_calls) else None
    return content_text, tool_calls, raw_message, stop_reason


class GeminiCliAdapter(BaseAdapter):
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
        oauth = _gemini_oauth_payload(api_key)
        if not oauth:
            raise APIConnectionError("Gemini CLI OAuth payload is missing bearer token")

        body: Dict[str, Any] = {
            "model": model,
            "request": {
                "contents": _messages_to_gemini_rest_contents(messages),
                "generationConfig": _gemini_rest_generation_config(dict(kwargs)),
            },
        }
        if oauth.get("projectId"):
            body["project"] = oauth["projectId"]

        # System instruction
        system_text = None
        for message in messages:
            if message.get("role") == "system":
                content = message.get("content")
                system_text = content if isinstance(content, str) else str(content)
                break
        if system_text:
            body["request"]["systemInstruction"] = {
                "role": "system",
                "parts": [{"text": system_text}],
            }

        # Tools / function declarations
        if tools:
            func_decls = []
            for tool in tools:
                func = tool.get("function", {}) or {}
                name = func.get("name", "")
                description = func.get("description", "")
                parameters = _clean_schema_for_gemini_cli(func.get("parameters", {}) or {})
                func_decls.append({
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                })
            body["request"]["tools"] = [{"functionDeclarations": func_decls}]

            # Debug: log function declarations to aid diagnosis of schema errors
            try:
                for fd in func_decls:
                    logger.debug(
                        "Gemini CLI functionDeclaration: name={} params={}",
                        fd.get("name"),
                        json.dumps(fd.get("parameters"), ensure_ascii=False)[:500],
                    )
            except Exception:
                pass

        url_base = str(base_url or _GOOGLE_GEMINI_CLI_BASE_URL).rstrip("/")
        url = f"{url_base}/v1internal:generateContent"
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {oauth['token']}",
            "Content-Type": "application/json",
            "User-Agent": "GeminiCLI/v23.5.0 (darwin; arm64) google-api-nodejs-client/9.15.1",
            "x-goog-api-client": "gl-python/omicverse",
            "Accept": "application/json",
        }
        req = urllib_request.Request(url, data=data, headers=headers, method="POST")
        ctx = ssl.create_default_context()

        try:
            started = time.time()
            with urllib_request.urlopen(req, context=ctx, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - started
            logger.info(f"✅ Gemini CLI request completed: {elapsed:.3f}s [{model}]")
        except HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                body_text = ""
            detail = body_text[:2000] if body_text else str(exc)

            # Log the failed request tools for diagnosis
            if "required fields" in detail.lower() or "schema" in detail.lower():
                logger.error(
                    "Gemini CLI schema error. functionDeclarations sent:\n{}",
                    json.dumps(
                        body.get("request", {}).get("tools", []),
                        indent=2, ensure_ascii=False,
                    )[:5000],
                )

            if exc.code == 429:
                raise RateLimitError(f"Gemini CLI generateContent failed: HTTP {exc.code}: {detail}") from exc
            if exc.code >= 500:
                raise ServiceUnavailableError(f"Gemini CLI generateContent failed: HTTP {exc.code}: {detail}") from exc
            raise APIConnectionError(f"Gemini CLI generateContent failed: HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            error_str = str(exc).lower()
            if "429" in error_str or "rate" in error_str:
                raise RateLimitError(str(exc)) from exc
            if "503" in error_str or "unavailable" in error_str:
                raise ServiceUnavailableError(str(exc)) from exc
            if "500" in error_str or "internal" in error_str:
                raise InternalServerError(str(exc)) from exc
            raise APIConnectionError(str(exc)) from exc

        content_text, tool_calls, _raw_message, _stop_reason = _extract_gemini_text_and_tool_calls(payload)
        response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        usage_data = response_payload.get("usageMetadata") if isinstance(response_payload, dict) else {}
        if not isinstance(usage_data, dict):
            usage_data = {}
        prompt_tokens = int(usage_data.get("promptTokenCount") or 0)
        completion_tokens = int(usage_data.get("candidatesTokenCount") or 0)
        total_tokens = int(usage_data.get("totalTokenCount") or (prompt_tokens + completion_tokens))

        collected_chunks = []
        # Include model so stream_chunk_builder / completion_cost can look up catalog pricing
        wire_model = f"gemini-cli/{model}"
        if content_text:
            collected_chunks.append({
                "model": wire_model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": content_text},
                    "finish_reason": None,
                }]
            })
            if process_chunk:
                await run_func(process_chunk, {"role": "assistant", "content": content_text})
        if tool_calls:
            collected_chunks.append({
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "tool_calls": tool_calls},
                    "finish_reason": None,
                }]
            })
        collected_chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if process_chunk:
            await run_func(process_chunk, {"stop": True})
        collected_chunks.append({
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "choices": [],
        })
        return collected_chunks
