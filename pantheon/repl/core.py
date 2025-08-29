import asyncio
import sys
import time
import signal
from datetime import datetime
from pathlib import Path

from rich.text import Text
from rich.live import Live
from rich.panel import Panel
from rich.markdown import Markdown
import yaml

# Simple readline support for history
try:
    import readline
    import atexit
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

from ..agent import Agent
from ..constant import CLI_HISTORY_FILE
from .ui import ReplUI
from .handlers.base import CommandHandler
from .handlers.template_handler import TemplateHandler
from .handlers.builtin.bash import BashCommandHandler
from .handlers.builtin.r import RCommandHandler
from .handlers.builtin.julia import JuliaCommandHandler
from .handlers.builtin.python import PythonCommandHandler
from .handlers.builtin.model_manager import ModelManagerCommandHandler


class Repl(ReplUI):
    """REPL for a single agent.

    Args:
        agent: The agent to use for the REPL.
    """
    def __init__(self, agent: Agent):
        super().__init__()  # init UI
        self.agent = agent
        
        self.current_task = None
        self.tool_calls_active = False
        self.session_start = datetime.now()
        self.message_count = 0
        self.python_enabled = False
        
        # Token statistics
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.current_input_tokens = 0
        self.current_output_tokens = 0
        
        # Processing status tracking
        self._current_live_display = None
        self._tools_executing = False
        self._current_agent_task = None
        self._current_tool_name = None
        
        # Setup history file
        self.history_file = Path(CLI_HISTORY_FILE)
        self.command_history = []
        self.history_index = -1

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

        self.handlers: list[CommandHandler] = [
            BashCommandHandler(self.console, self),
            RCommandHandler(self.console, self),
            JuliaCommandHandler(self.console, self),
            PythonCommandHandler(self.console, self),
            ModelManagerCommandHandler(self.console, self),
        ]

    def register_handler(self, handler: CommandHandler | str | Path):
        """Register a handler for processing commands
        
        Args:
            handler: A CommandHandler instance, a path to a YAML file, or a string.
        
        If a path or string is provided, the YAML file will be loaded and used to create a TemplateHandler instance.
        If a CommandHandler instance is provided, it will be added to the handlers list.
        """
        if isinstance(handler, CommandHandler):
            self.handlers.append(handler)
        elif isinstance(handler, str) or isinstance(handler, Path):
            yaml_path = Path(handler)
            if yaml_path.exists():
                with open(yaml_path, 'r') as f:
                    template = yaml.safe_load(f)
                template_handler = TemplateHandler(self.console, self, template)
                self.handlers.append(template_handler)
            else:
                self.console.print(f"[red]Template file not found: {yaml_path}[/red]")

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
                prompt_text = "... " if lines else ">   "

                if READLINE_AVAILABLE:
                    line = input(prompt_text)
                else:
                    self.console.print(f"[bright_blue]{prompt_text}[/bright_blue]", end=" ")
                    line = input()

                if line.strip() == "":
                    # Empty line ends input
                    break

                lines.append(line)

            # Return multi-line string
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
    
    def _add_output_tokens_estimate(self, content: str):
        """Add estimated tokens from tool calls or other agent messages"""
        if hasattr(self, 'estimated_output_tokens'):
            additional_tokens = self._estimate_tokens(content)
            self.estimated_output_tokens += additional_tokens

    async def run(self, message: str | dict | None = None):

        # Simple greeting 
        await self.print_greeting()
        
        # Set up connection between UI and token tracking
        self._parent_repl = self
        
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
            
            # Record user input in conversation history (except for special commands)
            if not current_message.strip().startswith('/'):
                self.add_to_conversation("user", current_message)
            
            # Handle special commands FIRST (before sending to API)
            cmd = current_message.strip()
            
            cmd_lower = cmd.lower()
            
            if cmd_lower in ["exit", "quit", "q", "/exit", "/quit", "/q"]:
                self._print_session_summary()
                break
            elif cmd_lower in ["help", "/help"]:
                self._print_help()
                current_message = None  # Reset to get new input
                continue
            elif cmd_lower in ["status", "/status"]:
                self._print_status()
                current_message = None  # Reset to get new input
                continue
            elif cmd_lower in ["clear", "/clear"]:
                self.console.clear()
                await self.print_greeting()
                current_message = None  # Reset to get new input
                continue
            elif cmd_lower in ["history", "/history"]:
                self._print_history()
                current_message = None  # Reset to get new input
                continue
            elif cmd_lower in ["tokens", "/tokens"]:
                self._print_token_analysis()
                current_message = None  # Reset to get new input
                continue
            elif cmd_lower in ["/save"] or current_message.strip().lower().startswith("/save"):
                self._handle_save_command(current_message.strip())
                current_message = None  # Reset to get new input
                continue
            
            # Handle custom commands
            continue_flag = False
            for handler in self.handlers:
                if handler.match_command(cmd):
                    current_message = await handler.handle_command(cmd)
                    if current_message is not None:
                        continue_flag = False
                    else:
                        continue_flag = True
                    break
            if continue_flag:
                continue
            
            # If not a special command, process with agent
            start_time = time.time()
            
            # Estimate input tokens
            input_tokens = self._estimate_tokens(current_message)
            output_tokens = 0
            
            # Create live status with real-time token tracking (Claude Code style)
            content_buffer = []
            estimated_output_tokens = 0  # Track estimated output tokens from all sources
            
            def process_chunk(chunk: dict):
                nonlocal estimated_output_tokens
                content = chunk.get("content")
                if content is not None:
                    content_buffer.append(content)
                    # Update estimated tokens when we get new content
                    estimated_output_tokens = self._estimate_tokens(''.join(content_buffer))

            # Tetris-style animation frames (different from Claude's *)
            animation_frames = ["▢", "▣", "▤", "▥", "▦", "▧", "▨", "▩"]
            frame_index = 0
            
            # Show Processing message immediately after user input (Claude Code style)
            processing_live = Live(console=self.console, refresh_per_second=4)
            processing_live.start()
            
            try:
                def update_processing_status():
                    nonlocal frame_index
                    # Use the estimated output tokens (updated by process_chunk and tool calls)
                    current_output_tokens = estimated_output_tokens
                    elapsed = time.time() - start_time
                    
                    # Create processing message with animated tetris block and real-time token info
                    current_frame = animation_frames[frame_index % len(animation_frames)]
                    
                    # Base status with animation and token info
                    if self._current_tool_name and self._tools_executing:
                        # Show tool name only when currently executing
                        status_text = f"[dim]{current_frame} Running [bold cyan]{self._current_tool_name}[/bold cyan]... • {self._format_token_count(input_tokens)} in, {self._format_token_count(current_output_tokens)} out"
                    else:
                        # Default processing message
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
                        # Always update processing status for real-time feedback
                        update_processing_status()
                    
                    # Store processing_live reference for tool calls
                    self._current_live_display = processing_live
                    
                    # Create a background task to keep updating progress during toolset execution
                    progress_update_task = None
                    async def periodic_progress_update():
                        """Background task to update progress during toolset execution"""
                        while not agent_task.done():
                            await asyncio.sleep(0.25)  # Update 4 times per second
                            if not agent_task.done():
                                update_processing_status()
                    
                    # Process with agent - tool outputs will display independently
                    # Create a cancellable task for the agent processing
                    agent_task = asyncio.create_task(
                        self.agent.run(
                            current_message,
                            process_chunk=smart_process_chunk,
                        )
                    )
                    
                    # Start background progress update task
                    progress_update_task = asyncio.create_task(periodic_progress_update())
                    
                    # Store the task so it can be cancelled on interrupt
                    self._current_agent_task = agent_task
                    
                    try:
                        await agent_task
                    except asyncio.CancelledError:
                        self.console.print("\n[yellow]Operation was cancelled[/yellow]")
                        raise KeyboardInterrupt
                    finally:
                        self._current_agent_task = None
                        # Cancel progress update task
                        if progress_update_task and not progress_update_task.done():
                            progress_update_task.cancel()
                            try:
                                await progress_update_task
                            except asyncio.CancelledError:
                                pass
                    
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
                    # Cancel progress update task
                    if 'progress_update_task' in locals() and progress_update_task and not progress_update_task.done():
                        progress_update_task.cancel()
                        try:
                            await progress_update_task
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
                    # Clear tool name for next request
                    self._current_tool_name = None
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
                    # Record AI response in conversation history
                    self.add_to_conversation("assistant", full_content.strip())
                    
                    # Check if content contains code blocks - if so, use plain text
                    if '```' in full_content or 'def ' in full_content or 'import ' in full_content:
                        self.console.print(full_content)
                    else:
                        self.console.print(Markdown(full_content))
            
            self.console.print()  # Add spacing
            current_message = None  # Reset to get new input

        print_task.cancel()
    
    def _handle_save_command(self, command: str):
        """Handle /save commands in REPL"""
        try:
            parts = command.split()
            filename = None
            
            if len(parts) > 1:
                # User specified a filename: /save myfile.md
                filename = parts[1]
                if not filename.endswith('.md'):
                    filename += '.md'
            
            # Check if there's conversation history to save
            if not hasattr(self, 'conversation_history') or not self.conversation_history:
                self.console.print("[yellow]No conversation history to save yet.[/yellow]")
                return
            
            # Export conversation to markdown
            saved_file = self.export_conversation_to_markdown(filename)
            self.console.print(f"[green]✅ Conversation saved to:[/green] {saved_file}")
            
        except Exception as e:
            self.console.print(f"[red]Error saving conversation: {str(e)}[/red]")
        self.console.print()  # Add spacing

    # Bio command handling moved to bio_handler.py


if __name__ == "__main__":
    agent = Agent(
        "agent",
        "You are a helpful assistant."
    )
    repl = Repl(agent)
    asyncio.run(repl.run())
