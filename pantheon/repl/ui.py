from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from typing import List
import json

import asyncio
from datetime import datetime
from pathlib import Path

from .renderers import (
    DisplayMode,
    DisplayConfig,
    ToolCallRenderer,
    ToolResultRenderer,
)
from .utils import (
    get_separator,
    format_tool_name,
    format_relative_time,
    CLAUDE_BOX,
    OutputAdapter,
    get_detailed_token_stats,
    render_token_panel,
)

# Simple readline support for history
try:
    import readline
    import atexit

    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False


from rich_pyfiglet import RichFiglet


from rich.columns import Columns
from rich.table import Table
from rich import box


def print_banner(console: Console, text: str = "PANTHEON"):
    """Print ASCII banner with gradient colors"""
    rich_fig = RichFiglet(
        text,
        font="ansi_regular",
        colors=["blue", "purple", "#FFC0CB"],
        horizontal=True,
    )
    console.print(rich_fig)


def print_agent_message_modern_style(
    agent_name: str,
    message: dict,
    console: Console | None = None,
    show_tool_details: bool = False,
    max_content_length: int | None = 800,
):
    """Print agent message in modern Claude Code style with minimal visual noise"""

    if console is None:
        # Use simple Console if not provided (though verify calling code)
        console = Console()

    # Use output adapter if console is wrapper, or check context?
    # Actually print_agent_message_modern_style receives 'console', we assume it's correct.

    # Handle tool calls with minimal visual noise
    if tool_calls := message.get("tool_calls"):
        for call in tool_calls:
            tool_name = call.get("function", {}).get("name")
            if tool_name:
                console.print(f"[dim]▶ Using {tool_name}[/dim]")
                if show_tool_details:
                    args = call.get("function", {}).get("arguments", "")
                    if args:
                        console.print(
                            f"[dim]  {args[:200]}{'...' if len(args) > 200 else ''}[/dim]"
                        )

    # Handle tool responses with clean formatting
    elif message.get("role") == "tool":
        content = message.get("content", "")
        if max_content_length and len(content) > max_content_length:
            content = content[:max_content_length] + "..."

        # Try to format nicely based on content type
        try:
            import json

            parsed = json.loads(content)
            from rich.syntax import Syntax

            formatted = json.dumps(parsed, indent=2)
            console.print(
                Syntax(formatted, "json", theme="monokai", line_numbers=False)
            )
        except Exception:
            console.print(f"[dim]{content}[/dim]")

    # Handle assistant messages with markdown
    elif message.get("role") == "assistant" and message.get("content"):
        content = message.get("content")
        if content.strip():
            markdown = Markdown(content)
            console.print(markdown)


class ReplUI:
    """Presentation layer for REPL: printing, input, formatting."""

    def __init__(self):
        # Output adapter for patch_stdout compatibility
        self._output = OutputAdapter()

        self.input_panel = Panel(
            Text("Type your message here...", style="dim"),
            title="Input",
            border_style="bright_blue",
        )
        self._tools_executing = False
        self._processing_live: Live | None = None
        self._current_tool_name = None

        # Multi-agent display state
        self._current_agent_name: str | None = None
        self._last_printed_agent: str | None = None
        self._is_multi_agent: bool = False

        # Stats
        self.message_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.session_start = datetime.now()
        self.command_history = []

        # Display configuration and renderers
        self.display_config = DisplayConfig()
        self._init_renderers()

    @property
    def console(self) -> Console:
        """Context-aware console: uses patched stdout inside patch_stdout."""
        return self._output.console

    @property
    def output(self) -> OutputAdapter:
        """Get the output adapter."""
        return self._output

    def _init_renderers(self):
        """Initialize or reinitialize renderers with current console."""
        self.tool_call_renderer = ToolCallRenderer(
            self.output.console, self.display_config
        )
        self.tool_result_renderer = ToolResultRenderer(
            self.output.console, self.display_config
        )

    def set_display_mode(self, mode: DisplayMode):
        """Set display mode (compact/verbose)"""
        self.display_config.mode = mode
        # Recreate renderers with updated config
        self._init_renderers()

    def _format_tool_name(self, tool_name: str) -> tuple[str, str]:
        """Format tool name for display.

        Converts 'toolset__function' format to readable display.
        Returns (formatted_name, raw_function_name) tuple.
        """
        if "__" in tool_name:
            toolset, function = tool_name.split("__", 1)
            # toolset: dim grey, function: cyan
            formatted = f"[grey50]{toolset} ›[/grey50] [cyan]{function}[/cyan]"
            return formatted, function
        return f"[cyan]{tool_name}[/cyan]", tool_name

    def _should_display_bash_in_box(self, command: str) -> bool:
        """Determine if a bash command should be displayed in a code box instead of inline"""
        command = command.strip()
        command_parts = command.split()

        # Check command length (long commands should use code box)
        if len(command) > 80:
            return True

        # Check if command has many arguments (likely complex)
        if command_parts and len(command_parts) > 6:
            return True

        # Check for multi-line commands or chained commands
        if "\n" in command or "&&" in command or "||" in command or ";" in command:
            return True

        return False

    def _get_bash_command_title(self, command: str) -> str:
        """Get an appropriate title for a bash command based on the tool being used"""
        command = command.strip().lower()
        command_parts = command.split()

        if not command_parts:
            return "Run bash command"

        # Extract the actual command name (remove path if present)
        first_command = command_parts[0].split("/")[-1]

        # Check for pipeline-style commands
        if any(connector in command for connector in ["&&", "||", ";", "|"]):
            return "Run pipeline"

        # Check for common patterns
        if first_command in ["wget", "curl"]:
            return "Download files"
        elif first_command in ["gunzip", "tar", "unzip"]:
            return "Extract files"
        elif first_command in ["mkdir", "cp", "mv", "rm", "ln"]:
            return "File operations"
        elif first_command in ["grep", "awk", "sed", "sort", "uniq", "cut", "wc"]:
            return "Text processing"
        elif first_command in ["git"]:
            return "Git operation"
        elif first_command in ["docker", "docker-compose"]:
            return "Docker operation"
        elif first_command in ["pip", "pip3", "conda", "npm", "yarn"]:
            return "Package management"

        return "Run bash command"

    def _wrap_bash_command(self, command: str, max_width: int = 71) -> List[str]:
        """Wrap a bash command for display, breaking at appropriate points"""
        # If command already has newlines, split by those first
        if "\n" in command:
            lines = command.split("\n")
        else:
            lines = [command]

        wrapped_lines = []
        for line in lines:
            # If line is short enough, keep it as is
            if len(line) <= max_width:
                wrapped_lines.append(line)
                continue

            # Try to break at logical points
            # Priority: space before flags (-), pipes (|), && or ||, semicolons, spaces
            current_line = ""
            remaining = line

            while remaining:
                if len(remaining) <= max_width:
                    wrapped_lines.append(remaining)
                    break

                # Find best break point
                break_point = max_width

                # Look for good break points in priority order
                # 1. Before a flag (space followed by -)
                for i in range(max_width - 1, max(0, max_width - 20), -1):
                    if (
                        i < len(remaining) - 1
                        and remaining[i] == " "
                        and remaining[i + 1] == "-"
                    ):
                        break_point = i + 1
                        break

                # 2. Before pipes, redirects, or logical operators
                if break_point == max_width:
                    for pattern in [" | ", " > ", " >> ", " && ", " || ", " ; "]:
                        idx = remaining[:max_width].rfind(pattern)
                        if idx > 0:
                            break_point = idx + 1
                            break

                # 3. At any space
                if break_point == max_width:
                    space_idx = remaining[:max_width].rfind(" ")
                    if space_idx > 0:
                        break_point = space_idx + 1

                # 4. If no good break point, break at max_width
                wrapped_lines.append(remaining[:break_point].rstrip())
                remaining = remaining[break_point:].lstrip()

                # Add continuation indicator for wrapped lines (except last)
                if remaining and not wrapped_lines[-1].endswith("\\"):
                    wrapped_lines[-1] = wrapped_lines[-1]

        return wrapped_lines

    def _print_agent_header(self, agent_name: str, transfer_from: str | None = None):
        """Print agent header line (only in multi-agent mode)."""
        if not self._is_multi_agent:
            return

        # Don't repeat header for same agent
        if agent_name == self._last_printed_agent:
            return

        self._last_printed_agent = agent_name

        # Build title
        if transfer_from:
            title = f"{transfer_from} → {agent_name}"
        else:
            title = agent_name

        # Print separator line
        line_width = 60
        padding = line_width - len(title) - 3
        self.console.print(
            f"\n[bold cyan]┌ {title} {'─' * max(padding, 3)}[/bold cyan]"
        )

    def print_info_box(self, recent_chats=None):
        """Print info box with 4 regions (Team, Session, Tokens, Help) in a 2x2 grid."""

        # 1. Team Info - Detailed Agent List
        team = getattr(self, "_team", None) or getattr(self, "team", None)
        team_lines = ["[bold]Team[/bold]"]
        if team and team.agents:
            # Show up to 5 agents to prevent box from getting too tall
            for idx, agent in enumerate(list(team.agents.values())[:5]):
                model = getattr(agent, "model", "unknown")
                if hasattr(agent, "models"):
                    models = agent.models
                    if isinstance(models, list) and models:
                        model = models[0]
                    elif isinstance(models, str):
                        model = models

                # Truncate model for display
                if len(model) > 20:
                    model = model[:17] + "..."

                team_lines.append(f"[dim]• {agent.name}[/dim] [dim italic]({model})[/]")

            if len(team.agents) > 5:
                team_lines.append(f"[dim]+ {len(team.agents) - 5} more...[/dim]")
        else:
            team_lines.append("[dim]No agents loaded[/dim]")
        team_text = "\n".join(team_lines)

        # 2. Session Info - Recent Sessions List
        session_lines = ["[bold]Sessions[/bold]"]
        if recent_chats:
            # Show top 5 recent chats
            for chat in recent_chats[:5]:
                name = chat.get("name", "Unnamed")
                if len(name) > 20:
                    name = name[:17] + "..."

                # Simple relative time
                last_activity = chat.get("last_activity_date")
                time_str = format_relative_time(last_activity)

                marker = (
                    "→" if chat.get("id") == self._chat_id else " "
                )  # _chat_id is on Repl, accessed via self if Repl inherits

                session_lines.append(
                    f"[dim]{marker} {name}[/dim] [dim italic]({time_str})[/]"
                )
        else:
            msg_count = getattr(self, "message_count", 0)
            session_duration = datetime.now() - self.session_start
            duration_mins = int(session_duration.total_seconds() / 60)
            session_lines.append(
                f"[dim]Current: {duration_mins}m, {msg_count} msgs[/dim]"
            )
            session_lines.append("[dim]No history loaded[/dim]")

        session_text = "\n".join(session_lines)

        # 3. Quick Start - Commands and shortcuts (merged row)
        quick_start_text = (
            "[bold]Quick Start[/bold]\n"
            "[dim]/help[/dim] commands   [dim]/agents[/dim] team   [dim]/exit[/dim] quit\n"
            "[dim]@path[/dim] file     [dim]@image:path[/dim] image   [dim]Ctrl+T[/dim] verbose\n"
            "[dim]Esc[/dim] cancel   [dim]Ctrl+D[/dim] exit   [dim]Alt+Enter[/dim] newline"
        )

        # Create Table (2 rows: top split, bottom merged)
        table = Table(
            show_header=False,
            box=CLAUDE_BOX,
            border_style="blue",
            expand=True,
            show_lines=True,
            padding=(0, 2),
            collapse_padding=True,
        )

        # Define columns
        table.add_column(ratio=1)
        table.add_column(ratio=1)

        # Row 1: Team | Sessions
        table.add_row(team_text, session_text)
        # 4. Providers - Show loaded API key providers
        from pantheon.utils.model_selector import PROVIDER_API_KEYS
        import os

        loaded = [p for p, env in PROVIDER_API_KEYS.items() if os.environ.get(env, "")]
        provider_lines = ["[bold]Providers[/bold]"]
        if loaded:
            provider_lines.append("[dim]" + ", ".join(loaded) + "[/dim]")
        else:
            provider_lines.append("[dim]None detected[/dim]")
        provider_lines.append("[dim italic]/keys[/dim italic] to manage, [dim italic]~/.pantheon/.env[/dim italic]")
        provider_text = "\n".join(provider_lines)

        # Row 2: Quick Start | Providers
        table.add_row(quick_start_text, provider_text)

        self.console.print(table)

    async def print_greeting(self):
        from pantheon import __version__
        self.console.print("[purple]Aristotle © 2025-2026[/purple]")
        print_banner(self.console)
        self.console.print()
        self.console.print(
            f"[bold italic]Multi-agent system for scientific research[/bold italic]  [dim]v{__version__}[/dim]"
        )
        self.console.print(
            "[bold italic dim]Pantheon is a research project, use with caution.[/bold italic dim]"
        )
        self.console.print()

        # Fetch recent chats if available (via Repl mixin)
        recent_chats = []
        chatroom = getattr(self, "_chatroom", None)
        if chatroom:
            try:
                result = await chatroom.list_chats()
                if result.get("success"):
                    recent_chats = result.get("chats", [])
            except Exception:
                pass

        # Print modern Info Box
        self.print_info_box(recent_chats=recent_chats)

        self.console.print()
        if READLINE_AVAILABLE:
            self.console.print("[dim]Use ↑/↓ arrows for command history[/dim]")
        # self.console.print() # Removed duplicate newline

    # --- Input ---
    def ask_user_input(self) -> str:
        """Get user input with multi-line support and readline history."""
        try:
            self.console.print(
                "[dim]Enter your message (press Enter twice to finish)[/dim]"
            )
            lines = []
            while True:
                # First input uses "> " prompt, subsequent lines use "... "
                prompt_text = "... " if lines else ">   "

                if READLINE_AVAILABLE:
                    line = input(prompt_text)
                else:
                    self.console.print(
                        f"[bright_blue]{prompt_text}[/bright_blue]", end=" "
                    )
                    line = input()

                # Empty line ends input
                if line.strip() == "":
                    break

                lines.append(line)

            # Return merged multi-line string
            return "\n".join(lines).strip()

        except KeyboardInterrupt:
            self.console.print("\n[dim]Ctrl+C pressed - operation cancelled[/dim]")
            return ""
        except EOFError:
            raise

    def _print_help(self):
        """Print available commands"""
        self.console.print(
            "[dim][bold blue]-- BASIC ------------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        self.console.print(
            "[dim][bold purple]/help    [/bold purple][/dim] - Show this help"
        )
        self.console.print(
            "[dim][bold purple]/status  [/bold purple][/dim] - Session info"
        )
        self.console.print(
            "[dim][bold purple]/history [/bold purple][/dim] - Show command history"
        )
        self.console.print(
            "[dim][bold purple]/tokens  [/bold purple][/dim] - Token usage analysis"
        )
        self.console.print(
            "[dim][bold purple]/compress[/bold purple][/dim] - Force context compression"
        )
        self.console.print(
            "[dim][bold purple]/save    [/bold purple][/dim] - Save conversation to (json) file"
        )
        self.console.print(
            "[dim][bold purple]/revert  [/bold purple][/dim] - Revert conversation for rollback (undo)"
        )
        self.console.print(
            "[dim][bold purple]/clear   [/bold purple][/dim] - Clear conversation (requires confirmation)"
        )
        self.console.print(
            "[dim][bold purple]!<cmd>   [/bold purple][/dim] - Execute bash command directly (no LLM)"
        )
        self.console.print(
            "[dim][bold purple]/view    [/bold purple][/dim] - View file in fullscreen: /view <path>"
        )
        self.console.print(
            "[dim][bold purple]/edit    [/bold purple][/dim] - Edit file in external editor: /edit <path>"
        )
        self.console.print(
            "[dim][bold purple]/exit    [/bold purple][/dim] - Exit cleanly"
        )
        self.console.print()

        self.console.print(
            "[dim][bold blue]-- SHORTCUTS --------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        self.console.print("[dim]Esc      [/dim] - Cancel operation / clear input")
        self.console.print("[dim]Ctrl+C   [/dim] - Cancel, press twice to exit")
        self.console.print("[dim]Ctrl+D   [/dim] - Exit immediately")
        self.console.print(
            "[dim]Ctrl+T   [/dim] - Toggle display mode (compact/verbose)"
        )
        self.console.print("[dim]Alt+Enter[/dim] - Insert newline (multiline input)")
        self.console.print("[dim]Ctrl+J   [/dim] - Insert newline (alternative)")
        self.console.print()

        self.console.print(
            "[dim][bold blue]-- CHAT MANAGEMENT --------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        self.console.print(
            "[dim][bold purple]/new     [/bold purple][/dim] - Create new chat session"
        )
        self.console.print(
            "[dim][bold purple]/list    [/bold purple][/dim] - List all chat sessions"
        )
        self.console.print(
            "[dim][bold purple]/resume  [/bold purple][/dim] - Resume another chat (by id, name, or 'last')"
        )
        self.console.print()

        self.console.print(
            "[dim][bold blue]-- AGENT MANAGEMENT -------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        self.console.print(
            "[dim][bold purple]/agents  [/bold purple][/dim] - Show agents in current team"
        )
        self.console.print(
            "[dim][bold purple]/agent   [/bold purple][/dim] - Switch to agent (by name or number)"
        )
        self.console.print(
            "[dim][bold purple]/team    [/bold purple][/dim] - Switch team: /team list | /team <id>"
        )
        self.console.print(
            "[dim][bold purple]/model   [/bold purple][/dim] - Show/set model: /model | /model <name|tag>"
        )
        self.console.print(
            "[dim][bold purple]/keys    [/bold purple][/dim] - Show/set API keys: /keys | /keys <n|name> <key|base_url key>"
        )
        self.console.print()

        self.console.print(
            "[dim][bold blue]-- MCP SERVERS ------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        self.console.print(
            "[dim][bold purple]/mcp     [/bold purple][/dim] - List all MCP servers and status"
        )
        self.console.print(
            "[dim][bold purple]/mcp start[/bold purple][/dim] - Start MCP server: /mcp start <name>"
        )
        self.console.print(
            "[dim][bold purple]/mcp stop [/bold purple][/dim] - Stop MCP server: /mcp stop <name>"
        )
        self.console.print(
            "[dim][bold purple]/mcp add  [/bold purple][/dim] - Add new server: /mcp add <name> <cmd>"
        )
        self.console.print(
            "[dim][bold purple]/mcp rm   [/bold purple][/dim] - Remove server: /mcp remove <name>"
        )
        self.console.print()

        self.console.print(
            "[dim][bold blue]-- DISPLAY ----------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        self.console.print(
            "[dim][bold purple]/v       [/bold purple][/dim] - Verbose mode (show all details)"
        )
        self.console.print(
            "[dim][bold purple]/c       [/bold purple][/dim] - Compact mode (truncated output, default)"
        )
        self.console.print()

        if READLINE_AVAILABLE:
            self.console.print(
                "[dim][bold blue]-- NAVIGATION -------------------------------------------------------[/bold blue][/dim]"
            )
            self.console.print()
            self.console.print(
                "[dim][bold purple]↑/↓[/bold purple] - Browse command history"
            )
        self.console.print()

    def _print_history(self):
        """Print recent command history"""
        self.console.print()
        self.console.print(
            "[dim][bold blue]-- HISTORY ---------------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        if not self.command_history:
            self.console.print("[dim]No command history yet[/dim]\n")
            return

        self.console.print(
            f"[bold purple]Command History[/bold purple] [dim]({len(self.command_history)} commands)[/dim]"
        )

        # Show last 10 commands
        recent = self.command_history[-10:]
        for i, cmd in enumerate(recent, 1):
            if len(recent) == 10 and i == 1 and len(self.command_history) > 10:
                self.console.print("[dim]...[/dim]")
            self.console.print(
                f"[dim]{len(self.command_history) - len(recent) + i:2d}.[/dim] {cmd}"
            )
        self.console.print()

    async def _print_token_analysis(self):
        """Print detailed token usage analysis with Claude Code-style UI."""
        # Gather data from chatroom/team
        chatroom = getattr(self, "_chatroom", None)
        chat_id = getattr(self, "_chat_id", None)
        team = getattr(self, "_team", None) or getattr(self, "team", None)

        # Fallback stats from local tracking
        fallback_stats = {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "message_count": self.message_count,
        }

        # Get token stats using utility
        token_info = await get_detailed_token_stats(
            chatroom, chat_id, team, fallback_stats
        )

        # Render the panel
        render_token_panel(self.output.console, token_info, self.session_start)

    def _print_status(self):
        """Print current session status"""
        session_duration = datetime.now() - self.session_start
        duration_mins = int(session_duration.total_seconds() / 60)

        self.console.print()
        self.console.print(
            "[dim][bold blue]-- STATUS -----------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()

        # Display agent/team info
        team = getattr(self, "_team", None) or getattr(self, "team", None)
        if team and len(team.agents) == 1:
            agent = list(team.agents.values())[0]
            self.console.print(f"[dim]• Agent:    [/dim] {agent.name}")
            if hasattr(agent, "models") and agent.models:
                model = (
                    agent.models[0] if isinstance(agent.models, list) else agent.models
                )
                self.console.print(f"[dim]• Model:    [/dim] {model}")
        elif team:
            memory = getattr(self, "memory", None)
            if memory:
                active = team.get_active_agent(memory)
                self.console.print(f"[dim]• Team:     [/dim] {len(team.agents)} agents")
                self.console.print(f"[dim]• Active:   [/dim] {active.name}")
            else:
                self.console.print(f"[dim]• Team:     [/dim] {len(team.agents)} agents")

        self.console.print(f"[dim]• Messages: [/dim] {self.message_count}")
        self.console.print(f"[dim]• Duration: [/dim] {duration_mins}m")
        self.console.print(
            f"[dim]• History:  [/dim] {len(self.command_history)} commands"
        )

        # Memory record path
        chatroom = getattr(self, "_chatroom", None)
        chat_id = getattr(self, "_chat_id", None)
        if chatroom and chat_id:
            try:
                # Read-only: displaying memory path, no need to fix
                memory = chatroom.memory_manager.get_memory(chat_id)
                storage_files = memory.storage_files
                if storage_files:
                    if len(storage_files) == 1:
                        self.console.print(f"[dim]• Memory:   [/dim] {Path(storage_files[0]).absolute()}")
                    else:
                        # Multiple files (JSONL backend)
                        self.console.print(f"[dim]• Memory:   [/dim] {Path(storage_files[0]).parent.absolute()}/")
                        for f in storage_files:
                            self.console.print(f"[dim]            [/dim] └─ {Path(f).name}")
            except Exception:
                pass

        self.console.print()

        # Token usage statistics
        total_tokens = self.total_input_tokens + self.total_output_tokens
        if total_tokens > 0:
            self.console.print(
                "[dim][bold blue]-- TOKENS -----------------------------------------------------------[/bold blue][/dim]"
            )
            self.console.print()
            self.console.print(
                f"[dim]  • Total:  [/dim] {self._format_token_count(total_tokens)}"
            )
            self.console.print(
                f"[dim]  • Input:  [/dim] {self._format_token_count(self.total_input_tokens)}"
            )
            self.console.print(
                f"[dim]  • Output: [/dim] {self._format_token_count(self.total_output_tokens)}"
            )

            # Show efficiency metrics
            if self.message_count > 0:
                avg_tokens_per_msg = total_tokens / self.message_count
                self.console.print(
                    f"[dim]  • Avg/msg:[/dim] {self._format_token_count(int(avg_tokens_per_msg))}"
                )
            self.console.print()

        if READLINE_AVAILABLE:
            self.console.print(f"[dim]Input:[/dim] readline (with history)")
        else:
            self.console.print(f"[dim]Input:[/dim] basic")
        self.console.print()

    def _replay_chat_history(self, messages: list[dict]):
        """Replay full chat history to the terminal.

        Renders user inputs, assistant responses, tool calls, and tool results
        using the same renderers as live streaming for a consistent look.
        """
        import json
        from rich.style import Style as RichStyle
        from rich.text import Text as RichText

        # Clear terminal via ANSI escape (works inside patch_stdout)
        print("\033[2J\033[H", end="", flush=True)

        last_agent = None

        for msg in messages:
            try:
                role = msg.get("role")
                if role in ("system", "compression"):
                    continue
                content = msg.get("content", "")

                agent_name = msg.get("agent_name")

                if role == "user" and isinstance(content, str) and content.strip():
                    # Skip internal/compression messages and REPL commands
                    if msg.get("_compression") or msg.get("_internal"):
                        continue
                    if content.strip().startswith("/"):
                        continue
                    # Skip bg task notification triggers
                    if "<bg_task_notification>" in content or content.startswith("[Background task '"):
                        continue
                    # Render user input with gray background using Rich
                    self.console.print()
                    for line in content.split("\n"):
                        styled = RichText(f"> {line}")
                        styled.stylize(RichStyle(bgcolor="grey23"))
                        self.console.print(styled)

                elif role == "assistant":
                    # Tool calls
                    if tool_calls := msg.get("tool_calls"):
                        for call in tool_calls:
                            tool_name = call.get("function", {}).get("name")
                            if tool_name:
                                try:
                                    args = json.loads(
                                        call.get("function", {}).get("arguments", "{}")
                                    )
                                except Exception:
                                    args = {}
                                self.console.print()
                                self.tool_call_renderer.render(tool_name, args)
                    # Text content (with Markdown fallback, same as live rendering)
                    if isinstance(content, str) and content.strip():
                        # Print agent name before response (matches live rendering)
                        if agent_name:
                            self.console.print(
                                f"[dim]→[/dim] [bold cyan]{agent_name}[/bold cyan]"
                            )
                        self.console.print()
                        try:
                            self.console.print(Markdown(content))
                        except Exception:
                            self.console.print(content)

                elif role == "tool":
                    tool_name = msg.get("tool_name", "")
                    raw = msg.get("raw_content")
                    if raw is not None and isinstance(raw, dict):
                        self.tool_result_renderer.render(tool_name, raw)
                    elif isinstance(content, str) and content.strip():
                        try:
                            result = json.loads(content)
                            self.tool_result_renderer.render(tool_name, result)
                        except Exception:
                            self.tool_result_renderer.render(tool_name, {"output": content})
                    self.console.print()
            except Exception:
                continue

    async def _print_session_summary(self):
        """Print a brief session summary before exit"""
        session_duration = datetime.now() - self.session_start
        duration_mins = int(session_duration.total_seconds() / 60)

        if self.message_count > 0:
            # Get accurate token stats from chatroom (same source as status bar)
            chatroom = getattr(self, "_chatroom", None)
            chat_id = getattr(self, "_chat_id", None)
            team = getattr(self, "_team", None)

            token_info = await get_detailed_token_stats(chatroom, chat_id, team, {})
            total_tokens = token_info.get("total", 0)
            total_cost = token_info.get("total_cost", 0)

            summary = f"Session: {self.message_count} messages in {duration_mins}m"
            if total_tokens > 0:
                summary += f" • {self._format_token_count(total_tokens)} tokens"
            if total_cost and total_cost > 0:
                summary += f" • ${total_cost:.4f}"
            self.console.print(f"\n[dim]{summary}[/dim]")
        self.console.print("[dim]Goodbye![/dim]")

    def _format_tool_output(self, output):
        """Format tool output for better readability in markdown"""
        import json

        if output is None:
            return "*No output*"

        # Handle dict outputs
        if isinstance(output, dict):
            # Special handling for common tool outputs
            if "result" in output and isinstance(output.get("result"), dict):
                # Python/R code execution results
                result = output["result"]
                stdout = output.get("stdout", "").strip()
                stderr = output.get("stderr", "").strip()

                formatted_lines = []

                # Format the main result
                if result:
                    try:
                        # Pretty print the result
                        result_str = json.dumps(result, indent=2, ensure_ascii=False)
                        formatted_lines.append("**Result:**")
                        formatted_lines.append("```json")
                        formatted_lines.append(result_str)
                        formatted_lines.append("```")
                    except Exception:
                        formatted_lines.append("**Result:**")
                        formatted_lines.append("```")
                        formatted_lines.append(str(result))
                        formatted_lines.append("```")

                # Add stdout if present
                if stdout:
                    formatted_lines.append("")
                    formatted_lines.append("**Standard Output:**")
                    formatted_lines.append("```")
                    formatted_lines.append(stdout)
                    formatted_lines.append("```")

                # Add stderr if present
                if stderr:
                    formatted_lines.append("")
                    formatted_lines.append("**Error Output:**")
                    formatted_lines.append("```")
                    formatted_lines.append(stderr)
                    formatted_lines.append("```")

                return (
                    "\n".join(formatted_lines)
                    if formatted_lines
                    else "```\n{}\n```".format(str(output))
                )

            # Special handling for todo outputs
            elif "success" in output and "summary" in output:
                formatted_lines = []
                if output.get("success"):
                    summary = output.get("summary", {})
                    total = output.get("total_todos", 0)

                    formatted_lines.append(f"✅ **Todo Status:** {total} total tasks")
                    if summary:
                        formatted_lines.append(
                            f"- Pending: {summary.get('pending', 0)}"
                        )
                        formatted_lines.append(
                            f"- In Progress: {summary.get('in_progress', 0)}"
                        )
                        formatted_lines.append(
                            f"- Completed: {summary.get('completed', 0)}"
                        )

                    # Add todos list if present
                    if "todos" in output and output["todos"]:
                        formatted_lines.append("")
                        formatted_lines.append("**Tasks:**")
                        for todo in output["todos"]:
                            status_icon = (
                                "✅"
                                if todo.get("status") == "completed"
                                else "🔄"
                                if todo.get("status") == "in_progress"
                                else "⏳"
                            )
                            formatted_lines.append(
                                f"- {status_icon} {todo.get('content', 'Unknown task')}"
                            )

                    return "\n".join(formatted_lines)

            # Generic dict formatting
            try:
                formatted = json.dumps(output, indent=2, ensure_ascii=False)
                return f"```json\n{formatted}\n```"
            except Exception:
                return f"```\n{str(output)}\n```"

        # Handle list outputs
        elif isinstance(output, list):
            try:
                formatted = json.dumps(output, indent=2, ensure_ascii=False)
                return f"```json\n{formatted}\n```"
            except Exception:
                return f"```\n{str(output)}\n```"

        # Handle string outputs
        elif isinstance(output, str):
            if "\n" in output or len(output) > 80:
                return f"```\n{output}\n```"
            else:
                return output

        # Default formatting
        else:
            return f"```\n{str(output)}\n```"

    def print_tool_call(self, tool_name: str, args: dict = None):
        """Print tool call with enhanced rendering"""
        # Mark that tools are executing
        self._tools_executing = True
        # Set current tool name for progress display
        self._current_tool_name = tool_name

        self.console.print()  # Add some space

        # Use new renderer for enhanced display
        self.tool_call_renderer.render(tool_name, args or {})

        self.console.print()  # Add space after tool call

    def print_tool_result(self, tool_name: str, result: dict):
        """Print tool result with enhanced rendering"""
        # Mark that tool execution is complete
        self._tools_executing = False
        # Clear current tool name since execution is done
        self._current_tool_name = None

        # Use new renderer for enhanced display
        rendered = self.tool_result_renderer.render(tool_name, result)

        if not rendered:
            # Fallback for unhandled tools
            self._print_tool_result_fallback(tool_name, result)

        self.console.print()  # Add space after result

    def _print_tool_result_fallback(self, tool_name: str, result: dict):
        """Fallback result rendering for unhandled tools"""
        # Skip tools that handle their own output
        skip_tools = [
            "edit",
            "notebook",
            "update_todo_status",
            "add_todo",
            "mark_task_done",
            "complete_current_todo",
            "work_on_next_todo",
        ]
        if any(tool in tool_name.lower() for tool in skip_tools) and isinstance(
            result, dict
        ):
            if result.get("success"):
                return

        if not isinstance(result, dict):
            self.console.print(f"  | {result}")
            return

        # Print success status
        if result.get("success"):
            self.console.print("[green]✓[/green] Success")

        # Try standard output fields first
        output = result.get("output") or result.get("result")
        if output:
            output_str = str(output)
            if len(output_str) > 200:
                output_str = output_str[:200] + "..."
            self.console.print(f"  ⎿  {output_str}")
            return

        # Generic rendering for other fields
        skip_keys = {"success", "error", "metadata", "timestamp", "output", "result"}
        for key, value in result.items():
            if key in skip_keys or value is None:
                continue
            self._render_fallback_field(key, value)

    def _render_fallback_field(self, key: str, value):
        """Render a single field in fallback mode"""
        if isinstance(value, list):
            # List: show count and first few items
            count = len(value)
            self.console.print(f"  [dim]{key}[/dim] ({count} items):")
            # Show first 5 items
            for i, item in enumerate(value[:5]):
                item_str = self._format_list_item(item)
                self.console.print(f"    {item_str}")
            if count > 5:
                self.console.print(f"    [dim]... +{count - 5} more[/dim]")
        elif isinstance(value, dict):
            # Dict: show as key-value pairs
            self.console.print(f"  [dim]{key}[/dim]:")
            for k, v in list(value.items())[:5]:
                v_str = str(v)
                if len(v_str) > 50:
                    v_str = v_str[:50] + "..."
                self.console.print(f"    {k}: {v_str}")
            if len(value) > 5:
                self.console.print(f"    [dim]... +{len(value) - 5} more[/dim]")
        else:
            # String/number: display directly
            value_str = str(value)
            if len(value_str) > 100:
                value_str = value_str[:100] + "..."
            self.console.print(f"  [dim]{key}:[/dim] {value_str}")

    def _format_list_item(self, item) -> str:
        """Format a list item for display"""
        if isinstance(item, dict):
            # For file entries: show name and type
            name = item.get("name", "")
            item_type = item.get("type", "")
            size = item.get("size", 0)
            if name:
                if item_type == "directory":
                    return f"[blue]{name}/[/blue]"
                elif item_type == "file":
                    size_str = self._format_size(size) if size else ""
                    return f"{name} [dim]({size_str})[/dim]" if size_str else name
                else:
                    return name
            # Generic dict: show first few keys
            keys = list(item.keys())[:3]
            preview = ", ".join(f"{k}={item[k]}" for k in keys)
            if len(preview) > 60:
                preview = preview[:60] + "..."
            return f"{{{preview}}}"
        else:
            item_str = str(item)
            if len(item_str) > 60:
                item_str = item_str[:60] + "..."
            return item_str

    def _format_size(self, size: int) -> str:
        """Format file size in human readable format"""
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f}GB"

    async def print_message(self):
        """Enhanced message handler with Claude Code style formatting"""
        try:
            while True:
                try:
                    raw_message = await self.team.events_queue.get()
                except asyncio.CancelledError:
                    break
                except Exception:
                    continue

                # Unpack Team event format: {"agent_name": ..., "event": ...}
                agent_name = None
                if (
                    isinstance(raw_message, dict)
                    and "agent_name" in raw_message
                    and "event" in raw_message
                ):
                    agent_name = raw_message["agent_name"]
                    message = raw_message["event"]
                else:
                    message = raw_message

                # Print agent header if agent changed (multi-agent mode)
                if agent_name and agent_name != self._current_agent_name:
                    self._print_agent_header(agent_name)
                    self._current_agent_name = agent_name

                # Handle tool calls with Claude Code style
                if tool_calls := message.get("tool_calls"):
                    # Estimate tokens for tool calls message
                    tool_call_content = json.dumps(tool_calls)
                    # Update token estimate if we have access to the parent REPL instance
                    if hasattr(self, "_parent_repl") and hasattr(
                        self._parent_repl, "estimated_output_tokens"
                    ):
                        additional_tokens = self._parent_repl._estimate_tokens(
                            tool_call_content
                        )
                        self._parent_repl.estimated_output_tokens += additional_tokens

                    for call in tool_calls:
                        tool_name = call.get("function", {}).get("name")
                        if tool_name:
                            try:
                                args = json.loads(
                                    call.get("function", {}).get("arguments", "{}")
                                )
                            except Exception:
                                args = {}
                            self.print_tool_call(tool_name, args)
                    continue

                # Handle tool responses with enhanced formatting
                elif message.get("role") == "tool":
                    tool_name = message.get("tool_name", "")
                    content = message.get("content", "")

                    # Prefer raw_content if available (original dict, not repr string)
                    raw_content = message.get("raw_content")
                    if raw_content is not None and isinstance(raw_content, dict):
                        self.print_tool_result(tool_name, raw_content)
                        continue

                    # Try to parse content as structured result
                    try:
                        # Try JSON first
                        result = json.loads(content)
                        self.print_tool_result(tool_name, result)
                    except json.JSONDecodeError:
                        # Try ast.literal_eval for repr() output (uses single quotes)
                        try:
                            import ast

                            result = ast.literal_eval(content)
                            if isinstance(result, dict):
                                self.print_tool_result(tool_name, result)
                            else:
                                self.print_tool_result(
                                    tool_name, {"output": str(result)}
                                )
                        except Exception:
                            # Final fallback: plain text
                            if content.strip():
                                self.print_tool_result(tool_name, {"output": content})
                    except Exception:
                        if content.strip():
                            self.print_tool_result(tool_name, {"output": content})
                    continue

                # Skip assistant messages - we handle them in main loop via content_buffer
                if message.get("role") == "assistant":
                    continue

                # Only print other message types (like system messages, if any)
                team = getattr(self, "_team", None) or getattr(self, "team", None)
                default_name = (
                    list(team.agents.keys())[0] if team and team.agents else "agent"
                )
                print_agent_message_modern_style(
                    agent_name or default_name,
                    message,
                    self.console,
                    show_tool_details=False,
                )
        except Exception:
            # Silently handle critical errors in print_message
            pass
