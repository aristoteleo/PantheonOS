from copy import deepcopy
import json
import warnings
from typing import Any, Callable

from .misc import run_func
from .log import logger


async def acompletion_openai(
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        response_format: Any | None = None,
        process_chunk: Callable | None = None,
        retry_times: int = 3,
        base_url: str | None = None,
        ):
    from openai import AsyncOpenAI, NOT_GIVEN, APIConnectionError

    # Create client with custom base_url if provided
    if base_url:
        client = AsyncOpenAI(base_url=base_url)
    else:
        client = AsyncOpenAI()
    chunks = []
    _tools = tools or NOT_GIVEN
    _pcall = (tools is not None) or NOT_GIVEN
    
    # Use beta API only for OpenAI reasoning models (o1, o3 series)
    if model.startswith('o'):
        stream_manager = client.beta.chat.completions.stream(
            model=model,
            messages=messages,
            tools=_tools,
            response_format=response_format or {"type": "text"},
        )
    else:
        stream_manager = client.beta.chat.completions.stream(
            model=model,
            messages=messages,
            tools=_tools,
            parallel_tool_calls=_pcall,
            response_format=response_format or {"type": "text"},
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
                            logger.info(f"⚡ OpenAI first chunk received: {ttfb:.3f}s (TTFB)")

                        if process_chunk and hasattr(chunk, 'choices') and chunk.choices and len(chunk.choices) > 0:
                            choice = chunk.choices[0]
                            if hasattr(choice, 'delta'):
                                delta = choice.delta.model_dump()
                                chunk_count += 1
                                await run_func(process_chunk, delta)
                            if hasattr(choice, 'finish_reason') and choice.finish_reason == "stop":
                                await run_func(process_chunk, {"stop": True})

                final_message = await stream.get_final_completion()
                total_stream_time = time.time() - stream_start_time
                logger.info(f"✅ OpenAI stream completed: {total_stream_time:.3f}s, {chunk_count} chunks")
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
        ):
    """
    Zhipu AI (智谱AI) completion using OpenAI-compatible API format
    
    Zhipu AI provides OpenAI-compatible endpoints, so we can use the OpenAI client
    with their custom base_url and API format.
    """
    from openai import AsyncOpenAI, NOT_GIVEN, APIConnectionError
    import os

    # Get API key from environment (ZAI_API_KEY for Zhipu AI)
    api_key = os.environ.get('ZAI_API_KEY')
    if not api_key:
        raise ValueError("ZAI_API_KEY environment variable not set (required for Zhipu AI)")

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
                stream=True
            )
            
            collected_messages = []
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    choice = chunk.choices[0]
                    if hasattr(choice, 'delta'):
                        delta = choice.delta
                        if delta.content:
                            collected_messages.append(delta.content)
                        if process_chunk:
                            await run_func(process_chunk, delta.model_dump())
                    if hasattr(choice, 'finish_reason') and choice.finish_reason == "stop":
                        if process_chunk:
                            await run_func(process_chunk, {"stop": True})
            
            # Construct final message in pantheon-compatible format
            class ZhipuMessage:
                def __init__(self, content, role='assistant', tool_calls=None):
                    self.content = content
                    self.role = role 
                    self.tool_calls = tool_calls
                
                def model_dump(self):
                    return {
                        'content': self.content,
                        'role': self.role,
                        'tool_calls': self.tool_calls
                    }

            class ZhipuChoice:
                def __init__(self, message):
                    self.message = message

            class ZhipuResponse:
                def __init__(self, choices):
                    self.choices = choices

            final_message = ZhipuResponse([
                ZhipuChoice(ZhipuMessage(''.join(collected_messages)))
            ])
            
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
    
    # Add base_url if provided (litellm uses api_base parameter)
    if base_url:
        kwargs["api_base"] = base_url
    
    response = await litellm.acompletion(**kwargs)
    async for chunk in response:
        if process_chunk and hasattr(chunk, 'choices') and chunk.choices and len(chunk.choices) > 0:
            choice = chunk.choices[0]
            if hasattr(choice, 'delta'):
                await run_func(process_chunk, choice.delta.model_dump())
            if hasattr(choice, 'finish_reason') and choice.finish_reason == "stop":
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
    }

    for msg in messages:
        for field in UI_ONLY_FIELDS:
            if field in msg:
                del msg[field]

    return messages


def process_messages_for_model(messages: list[dict], model: str) -> list[dict]:
    messages = deepcopy(messages)
    messages = remove_parsed(messages)
    messages = remove_raw_content(messages)
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


async def openai_embedding(texts: list[str], model: str = "text-embedding-3-large") -> list[list[float]]:
    import openai
    import os

    # 从环境变量读取配置
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE")

    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url
    )
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


# ============ Timing Tracker ============

import time
from contextlib import asynccontextmanager


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
