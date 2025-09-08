from typing import TYPE_CHECKING, Any
import json

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.markdown import Markdown
from rich_pyfiglet import RichFiglet

if TYPE_CHECKING:
    from ..agent import Agent, RemoteAgent


async def print_agent(agent: "Agent | RemoteAgent", console: Console | None = None):
    from ..agent import RemoteAgent
    is_remote = isinstance(agent, RemoteAgent)
    if is_remote:
        await agent.fetch_info()
    if console is None:
        console = Console()
    def _print(msg: str):
        console.print(msg)

    _print(f"  - [blue]{agent.name}[/blue]")
    # print remote info
    if is_remote:
        _print("    - [green]Remote[/green]")
        _print(f"      - Server: {agent.server_host}:{agent.server_port}")
        _print(f"      - Service ID: {agent.service_id_or_name}")
    # print agent model
    _print("    - [green]Model:[/green]")
    for model in agent.models:
        _print(f"      - {model}")
    # print agent instructions
    _print(f"    - [green]Instructions:[/green] {agent.instructions}")
    # print agent tools
    if is_remote:
        function_names = agent.functions_names
        toolset_proxies_names = agent.toolset_proxies_names
    else:
        function_names = agent.functions.keys()
        toolset_proxies_names = agent.toolset_services.keys()
    if function_names:
        _print("    - [green]Tools:[/green]")
        for func_name in function_names:
            _print(f"      - {func_name}")
    if toolset_proxies_names:
        _print("    - [green]Remote ToolSets:[/green]")
        for proxy_name in toolset_proxies_names:
            _print(f"      - {proxy_name}")


def print_banner(console: Console, text: str = "PANTHEON"):
    """Print ASCII banner with gradient colors"""
    rich_fig = RichFiglet(
        text,
        font="ansi_regular",
        colors=["blue", "purple", "#FFC0CB"],
        horizontal=True,
    )
    console.print(rich_fig)


def _print_tool_call(console: Console, tool_name: str, args: dict = None):
    """Print tool call in Claude Code style with fancy boxes"""
    # Create the title with the tool name
    title = f"🔧 [bold cyan]{tool_name}[/bold cyan]"
    
    if not args:
        # Simple panel if no arguments
        panel = Panel(
            "[dim italic]No arguments[/dim italic]",
            title=title,
            border_style="cyan",
            padding=(0, 1)
        )
        console.print(panel)
        return
    
    # Create a table for displaying arguments
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="yellow", no_wrap=True)
    table.add_column("Value")
    
    # Process each argument
    for key, value in args.items():
        # Check if the value looks like code or a command
        if _is_code_or_command(key, value):
            # Determine the syntax highlighting language
            lang = _get_syntax_language(key, value)
            
            # Create syntax highlighted code block
            if isinstance(value, str):
                syntax = Syntax(
                    value,
                    lang,
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True
                )
                table.add_row(f"[bold]{key}:[/bold]", syntax)
            else:
                table.add_row(f"[bold]{key}:[/bold]", _format_value(value))
        else:
            # Regular value display
            table.add_row(f"[bold]{key}:[/bold]", _format_value(value))
    
    # Create the panel with the table
    panel = Panel(
        table,
        title=title,
        border_style="cyan",
        padding=(1, 1)
    )
    
    console.print(panel)


def _is_code_or_command(key: str, value: Any) -> bool:
    """Check if a value appears to be code or a command"""
    if not isinstance(value, str):
        return False
    
    # Common parameter names that contain code/commands
    code_keywords = [
        'code', 'command', 'script', 'query', 'sql', 
        'content', 'source', 'body', 'text', 'pattern',
        'old_string', 'new_string', 'prompt'
    ]
    
    # Check if key suggests code content
    key_lower = key.lower()
    if any(keyword in key_lower for keyword in code_keywords):
        return True
    
    # Check for common code patterns
    code_patterns = [
        '\n',  # Multi-line content
        '    ',  # Indentation
        '\t',  # Tabs
        'def ', 'class ', 'function ', 'const ', 'let ', 'var ',  # Code keywords
        'import ', 'from ', 'export ',  # Module keywords
        '#!/', '#include', '<?php',  # Shebang/headers
        '$(', '${',  # Shell variables
        'git ', 'npm ', 'pip ', 'python ', 'node ',  # Commands
    ]
    
    return any(pattern in value for pattern in code_patterns)


def _get_syntax_language(key: str, value: str) -> str:
    """Determine the appropriate syntax highlighting language"""
    key_lower = key.lower()
    
    # Check key for hints
    if 'python' in key_lower or 'py' in key_lower:
        return 'python'
    elif 'javascript' in key_lower or 'js' in key_lower:
        return 'javascript'
    elif 'typescript' in key_lower or 'ts' in key_lower:
        return 'typescript'
    elif 'sql' in key_lower:
        return 'sql'
    elif 'bash' in key_lower or 'shell' in key_lower or 'command' in key_lower:
        return 'bash'
    elif 'json' in key_lower:
        return 'json'
    elif 'yaml' in key_lower or 'yml' in key_lower:
        return 'yaml'
    elif 'html' in key_lower:
        return 'html'
    elif 'css' in key_lower:
        return 'css'
    
    # Check content for hints
    if value.startswith('#!/bin/bash') or value.startswith('#!/bin/sh'):
        return 'bash'
    elif 'import ' in value or 'from ' in value or 'def ' in value:
        return 'python'
    elif 'function ' in value or 'const ' in value or 'let ' in value:
        return 'javascript'
    elif 'SELECT ' in value.upper() or 'INSERT ' in value.upper():
        return 'sql'
    elif value.strip().startswith('{') or value.strip().startswith('['):
        return 'json'
    elif value.startswith('git ') or value.startswith('npm ') or value.startswith('pip '):
        return 'bash'
    
    # Default to text if no specific language detected
    return 'text'


def _format_value(value: Any) -> str:
    """Format non-code values for display"""
    if value is None:
        return "[dim italic]None[/dim italic]"
    elif isinstance(value, bool):
        return f"[green]{value}[/green]" if value else f"[red]{value}[/red]"
    elif isinstance(value, (int, float)):
        return f"[magenta]{value}[/magenta]"
    elif isinstance(value, dict):
        # Format dict as JSON-like structure
        items = []
        for k, v in value.items():
            items.append(f"  {k}: {v}")
        return "{\n" + "\n".join(items) + "\n}"
    elif isinstance(value, list):
        # Format list
        if len(value) > 10:
            # Truncate long lists
            items = [str(item) for item in value[:10]]
            return f"[{', '.join(items)}, ... ({len(value)} items total)]"
        return f"[{', '.join(str(item) for item in value)}]"
    else:
        # Convert to string and apply some formatting
        str_value = str(value)
        if len(str_value) > 500:
            # Truncate very long strings
            return str_value[:500] + f"... [dim](truncated, {len(str_value)} chars total)[/dim]"
        return str_value


def _print_tool_response(
    console: Console,
    tool_name: str,
    content: Any,
    max_length: int | None = None
):
    """Print tool response with special field handling for result, stdout, stderr"""
    # Create the title with the tool name
    title = f"📥 [bold green]{tool_name}[/bold green] Response"
    
    if not content:
        # Simple panel if no content
        panel = Panel(
            "[dim italic]Empty response[/dim italic]",
            title=title,
            border_style="green",
            padding=(0, 1)
        )
        console.print(panel)
        return
    
    # Create a table for displaying response fields
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="yellow", no_wrap=True)
    table.add_column("Value")
    
    # Process content based on type
    if isinstance(content, dict):
        # Special fields that get special formatting
        special_fields = ['result', 'stdout', 'stderr']
        
        # First add special fields if they exist
        for field in special_fields:
            if field in content:
                value = content[field]
                # Check if value is not null/None/empty
                if value is not None and value != "" and value != []:
                    if field == 'stdout':
                        # Format stdout with green highlighting
                        formatted = Syntax(
                            str(value),
                            "text",
                            theme="monokai",
                            line_numbers=False,
                            word_wrap=True
                        )
                        table.add_row(
                            f"[bold green]{field}:[/bold green]",
                            Panel(formatted, border_style="green", padding=(0, 1))
                        )
                    elif field == 'stderr':
                        # Format stderr with red highlighting
                        formatted = Syntax(
                            str(value),
                            "text",
                            theme="monokai",
                            line_numbers=False,
                            word_wrap=True
                        )
                        table.add_row(
                            f"[bold red]{field}:[/bold red]",
                            Panel(formatted, border_style="red", padding=(0, 1))
                        )
                    elif field == 'result':
                        # Format result with syntax highlighting if it's code
                        if isinstance(value, str):
                            lang = _get_syntax_language('result', value)
                            formatted = Syntax(
                                value,
                                lang,
                                theme="monokai",
                                line_numbers=False,
                                word_wrap=True
                            )
                            table.add_row(
                                f"[bold cyan]{field}:[/bold cyan]",
                                Panel(formatted, border_style="cyan", padding=(0, 1))
                            )
                        else:
                            # JSON format for dict/list results
                            formatted_result = json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value)
                            formatted = Syntax(
                                formatted_result,
                                "json" if isinstance(value, (dict, list)) else "text",
                                theme="monokai",
                                line_numbers=False,
                                word_wrap=True
                            )
                            table.add_row(
                                f"[bold cyan]{field}:[/bold cyan]",
                                Panel(formatted, border_style="cyan", padding=(0, 1))
                            )
        
        # Then add other fields
        for key, value in content.items():
            if key not in special_fields:
                # Format the value
                if isinstance(value, str) and _is_code_or_command(key, value):
                    # Code-like content
                    lang = _get_syntax_language(key, value)
                    formatted_value = Syntax(
                        value,
                        lang,
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=True
                    )
                    table.add_row(f"[bold]{key}:[/bold]", formatted_value)
                elif isinstance(value, (dict, list)):
                    # JSON format for complex types
                    json_str = json.dumps(value, indent=2)
                    if max_length and len(json_str) > max_length:
                        json_str = json_str[:max_length] + "\n... (truncated)"
                    formatted_value = Syntax(
                        json_str,
                        "json",
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=True
                    )
                    table.add_row(f"[bold]{key}:[/bold]", formatted_value)
                else:
                    # Simple value
                    str_value = _format_value(value)
                    if max_length and len(str_value) > max_length:
                        str_value = str_value[:max_length] + "... (truncated)"
                    table.add_row(f"[bold]{key}:[/bold]", str_value)
    
    else:
        # Non-dict content - display as single value
        if isinstance(content, (list, tuple)):
            formatted_content = json.dumps(content, indent=2)
            lang = "json"
        else:
            formatted_content = str(content)
            lang = "text"
        
        if max_length and len(formatted_content) > max_length:
            formatted_content = formatted_content[:max_length] + "\n... (truncated)"
        
        syntax = Syntax(
            formatted_content,
            lang,
            theme="monokai",
            line_numbers=False,
            word_wrap=True
        )
        table.add_row("[bold]response:[/bold]", syntax)
    
    # Create the panel with the table
    panel = Panel(
        table,
        title=title,
        border_style="green",
        padding=(1, 1)
    )
    
    console.print(panel)


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
        console = Console()

    # Handle tool calls using the new _print_tool_call function
    if print_tool_call and (tool_calls := message.get("tool_calls")):
        # Print agent header
        console.print(f"\n[bold]🤖 Agent [blue]{agent_name}[/blue] is calling tools:[/bold]")
        
        for call in tool_calls:
            func_name = call.get('function', {}).get('name')
            func_args_str = call.get('function', {}).get('arguments', '{}')
            
            # Parse arguments if they're a JSON string
            try:
                if isinstance(func_args_str, str):
                    func_args = json.loads(func_args_str)
                else:
                    func_args = func_args_str
            except (json.JSONDecodeError, TypeError):
                # If parsing fails, treat as a simple string
                func_args = {"arguments": func_args_str}
            
            # Use the new _print_tool_call function
            _print_tool_call(console, func_name, func_args)
            console.print()  # Add spacing between tool calls
    
    # Handle tool responses
    if print_tool_response and message.get("role") == "tool":
        func_name = message.get('tool_name', 'Unknown Tool')
        
        # Get the raw content
        content = None
        if "raw_content" in message:
            content = message["raw_content"]
        elif "content" in message:
            content = message.get("content")
        
        # Use the new _print_tool_response function
        _print_tool_response(console, func_name, content, max_tool_call_message_length)
    
    # Handle assistant messages
    elif print_assistant_message and message.get("role") == "assistant":
        if content := message.get("content"):
            # Print agent header
            console.print(f"\n[bold]💬 [blue]{agent_name}[/blue]:[/bold]")
            
            if print_markdown:
                # Create a nice panel for markdown content
                markdown_content = Markdown(content)
                assistant_panel = Panel(
                    markdown_content,
                    border_style="blue",
                    padding=(1, 2)
                )
                console.print(assistant_panel)
            else:
                # Plain text in a panel
                assistant_panel = Panel(
                    content,
                    title="Assistant Message",
                    border_style="blue",
                    padding=(1, 2)
                )
                console.print(assistant_panel)

