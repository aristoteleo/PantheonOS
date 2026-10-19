"""Bound a provider call by useful stream progress as well as elapsed time."""

import asyncio
import math
from contextlib import suppress
from typing import Awaitable, Callable, TypeVar

from .misc import run_func

T = TypeVar("T")


class ModelRequestTimeout(TimeoutError):
    """An exhausted model attempt; move to the next model instead of retrying it."""


def is_model_timeout(error: Exception) -> bool:
    """Include SDK timeouts that can fire just before our watchdog deadline."""
    import httpx
    from openai import APITimeoutError

    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
            return True
        error = error.__cause__
    return False


def has_model_output(chunk: dict) -> bool:
    """Role-only deltas, usage and transport heartbeats are not progress."""
    if any(chunk.get(key) for key in ("content", "reasoning_content", "reasoning", "reasoning_details")):
        return True
    for call in chunk.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") or function.get("arguments"):
            return True
    return False


async def bounded_model_request(
    request: Callable[[Callable], Awaitable[T]],
    process_chunk: Callable | None,
    *,
    model: str,
    idle_timeout: float = 120,
    request_timeout: float = 600,
) -> T:
    """Cancel a stalled/overlong attempt, including an in-flight HTTP stream.

    HTTP read timeouts reset on SSE comments and other bytes. Only actual model
    output resets this idle deadline. A separate total deadline also bounds a
    provider that trickles tokens indefinitely. Neither deadline covers tools.
    """
    if any(not math.isfinite(value) or value <= 0 for value in (idle_timeout, request_timeout)):
        raise ValueError("Model request deadlines must be finite positive seconds")
    loop = asyncio.get_running_loop()
    started = last_output = loop.time()

    async def on_chunk(chunk):
        nonlocal last_output
        if has_model_output(chunk):
            last_output = loop.time()
        if process_chunk is not None:
            await run_func(process_chunk, chunk)

    task = asyncio.ensure_future(request(on_chunk))
    try:
        while not task.done():
            now = loop.time()
            remaining_idle = idle_timeout - (now - last_output)
            remaining_total = request_timeout - (now - started)
            remaining = min(remaining_idle, remaining_total)
            if remaining <= 0:
                reason = (
                    f"no model output for {idle_timeout:g}s"
                    if remaining_idle <= remaining_total
                    else f"request exceeded {request_timeout:g}s"
                )
                raise ModelRequestTimeout(f"{model}: {reason}")
            await asyncio.wait({task}, timeout=remaining)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


def is_transient_model_error(error: Exception) -> bool:
    """An overloaded, rate-limited or unreachable provider: the same request
    may succeed if sent again shortly."""
    if is_model_timeout(error):
        return False
    import re

    text = str(error).lower()
    # A rejected request (bad request, auth, permission, not found) fails the
    # same way again, whatever exception the adapter raised for it.
    if re.search(r"\b(?:http|status|error code)[ :=]*(?:400|401|402|403|404|422)\b", text):
        return False
    from pantheonos.utils.adapters.base import (
        APIConnectionError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
    )
    if isinstance(error, (ServiceUnavailableError, InternalServerError, RateLimitError, APIConnectionError)):
        return True
    return any(marker in text for marker in (
        "overloaded", "rate limit", "rate_limit", "capacity", "temporarily", "http 429", "http 503", "http 529",
    ))


async def retry_transient(
    request: Callable[[Callable | None], Awaitable[T]],
    process_chunk: Callable | None,
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.5,
    is_transient: Callable[[Exception], bool] = is_transient_model_error,
) -> T:
    """Send a direct provider request again after a transient failure, but only
    while nothing has been streamed: a request that produced output is never
    replayed. The Agent loop does not retry; Model Services never replays.
    """
    import random

    for attempt in range(max(1, attempts)):
        emitted = False

        async def on_chunk(chunk):
            nonlocal emitted
            if has_model_output(chunk):
                emitted = True
            if process_chunk is not None:
                await run_func(process_chunk, chunk)

        try:
            return await request(on_chunk)
        except Exception as error:
            if emitted or attempt + 1 >= attempts or not is_transient(error):
                raise
            delay = min(base_delay * (2 ** attempt), max_delay) * random.uniform(1 - jitter, 1 + jitter)
            from pantheonos.utils.log import logger
            logger.warning("Transient model error (attempt {}/{}): {}. Retrying in {:.1f}s",
                           attempt + 1, attempts, error, delay)
            await asyncio.sleep(max(delay, 0))
    raise AssertionError("unreachable")
