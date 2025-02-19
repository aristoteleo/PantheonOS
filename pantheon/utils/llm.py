from copy import deepcopy
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
        ) -> dict:
    from openai import AsyncOpenAI, NOT_GIVEN, APIConnectionError

    client = AsyncOpenAI()
    chunks = []
    _tools = tools or NOT_GIVEN
    _pcall = (tools is not None) or NOT_GIVEN
    stream_manager = client.beta.chat.completions.stream(
        model=model,
        messages=messages,
        tools=_tools,
        parallel_tool_calls=_pcall,
        response_format=response_format or {"type": "text"},
    )

    while retry_times > 0:
        try:
            async with stream_manager as stream:
                async for event in stream:
                    if event.type == "chunk":
                        chunk = event.chunk
                        chunks.append(chunk.model_dump())
                        if process_chunk:
                            delta = chunk.choices[0].delta.model_dump()
                            await run_func(process_chunk, delta)
                            if chunk.choices[0].finish_reason == "stop":
                                await run_func(process_chunk, {"stop": True})
                final_message = await stream.get_final_completion()
                break
        except APIConnectionError as e:
            logger.error(f"OpenAI API connection error: {e}")
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
        ) -> dict:
    litellm = import_litellm()
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        tools=tools,
        response_format=response_format,
        stream=True,
    )
    async for chunk in response:
        if process_chunk:
            choice = chunk.choices[0]
            await run_func(process_chunk, choice.delta.model_dump())
            if choice.finish_reason == "stop":
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


def process_messages(messages: list[dict], model: str) -> list[dict]:
    messages = deepcopy(messages)
    messages = remove_parsed(messages)
    if model.startswith("deepseek/"):
        messages = convert_tool_message(messages)
    return messages


def process_messages_for_save(messages: list[dict]) -> list[dict]:
    return remove_parsed(messages)
