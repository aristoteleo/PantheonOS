import asyncio
import hashlib
import inspect
import json
from typing import Any, Callable, List

from funcdesc.desc import NotDef
from funcdesc.pydantic import Description, desc_to_pydantic
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
    from pantheon.endpoint.core import Endpoint

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
    # Regular synchronous function or callable - run in thread pool to avoid blocking event loop
    else:
        return await asyncio.to_thread(func, *args, **kwargs)


def _parse_docstring_args(docstring: str | None) -> dict[str, str]:
    """Parse docstring Args section into a dict using docstring_parser library.

    We use docstring_parser directly instead of funcdesc's update_by_docstring=True because:
    1. Unstable: funcdesc requires exact match between docstring params and function signature,
       raising IndexError if they differ (e.g., missing Args section).
    2. Redundancy: update_by_docstring keeps Args in desc.doc while also parsing to arg.doc,
       resulting in duplicate information sent to LLM.

    Args:
        docstring: The function docstring.

    Returns:
        dict mapping parameter names to their descriptions.
    """
    if not docstring:
        return {}

    try:
        import docstring_parser

        doc = docstring_parser.parse(docstring)
        return {p.arg_name: p.description or "" for p in doc.params}
    except Exception:
        # Fallback to empty dict if parsing fails
        return {}


def _strip_docstring_args(docstring: str | None) -> str:
    """Strip only Args section from docstring, keeping Returns/Raises/Examples/Note.

    Args section is removed since parameters are parsed separately into schema.
    Other sections (Returns, Raises, Examples, Note) are kept as they provide
    useful context for LLM to understand tool behavior.
    """
    if not docstring:
        return ""

    try:
        import docstring_parser
        from docstring_parser import DocstringStyle

        doc = docstring_parser.parse(docstring)
        parts = []

        # Add short and long descriptions
        if doc.short_description:
            parts.append(doc.short_description)
        if doc.long_description:
            parts.append(doc.long_description)

        # Reconstruct other sections (skip params/args)
        if doc.returns:
            parts.append(f"Returns:\n    {doc.returns.description}")
        if doc.raises:
            raises_text = "\n".join(
                f"    {r.type_name}: {r.description}" for r in doc.raises
            )
            parts.append(f"Raises:\n{raises_text}")
        if doc.examples:
            examples_text = "\n".join(
                e.description for e in doc.examples if e.description
            )
            if examples_text:
                parts.append(f"Examples:\n{examples_text}")

        # Check for Note in meta
        for meta in doc.meta:
            if hasattr(meta, "key") and meta.key and meta.key.lower() == "note":
                parts.append(f"Note:\n    {meta.description}")

        return "\n\n".join(parts) if parts else ""
    except Exception:
        # Fallback: return original if parsing fails
        return docstring.strip()


def desc_to_openai_dict(
    desc: Description,
    skip_params: List[str] = [],
    relaxed_schema: bool = False,
) -> dict:
    _relaxed = relaxed_schema

    # Filter inputs without modifying original desc.inputs
    filtered_inputs = [arg for arg in desc.inputs if arg.name not in skip_params]

    from openai import pydantic_function_tool

    pydantic_model = desc_to_pydantic(desc)["inputs"]
    oai_func_dict = pydantic_function_tool(pydantic_model)
    oai_params = oai_func_dict["function"]["parameters"]["properties"]

    # Parse docstring Args section to fill in missing arg.doc
    docstring_args = _parse_docstring_args(desc.doc)

    # Strip redundant sections from tool description
    tool_description = _strip_docstring_args(desc.doc)

    parameters = {}
    required = []

    for arg in filtered_inputs:
        # Use arg.doc if available, otherwise try parsed docstring
        arg_description = arg.doc or docstring_args.get(arg.name, "")

        pdict = {
            "description": arg_description,
        }
        oai_pdict = oai_params[arg.name]
        if "type" in oai_pdict:
            pdict["type"] = oai_pdict["type"]
        if "items" in oai_pdict:
            pdict["items"] = oai_pdict["items"]
        if "anyOf" in oai_pdict:
            # Gemini compatibility: Flatten "anyOf": [{"type": "array"}, {"type": "null"}]
            # to just "type": "array". OpenAI handles optional arrays fine without explicit null union.
            array_option = next(
                (item for item in oai_pdict["anyOf"] if item.get("type") == "array"),
                None,
            )
            if array_option:
                pdict["type"] = "array"
                pdict["items"] = array_option.get("items", {})
            else:
                pdict["anyOf"] = oai_pdict["anyOf"]

        parameters[arg.name] = pdict

        if _relaxed:
            if arg.default is NotDef:
                required.append(arg.name)
        else:
            required.append(arg.name)

    func_dict = {
        "type": "function",
        "function": {
            "name": desc.name,
            "description": tool_description,
            "strict": not _relaxed,
        },
    }
    if (not _relaxed) or (len(parameters) > 0):
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


def find_free_port(
    start_port: int = 3100,
    host: str = "127.0.0.1",
    max_attempts: int = 100,
) -> int:
    """Find a free port starting from start_port.

    Tries to bind to ports sequentially until an available one is found.

    Args:
        start_port: Port number to start searching from.
        host: Host address to bind to.
        max_attempts: Maximum number of ports to try.

    Returns:
        First available port number.

    Raises:
        RuntimeError: If no free port is found within the range.
    """
    import socket

    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return port
        except OSError:
            continue
    raise RuntimeError(
        f"No free port found in range {start_port}-{start_port + max_attempts}"
    )


def unwrap_single_layer(data: Any) -> Any:
    """Unwrap one layer of JSON strings in the data structure.
    
    This handles cases where JSON data is nested as strings.
    Only unwraps one layer to avoid excessive processing.
    
    Args:
        data: Data structure to unwrap
        
    Returns:
        Unwrapped data (one layer only)
        
    Examples:
        >>> unwrap_single_layer({"result": "[1, 2, 3]"})
        {"result": [1, 2, 3]}
        
        >>> unwrap_single_layer({"data": '{"key": "value"}'})
        {"data": {"key": "value"}}
    """
    def try_parse_json(s: str) -> Any:
        """Try to parse a string as JSON, return original if fails."""
        if not isinstance(s, str):
            return s
        
        stripped = s.strip()
        if len(stripped) <= 2:
            return s
        
        # Check if looks like JSON
        if not ((stripped.startswith('{') and stripped.endswith('}')) or
                (stripped.startswith('[') and stripped.endswith(']'))):
            return s
        
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return s
    
    if isinstance(data, dict):
        return {k: try_parse_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [try_parse_json(item) for item in data]
    else:
        return try_parse_json(data)
