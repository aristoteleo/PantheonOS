import asyncio
import sys
import time
import json
import signal
from datetime import datetime
import os
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich.columns import Columns
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.layout import Layout

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

# Import toolsets from pantheon-toolsets
try:
    from pantheon.toolsets.python import PythonInterpreterToolSet
    PYTHON_TOOLSET_AVAILABLE = True
except ImportError:
    PYTHON_TOOLSET_AVAILABLE = False
    print("Warning: PythonInterpreterToolSet not available. Install pantheon-toolsets for Python execution.")


class Repl:
    """REPL for a single agent.

    Args:
        agent: The agent to use for the REPL.
    """
    def __init__(self, agent: Agent | RemoteAgent, enable_python: bool = True):
        self.agent = agent
        self.console = Console()
        self.current_task = None
        self.tool_calls_active = False
        self.session_start = datetime.now()
        self.message_count = 0
        
        # Token statistics
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        
        # Processing status tracking
        self._current_live_display = None
        self._tools_executing = False
        self._current_agent_task = None
        
        # Setup history file
        self.history_file = Path.home() / ".pantheon_history"
        self.command_history = []
        self.history_index = -1
        
        # Setup Python toolset if available and enabled
        self.python_enabled = enable_python and PYTHON_TOOLSET_AVAILABLE
        if self.python_enabled:
            self._setup_python_toolset()
        
        # Setup input system
        self._setup_input_system()
        self._load_history()
        
        # Setup signal handlers for better interrupt handling
        self._setup_signal_handlers()
        
        # Simple fixed input panel at bottom
        self.input_panel = Panel(
            Text("Type your message here...", style="dim"),
            title="Input",
            border_style="bright_blue"
        )

    def _setup_python_toolset(self):
        """Setup Python toolset for code execution"""
        try:
            self.python_toolset = PythonInterpreterToolSet("python_interpreter")
            # Claude Code style is handled by the toolset itself
            self.agent.toolset(self.python_toolset)
            self.console.print("[dim]Python execution enabled[/dim]")
        except Exception as e:
            self.console.print(f"[dim]Warning: Failed to setup Python toolset: {e}[/dim]")
            self.python_enabled = False
            self.python_toolset = None

    def _setup_shell_toolset_callback(self):
        """Setup shell toolset callback - simplified"""
        pass
        
    def _setup_signal_handlers(self):
        """Setup signal handlers for better interrupt management"""
        self._interrupt_count = 0
        self._last_interrupt_time = 0
        
        def signal_handler(signum, frame):
            import time
            current_time = time.time()
            
            # If interrupts come within 2 seconds of each other, count them
            if current_time - self._last_interrupt_time < 2.0:
                self._interrupt_count += 1
            else:
                self._interrupt_count = 1
                
            self._last_interrupt_time = current_time
            
            # Show cancellation message for first interrupt
            if self._interrupt_count == 1:
                self.console.print("\n[yellow]Operation interrupted - press Ctrl+C again within 2 seconds to force exit[/yellow]")
                # Try to cancel the current agent task if it exists
                if hasattr(self, '_current_agent_task') and self._current_agent_task and not self._current_agent_task.done():
                    try:
                        self._current_agent_task.cancel()
                    except Exception:
                        pass  # Ignore errors during cancellation
            elif self._interrupt_count >= 2:
                self.console.print("\n[red]Force exit requested[/red]")
                sys.exit(1)
            
            # For first interrupt, let KeyboardInterrupt be raised normally
            # This allows the normal interrupt handling to work
        
        # Only set up signal handler on Unix-like systems
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, signal_handler)

    async def print_greeting(self):
        await print_banner(self.console)
        self.console.print(
            "[bold]Welcome to the Pantheon REPL![/bold]\n" +
            "Single-cell genomics analysis assistant\n"
        )
        
        # Agent info in a compact format
        self.console.print("[bold]Current agent:[/bold]")
        agent_info = f"  - [bright_blue]{self.agent.name}[/bright_blue]"
        if hasattr(self.agent, 'models') and self.agent.models:
            model = self.agent.models[0] if isinstance(self.agent.models, list) else self.agent.models
            agent_info += f" [dim]•[/dim] [yellow]{model}[/yellow]"
        
        self.console.print(agent_info)
        self.console.print("[dim]Type your message, 'exit' to quit, or 'help' for commands[/dim]")
        if READLINE_AVAILABLE:
            self.console.print("[dim]Use ↑/↓ arrows for command history[/dim]")
        self.console.print()

    def print_tool_call(self, tool_name: str, args: dict = None):
        """Print tool call in Claude Code style with fancy boxes"""
        # Mark that tools are executing
        self._tools_executing = True
        
        self.console.print()  # Add some space
        
        # Claude Code style tool call display
        if tool_name in ["run_code", "run_code_in_interpreter", "run_python", "run_r"] and args and 'code' in args:
            # Special handling for code execution
            if tool_name in ["run_python", "run_code", "run_code_in_interpreter"]:
                self.console.print("⏺ [bold]Python[/bold]")
                header_title = "Run Python code"
            elif tool_name == "run_r":
                self.console.print("⏺ [bold]R[/bold]")
                header_title = "Run R code"
            
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
    
    def _setup_input_system(self):
        """Setup simple input system with readline history"""
        if READLINE_AVAILABLE:
            # Setup readline with history
            if self.history_file.exists():
                readline.read_history_file(str(self.history_file))
            atexit.register(readline.write_history_file, str(self.history_file))
            readline.set_history_length(1000)
            
            # Simple readline configuration for better user experience
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind("set completion-ignore-case on")
            
            # Configure readline to prevent prompt corruption
            readline.set_startup_hook(None)
            readline.set_pre_input_hook(None)
            
            # Ensure proper history navigation
            readline.parse_and_bind("\"\\e[A\": previous-history")
            readline.parse_and_bind("\"\\e[B\": next-history")
    
    def _load_history(self):
        """Load command history from file"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.command_history = [line.strip() for line in f.readlines()[-100:]]  # Keep last 100
            except Exception:
                self.command_history = []
    
    def _save_history(self):
        """Save command history to file"""
        try:
            with open(self.history_file, 'a', encoding='utf-8') as f:
                if self.command_history:
                    f.write(self.command_history[-1] + '\n')
        except Exception:
            pass  # Silently ignore history save errors
            
    def _add_to_history(self, command: str):
        """Add command to history"""
        command = command.strip()
        if command and (not self.command_history or self.command_history[-1] != command):
            self.command_history.append(command)
            self._save_history()
            self.history_index = len(self.command_history)
    
    def show_input_panel(self):
        """Show the input panel at bottom"""
        self.console.print("\n")
        self.console.print(self.input_panel)

    def ask_user_input(self) -> str:
        """Get user input with multi-line support and readline history."""
        try:
            self.console.print("[dim]Enter your message (press Enter twice to finish)[/dim]")
            lines = []
            while True:
                # 第一次输入用 "> " 提示，后续行用 "... "
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

    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using rough approximation (4 chars ≈ 1 token)"""
        if not text:
            return 0
        # Simple estimation: ~4 characters per token for English text
        return max(1, len(text) // 4)
    
    def _format_token_count(self, count: int) -> str:
        """Format token count with appropriate units and thousand separators"""
        if count >= 1000000:
            return f"{count/1000000:.1f}M"
        elif count >= 10000:  # Use K for 10K+
            return f"{count/1000:.1f}K"
        elif count >= 1000:   # Use comma separator for 1K-10K
            return f"{count:,}"
        else:
            return str(count)
    
    def _update_token_stats(self, input_tokens: int, output_tokens: int):
        """Update token statistics"""
        self.current_input_tokens = input_tokens
        self.current_output_tokens = output_tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

    async def run(self, message: str | dict | None = None):
        # Suppress verbose logging for cleaner output
        import logging
        logging.getLogger().setLevel(logging.WARNING)
        import loguru
        loguru.logger.remove()
        loguru.logger.add(sys.stdout, level="WARNING")

        # Set up shell toolset callback now that agent is fully configured
        self._setup_shell_toolset_callback()

        # Simple greeting 
        await self.print_greeting()
        
        # Start the message printing task
        print_task = asyncio.create_task(self.print_message())

        # Handle initial message if provided
        current_message = message
        if current_message is not None:
            self._add_to_history(current_message)

        # Main message processing loop
        while True:            
            # Get message (either initial message or new user input)
            if current_message is None:
                try:
                    current_message = self.ask_user_input()
                    # Skip empty messages (from ESC interruption)
                    if not current_message.strip():
                        continue
                    self._add_to_history(current_message)
                except (KeyboardInterrupt, EOFError):
                    self.console.print("\n[dim]Session interrupted[/dim]")
                    self._print_session_summary()
                    break
            
            # Handle special commands FIRST (before sending to API)
            cmd = current_message.lower().strip()
            
            if cmd in ["exit", "quit", "q", "/exit", "/quit", "/q"]:
                self._print_session_summary()
                break
            elif cmd in ["help", "/help"]:
                self._print_help()
                current_message = None  # Reset to get new input
                continue
            elif cmd in ["status", "/status"]:
                self._print_status()
                current_message = None  # Reset to get new input
                continue
            elif cmd in ["clear", "/clear"]:
                self.console.clear()
                await self.print_greeting()
                current_message = None  # Reset to get new input
                continue
            elif cmd in ["history", "/history"]:
                self._print_history()
                current_message = None  # Reset to get new input
                continue
            elif cmd in ["tokens", "/tokens"]:
                self._print_token_analysis()
                current_message = None  # Reset to get new input
                continue
            elif current_message.strip().startswith("/model"):
                self._handle_model_command(current_message.strip())
                current_message = None  # Reset to get new input
                continue
            elif current_message.strip().startswith("/api-key"):
                self._handle_api_key_command(current_message.strip())
                current_message = None  # Reset to get new input
                continue
            elif current_message.strip().startswith("/atac"):
                await self._handle_atac_command(current_message.strip())
                # Check if there's a pending ATAC message to process
                if hasattr(self, '_pending_atac_message'):
                    current_message = self._pending_atac_message
                    del self._pending_atac_message
                    # Continue to process this message
                else:
                    current_message = None  # Reset to get new input
                    continue
            
            # If not a special command, process with agent
            start_time = time.time()
            
            # Estimate input tokens
            input_tokens = self._estimate_tokens(current_message)
            output_tokens = 0
            
            # Create live status with real-time token tracking (Claude Code style)
            content_buffer = []
            
            def process_chunk(chunk: dict):
                content = chunk.get("content")
                if content is not None:
                    content_buffer.append(content)

            # Tetris-style animation frames (different from Claude's *)
            animation_frames = ["▢", "▣", "▤", "▥", "▦", "▧", "▨", "▩"]
            frame_index = 0
            
            # Show Processing message immediately after user input (Claude Code style)
            processing_live = Live(console=self.console, refresh_per_second=4)
            processing_live.start()
            
            try:
                def update_processing_status():
                    nonlocal frame_index
                    current_output_tokens = self._estimate_tokens(''.join(content_buffer))
                    elapsed = time.time() - start_time
                    
                    # Create processing message with animated tetris block and real-time token info
                    current_frame = animation_frames[frame_index % len(animation_frames)]
                    status_text = f"[dim]{current_frame} Processing... • {self._format_token_count(input_tokens)} in, {self._format_token_count(current_output_tokens)} out"
                    if elapsed > 1:
                        status_text += f" • {elapsed:.1f}s"
                    status_text += "[/dim]"
                    
                    processing_live.update(Text.from_markup(status_text))
                    frame_index += 1

                try:
                    # Initial processing status display
                    update_processing_status()
                    
                    # Track if tools are executing
                    self._tools_executing = False
                    
                    def smart_process_chunk(chunk: dict):
                        # Store content
                        process_chunk(chunk)
                        # Only update processing status if no tools are executing
                        if not self._tools_executing:
                            update_processing_status()
                    
                    # Store processing_live reference for tool calls
                    self._current_live_display = processing_live
                    
                    # Process with agent - tool outputs will display independently
                    # Create a cancellable task for the agent processing
                    agent_task = asyncio.create_task(
                        self.agent.run(
                            current_message,
                            process_chunk=smart_process_chunk,
                        )
                    )
                    
                    # Store the task so it can be cancelled on interrupt
                    self._current_agent_task = agent_task
                    
                    try:
                        await agent_task
                    except asyncio.CancelledError:
                        self.console.print("\n[yellow]Operation was cancelled[/yellow]")
                        raise KeyboardInterrupt
                    finally:
                        self._current_agent_task = None
                    
                    # Final output token calculation
                    if content_buffer:
                        full_content = ''.join(content_buffer)
                        if full_content.strip():
                            output_tokens = self._estimate_tokens(full_content)
                    
                    # Update token statistics
                    self._update_token_stats(input_tokens, output_tokens)
                    self.message_count += 1
                    
                except KeyboardInterrupt:
                    #self.console.print("\n[yellow]Operation cancelled by user[/yellow]")
                    # Reset interrupt counter since we handled it gracefully
                    self._interrupt_count = 0
                    # Cancel any running agent task
                    if self._current_agent_task and not self._current_agent_task.done():
                        self._current_agent_task.cancel()
                        try:
                            await self._current_agent_task
                        except asyncio.CancelledError:
                            pass
                    current_message = None  # Reset to get new input
                    continue
                except Exception as e:
                    self.console.print(f"\n[red]Error:[/red] {str(e)}")
                    self.console.print("[dim]You can continue the conversation or type 'exit' to quit[/dim]")
                finally:
                    # Stop processing display
                    processing_live.stop()
                    self._tools_executing = False
                    self._current_live_display = None
                    # Clean up agent task reference
                    if self._current_agent_task and not self._current_agent_task.done():
                        self._current_agent_task.cancel()
                    self._current_agent_task = None
            finally:
                # Ensure processing is stopped
                if 'processing_live' in locals():
                    processing_live.stop()
            
            # Processing is complete - clear the status line and show final content
            self.console.print()  # Clear processing status with newline
            
            # Print accumulated content after processing
            if content_buffer:
                full_content = ''.join(content_buffer)
                if full_content.strip():
                    # Check if content contains code blocks - if so, use plain text
                    if '```' in full_content or 'def ' in full_content or 'import ' in full_content:
                        self.console.print(full_content)
                    else:
                        self.console.print(Markdown(full_content))
            
            self.console.print()  # Add spacing
            current_message = None  # Reset to get new input

        print_task.cancel()
    
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
        if self.python_enabled:
            self.console.print("[dim]write python script to calculate statistics[/dim]")
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

    def _handle_model_command(self, command: str):
        """Handle /model commands in REPL"""
        try:
            if hasattr(self.agent, '_model_manager') and self.agent._model_manager:
                result = self.agent._model_manager.handle_model_command(command)
                # Print result as plain text to avoid formatting issues
                self.console.print(result)
            else:
                self.console.print("[red]Model management not available. Please restart with the CLI.[/red]")
        except Exception as e:
            self.console.print(f"[red]Error handling model command: {str(e)}[/red]")
        self.console.print()  # Add spacing

    def _handle_api_key_command(self, command: str):
        """Handle /api-key commands in REPL"""
        try:
            if hasattr(self.agent, '_api_key_manager') and self.agent._api_key_manager:
                result = self.agent._api_key_manager.handle_api_key_command(command)
                # Print result as plain text to avoid formatting issues
                self.console.print(result)
            else:
                self.console.print("[red]API key management not available. Please restart with the CLI.[/red]")
        except Exception as e:
            self.console.print(f"[red]Error handling API key command: {str(e)}[/red]")
        self.console.print()  # Add spacing

    async def _handle_atac_command(self, command: str):
        """Handle /atac commands for ATAC-seq analysis"""
        parts = command.split(maxsplit=2)
        
        if len(parts) == 1:
            # Just /atac - show help
            self.console.print("\n[bold]🧬 ATAC-seq Analysis Helper[/bold]")
            self.console.print("[dim]/atac init[/dim] - Enter ATAC-seq analysis mode")
            self.console.print("[dim]/atac upstream <folder>[/dim] - Run upstream ATAC-seq analysis on folder")
            self.console.print("[dim]/atac cellranger <folder>[/dim] - Auto-detect and run Cell Ranger ATAC workflow")
            self.console.print("\n[dim]Examples:[/dim]")
            self.console.print("[dim]  /atac init                        # Enter ATAC mode[/dim]")
            self.console.print("[dim]  /atac upstream ./fastq_data       # Analyze bulk ATAC-seq data[/dim]")
            self.console.print("[dim]  /atac cellranger ./scatac_data    # Auto-analyze Cell Ranger ATAC data[/dim]")
            self.console.print()
            return
        
        if parts[1] == "init":
            # Enter ATAC mode - simple mode activation without automation
            self.console.print("\n[bold cyan]🧬 Entering ATAC-seq Analysis Mode[/bold cyan]")
            
            # Clear all existing todos when entering ATAC mode
            clear_message = "Clear all existing todos. I'm now in ATAC-seq analysis mode and ready to help with ATAC-seq analysis tasks."
            self._pending_atac_message = clear_message
            
            self.console.print("[dim]Clearing existing todos and preparing ATAC environment...[/dim]")
            self.console.print("[dim]ATAC-seq mode activated. You can now use ATAC tools directly.[/dim]")
            
            # Show available tools and command structure
            self.console.print("\n[bold green]Available ATAC tools:[/bold green]")
            self.console.print("  - atac.scan_folder() - to scan data folders")
            self.console.print("  - atac.auto_detect_species() - for species detection")
            self.console.print("  - atac.setup_genome_resources() - for reference setup")
            self.console.print("  - atac.run_fastqc(), atac.align_bowtie2(), etc. - for analysis steps")
            self.console.print("  - atac.generate_atac_qc_report() - for QC reports with MultiQC integration")
            self.console.print("  - Todo management tools for tracking progress")
            
            self.console.print("\n[bold blue]The command structure is now clean:[/bold blue]")
            self.console.print("  - /atac init - Enter ATAC mode (simple prompt loading)")
            self.console.print("  - /atac upstream <folder> - Run upstream analysis on specific folder")
            self.console.print("  - /atac cellranger <folder> - Auto-detect and run Cell Ranger ATAC workflow")
            self.console.print()
            
            # Set a simple message to clear todos only
        
        elif parts[1] == "upstream":
            # Run upstream analysis on specific folder
            if len(parts) < 3:
                self.console.print("[red]Error: Please specify a folder path[/red]")
                self.console.print("[dim]Usage: /atac upstream <folder_path>[/dim]")
                self.console.print("[dim]Example: /atac upstream ./fastq_data[/dim]")
                return
                
            try:
                from ..cli.atac_simple import generate_atac_analysis_message
                
                folder_path = parts[2]
                self.console.print(f"\n[bold cyan]🧬 Starting Upstream ATAC-seq Analysis[/bold cyan]")
                self.console.print(f"[dim]Target folder: {folder_path}[/dim]")
                self.console.print("[dim]Preparing upstream analysis pipeline...[/dim]\n")
                
                # Generate the analysis message with folder
                atac_message = generate_atac_analysis_message(folder_path=folder_path)
                
                # Set this as the next message to process
                self._pending_atac_message = atac_message
                
                self.console.print("[dim]Sending upstream ATAC-seq analysis request...[/dim]\n")
                
            except ImportError as e:
                self.console.print(f"[red]Error: ATAC module not available: {e}[/red]")
            except Exception as e:
                self.console.print(f"[red]Error preparing upstream analysis: {str(e)}[/red]")
        
        elif parts[1] == "cellranger":
            if len(parts) < 3:
                self.console.print("[red]Error: Please specify a folder path[/red]")
                self.console.print("[dim]Usage: /atac cellranger <folder_path>[/dim]")
                self.console.print("[dim]Example: /atac cellranger ./scatac_data[/dim]")
                return
                
            try:
                from ..cli.atac_simple import generate_atac_cellranger_message
                
                folder_path = parts[2]
                self.console.print(f"\n[bold cyan]🧬 Starting Cell Ranger ATAC Auto-Analysis[/bold cyan]")
                self.console.print(f"[dim]Target folder: {folder_path}[/dim]")
                self.console.print("[dim]Auto-detecting workflow and data type...[/dim]\n")
                
                # Generate the Cell Ranger ATAC analysis message with folder
                atac_message = generate_atac_cellranger_message(folder_path)
                
                # Set this as the next message to process
                self._pending_atac_message = atac_message
                
                self.console.print("[dim]Sending Cell Ranger ATAC analysis request...[/dim]\n")
                
            except ImportError as e:
                self.console.print(f"[red]Error: ATAC module not available: {e}[/red]")
            except Exception as e:
                self.console.print(f"[red]Error preparing Cell Ranger ATAC analysis: {str(e)}[/red]")
        
        else:
            self.console.print(f"[red]Unknown ATAC command: {parts[1]}[/red]")
            self.console.print("[dim]Available commands:[/dim]")
            self.console.print("[dim]  /atac init - Enter ATAC mode[/dim]")
            self.console.print("[dim]  /atac upstream <folder> - Run upstream analysis[/dim]")
            self.console.print("[dim]  /atac cellranger <folder> - Run Cell Ranger ATAC auto-analysis[/dim]")
        
        self.console.print()  # Add spacing


if __name__ == "__main__":
    agent = Agent(
        "agent",
        "You are a helpful assistant."
    )
    repl = Repl(agent)
    asyncio.run(repl.run())
