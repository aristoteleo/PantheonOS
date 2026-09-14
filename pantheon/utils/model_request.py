"""Bound a provider call by useful stream progress as well as elapsed time."""

import asyncio
import math
from contextlib import suppress
from typing import Awaitable, Callable, TypeVar

from .misc import run_func

T = TypeVar("T")


class ModelRequestTimeout(TimeoutError):
    """An exhausted model attempt; move to the next model instead of retrying it."""


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
