# repl_ui.py
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
import json

import asyncio
import sys
import time
import signal
from datetime import datetime

# Simple readline support for history
try:
    import readline
    import atexit
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

from ..agent import Agent
from ..remote.agent import RemoteAgent
from ..utils.misc import print_agent_message, print_agent, print_banner, print_agent_message_modern_style



class ReplUI:
    """Presentation layer for REPL: printing, input, formatting."""
    def __init__(self):
        self.console = Console()
        self.input_panel = Panel(Text("Type your message here...", style="dim"),
                                 title="Input", border_style="bright_blue")
        self._tools_executing = False
        self._processing_live: Live | None = None

    async def print_greeting(self):
        await print_banner(self.console)
        self.console.print(
            "[bold]Welcome to the Pantheon CLI![/bold]\n" +
            "The First AI+ Assistant for Science!\n"
        )
        
        # Agent info in a compact format
        self.console.print("[bold]Model:[/bold]")
        agent_info = f"  - [bright_blue]{self.agent.name}[/bright_blue]"
        if hasattr(self.agent, 'models') and self.agent.models:
            model = self.agent.models[0] if isinstance(self.agent.models, list) else self.agent.models
            agent_info += f" [dim]•[/dim] [yellow]{model}[/yellow]"
        
        self.console.print(agent_info)
        self.console.print("[dim]Type your message, '/exit' to quit, or '/help' for commands[/dim]")
        
        self.console.print("[dim]'/model' for available models, '/api-key' for API keys[/dim]")
        if READLINE_AVAILABLE:
            self.console.print()
            self.console.print("[dim]Use ↑/↓ arrows for command history[/dim]")
        self.console.print()
    
    # --- Input ---
    def ask_user_input(self) -> str:
        """Get user input with multi-line support and readline history."""
        try:
            self.console.print("[dim]Enter your message (press Enter twice to finish)[/dim]")
            lines = []
            while True:
                # First input uses "> " prompt, subsequent lines use "... "
                prompt_text = "... " if lines else ">   "

                if READLINE_AVAILABLE:
                    line = input(prompt_text)
                else:
                    self.console.print(f"[bright_blue]{prompt_text}[/bright_blue]", end=" ")
                    line = input()

                # 空行结束
                if line.strip() == "":
                    break

                lines.append(line)

            # 返回多行合并的字符串
            return "\n".join(lines).strip()

        except KeyboardInterrupt:
            self.console.print("\n[dim]Ctrl+C pressed - operation cancelled[/dim]")
            return ""
        except EOFError:
            raise

    def _print_help(self):
        """Print available commands"""
        self.console.print("\n[bold]Commands:[/bold]")
        self.console.print("[dim]/help[/dim] - Show this help")
        self.console.print("[dim]/status[/dim] - Session info")
        self.console.print("[dim]/history[/dim] - Show command history")
        self.console.print("[dim]/tokens[/dim] - Token usage analysis")  
        self.console.print("[dim]/clear[/dim] - Clear screen")
        self.console.print("[dim]/atac init[/dim] - ATAC-seq analysis helper 🧬")
        self.console.print("[dim]/exit[/dim] - Exit cleanly")
        self.console.print("[dim]Ctrl+C[/dim] - Cancel current operation")
        self.console.print("[dim]Ctrl+C x2[/dim] - Force exit (within 2 seconds)")
        
        # Check if model/API key management is available
        if hasattr(self.agent, '_model_manager') or hasattr(self.agent, '_api_key_manager'):
            self.console.print("\n[bold]Model & API Management:[/bold]")
            if hasattr(self.agent, '_model_manager'):
                self.console.print("[dim]/model list[/dim] - List available models")
                self.console.print("[dim]/model current[/dim] - Show current model")  
                self.console.print("[dim]/model <id>[/dim] - Switch to model")
            if hasattr(self.agent, '_api_key_manager'):
                self.console.print("[dim]/api-key list[/dim] - Show API key status")
                self.console.print("[dim]/api-key <provider> <key>[/dim] - Set API key")
        
        if READLINE_AVAILABLE:
            self.console.print("\n[bold]Navigation:[/bold]")
            self.console.print("[dim]↑/↓[/dim] - Browse command history")
        
        self.console.print("\n[bold]Examples:[/bold]")
        self.console.print("[dim]analyze single cell data[/dim]")
        self.console.print("[dim]run quality control[/dim]")
        self.console.print("[dim]create UMAP plot[/dim]")
        if hasattr(self.agent, '_model_manager'):
            self.console.print("[dim]/model gpt-4o[/dim] - Switch to GPT-4o")
            self.console.print("[dim]/api-key openai sk-...[/dim] - Set OpenAI key")
        self.console.print()
    
    def _print_history(self):
        """Print recent command history"""
        if not self.command_history:
            self.console.print("\n[dim]No command history yet[/dim]\n")
            return
            
        self.console.print(f"\n[bold]Command History[/bold] [dim]({len(self.command_history)} commands)[/dim]")
        
        # Show last 10 commands
        recent = self.command_history[-10:]
        for i, cmd in enumerate(recent, 1):
            if len(recent) == 10 and i == 1 and len(self.command_history) > 10:
                self.console.print("[dim]...[/dim]")
            self.console.print(f"[dim]{len(self.command_history) - len(recent) + i:2d}.[/dim] {cmd}")
        self.console.print()
    
    def _print_token_analysis(self):
        """Print detailed token usage analysis"""
        total_tokens = self.total_input_tokens + self.total_output_tokens
        
        if total_tokens == 0:
            self.console.print("\n[dim]No token usage data yet[/dim]\n")
            return
        
        self.console.print(f"\n[bold]Token Analysis[/bold]")
        
        # Basic stats
        self.console.print(f"[dim]Total:[/dim] {self._format_token_count(total_tokens)} tokens")
        self.console.print(f"[dim]  • Input:[/dim] {self._format_token_count(self.total_input_tokens)} ({self.total_input_tokens/total_tokens*100:.1f}%)")
        self.console.print(f"[dim]  • Output:[/dim] {self._format_token_count(self.total_output_tokens)} ({self.total_output_tokens/total_tokens*100:.1f}%)")
        
        # Efficiency metrics
        if self.message_count > 0:
            avg_total = total_tokens / self.message_count
            avg_input = self.total_input_tokens / self.message_count
            avg_output = self.total_output_tokens / self.message_count
            
            self.console.print(f"\n[bold]Per Message Average:[/bold]")
            self.console.print(f"[dim]Total:[/dim] {self._format_token_count(int(avg_total))}")
            self.console.print(f"[dim]Input:[/dim] {self._format_token_count(int(avg_input))}")
            self.console.print(f"[dim]Output:[/dim] {self._format_token_count(int(avg_output))}")
        
        # Usage recommendations
        self.console.print(f"\n[bold]Tips:[/bold]")
        if avg_input > 1000:
            self.console.print("[dim]• Consider shorter prompts to reduce input tokens[/dim]")
        if self.total_output_tokens / max(1, self.total_input_tokens) > 3:
            self.console.print("[dim]• High output ratio - responses are verbose[/dim]")
        if self.message_count > 5 and avg_total < 100:
            self.console.print("[dim]• Efficient usage - good token management[/dim]")
        elif avg_total > 2000:
            self.console.print("[dim]• High token usage - consider optimizing prompts[/dim]")
        
        self.console.print()
        
    def _print_status(self):
        """Print current session status"""
        session_duration = datetime.now() - self.session_start
        duration_mins = int(session_duration.total_seconds() / 60)
        
        self.console.print(f"\n[bold]Session Status:[/bold]")
        self.console.print(f"[dim]• Agent:[/dim] {self.agent.name}")
        if hasattr(self.agent, 'models') and self.agent.models:
            model = self.agent.models[0] if isinstance(self.agent.models, list) else self.agent.models
            self.console.print(f"[dim]• Model:[/dim] {model}")
        self.console.print(f"[dim]Messages:[/dim] {self.message_count}")
        self.console.print(f"[dim]Duration:[/dim] {duration_mins}m")
        self.console.print(f"[dim]History:[/dim] {len(self.command_history)} commands")
        
        # Token usage statistics
        total_tokens = self.total_input_tokens + self.total_output_tokens
        if total_tokens > 0:
            self.console.print(f"[dim]Tokens:[/dim] {self._format_token_count(total_tokens)} total")
            self.console.print(f"[dim]  • Input:[/dim] {self._format_token_count(self.total_input_tokens)}")
            self.console.print(f"[dim]  • Output:[/dim] {self._format_token_count(self.total_output_tokens)}")
            
            # Show efficiency metrics
            if self.message_count > 0:
                avg_tokens_per_msg = total_tokens / self.message_count
                self.console.print(f"[dim]  • Avg/msg:[/dim] {self._format_token_count(int(avg_tokens_per_msg))}")
        
        if READLINE_AVAILABLE:
            self.console.print(f"[dim]Input:[/dim] readline (with history)")
        else:
            self.console.print(f"[dim]Input:[/dim] basic")
        self.console.print()

    def _print_session_summary(self):
        """Print a brief session summary before exit"""
        session_duration = datetime.now() - self.session_start
        duration_mins = int(session_duration.total_seconds() / 60)
        
        if self.message_count > 0:
            summary = f"Session: {self.message_count} messages in {duration_mins}m"
            total_tokens = self.total_input_tokens + self.total_output_tokens
            if total_tokens > 0:
                summary += f" • {self._format_token_count(total_tokens)} tokens"
            self.console.print(f"\n[dim]{summary}[/dim]")
        self.console.print("[dim]Goodbye![/dim]")

    def print_tool_call(self, tool_name: str, args: dict = None):
        """Print tool call in Claude Code style with fancy boxes"""
        # Mark that tools are executing
        self._tools_executing = True
        
        self.console.print()  # Add some space
        
        # Claude Code style tool call display
        if tool_name in ["run_code", "run_code_in_interpreter", "run_python", "run_r", "run_julia"] and args and 'code' in args:
            # Special handling for code execution
            if tool_name in ["run_python", "run_code", "run_code_in_interpreter"]:
                self.console.print("⏺ [bold]Python[/bold]")
                header_title = "Run Python code"
            elif tool_name == "run_r":
                self.console.print("⏺ [bold]R[/bold]")
                header_title = "Run R code"
            elif tool_name == "run_julia":
                self.console.print("⏺ [bold]Julia[/bold]")
                header_title = "Run Julia code"
            
            # Create a fancy code block
            code = args['code']
            lines = code.split('\n')
            
            # Create the box
            self.console.print("╭" + "─" * 79 + "╮")
            title_padding = " " * (79 - len(header_title) - 4)
            self.console.print(f"│ [bold]{header_title}[/bold]{title_padding}   │")
            self.console.print("│ ╭" + "─" * 75 + "╮ │")

            # Limit display lines (show first 10 + last 10 if > 20 lines)
            max_display_lines = 20
            if len(lines) <= max_display_lines:
                # Show all lines
                display_lines = lines
            else:
                # Show first 10, ellipsis, last 10
                first_lines = lines[:10]
                last_lines = lines[-10:]
                display_lines = first_lines + [f"... (showing 20 of {len(lines)} lines) ..."] + last_lines
            
            for line in display_lines:
                # Truncate long lines and pad short ones
                display_line = line[:75] if len(line) <= 75 else line[:72] + "..."
                padded_line = display_line.ljust(75)
                self.console.print(f"│ │ {padded_line[:71]}   │ │")
            
            self.console.print("│ ╰" + "─" * 75 + "╯ │")
            self.console.print("╰" + "─" * 79 + "╯")
            
        elif tool_name in ["run_command", "run_command_in_shell"] and args and 'command' in args:
            # Shell command execution
            command = args['command']
            self.console.print(f"⏺ [bold]Bash[/bold]({command})")
            
        else:
            # Generic tool call
            if args:
                # Try to show the most relevant argument
                key_arg = None
                if 'file_path' in args:
                    key_arg = f"file_path='{args['file_path']}'"
                elif 'pattern' in args:
                    key_arg = f"pattern='{args['pattern']}'"
                elif 'query' in args:
                    key_arg = f"query='{args['query'][:50]}...'" if len(str(args['query'])) > 50 else f"query='{args['query']}'"
                elif 'code' in args:
                    # Display code for run_python and run_r tools
                    code_lines = str(args['code']).strip().split('\n')
                    if len(code_lines) == 1 and len(code_lines[0]) <= 60:
                        key_arg = f"code='{code_lines[0]}'"
                    elif len(code_lines) <= 3 and all(len(line) <= 50 for line in code_lines):
                        code_preview = '; '.join(line.strip() for line in code_lines)
                        key_arg = f"code='{code_preview[:70]}...'" if len(code_preview) > 70 else f"code='{code_preview}'"
                    else:
                        first_line = code_lines[0][:50]
                        key_arg = f"code='{first_line}... ({len(code_lines)} lines)'"
                
                if key_arg:
                    self.console.print(f"⏺ [bold]{tool_name}[/bold]({key_arg})")
                else:
                    self.console.print(f"⏺ [bold]{tool_name}[/bold](...)")
            else:
                self.console.print(f"⏺ [bold]{tool_name}[/bold]()")
        
        self.console.print()  # Add space after tool call
        
    def print_tool_result(self, tool_name: str, result: dict):
        """Print tool result in Claude Code style with diff support"""
        
        # Mark that tool execution is complete
        self._tools_executing = False
        
        # Special handling for toolsets that print their own output - skip normal output box
        skip_tools = ['edit', 'write', 'read', 'file', 'glob', 'grep', 'ls', 'notebook']
        if any(tool in tool_name.lower() for tool in skip_tools) and isinstance(result, dict):
            if result.get('success'):
                # For successful operations, don't show any output box
                # The content was already printed by the toolset
                return

        # Show tool output in Claude Code style
        if isinstance(result, dict) and 'output' in result:
            output = result['output']
        elif isinstance(result, dict) and 'result' in result:
            output = result['result']
        else:
            output = str(result)
        
        if output and output.strip():
            # Create a Claude Code style output box
            lines = output.strip().split('\n')
            max_width = min(79, max(len(line) for line in lines) + 4)
            
            self.console.print("╭" + "─" * (max_width - 2) + "╮")
            self.console.print("│ [bold]Output[/bold]" + " " * (max_width - 9) + "│")
            self.console.print("├" + "─" * (max_width - 2) + "┤")
            
            for line in lines:
                # Handle long lines
                if len(line) > max_width - 4:
                    padded_line = line[:max_width - 7] + "..."
                else:
                    padded_line = line
                
                padding = max_width - len(padded_line) - 4
                self.console.print(f"│ {padded_line}" + " " * padding + " │")
            
            self.console.print("╰" + "─" * (max_width - 2) + "╯")
            self.console.print()  # Add space after output

    async def print_message(self):
        """Enhanced message handler with Claude Code style formatting"""
        while True:
            message = await self.agent.events_queue.get()
            
            # Handle tool calls with Claude Code style
            if tool_calls := message.get("tool_calls"):
                for call in tool_calls:
                    tool_name = call.get('function', {}).get('name')
                    if tool_name:
                        try:
                            args = json.loads(call.get('function', {}).get('arguments', '{}'))
                        except:
                            args = {}
                        self.print_tool_call(tool_name, args)
                continue
                
            # Handle tool responses with enhanced formatting
            elif message.get("role") == "tool":
                tool_name = message.get("tool_name", "")
                content = message.get("content", "")
                
                # Show tool results in Claude Code style
                try:
                    # Try to parse as JSON for structured results
                    result = json.loads(content)
                    self.print_tool_result(tool_name, result)
                except:
                    # Fallback for plain text results
                    if content.strip():
                        # Create a simple output display for non-JSON results
                        self.print_tool_result(tool_name, {"output": content})
                continue
                
            # Skip assistant messages - we handle them in main loop via content_buffer
            if message.get("role") == "assistant":
                continue
            
            # Only print other message types (like system messages, if any)
            print_agent_message_modern_style(
                self.agent.name, 
                message, 
                self.console,
                show_tool_details=False
            )