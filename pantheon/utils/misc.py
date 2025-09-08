import inspect
import json
from typing import Callable, List

from funcdesc.desc import NotDef
from funcdesc.pydantic import Description, desc_to_pydantic
from openai import pydantic_function_tool
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel


async def run_func(func: Callable, *args, **kwargs):
    # Import here to avoid circular imports
    from magique.worker import ReverseCallable
    
    # Check if it's a regular coroutine function or ReverseCallable
    if inspect.iscoroutinefunction(func) or isinstance(func, ReverseCallable):
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
    # remove skip_params from desc.inputs
    new_inputs = []
    for arg in desc.inputs:
        if arg.name in skip_params:
            continue
        new_inputs.append(arg)
    desc.inputs = new_inputs

    pydantic_model = desc_to_pydantic(desc)["inputs"]
    oai_func_dict = pydantic_function_tool(pydantic_model)
    oai_params = oai_func_dict["function"]["parameters"]["properties"]

    parameters = {}
    required = []

    for arg in desc.inputs:
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
            func_name = call.get('function', {}).get('name')
            func_args = call.get('function', {}).get('arguments')
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
        func_name = message.get('tool_name')
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


