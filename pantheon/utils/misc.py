import hashlib
import inspect
import json
from typing import Any, Callable, List

from funcdesc.desc import NotDef
from funcdesc.pydantic import Description, desc_to_pydantic
from openai import pydantic_function_tool
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel


def generate_service_id(id_hash: str) -> str:
    """
    Generate a service ID from id_hash using SHA256.

    This function matches the logic in NATSRemoteWorker (pantheon/remote/backend/nats.py)
    to ensure consistent service_id generation across different components.

    Args:
        id_hash: The hash string to generate service_id from

    Returns:
        A 64-character hexadecimal string (SHA256 hash)

    Example:
        >>> service_id = generate_service_id("my-stable-id-123")
        >>> len(service_id)
        64
        >>> all(c in '0123456789abcdef' for c in service_id)
        True
    """
    id_hash_str = str(id_hash)
    hash_obj = hashlib.sha256(id_hash_str.encode())
    return hash_obj.hexdigest()


async def call_endpoint_method(
    endpoint_service: Any, endpoint_method_name: str, **kwargs
) -> Any:
    """
    Call a method on endpoint service, supporting both Endpoint instances and remote services.

    This function handles both:
    - Direct Endpoint instances: calls method directly
    - Remote services: uses invoke() for RPC
    """
    # Import here to avoid circular imports
    from ..endpoint.core import Endpoint

    if isinstance(endpoint_service, Endpoint):
        # Direct Endpoint instance - call method directly
        method = getattr(endpoint_service, endpoint_method_name)
        return await method(**kwargs)
    else:
        # Remote service - use invoke() for RPC
        return await endpoint_service.invoke(endpoint_method_name, kwargs)


async def run_func(func: Callable, *args, **kwargs):
    # Check if it's a regular coroutine function
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    # Check if it's a callable object with async __call__ method
    elif hasattr(func, "__call__") and inspect.iscoroutinefunction(func.__call__):
        return await func(*args, **kwargs)
    # Regular synchronous function or callable
    else:
        return func(*args, **kwargs)


def desc_to_openai_dict(
    desc: Description,
    skip_params: List[str] = [],
    litellm_mode: bool = False,
) -> dict:
    # Filter inputs without modifying original desc.inputs
    filtered_inputs = [arg for arg in desc.inputs if arg.name not in skip_params]

    pydantic_model = desc_to_pydantic(desc)["inputs"]
    oai_func_dict = pydantic_function_tool(pydantic_model)
    oai_params = oai_func_dict["function"]["parameters"]["properties"]

    parameters = {}
    required = []

    for arg in filtered_inputs:
        pdict = {
            "description": arg.doc or "",
        }
        oai_pdict = oai_params[arg.name]
        if "type" in oai_pdict:
            pdict["type"] = oai_pdict["type"]
        if "items" in oai_pdict:
            pdict["items"] = oai_pdict["items"]
        if "anyOf" in oai_pdict:
            pdict["anyOf"] = oai_pdict["anyOf"]

        parameters[arg.name] = pdict

        if litellm_mode:
            if arg.default is NotDef:
                required.append(arg.name)
        else:
            required.append(arg.name)

    func_dict = {
        "type": "function",
        "function": {
            "name": desc.name,
            "description": desc.doc or "",
            "strict": not litellm_mode,
        },
    }
    if (not litellm_mode) or (len(parameters) > 0):
        func_dict["function"]["parameters"] = {
            "type": "object",
            "properties": parameters,
            "required": required,
            "additionalProperties": False,
        }
    return func_dict


def print_agent_message(
    agent_name: str,
    message: dict,
    console: Console | None = None,
    print_tool_call: bool = True,
    print_assistant_message: bool = True,
    print_tool_response: bool = True,
    print_markdown: bool = True,
    max_tool_call_message_length: int | None = 1000,
):
    if console is None:

        def _print(msg: str, title: str | None = None):
            print(msg)

        def _print_markdown(msg: str):
            print(msg)
    else:

        def _print(msg: str, title: str | None = None):
            if title is not None:
                panel = Panel(msg, title=title)
                console.print(panel)
            else:
                console.print(msg)

        def _print_markdown(msg: str):
            markdown = Markdown(msg)
            console.print(markdown)

    if print_tool_call and (tool_calls := message.get("tool_calls")):
        for call in tool_calls:
            func_name = call.get("function", {}).get("name")
            func_args = call.get("function", {}).get("arguments")
            _print(
                f"[bold]Agent [blue]{agent_name}[/blue] is using tool "
                f"[green]{func_name}[/green]:[/bold] "
                f"[yellow]{func_args}[/yellow]",
                f"Tool Call({func_name})",
            )
            _print("")
    if print_tool_response and message.get("role") == "tool":
        try:
            formatted_content = json.dumps(message["raw_content"], indent=2)
        except Exception:
            formatted_content = message.get("content")
        if max_tool_call_message_length is not None:
            formatted_content = formatted_content[:max_tool_call_message_length]
            formatted_content += "......"
        func_name = message.get("tool_name")
        _print(
            f"[bold]Agent [blue]{agent_name}[/blue] is using tool "
            f"[green]{func_name}[/green]:[/bold] "
            f"[yellow]{formatted_content}[/yellow]",
            f"Tool Response({func_name})",
        )
    elif print_assistant_message and message.get("role") == "assistant":
        if message.get("content"):
            if print_markdown:
                _print(f"[bold][blue]{agent_name}[/blue]:[/bold]")
                _print_markdown(message.get("content"))
            else:
                _print(
                    f"[bold]Agent [blue]{agent_name}[/blue]'s message:[/bold]\n"
                    f"[yellow]{message.get('content')}[/yellow]",
                    "Agent Message",
                )
