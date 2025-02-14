from typing import List, TYPE_CHECKING, Callable
import inspect

from funcdesc.desc import NotDef
from funcdesc.pydantic import desc_to_pydantic, Description
from openai import pydantic_function_tool
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown


if TYPE_CHECKING:
    from ..agent import Agent


async def run_func(func: Callable, *args, **kwargs):
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
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

    pydantic_model = desc_to_pydantic(desc)['inputs']
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
    ):
    if console is None:
        def _print(msg: str, title: str):
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
            _print(
                f"[bold]Agent [blue]{agent_name}[/blue] is using tool "
                f"[green]{call.get('function', {}).get('name')}[/green]:[/bold] "
                f"[yellow]{call.get('function', {}).get('arguments')}[/yellow]",
                "Tool Call"
            )
    if print_tool_response and message.get("role") == "tool":
        _print(
            f"[bold]Agent [blue]{agent_name}[/blue] is using tool "
            f"[green]{message.get('tool_name')}[/green]:[/bold] "
            f"[yellow]{message.get('content')}[/yellow]",
            "Tool Response"
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
                    "Agent Message"
                )


def print_agent(agent: "Agent", console: Console | None = None):
    if console is None:
        def _print(msg: str):
            print(msg)
    else:
        def _print(msg: str):
            console.print(msg)
    _print(f"  - [blue]{agent.name}[/blue]")
    # print agent instructions
    _print(f"    - [green]Instructions:[/green] {agent.instructions}")
    # print agent tools
    if agent.functions:
        _print("    - [green]Tools:[/green]")
        for func in agent.functions.values():
            _print(f"      - {func.__name__}")
    if agent.toolset_proxies:
        _print("    - [green]Remote ToolSets:[/green]")
        for proxy in agent.toolset_proxies.values():
            _print(f"      - {proxy.service_info.service_name}")
