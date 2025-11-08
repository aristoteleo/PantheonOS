import json
import re
import time
import warnings
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


async def acompletion_zhipu(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    retry_times: int = 3,
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
    model_params: dict | None = None,
):
    """
    Zhipu AI (智谱AI) completion using OpenAI-compatible API format

    Zhipu AI provides OpenAI-compatible endpoints, so we can use the OpenAI client
    with their custom base_url and API format.
    """
    import os

    from openai import NOT_GIVEN, APIConnectionError, AsyncOpenAI

    # Get API key from environment (ZAI_API_KEY for Zhipu AI)
    api_key = os.environ.get("ZAI_API_KEY")
    if not api_key:
        raise ValueError(
            "ZAI_API_KEY environment variable not set (required for Zhipu AI)"
        )

    # Create client pointing to Zhipu AI endpoint
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    _tools = tools or NOT_GIVEN

    while retry_times > 0:
        try:
            # Use standard streaming API (Zhipu AI doesn't support beta API)
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_tools,
                response_format=response_format,
                stream=True,
                **model_params,
            )

            collected_messages = []
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]
                    if hasattr(choice, "delta"):
                        delta = choice.delta
                        if delta.content:
                            collected_messages.append(delta.content)
                        if process_chunk:
                            await run_func(process_chunk, delta.model_dump())
                    if (
                        hasattr(choice, "finish_reason")
                        and choice.finish_reason == "stop"
                    ):
                        if process_chunk:
                            await run_func(process_chunk, {"stop": True})

            # Construct final message in pantheon-compatible format
            class ZhipuMessage:
                def __init__(self, content, role="assistant", tool_calls=None):
                    self.content = content
                    self.role = role
                    self.tool_calls = tool_calls

                def model_dump(self):
                    return {
                        "content": self.content,
                        "role": self.role,
                        "tool_calls": self.tool_calls,
                    }

            class ZhipuChoice:
                def __init__(self, message):
                    self.message = message

            class ZhipuResponse:
                def __init__(self, choices):
                    self.choices = choices

            final_message = ZhipuResponse(
                [ZhipuChoice(ZhipuMessage("".join(collected_messages)))]
            )

            break
        except APIConnectionError as e:
            logger.error(f"Zhipu AI API connection error: {e}")
            retry_times -= 1
        except Exception as e:
            logger.error(f"Zhipu AI API error: {e}")
            retry_times -= 1

    return final_message


def import_litellm():
    warnings.filterwarnings("ignore")
    import litellm

    litellm.suppress_debug_info = True
    return litellm


async def acompletion_litellm(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    base_url: str | None = None,
    model_params: dict | None = None,
):
    litellm = import_litellm()

    # Prepare arguments for litellm
    kwargs = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "response_format": response_format,
        "stream": True,
    }
    if model_params:
        kwargs.update(**model_params)

    # Add base_url if provided (litellm uses api_base parameter)
    if base_url:
        kwargs["api_base"] = base_url

    response = await litellm.acompletion(**kwargs)
    async for chunk in response:
        if (
            process_chunk
            and hasattr(chunk, "choices")
            and chunk.choices
            and len(chunk.choices) > 0
        ):
            choice = chunk.choices[0]
            if hasattr(choice, "delta"):
                await run_func(process_chunk, choice.delta.model_dump())
            if hasattr(choice, "finish_reason") and choice.finish_reason == "stop":
                await run_func(process_chunk, {"stop": True})
    complete_resp = litellm.stream_chunk_builder(response.chunks)
    return complete_resp


def remove_parsed(messages: list[dict]) -> list[dict]:
    for message in messages:
        if "parsed" in message:
            del message["parsed"]
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
        # Metadata fields
        "_metadata",
    }

    for msg in messages:
        for field in UI_ONLY_FIELDS:
            if field in msg:
                del msg[field]

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
    messages = remove_raw_content(messages)
    messages = filter_tool_messages(messages)
    messages = remove_extra_fields(messages)
    messages = remove_ui_fields(messages)  # Remove UI-only fields
    return messages


def process_messages_for_store(messages: list[dict]) -> list[dict]:
    messages = deepcopy(messages)
    messages = remove_parsed(messages)
    messages = remove_unjsonifiable_raw_content(messages)
    return messages


def process_messages_for_hook_func(messages: list[dict]) -> list[dict]:
    messages = deepcopy(messages)
    messages = remove_unjsonifiable_raw_content(messages)
    return messages


async def openai_embedding(
    texts: list[str], model: str = "text-embedding-3-large"
) -> list[list[float]]:
    import os

    import openai

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE")

    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
    resp = await client.embeddings.create(input=texts, model=model)
    return [d.embedding for d in resp.data]


def remove_hidden_fields(content: dict) -> dict:
    content = deepcopy(content)
    if "hidden_to_model" in content:
        hidden_fields = content.pop("hidden_to_model")
        for field in hidden_fields:
            if field in content:
                content.pop(field)
    return content


def process_tool_result(result: dict) -> dict:
    result = deepcopy(result)
    if isinstance(result, dict):
        result = remove_hidden_fields(result)
        result = filter_base64_in_tool_result(result)
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


def count_tokens_in_messages(messages: list[dict], model: str) -> dict:
    """Count tokens with per-role breakdown and context usage metrics."""
    try:
        from litellm.utils import token_counter, get_model_info

        total_tokens = 0
        tokens_by_role = {}
        message_counts = {}

        # Count tokens per message
        for msg in messages:
            role = msg.get("role", "unknown")
            msg_tokens = token_counter(model=model, messages=[msg])

            total_tokens += msg_tokens
            tokens_by_role[role] = tokens_by_role.get(role, 0) + msg_tokens
            message_counts[role] = message_counts.get(role, 0) + 1

        model_info = get_model_info(model)

        # Calculate usage metrics
        max_input_tokens = model_info.get("max_input_tokens", 150000) or 150000
        max_output_tokens = model_info.get("max_output_tokens", 150000) or 150000
        max_tokens = max_input_tokens + max_output_tokens
        remaining = max(0, max_tokens - total_tokens)
        usage_percent = (
            round((total_tokens / max_tokens * 100), 1) if max_tokens > 0 else 0
        )
        # calculate cost for the current model
        cost_per_token = model_info.get("input_cost_per_token", 0) or 0
        current_cost = round(total_tokens * cost_per_token, 4)
        return {
            "total": int(total_tokens),
            "by_role": tokens_by_role,
            "message_counts": message_counts,
            "max_tokens": max_tokens,
            "remaining": remaining,
            "usage_percent": usage_percent,
            "warning_90": usage_percent >= 90,
            "critical_95": usage_percent >= 95,
            "current_cost": current_cost,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        return {
            "total": 0,
            "by_role": {},
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
        "system": "\033[38;5;103m",  # Dusty blue
        "user": "\033[38;5;108m",  # Dusty green
        "assistant": "\033[38;5;137m",  # Dusty brown
        "tool": "\033[38;5;94m",  # Dark magenta/wine
    }
    reset_color = "\033[0m"

    # Build the stacked bar showing used vs remaining
    used_ratio = total_tokens / max_tokens if max_tokens > 0 else 0
    used_width = max(1, round(used_ratio * bar_width))
    remaining_width = bar_width - used_width

    # Build segments for used tokens (colored by role)
    role_order = ["system", "user", "assistant", "tool"]
    used_bar_segments = []
    for role in role_order:
        if role not in by_role or by_role[role] == 0:
            continue

        tokens = by_role[role]
        segment_width = (
            max(1, round((tokens / total_tokens) * used_width))
            if total_tokens > 0
            else 0
        )

        color = role_colors.get(role, "")
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
    for role in role_order:
        if role not in by_role or by_role[role] == 0:
            continue

        tokens = by_role[role]
        percentage = (tokens / total_tokens) * 100
        msg_count = message_counts.get(role, 0)
        color = role_colors.get(role, "")

        # Format: "Role: X tokens (Y%, Z msgs)"
        role_summary = (
            f"{color}{role}{reset_color}: {tokens} tokens ({percentage:.0f}%, "
            f"{msg_count} msg{'s' if msg_count != 1 else ''})"
        )
        summary_parts.append(role_summary)

    summary_line = "   " + " | ".join(summary_parts)
    # Add current cost to summary line
    current_cost = token_info.get("current_cost", 0)
    summary_line += f" | Cost: ${current_cost:.4f}"

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
