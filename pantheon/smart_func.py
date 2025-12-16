import asyncio
from typing import Callable
from functools import wraps, partial
import inspect

from funcdesc.parse import parse_func
from funcdesc.pydantic import value_to_field
from pydantic import create_model

from .agent import Agent


def _merge_args(desc, args: tuple, kwargs: dict) -> dict:
    merged = {}
    for i, val in enumerate(desc.inputs):
        if i < len(args):
            merged[val.name] = args[i]
    merged.update(kwargs)
    return merged


def smart_func(
        func: Callable | None = None,
        model: str = "low",
        tools: list[Callable] | None = None,
        use_memory: bool = False,
        memory: list[dict] | None = None,
    ):
    """Decorator for creating a smart function.
    Smart function is a function that uses LLM to perform tasks.

    Args:
        func: The function to be decorated.
        model: The model to use for the smart function.
        tools: The tools to use for the smart function.
        use_memory: Whether to use short term memory.
        memory: The short term memory to use.
    """
    if func is None:
        return partial(
            smart_func,
            model=model,
            tools=tools,
            use_memory=use_memory,
            memory=memory,
        )

    desc = parse_func(func)

    fields = {}
    for val in desc.inputs:
        field = value_to_field(val)
        fields[val.name] = (val.type or str, field)

    Input = create_model(
        "Input",
        **fields
    )

    val = desc.outputs[0]

    agent = Agent(
        name="smart_func",
        instructions=desc.doc,
        model=model,
        tools=tools,
        response_format=val.type,
        use_memory=use_memory,
        memory=memory,
    )

    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            merged = _merge_args(desc, args, kwargs)
            input = Input(**merged)
            response = await agent.run(input)
            return response.content
    else:
        @wraps(func)
        def wrapper(*args, **kwargs):
            merged = _merge_args(desc, args, kwargs)
            input = Input(**merged)
            response = asyncio.run(agent.run(input))
            return response.content

    return wrapper

