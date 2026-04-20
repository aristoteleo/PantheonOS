"""REPL - Command line interface for Pantheon agents, based on ChatRoom."""

import asyncio
import re
import sys
import time
import signal
import threading
from typing import List, Dict, Any
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

# Import suppress_aiohttp_warnings (log.py also registers warning filters on import)
from pantheon.utils.log import suppress_aiohttp_warnings, logger

from rich.text import Text
from rich.live import Live
from rich.markdown import Markdown

# prompt_toolkit for enhanced input
from prompt_toolkit.patch_stdout import patch_stdout

# readline support (fallback for ask_user_input only)
try:
    import readline
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

from pantheon.agent import Agent
from pantheon.team import Team
from pantheon.team.pantheon import PantheonTeam
from pantheon.chatroom import ChatRoom
from pantheon.constant import CLI_HISTORY_FILE
from .ui import ReplUI
from .renderers import DisplayMode
from .task_renderers import TaskUIRenderer, NotifyUIRenderer
from .handlers.base import CommandHandler
from .handlers.template_handler import TemplateHandler, load_template
from .handlers.builtin.bash import BashCommandHandler
from .prompt_app import PantheonInputApp, ReplCompleter
from .utils import get_animation_frames, get_separator, format_tool_name, format_relative_time


class Repl(ReplUI):
    """REPL for agent or team interaction, based on ChatRoom.

    Supports multiple initialization modes:
    - agent: Pass an Agent or Team directly (legacy mode, creates embedded ChatRoom)
    - chatroom: Pass an existing ChatRoom instance
    - endpoint: Pass an Endpoint instance (creates ChatRoom with it)
    - None: Auto-create ChatRoom with embedded Endpoint

    Args:
        agent: An Agent or Team instance (legacy mode).
        chatroom: An existing ChatRoom instance.
        endpoint: An Endpoint instance to create ChatRoom with.
        memory_dir: Directory for chat persistence.
        chat_id: Specific chat ID to use (creates new if None).
    """

    def __init__(
        self,
        agent: Agent | Team | None = None,
        chatroom: ChatRoom | None = None,
        endpoint: "Endpoint | None" = None,
        memory_dir: str | None = None,
        chat_id: str | None = None,
    ):
        if memory_dir is None:
            from pantheon.settings import get_settings
            memory_dir = str(get_settings().memory_dir)
        super().__init__()  # init UI

        # Determine ChatRoom source
        if chatroom is not None:
            # Mode 1: Use existing ChatRoom
            self._chatroom = chatroom
        elif agent is not None:
            # Mode 2: Legacy - create ChatRoom from Agent/Team
            self._chatroom = self._create_chatroom_from_agent(agent, memory_dir)
        elif endpoint is not None:
            # Mode 3: Create ChatRoom with provided Endpoint
            from pantheon.settings import get_settings
            settings = get_settings()
            self._chatroom = ChatRoom(
                endpoint=endpoint,
                memory_dir=memory_dir,
                enable_nats_streaming=False,
            )
        else:
            # Mode 4: Auto-create everything
            from pantheon.settings import get_settings
            settings = get_settings()
            self._chatroom = ChatRoom(
                endpoint=None,  # Auto-create Endpoint
                memory_dir=memory_dir,
                enable_nats_streaming=False,
            )

        # Current chat session
        self._chat_id = chat_id

        # Reference to team for UI display (will be set after first chat)
        self._team: PantheonTeam | None = None
        if agent is not None:
            if isinstance(agent, Team):
                self._team = agent
            else:
                self._team = PantheonTeam([agent])

        # Set multi-agent mode flag (for UI display)
        self._is_multi_agent = (
            self._team is not None and len(self._team.agents) > 1
        )

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
        self._current_tool_name = None
        # Flag to skip stale token update after compression
        self._skip_token_update = False

        # Setup history file
        self.history_file = Path(CLI_HISTORY_FILE)
        if not self.history_file.exists():
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            self.history_file.touch()
        self.command_history = []
        self.history_index = -1

        # Migrate legacy history format if needed, then load
        self._migrate_history_if_needed()
        self._load_history()

        # Setup signal handlers for better interrupt handling
        self._setup_signal_handlers()

        # Command handlers
        from .handlers.builtin.view import ViewCommandHandler
        from .handlers.builtin.mcp import MCPCommandHandler
        from .handlers.builtin.edit import EditHandler
        from .handlers.builtin.revert import RevertCommandHandler
        self.handlers: list[CommandHandler] = [
            BashCommandHandler(self.console, self),
            ViewCommandHandler(self.console, self),
            MCPCommandHandler(self.console, self),
            EditHandler(self.console, self),
            RevertCommandHandler(self.console, self),
        ]

        # Save terminal state for restoration on exit
        self._saved_terminal_attrs = None
        try:
            import termios
            self._saved_terminal_attrs = termios.tcgetattr(sys.stdin.fileno())
        except Exception:
            pass

        # prompt_toolkit application for enhanced input
        self.prompt_app: PantheonInputApp | None = None
        self._use_prompt_toolkit = True  # Enable new UI by default
        self.message_queue = None
        
        # Task UI renderers
        self.task_ui_renderer = TaskUIRenderer(self.console)
        self.notify_ui_renderer = NotifyUIRenderer(self.console)

        # Cache last listed team files for index selection in /team
        self._last_team_files: list[dict] | None = None
        
        # ✅ P3.1: Real-time status bar update
        self._status_update_task: asyncio.Task | None = None
        self._is_processing = False
        self._status_update_requested = False
        self._last_status_update = 0.0
        
        # Pending user approval for notify_user        # Pending operations state
        self._pending_approval: dict | None = None
        self._pending_clear_confirmation: bool = False  # For /clear confirmation

    def _create_chatroom_from_agent(
        self, agent: Agent | Team, memory_dir: str
    ) -> ChatRoom:
        """Create ChatRoom from Agent/Team (legacy compatibility)."""
        # Wrap single Agent in PantheonTeam
        if isinstance(agent, Team):
            team = agent
        else:
            team = PantheonTeam([agent])

        # Create ChatRoom with default_team (bypasses template system)
        from pantheon.settings import get_settings
        settings = get_settings()
        return ChatRoom(
            endpoint=None,  # Auto-create Endpoint
            memory_dir=memory_dir,
            enable_nats_streaming=False,
            default_team=team,
        )

    def register_handler(self, handler: CommandHandler | str | Path):
        """Register a handler for processing commands."""
        if isinstance(handler, CommandHandler):
            self.handlers.append(handler)
        elif isinstance(handler, (str, Path)):
            p = Path(handler)
            if p.exists():
                template = load_template(p)
                template_handler = TemplateHandler(self.console, self, template)
                self.handlers.append(template_handler)
            else:
                self.console.print(f"[red]Template file not found: {p}[/red]")

    def _setup_bg_complete_hooks(self):
        """Wire bg task completion → REPL message_queue.

        Uses Agent.setup_bg_notify_queue() to push notifications into the
        REPL's message_queue, triggering _processing_loop on completion.
        """
        if not self._team or not self.message_queue:
            return
        for agent in self._team.agents.values():
            if hasattr(agent, "setup_bg_notify_queue"):
                agent.setup_bg_notify_queue(self.message_queue)

    def _setup_signal_handlers(self):
        """Setup signal handlers for better interrupt management."""
        self._interrupt_count = 0
        self._last_interrupt_time = 0.0

        def signal_handler(signum, frame):
            should_exit = self.handle_interrupt()
            if should_exit:
                # Use prompt_toolkit's app.exit() for proper terminal restoration
                if self.prompt_app and hasattr(self.prompt_app, 'app') and self.prompt_app.app.is_running:
                    self.prompt_app.app.exit()
                else:
                    sys.exit(1)

        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, signal_handler)

    async def _cleanup_resources(self):
        """Clean up resources before exit."""
        # Clean up ChatRoom resources (stops learning pipeline which saves skillbook)
        try:
            if hasattr(self, '_chatroom') and self._chatroom:
                await self._chatroom.cleanup()
        except Exception:
            pass

    def handle_interrupt(self) -> bool:
        """Handle Ctrl+C interrupt with double-press logic.
        
        Returns:
            True if should exit (double press), False otherwise.
        """
        current_time = time.time()

        if current_time - self._last_interrupt_time < 2.0:
            self._interrupt_count += 1
        else:
            self._interrupt_count = 1

        self._last_interrupt_time = current_time

        if self._interrupt_count == 1:
            self.output.console.print(
                "\n[yellow]Press Ctrl+C again within 2 seconds to exit[/yellow]"
            )
            # Cancel any running agent task
            if (
                hasattr(self, "_current_agent_task")
                and self._current_agent_task
                and not self._current_agent_task.done()
            ):
                try:
                    self._current_agent_task.cancel()
                except Exception:
                    pass
            return False
        elif self._interrupt_count >= 2:
            # Simple sync summary (async version not available in signal handler)
            self.console.print(f"\n[dim]Session: {self.message_count} messages[/dim]")
            self.console.print("[dim]Goodbye![/dim]")
            return True
        return False

    async def _processing_loop(self):
        """Concurrent loop for processing messages from queue."""
        while True:
            try:
                # Get message from queue
                current_message = await self.message_queue.get()
                
                # Check for exit command first (handled here to stop processing)
                if current_message.strip().lower() in ["exit", "quit", "q", "/exit", "/quit", "/q"]:
                     self.message_queue.task_done()
                     await self._print_session_summary()
                     # We can't easily break the main run wait, so we might need a signal or just let the input app exit
                     # Actually input app exit is handled by prompt_toolkit Exit exception usually.
                     # But here we are consuming from queue. 
                     # Let's handle commands same as before.
                     pass 

                # Show brief notification for bg task completions
                bg_match = re.match(
                    r"\[Background task '([^']+)' \(([^)]+)\) (\w+)\.",
                    current_message,
                )
                if bg_match:
                    task_id, tool_name, status = bg_match.groups()
                    # Strip agent prefix from tool name
                    short_name = tool_name.split("__")[-1] if "__" in tool_name else tool_name
                    color = {"completed": "green", "failed": "red", "cancelled": "yellow"}.get(status, "blue")
                    self.console.print(
                        f"\n  [{color}]⬤[/{color}] Background task [bold]{short_name}[/bold] "
                        f"[{color}]{status}[/{color}] [dim]({task_id})[/dim]\n"
                    )

                # Process message
                if self.prompt_app:
                    self.prompt_app.start_processing(self._estimate_tokens(current_message))

                    # ✅ P3.1: Start status update loop
                    if not self._is_processing:
                        self._is_processing = True
                        self._last_status_update = time.time()
                        self._status_update_task = asyncio.create_task(self._status_update_loop())

                    # Yield to event loop to let prompt_toolkit render first frame
                    await asyncio.sleep(0)

                try:
                    # Reuse existing logic to process command or chat
                    # We need to adapt the command handling logic from run() to here
                    await self._handle_message_or_command(current_message)
                except Exception as e:
                    self.console.print(f"[red]Error processing message: {e}[/red]")
                finally:
                    if self.prompt_app:
                        # ✅ P3.1: Request final update before stopping
                        self._status_update_requested = True
                        await asyncio.sleep(0.1)  # Give update loop time to process
                        
                        # Stop processing and background loop
                        self._is_processing = False
                        if self._status_update_task:
                            self._status_update_task.cancel()
                            try:
                                await self._status_update_task
                            except asyncio.CancelledError:
                                pass
                            self._status_update_task = None
                        
                        self.prompt_app.stop_processing()
                        # Final update after processing: use accurate full calculation
                        # so idle ctx: display matches /tokens output
                        await self._update_status_bar_accurate()
                    self.message_queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.console.print(f"[red]Processing loop error: {e}[/red]")
                await asyncio.sleep(1)

    def _migrate_history_if_needed(self):
        """Migrate legacy or mixed-format history to clean FileHistory format.

        Legacy format: one command per line (raw text).
        FileHistory format: '# timestamp' header, then '+' prefixed lines per entry.

        Handles mixed files by processing lines sequentially, preserving order.
        """
        if not self.history_file.exists():
            return
        try:
            raw = self.history_file.read_bytes()
            if not raw.strip():
                return
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()

            # Detect if migration is needed: any non-empty, non-comment, non-'+' lines
            needs_migration = False
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not stripped.startswith("+"):
                    needs_migration = True
                    break

            if not needs_migration:
                return

            # Parse mixed format sequentially to preserve order
            commands = []
            pending_plus_lines = []

            def flush_plus():
                if pending_plus_lines:
                    cmd = "\n".join(pending_plus_lines)
                    commands.append(cmd)
                    pending_plus_lines.clear()

            for line in lines:
                if line.startswith("+"):
                    pending_plus_lines.append(line[1:].rstrip("\r"))
                elif line.startswith("#") or not line.strip():
                    flush_plus()
                else:
                    flush_plus()
                    cleaned = line.strip().strip("\r")
                    if cleaned:
                        commands.append(cleaned)
            flush_plus()

            # Filter out garbage (bare timestamps, '+' prefixed from broken migration)
            clean = []
            for cmd in commands:
                if cmd.startswith("#") or cmd.startswith("+"):
                    continue
                clean.append(cmd)

            # Deduplicate consecutive
            deduped = []
            for cmd in clean:
                if not deduped or deduped[-1] != cmd:
                    deduped.append(cmd)

            # Rewrite in clean FileHistory format
            with open(self.history_file, "wb") as f:
                for cmd in deduped:
                    f.write(b"\n# migrated\n")
                    for cmd_line in cmd.split("\n"):
                        f.write(f"+{cmd_line}\n".encode("utf-8"))

            logger.debug(f"Migrated {len(deduped)} history entries to FileHistory format")
        except Exception as e:
            logger.debug(f"History migration skipped: {e}")

    def _load_history(self):
        """Load command history from FileHistory-format file."""
        from prompt_toolkit.history import FileHistory
        try:
            fh = FileHistory(str(self.history_file))
            # load_history_strings returns newest-first, we want chronological
            self.command_history = list(reversed(list(fh.load_history_strings())))
            # Keep only last 500
            if len(self.command_history) > 500:
                self.command_history = self.command_history[-500:]
        except Exception:
            self.command_history = []

    def _add_to_history(self, command: str):
        """Add command to in-memory history list.

        File persistence is handled by prompt_toolkit's FileHistory (via
        buffer.append_to_history() in PantheonInputApp.accept_input).
        This method only updates the in-memory list for /history display.
        """
        command = command.strip()
        if not command:
            return
        if self.command_history and self.command_history[-1] == command:
            return
        self.command_history.append(command)
        self.history_index = len(self.command_history)

    def _persist_to_history_file(self, command: str):
        """Write a single command to the history file in FileHistory format.

        Only needed for commands not submitted via prompt_toolkit (e.g. initial message).
        """
        try:
            import datetime
            with open(self.history_file, "ab") as f:
                f.write(f"\n# {datetime.datetime.now()}\n".encode("utf-8"))
                for line in command.split("\n"):
                    f.write(f"+{line}\n".encode("utf-8"))
        except Exception:
            pass

    def ask_user_input(self) -> str:
        """Get user input with multi-line support and readline history."""
        try:
            self.console.print(
                "[dim]Enter your message (press Enter twice to finish)[/dim]"
            )
            lines = []
            while True:
                prompt_text = "... " if lines else ">   "

                if READLINE_AVAILABLE:
                    line = input(prompt_text)
                else:
                    self.console.print(
                        f"[bright_blue]{prompt_text}[/bright_blue]", end=" "
                    )
                    line = input()

                if line.strip() == "":
                    break

                lines.append(line)

            return "\n".join(lines).strip()

        except KeyboardInterrupt:
            self.console.print("\n[dim]Ctrl+C pressed - operation cancelled[/dim]")
            return ""
        except EOFError:
            raise

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text.
        
        Reuses the language-aware estimation from llm._fallback_token_count.
        """
        from pantheon.utils.llm import _fallback_token_count
        return _fallback_token_count(text)

    def _format_token_count(self, count: int) -> str:
        """Format token count with appropriate units."""
        if count >= 1000000:
            return f"{count/1000000:.1f}M"
        elif count >= 10000:
            return f"{count/1000:.1f}K"
        elif count >= 1000:
            return f"{count:,}"
        else:
            return str(count)

    def _update_token_stats(self, input_tokens: int, output_tokens: int):
        """Update token statistics."""
        self.current_input_tokens = input_tokens
        self.current_output_tokens = output_tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

    async def _update_status_bar_token_usage(self):
        """Update token usage percentage in status bar (async).

        Optimized to use cached reads from message metadata instead of
        full token counting for better performance.
        """
        if not self.prompt_app:
            return

        # After compression, _handle_compress already set an accurate estimate.
        # Skip this call to avoid overwriting with stale metadata from
        # preserved messages. The next LLM response will clear the flag.
        if self._skip_token_update:
            self._skip_token_update = False
            return
        
        try:
            # ✅ Fast path: Read from cached metadata (O(1))
            if self._chatroom and self._chat_id:
                # Read-only: getting token statistics, no need to fix
                memory = self._chatroom.memory_manager.get_memory(self._chat_id)
                if memory:
                    # Get root agent messages with LLM view (respects compression truncation)
                    messages = memory.get_messages(execution_context_id=None, for_llm=True)
                    
                    if messages:
                        # Find last message with token metadata
                        # After compression, there may be no assistant message yet
                        last_with_tokens = next(
                            (m for m in reversed(messages)
                             if "_metadata" in m and "total_tokens" in m["_metadata"]),
                            None
                        )
                        
                        if last_with_tokens:
                            meta = last_with_tokens["_metadata"]
                            total_tokens = meta.get("total_tokens", 0)
                            max_tokens = meta.get("max_tokens", 200000)

                            # Calculate usage percentage
                            usage_pct = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0

                            # Calculate total cost from all messages (including compressed)
                            all_messages = memory.get_messages(for_llm=False)
                            from pantheon.utils.llm import calculate_total_cost_from_messages
                            total_cost = calculate_total_cost_from_messages(all_messages)

                            # Update status bar
                            self.prompt_app.update_token_usage(usage_pct, total_cost)
                        # else: no valid metadata (e.g., after compression) — fall through to accurate path
                        return

            # Fallback: Use detailed stats if fast path fails (e.g., no metadata found)
            from .utils import get_detailed_token_stats
            fallback = {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "message_count": self.message_count,
            }
            token_info = await get_detailed_token_stats(
                self._chatroom, self._chat_id, self._team, fallback
            )
            usage_pct = token_info.get("usage_percent", 0)
            total_cost = token_info.get("total_cost") or 0.0
            self.prompt_app.update_token_usage(usage_pct, total_cost)

        except Exception:
            pass  # Silently ignore errors


    async def _update_status_bar_accurate(self):
        """Accurate status bar update using full token calculation (same as /tokens).

        Called once after processing completes so the idle display matches /tokens.
        Slower than _update_status_bar_token_usage but gives the correct value.
        """
        if not self.prompt_app:
            return
        try:
            from .utils import get_detailed_token_stats
            fallback = {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "message_count": self.message_count,
            }
            token_info = await get_detailed_token_stats(
                self._chatroom, self._chat_id, self._team, fallback
            )
            usage_pct = token_info.get("usage_percent", 0)
            total_cost = token_info.get("total_cost") or 0.0
            self.prompt_app.update_token_usage(usage_pct, total_cost)
        except Exception:
            pass  # Silently ignore errors

    async def _status_update_loop(self):
        """Background loop for real-time status bar updates.
        
        Updates occur on:
        1. Periodic intervals (every 8 seconds)
        2. Event triggers (when _status_update_requested is set)
        
        Includes debouncing (min 3s between updates) to prevent excessive calls.
        """
        UPDATE_INTERVAL = 8  # Periodic update every 8 seconds
        MIN_INTERVAL = 3      # Minimum time between updates (debouncing)
        
        while self._is_processing:
            try:
                now = time.time()
                should_update = False
                
                # Check 1: Periodic update
                if now - self._last_status_update >= UPDATE_INTERVAL:
                    should_update = True
                
                # Check 2: Event-driven update (with debouncing)
                if self._status_update_requested:
                    if now - self._last_status_update >= MIN_INTERVAL:
                        should_update = True
                        self._status_update_requested = False
                
                # Perform update if needed
                if should_update:
                    await self._update_status_bar_token_usage()
                    self._last_status_update = now
                
                # Sleep before next check
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                # Clean shutdown
                break
            except Exception as e:
                logger.debug(f"Status update loop error: {e}")
                await asyncio.sleep(1)


    async def _setup(self):
        """Initialize REPL session (chat, team, and background services)."""
        # Create or get chat session
        if self._chat_id is None:
            result = await self._chatroom.create_chat("repl-session")
            self._chat_id = result["chat_id"]

        # Get team reference for UI display
        if self._team is None:
            self._team = await self._chatroom.get_team_for_chat(self._chat_id, save_to_memory=False)
            self._is_multi_agent = len(self._team.agents) > 1

        # Start ChatRoom setup in background (MCP servers, etc.)
        # This runs after UI is shown, so user sees REPL immediately
        # After setup, warm up tools cache and LLM connection to reduce first-message latency
        asyncio.create_task(self._setup_and_warmup())

    async def _setup_and_warmup(self):
        """Run ChatRoom setup then pre-populate tools cache.

        This runs in the background so the REPL prompt appears immediately.
        Pre-fetching tools eliminates ~700ms on the first message.
        """
        try:
            await self._chatroom.run_setup()
        except Exception as e:
            logger.error(f"ChatRoom setup failed: {e}")
            return

        # Pre-populate tools cache so first message doesn't pay the cost
        try:
            if self._team and self._team.agents:
                agent = next(iter(self._team.agents.values()), None)
                if agent:
                    await agent.get_tools_for_llm()
                    logger.debug("[WARMUP] Tools cache populated")
        except Exception as e:
            logger.debug(f"[WARMUP] Tools warmup error (non-critical): {e}")

    async def run(self, message: str | dict | None = None, disable_logging: bool = True, log_to_file: bool = True, log_level: str = "CRITICAL"):
        """Main REPL loop.
        
        Args:
            message: Optional initial message to process
            disable_logging: If True, suppress console logging (only show ERROR)
            log_to_file: If True, save all logs to .pantheon/logs/ (even when console logging is suppressed)
        """
        # Setup file logging FIRST (before suppressing console output)
        # This ensures all logs are captured to file for debugging
        if log_to_file:
            from pantheon.settings import get_settings
            from pantheon.utils.log import setup_file_logging
            
            # Save logs to 'repl' subdirectory
            log_dir = get_settings().logs_dir / "repl"
            log_file = setup_file_logging(log_dir=log_dir, session_name="repl")
            self._log_file = log_file  # Store for reference
        
        if disable_logging:
            from pantheon.utils.log import set_level
            set_level("ERROR")  # Only show ERROR level on console, file still captures all

        # Initialize
        _resuming_chat = self._chat_id is not None  # Pre-set chat_id means resuming
        await self._setup()
        self.message_queue = asyncio.Queue()
        # Hook bg task completion → message_queue (must be after queue creation)
        self._setup_bg_complete_hooks()

        # Print greeting first (REPL shows immediately)
        await self.print_greeting()

        # Initialize prompt_toolkit app if enabled
        if self._use_prompt_toolkit:
            self.prompt_app = PantheonInputApp(
                str(self.history_file),
                ReplCompleter(self),
                self,
                self.message_queue
            )
            # Update model/agent info
            if self._team and self._team.agents:
                agent_names = list(self._team.agents.keys())
                if agent_names:
                    first_agent_name = agent_names[0]
                    agent = self._team.agents[first_agent_name]
                    
                    # Extract model name handling both scalar 'model' and list 'models'
                    model = getattr(agent, 'model', 'unknown')
                    if hasattr(agent, 'models'):
                         models = agent.models
                         if isinstance(models, list) and models:
                             model = models[0]
                         elif isinstance(models, str):
                             model = models
                             
                    self.prompt_app.update_model(model)
                    self.prompt_app.update_agent(first_agent_name)
            
            # Set prompt_app reference for task UI renderer
            self.task_ui_renderer.set_prompt_app(self.prompt_app)

            # If resuming a chat (chat_id was pre-set), load session history
            _replay_msgs = None
            if _resuming_chat:
                try:
                    from pantheon.utils.misc import run_func
                    memory = await run_func(self._chatroom.memory_manager.get_memory, self._chat_id)
                    if memory:
                        # Root-level user messages for ↑/↓ history
                        root_msgs = memory.get_messages(execution_context_id=None, for_llm=False)
                        user_inputs = [
                            m["content"] for m in root_msgs
                            if m.get("role") == "user" and isinstance(m.get("content"), str)
                        ]
                        if user_inputs:
                            self.prompt_app.set_session_history(user_inputs)
                            self.command_history = user_inputs.copy()
                            self.history_index = len(self.command_history)
                        # Save for replay after patch_stdout is active
                        _replay_msgs = memory.get_messages(for_llm=False)
                except Exception:
                    pass

            # Note: Renderers will be re-initialized inside patch_stdout context in loop

        self._parent_repl = self

        # Handle initial message if provided
        if message is not None:
            self._add_to_history(message)
            self._persist_to_history_file(message)
            self.message_queue.put_nowait(message)

        # Main concurrency setup
        try:
             # Logic split: 
             # 1. Input App (run_async) - handles user typing
             # 2. Processing Loop - handles commands/agents
             
             if self._use_prompt_toolkit:
                 with patch_stdout(raw=True):
                     # Enter patch context for output adapter
                     self.output.enter_patch_context()
                     # Reinitialize renderers with patched console
                     self._init_renderers()
                      
                     # Reconfigure loguru to use patched stdout
                     # This ensures log output doesn't break the REPL rendering
                     # Use set_level instead of remove() to preserve file logging
                     from pantheon.utils.log import set_level
                     set_level(log_level)

                     # Replay chat history if resuming (must be after renderers init + patch_stdout)
                     if _replay_msgs:
                         self._replay_chat_history(_replay_msgs)

                     # Create background processing task
                     processing_task = asyncio.create_task(self._processing_loop())
                     
                     try:
                         try:
                             # Run input app (blocks until app exit)
                             await self.prompt_app.run_async()
                         except (EOFError, KeyboardInterrupt):
                             pass # Suppress stack traces on exit
                         except Exception as e:
                             # Log unexpected exceptions instead of letting them propagate
                             # This prevents "Press ENTER to continue..." or similar prompts
                             logger.warning(f"Prompt app exception: {e}")
                     finally:
                         # Cancel processing loop on exit
                         processing_task.cancel()
                         try:
                             await processing_task
                         except asyncio.CancelledError:
                             pass
             else:
                 # Fallback legacy loop (readline)
                 while True:
                     msg = self.ask_user_input()
                     if not msg.strip(): continue
                     await self._handle_message_or_command(msg)

        finally:
            # Clean up ChatRoom resources (saves skillbook via learning pipeline)
            await self._cleanup_resources()

            # Suppress any remaining aiohttp/SSL warnings during GC
            try:
                loop = asyncio.get_event_loop()
                loop.set_exception_handler(suppress_aiohttp_warnings)
            except Exception:
                pass

            if self._use_prompt_toolkit:
                self.output.exit_patch_context()

            # Print resume hint after patch_stdout exits (direct to real terminal)
            print("\033[2mResume this chat with: \033[0m\033[2;36mpantheon cli --resume\033[0m")

            # Restore terminal state saved at REPL startup — prompt_toolkit
            # may leave terminal in raw mode if cleanup or async tasks
            # interfere with its exit path
            if self._saved_terminal_attrs is not None:
                try:
                    import termios
                    termios.tcsetattr(
                        sys.stdin.fileno(),
                        termios.TCSANOW,
                        self._saved_terminal_attrs,
                    )
                except Exception:
                    pass

            # Suppress SSL transport errors during GC on Windows
            # (asyncio ProactorEventLoop + aiohttp/httpx SSL connections)
            import os
            try:
                sys.stderr = open(os.devnull, "w")
            except Exception:
                pass

    async def _handle_message_or_command(self, current_message: str):
        """Process a single message or command (extracted from run loop)."""
        if not current_message:
            return

        self._add_to_history(current_message)
        
        # Check if we're waiting for /clear confirmation
        if self._pending_clear_confirmation:
            self._pending_clear_confirmation = False
            confirmation = current_message.strip().lower()
            
            if confirmation == "yes":
                # Confirmed - proceed with deletion
                self.console.clear()
                self.task_ui_renderer.reset()
                
                # Delete old chat and create new
                if self._chat_id:
                    await self._chatroom.delete_chat(self._chat_id)
                result = await self._chatroom.create_chat("repl-session")
                self._chat_id = result["chat_id"]
                self._current_agent_name = None
                self._last_printed_agent = None
                
                # Reset status bar to first agent
                if self.prompt_app and self._team and self._team.agents:
                    first_agent_name = list(self._team.agents.keys())[0]
                    self.prompt_app.update_agent(first_agent_name)
                
                await self.print_greeting()
                self.console.print("[green]✓[/green] New conversation started.\n")
            else:
                # Cancelled
                self.console.print("[dim]Cancelled. Conversation preserved.[/dim]\n")
            return

        # Handle commands
        cmd = current_message.strip()
        cmd_lower = cmd.lower()

        # Exit commands
        if cmd_lower in ["exit", "quit", "q", "/exit", "/quit", "/q"]:
            await self._print_session_summary()
            # For prompt_toolkit app, we need to trigger app exit
            if self.prompt_app:
                self.prompt_app.app.exit()
            else:
                 sys.exit(0)
            return

        # Help command
        elif cmd_lower in ["help", "/help"]:
            self._print_help()
            return

        # Status command
        elif cmd_lower in ["status", "/status"]:
            self._print_status()
            return

        # Clear command
        elif cmd_lower in ["clear", "/clear"]:
            await self._handle_clear()
            return

        # History command
        elif cmd_lower in ["history", "/history"]:
            self._print_history()
            return

        # Tokens command
        elif cmd_lower in ["tokens", "/tokens"]:
            await self._print_token_analysis()
            return

        # Compress command
        elif cmd_lower in ["/compress"]:
            await self._handle_compress()
            return

        # Save command
        elif cmd_lower == "/save" or cmd_lower.startswith("/save "):
            self._handle_save_command(cmd)
            return

        # Load command
        elif cmd_lower.startswith("/load "):
            self._handle_load_command(cmd)
            return

        # New chat command
        elif cmd_lower in ["/new", "/new-chat"]:
            await self._handle_new_chat()
            return

        # List chats command
        elif cmd_lower in ["/list", "/chats"]:
            await self._handle_list_chats()
            return

        # Resume chat command
        elif cmd_lower == "/resume":
            await self._handle_list_chats()
            return
        elif cmd_lower.startswith("/resume "):
            chat_arg = cmd[8:].strip()
            await self._handle_resume_chat(chat_arg)
            return

        # Agents command
        elif cmd_lower in ["/agents"]:
            await self._handle_show_agents()
            return

        # Team management command: /team [list|<id|name|index|path>]
        elif cmd_lower == "/team" or cmd_lower.startswith("/team "):
            args = cmd[5:].strip()
            await self._handle_team_command(args)
            return

        # Agent switch command: /agent <name> or /agent <number>
        elif cmd_lower.startswith("/agent "):
            agent_arg = cmd[7:].strip()
            await self._handle_switch_agent(agent_arg)
            return

        # Model command: /model [model_name_or_tag]
        elif cmd_lower == "/model" or cmd_lower.startswith("/model "):
            args = cmd[6:].strip()
            await self._handle_model_command(args)
            return

        # API keys command
        elif cmd_lower == "/keys" or cmd_lower.startswith("/keys "):
            args = cmd[5:].strip()
            self._handle_keys_command(args)
            return

        # Verbose mode command
        elif cmd_lower in ["/verbose", "/v"]:
            self.set_display_mode(DisplayMode.VERBOSE)
            self.output.console.print("[green]✓[/green] Switched to [bold]verbose[/bold] mode")
            self.output.console.print("[dim]  All code, file content, and tool details will be shown[/dim]")
            self.output.console.print()
            return

        # Compact mode command
        elif cmd_lower in ["/compact", "/c"]:
            self.set_display_mode(DisplayMode.COMPACT)
            self.output.console.print("[green]✓[/green] Switched to [bold]compact[/bold] mode")
            self.output.console.print("[dim]  Output will be truncated for readability[/dim]")
            self.output.console.print()
            return

        # Custom command handlers
        continue_flag = False
        for handler in self.handlers:
            if handler.match_command(cmd):
                result = await handler.handle_command(cmd)
                # If command returns a result, we treat it as next message? 
                # Original logic was complex loop. Simplified here: 
                # If handler returns None, we are done. If str, it's new input (uncommon).
                if result is not None:
                     # Recursive call for chained commands? 
                     # For now, just print or ignore to match previous flow mostly
                     pass
                return

        # Process with ChatRoom
        await self._process_message(current_message)
    
    def _should_display_tool_in_scrollback(self, tool_name: str) -> bool:
        """Determine if tool should be displayed in scrollback history.
        
        In compact mode with an active task:
        - task_boundary and notify_user are hidden (handled by Task UI)
        - Other tools are hidden (shown in Task UI's Latest Actions)
        
        In verbose mode: always show all tools.
        
        Args:
            tool_name: Full tool name
            
        Returns:
            True if tool should be displayed in scrollback
        """
        # Verbose mode: show everything
        if self.display_config.mode == DisplayMode.VERBOSE:
            return True
        
        # task_boundary is always handled by Task UI, never show in scrollback
        if "task_boundary" in tool_name:
            return False
        
        # notify_user displays its own panel, don't duplicate
        if "notify_user" in tool_name:
            return False
        
        # In compact mode with active task: hide tools (shown in Task UI)
        if self.task_ui_renderer.has_active_task():
            return False
        
        # No active task: show normally
        return True

    def _build_chat_message(self, message: str) -> list[dict]:
        """Build chat message, handling @image: attachments.
        
        Delegates to vision.parse_image_mentions for unified image handling.
        Workspace is automatically resolved from settings.
        
        Args:
            message: User input string (may contain @image: tokens)
            
        Returns:
            List of message dicts in OpenAI format
        """
        from pantheon.utils.vision import parse_image_mentions
        return parse_image_mentions(message)

    async def _process_message(self, message: str):
        """Process a message through ChatRoom."""
        # Yield early to let prompt_toolkit render processing state
        await asyncio.sleep(0)

        start_time = time.time()

        # Estimate input tokens
        input_tokens = self._estimate_tokens(message)
        output_tokens = 0

        # Content buffers for streaming
        content_buffer = []
        tool_calls_content_buffer = []
        estimated_output_tokens = 0

        def process_chunk(chunk: dict):
            nonlocal estimated_output_tokens
            content = chunk.get("content")
            tool_calls = chunk.get("tool_calls")
            if content is None and tool_calls is None:
                return
            if tool_calls is not None:
                for tool_call in tool_calls:
                    if "function" in tool_call:
                        if "arguments" in tool_call["function"]:
                            t_content = (content or "") + tool_call["function"][
                                "arguments"
                            ]
                            tool_calls_content_buffer.append(t_content)
            if content is not None:
                content_buffer.append(content)
            estimated_output_tokens = self._estimate_tokens(
                "".join(content_buffer + tool_calls_content_buffer)
            )

        # Animation frames and separator from utils
        animation_frames = get_animation_frames()
        sep = get_separator()


        processing_live = Live(console=self.console, refresh_per_second=8, transient=True)
        if not self.prompt_app:
            processing_live.start()

        try:
            # Wave effect brightness levels (grey scale gradient)
            wave_colors = ["grey30", "grey42", "grey54", "grey66", "grey78", "grey89", "white", "grey89", "grey78", "grey66", "grey54", "grey42"]

            def create_wave_text(text: str, offset: int) -> str:
                """Create text with wave brightness effect."""
                result = []
                for i, char in enumerate(text):
                    # Calculate wave position for this character
                    wave_pos = (i + offset) % len(wave_colors)
                    color = wave_colors[wave_pos]
                    result.append(f"[{color}]{char}[/{color}]")
                return "".join(result)

            def update_processing_status():
                current_output_tokens = estimated_output_tokens
                elapsed = time.time() - start_time

                # Time-based animation for consistent speed
                animation_fps = 8  # Spinner: 8 frames per second
                wave_fps = 4  # Wave: 4 steps per second (slower for visual effect)

                animation_index = int(elapsed * animation_fps) % len(animation_frames)
                wave_offset = int(elapsed * wave_fps)

                current_frame = animation_frames[animation_index]
                
                if self.prompt_app:
                    # Update Task UI Spinner (if active task)
                    if self.task_ui_renderer.has_active_task():
                        self.task_ui_renderer.advance_spinner()

                    # Update Prompt App Status Bar with animation state
                    status_str = f"{'Running ' + format_tool_name(self._current_tool_name) + '...' if (self._current_tool_name and self._tools_executing) else 'Processing...'}"
                    self.prompt_app.update_processing(
                        status=status_str,
                        output_tokens=current_output_tokens,
                        spinner=current_frame,
                        elapsed=elapsed,
                        wave_offset=wave_offset
                    )
                else:
                    # Update Rich Live Status
                    current_frame = animation_frames[animation_index]
    
                    # Build agent prefix for multi-agent mode
                    agent_prefix = ""
                    if self._is_multi_agent and self._current_agent_name:
                        agent_prefix = f"[cyan]{self._current_agent_name}[/cyan] "
    
                    # Only show token counts when we have output tokens
                    if current_output_tokens > 0:
                        token_info = f"[dim]{sep} {self._format_token_count(input_tokens)} in, {self._format_token_count(current_output_tokens)} out"
                    else:
                        token_info = ""

                    if self._current_tool_name and self._tools_executing:
                        display_name = format_tool_name(self._current_tool_name)
                        wave_text = create_wave_text(f"Running {display_name}...", wave_offset)
                        status_text = f"[dim]{current_frame}[/dim] {agent_prefix}{wave_text} {token_info}"
                    else:
                        wave_text = create_wave_text("Processing...", wave_offset)
                        status_text = f"[dim]{current_frame}[/dim] {agent_prefix}{wave_text} {token_info}"
    
                    if elapsed > 1:
                        status_text += f"[dim] {sep} {elapsed:.1f}s[/dim]"
    
                    processing_live.update(Text.from_markup(status_text))

            try:
                update_processing_status()
                self._tools_executing = False

                # Thread-based animation events (defined early for use in callbacks)
                animation_stop_event = threading.Event()
                animation_pause_event = threading.Event()  # For pausing during prints

                def smart_process_chunk(chunk: dict):
                    process_chunk(chunk)
                    # Note: animation now handled by separate thread

                def process_step_message(step: dict):
                    """Handle step messages (tool calls, tool results, assistant content)."""
                    # Track agent for status bar updates
                    agent_name = step.get("agent_name")
                    if agent_name:
                        # Update current agent name and status bar
                        if agent_name != self._current_agent_name:
                            self._current_agent_name = agent_name
                            if self.prompt_app:
                                self.prompt_app.update_agent(agent_name)

                    # Handle assistant content FIRST (before tool calls)
                    # This prints intermediate thoughts before showing tool usage
                    if step.get("role") == "assistant" and step.get("content"):
                        assistant_content = step.get("content")
                        if assistant_content.strip():
                            # Add message to task UI recent activity
                            self.task_ui_renderer.add_message(assistant_content)
                            
                            # Pause animation, print content, resume animation
                            animation_pause_event.set()
                            if not self.prompt_app:
                                processing_live.stop()
                            # Print agent name before each response (multi-agent mode)
                            if agent_name and self._is_multi_agent:
                                self.console.print(
                                    f"[dim]→[/dim] [bold cyan]{agent_name}[/bold cyan]"
                                )
                            self.console.print()
                            try:
                                self.console.print(Markdown(assistant_content.strip()))
                            except Exception:
                                self.console.print(assistant_content.strip())
                            self.console.print()
                            if not self.prompt_app:
                                processing_live.start()
                            animation_pause_event.clear()
                            # Clear buffer to avoid duplicate printing at the end
                            content_buffer.clear()

                    # Handle tool calls
                    if tool_calls := step.get("tool_calls"):
                        for call in tool_calls:
                            tool_name = call.get("function", {}).get("name")
                            if tool_name:
                                try:
                                    import json
                                    args = json.loads(
                                        call.get("function", {}).get("arguments", "{}")
                                    )
                                except Exception:
                                    args = {}
                                
                                # Update Task UI
                                if "task_boundary" in tool_name:
                                    self.task_ui_renderer.update_task_boundary(args)
                                elif "notify_user" in tool_name:
                                    self.task_ui_renderer.on_notify_user()
                                    
                                    # Render notification immediately using arguments
                                    # specific mapping for notify_user args -> renderer format
                                    notification_data = {
                                        "message": args.get("message") or args.get("Message", ""),
                                        "paths": args.get("paths_to_review") or args.get("PathsToReview", []),
                                        "interrupt": args.get("blocked_on_user") or args.get("BlockedOnUser", False),
                                        "questions": args.get("questions") or args.get("Questions", [])
                                    }
                                    self.notify_ui_renderer.render_notification(notification_data)

                                    # Save pending approval for interactive dialog (shown after chat completes)
                                    if notification_data.get("interrupt"):
                                        self._pending_approval = notification_data
                                else:
                                    # Add tool to recent activity (skip notify_user - has its own UI)
                                    self.task_ui_renderer.add_tool_call(tool_name, args=args, is_running=True)
                                
                                # Display in scrollback (filtered in compact mode with active task)
                                if self._should_display_tool_in_scrollback(tool_name):
                                    self.print_tool_call(tool_name, args)

                    # Handle tool results
                    elif step.get("role") == "tool":
                        tool_name = step.get("tool_name", "")
                        content = step.get("content", "")
                        
                        # Update Task UI
                        self.task_ui_renderer.update_tool_complete(tool_name)
                        
                        # Handle notify_user result
                        if "notify_user" in tool_name:
                            pass # Handled in tool_calls phase

                        # Prefer raw_content if available (original dict)
                        raw_content = step.get("raw_content")
                        
                        # Display in scrollback (filtered in compact mode with active task)
                        if self._should_display_tool_in_scrollback(tool_name):
                            if raw_content is not None and isinstance(raw_content, dict):
                                self.print_tool_result(tool_name, raw_content)
                            else:
                                # Try to parse content
                                try:
                                    import json
                                    result = json.loads(content)
                                    self.print_tool_result(tool_name, result)
                                except json.JSONDecodeError:
                                    # Try ast.literal_eval for repr() output
                                    try:
                                        import ast
                                        result = ast.literal_eval(content)
                                        if isinstance(result, dict):
                                            self.print_tool_result(tool_name, result)
                                        else:
                                            self.print_tool_result(tool_name, {"output": str(result)})
                                    except Exception:
                                        if content.strip():
                                            self.print_tool_result(tool_name, {"output": content})
                                except Exception:
                                    if content.strip():
                                        self.print_tool_result(tool_name, {"output": content})

                self._current_live_display = processing_live

                def animation_thread_func():
                    """Run animation in separate thread to avoid event loop blocking."""
                    while not animation_stop_event.is_set():
                        # Check if paused (during content printing)
                        if not animation_pause_event.is_set():
                            try:
                                update_processing_status()
                            except Exception:
                                pass  # Ignore errors in animation thread
                        time.sleep(0.125)  # 8 fps

                animation_thread = threading.Thread(target=animation_thread_func, daemon=True)
                animation_thread.start()

                # Build message - check for @image: attachments
                chat_message = self._build_chat_message(message)

                # Call ChatRoom.chat()
                chat_task = asyncio.create_task(
                    self._chatroom.chat(
                        chat_id=self._chat_id,
                        message=chat_message,
                        process_chunk=smart_process_chunk,
                        process_step_message=process_step_message,
                    )
                )

                self._current_agent_task = chat_task

                # Yield to event loop to let prompt_toolkit refresh before blocking on chat
                await asyncio.sleep(0)

                try:
                    result = await chat_task
                except asyncio.CancelledError:
                    self.console.print("\n[yellow]Operation was cancelled[/yellow]")
                    raise KeyboardInterrupt
                finally:
                    self._current_agent_task = None
                    # Stop animation thread
                    animation_stop_event.set()
                    animation_thread.join(timeout=0.5)

                # Check for errors in result
                if result and not result.get("success", True):
                    processing_live.stop()  # Stop Live before printing error
                    error_msg = result.get("message", "Unknown error")
                    self.console.print(f"\n[red]Error:[/red] {error_msg}")
                    self.console.print(
                        "[dim]You can continue the conversation or type 'exit' to quit[/dim]"
                    )
                    return

                # Final output token calculation
                if content_buffer:
                    full_content = "".join(content_buffer)
                    if full_content.strip():
                        output_tokens = self._estimate_tokens(full_content)

                # Update token statistics
                self._update_token_stats(input_tokens, output_tokens)
                self.message_count += 1

            except KeyboardInterrupt:
                self._interrupt_count = 0
                if self._current_agent_task and not self._current_agent_task.done():
                    self._current_agent_task.cancel()
                    try:
                        await self._current_agent_task
                    except asyncio.CancelledError:
                        pass
                # Stop animation thread
                if "animation_stop_event" in locals():
                    animation_stop_event.set()
                if "animation_thread" in locals() and animation_thread.is_alive():
                    animation_thread.join(timeout=0.5)
                return
            except Exception as e:
                self.console.print(f"\n[red]Error:[/red] {str(e)}")
                self.console.print(
                    "[dim]You can continue the conversation or type 'exit' to quit[/dim]"
                )
            finally:
                # Stop animation thread first
                if "animation_stop_event" in locals():
                    animation_stop_event.set()
                if "animation_thread" in locals() and animation_thread.is_alive():
                    animation_thread.join(timeout=0.5)
                processing_live.stop()
                self._tools_executing = False
                self._current_live_display = None
                self._current_tool_name = None
                if self._current_agent_task and not self._current_agent_task.done():
                    self._current_agent_task.cancel()
                self._current_agent_task = None
        finally:
            if "processing_live" in locals():
                processing_live.stop()

        # Print final content with agent label (multi-agent mode)
        # Only print if there's content (buffer may have been cleared during streaming)
        if content_buffer:
            full_content = "".join(content_buffer)
            if full_content.strip():
                # Show agent label before final response (multi-agent mode)
                if self._is_multi_agent and self._current_agent_name:
                    self.console.print(f"[dim]→[/dim] [bold cyan]{self._current_agent_name}[/bold cyan]")
                    self.console.print()
                try:
                    self.console.print(Markdown(full_content.strip()))
                except Exception:
                    self.console.print(full_content.lstrip('\n'))
                self.console.print()
        
        # Handle pending approval (notify_user with interrupt=True)
        if self._pending_approval:
            await self._handle_pending_approval()
    
    async def _handle_pending_approval(self):
        """Handle pending user approval with interactive dialog.

        Shows an interactive dialog for notify_user with interrupt=True,
        allowing users to review files and answer questions.
        """
        from prompt_toolkit.application.run_in_terminal import in_terminal
        from .viewers.unified_dialog import show_unified_dialog

        approval_data = self._pending_approval
        self._pending_approval = None  # Clear immediately

        if not approval_data:
            return

        message = approval_data.get("message", "")
        paths = approval_data.get("paths", [])
        questions = approval_data.get("questions", [])

        try:
            # Show unified dialog using in_terminal to suspend REPL UI
            if self.prompt_app and hasattr(self.prompt_app, 'app') and self.prompt_app.app.is_running:
                async with in_terminal():
                    result = await show_unified_dialog(
                        message=message,
                        paths=paths,
                        questions=questions
                    )
            else:
                # No active prompt app, run directly
                result = await show_unified_dialog(
                    message=message,
                    paths=paths,
                    questions=questions
                )

            # Handle result
            if result.submitted:
                # Format answers as user message
                answer_text = self._format_question_answers(result.answers)

                if result.answers:
                    self.console.print(f"[green]✓ Submitted {len(result.answers)} answer(s)[/green]")
                else:
                    self.console.print("[green]✓ Approved[/green]")

                # Put answer message in queue - this simulates user input
                if self.message_queue:
                    await self.message_queue.put(answer_text)
                else:
                    await self._chatroom.chat(
                        chat_id=self._chat_id,
                        message=answer_text,
                    )
            elif result.feedback:
                # User provided rejection feedback
                from .user_response import UserResponseFormatter
                feedback_text = UserResponseFormatter.format_rejection(result.feedback)

                self.console.print(f"[yellow]→ Feedback sent[/yellow]")

                if self.message_queue:
                    await self.message_queue.put(feedback_text)
                else:
                    await self._chatroom.chat(
                        chat_id=self._chat_id,
                        message=feedback_text,
                    )
            else:
                # User cancelled
                self.console.print("[yellow]⚠ Cancelled[/yellow]")

        except Exception as e:
            logger.error(f"Error in pending approval dialog: {e}", exc_info=True)
            self.console.print(f"[red]Error showing dialog: {e}[/red]")

    def _format_question_answers(self, answers: List[Dict[str, Any]]) -> str:
        """Format question answers as user message.

        Uses unified user response format for consistency.
        """
        from .user_response import UserResponseFormatter
        return UserResponseFormatter.format_question_answers(answers)

    # ===== Chat management commands =====

    async def _handle_new_chat(self):
        """Create a new chat session."""
        self.task_ui_renderer.reset()
        result = await self._chatroom.create_chat()
        self._chat_id = result["chat_id"]

        # Revert ↑/↓ history to global file history
        if self.prompt_app:
            self.prompt_app.set_session_history(None)
            self._load_history()

        self.console.print(
            f"[green]✅ Created new chat:[/green] {result.get('chat_name', self._chat_id)}"
        )
        self.console.print()

    async def _handle_list_chats(self):
        """List all chat sessions."""
        result = await self._chatroom.list_chats()
        if result.get("success"):
            chats = result.get("chats", [])
            self.console.print()
            self.console.print(
                "[dim][bold blue]-- CHATS ------------------------------------------------------------[/bold blue][/dim]"
            )
            self.console.print()
            if not chats:
                self.console.print("[dim]No chats found[/dim]")
            else:
                # Print header
                self.console.print(
                    f"[dim]  #   {'Name':<30} {'Last Activity':<18} ID[/dim]"
                )
                self.console.print(
                    "[dim]  ─────────────────────────────────────────────────────────────────[/dim]"
                )
                # Print chats
                for idx, chat in enumerate(chats[:10], 1):  # Show last 10
                    marker = "→" if chat.get("id") == self._chat_id else " "
                    name = chat.get("name", "Unnamed")
                    # Truncate long names
                    if len(name) > 28:
                        name = name[:25] + "..."
                    chat_id = chat.get("id", "")[:8]
                    last_activity = format_relative_time(
                        chat.get("last_activity_date")
                    )
                    self.console.print(
                        f"[dim]{marker}[/dim] [cyan]{idx:<3}[/cyan] [bold]{name:<30}[/bold] [dim]{last_activity:<18}[/dim] [dim]{chat_id}[/dim]"
                    )
            self.console.print(
                "[dim]  Use [bold]/resume <#>[/bold] or [bold]/resume <name>[/bold] to switch to a chat[/dim]"
            )
            self.console.print()
        else:
            self.console.print(f"[red]Error listing chats: {result.get('message')}[/red]")

    async def _handle_resume_chat(self, chat_arg: str):
        """Resume a different chat session.
        
        Args:
            chat_arg: Chat ID, name prefix, or 'last' for the most recent chat (excluding current)
        """
        # Get chat list
        result = await self._chatroom.list_chats()
        if not result.get("success"):
            self.console.print(f"[red]Error listing chats: {result.get('message')}[/red]")
            self.console.print()
            return
            
        chats = result.get("chats", [])
        found = None
        
        # Handle 'last' argument - switch to most recent chat (excluding current)
        if chat_arg.lower() == "last":
            # Find the most recent chat that is not the current one
            for chat in chats:
                if chat.get("id") != self._chat_id:
                    found = chat
                    break
            
            if not found:
                self.console.print("[yellow]No other chat sessions found[/yellow]")
                self.console.print()
                return
        else:
            # Search by numeric index (from /list), ID prefix, or name prefix
            if chat_arg.isdigit():
                idx = int(chat_arg)
                if 1 <= idx <= len(chats):
                    found = chats[idx - 1]

            if not found:
                # Search by ID or name prefix
                for chat in chats:
                    if chat.get("id", "").startswith(chat_arg) or chat.get(
                        "name", ""
                    ).lower().startswith(chat_arg.lower()):
                        found = chat
                        break

            if not found:
                self.console.print(f"[red]Chat not found: {chat_arg}[/red]")
                self.console.print()
                return

        # Switch to the found chat
        self.task_ui_renderer.reset()
        self._chat_id = found["id"]

        # Reload team from the new chat's memory (each chat persists its own team)
        # Fall back to default team if the saved template can no longer be created
        # (e.g., template file deleted, required services unavailable)
        try:
            self._team = await self._chatroom.get_team_for_chat(self._chat_id)
        except Exception as e:
            logger.warning(f"Failed to load team for chat {self._chat_id}: {e}")
            self.console.print(
                f"[yellow]Warning: Could not load saved team ({e}), falling back to default[/yellow]"
            )
            # Force reload from default template by clearing stored template
            try:
                from pantheon.utils.misc import run_func
                # Read-only: clearing team template from extra_data, no need to fix
                memory = await run_func(self._chatroom.memory_manager.get_memory, self._chat_id)
                if hasattr(memory, "extra_data") and "team_template" in memory.extra_data:
                    memory.delete_metadata("team_template")
                # Clear cache so get_team_for_chat creates fresh default
                self._chatroom.chat_teams.pop(self._chat_id, None)
                self._team = await self._chatroom.get_team_for_chat(self._chat_id)
            except Exception as e2:
                self.console.print(f"[red]Error loading default team: {e2}[/red]")
                self.console.print()
                return
        self._is_multi_agent = len(self._team.agents) > 1
        self._current_agent_name = None
        self._last_printed_agent = None

        # Update status bar
        if self.prompt_app and self._team and self._team.agents:
            agent_names = list(self._team.agents.keys())
            if agent_names:
                first_agent_name = agent_names[0]
                agent = self._team.agents[first_agent_name]
                model = getattr(agent, 'model', 'unknown')
                if hasattr(agent, 'models'):
                    models = agent.models
                    if isinstance(models, list) and models:
                        model = models[0]
                    elif isinstance(models, str):
                        model = models
                self.prompt_app.update_agent(first_agent_name)
                self.prompt_app.update_model(model)

        # Load session messages, switch ↑/↓ history, and replay chat
        try:
            from pantheon.utils.misc import run_func
            from pantheon.repl.conversationRecovery import loadConversationForResume
            from pantheon.repl.sessionRestore import (
                exitRestoredWorktree,
                processResumedConversation,
                restoreReadFileState,
            )

            exitRestoredWorktree()

            memory = await run_func(
                self._chatroom.memory_manager.get_memory,
                self._chat_id,
                auto_fix=True,
            )
            if memory:
                # Root-level user messages for ↑/↓ history
                resume_result = loadConversationForResume(
                    memory,
                    execution_context_id=None,
                )
                processed_resume = await processResumedConversation(
                    resume_result or {},
                    {"forkSession": False},
                    {
                        "memory": memory,
                        "initialState": {},
                    },
                )
                root_msgs = processed_resume.get(
                    "messages",
                    (resume_result or {}).get(
                        "messages",
                        memory.get_messages(execution_context_id=None, for_llm=False),
                    ),
                )
                logger.info(
                    "[resume] repl chat_id={} root_messages={} resumed_messages={}",
                    self._chat_id,
                    len(memory.get_messages(execution_context_id=None, for_llm=False)),
                    len(root_msgs),
                )
                user_inputs = [
                    m["content"] for m in root_msgs
                    if m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and not m.get("isMeta")
                ]
                if self.prompt_app:
                    self.prompt_app.set_session_history(user_inputs if user_inputs else None)
                self.command_history = user_inputs.copy()
                self.history_index = len(self.command_history)
                # Replay full chat history (all agents)
                full_resume_result = loadConversationForResume(memory)
                full_processed_resume = await processResumedConversation(
                    full_resume_result or {},
                    {"forkSession": False},
                    {
                        "memory": memory,
                        "initialState": {},
                    },
                )
                all_msgs = full_processed_resume.get(
                    "messages",
                    (full_resume_result or {}).get(
                        "messages",
                        memory.get_messages(for_llm=False),
                    ),
                )
                restoreReadFileState(all_msgs, str(Path.cwd()))
                self._replay_chat_history(all_msgs)
        except Exception:
            pass

        self.console.print(
            f"\n[green]✅ Resumed:[/green] {found.get('name', self._chat_id)}"
        )
        self.console.print()

    async def _handle_show_agents(self):
        """Show agents in current team with current agent indicator."""
        self.console.print()
        self.console.print(
            "[dim][bold blue]-- AGENTS -----------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()

        if self._team:
            # Determine current active agent
            current_agent_name = self._current_agent_name
            if not current_agent_name:
                # Default to first agent if not set
                current_agent_name = list(self._team.agents.keys())[0] if self._team.agents else None

            # Print header
            self.console.print(
                f"[dim]  #   {'Name':<20} {'Model':<25} Description[/dim]"
            )
            self.console.print(
                "[dim]  ─────────────────────────────────────────────────────────────────[/dim]"
            )

            for idx, agent in enumerate(self._team.agents.values(), 1):
                # Check if this is the current agent
                is_current = agent.name == current_agent_name
                marker = "→" if is_current else " "

                # Get model info
                if hasattr(agent, "models") and agent.models:
                    model = (
                        agent.models[0]
                        if isinstance(agent.models, list)
                        else agent.models
                    )
                    # Truncate long model names
                    if len(model) > 23:
                        model = model[:20] + "..."
                else:
                    model = "-"

                # Get description
                description = ""
                if hasattr(agent, "description") and agent.description:
                    description = agent.description
                    if len(description) > 30:
                        description = description[:27] + "..."

                # Format agent name
                name = agent.name
                if len(name) > 18:
                    name = name[:15] + "..."

                if is_current:
                    self.console.print(
                        f"[dim]{marker}[/dim] [cyan]{idx:<3}[/cyan] [bold cyan]{name:<20}[/bold cyan] [dim]{model:<25}[/dim] [dim]{description}[/dim]"
                    )
                else:
                    self.console.print(
                        f"[dim]{marker}[/dim] [cyan]{idx:<3}[/cyan] [bold]{name:<20}[/bold] [dim]{model:<25}[/dim] [dim]{description}[/dim]"
                    )
        else:
            self.console.print("[dim]No team loaded[/dim]")
        self.console.print()

    async def _handle_team_command(self, args: str):
        """Manage team templates: list and switch current chat's team.
        
        Usage:
          /team            Show usage and current team
          /team list       List available team templates
          /team <sel>      Switch to team by id|name|index|path (teams/xxx.md)
        """
        try:
            # Show usage and current team
            if not args or args in {"help", "?"}:
                self.console.print()
                self.console.print("[dim][bold blue]-- TEAM -------------------------------------------------------------[/bold blue][/dim]")
                self.console.print()
                # Try to fetch current template name
                current_name = None
                try:
                    tpl = await self._chatroom.get_chat_template(self._chat_id)
                    if tpl.get("success"):
                        t = tpl.get("template") or {}
                        current_name = t.get("name") or t.get("id")
                except Exception:
                    pass
                if current_name:
                    self.console.print(f"[dim]Current team:[/dim] [bold]{current_name}[/bold]")
                self.console.print("[dim]/team list[/dim] - List available team templates")
                self.console.print("[dim]/team <id|name|index|path>[/dim] - Switch to a team template")
                self.console.print()
                return

            # List teams
            if args.lower().startswith("list"):
                result = await self._chatroom.list_template_files("teams")
                if not result.get("success"):
                    self.console.print(f"[red]Failed to list team templates: {result.get('error') or result.get('message')}[/red]")
                    self.console.print()
                    return
                files = result.get("files", [])
                self._last_team_files = files
                self.console.print()
                self.console.print("[dim][bold blue]-- TEAMS ------------------------------------------------------------[/bold blue][/dim]")
                self.console.print()
                if not files:
                    self.console.print("[dim]No team templates found[/dim]")
                    self.console.print()
                    return
                # Header
                self.console.print(f"[dim]  #   {'Name':<24} {'ID':<18} Path[/dim]")
                self.console.print("[dim]  ───────────────────────────────────────────────────────────────[/dim]")
                for idx, f in enumerate(files, 1):
                    name = (f.get("name") or "").strip() or "Unnamed"
                    if len(name) > 22:
                        name = name[:19] + "..."
                    fid = (f.get("id") or "").strip()
                    if len(fid) > 16:
                        fid = fid[:13] + "..."
                    path = f.get("path", "")
                    self.console.print(f"[dim]  [/dim][cyan]{idx:<3}[/cyan] [bold]{name:<24}[/bold] [dim]{fid:<18}[/dim] [dim]{path}[/dim]")
                self.console.print()
                return

            # Resolve selection to a template file path
            selection = args.strip()
            files = self._last_team_files
            if not files:
                r = await self._chatroom.list_template_files("teams")
                files = r.get("files", []) if r.get("success") else []

            file_path = None
            # Index selection
            if selection.isdigit() and files:
                idx = int(selection)
                if 1 <= idx <= len(files):
                    file_path = files[idx - 1].get("path")
                else:
                    self.console.print(f"[red]Invalid index: {idx}[/red]")
                    self.console.print()
                    return
            # Direct path
            if file_path is None and ("/" in selection or selection.endswith(".md")):
                file_path = selection
            # Match by id or name
            if file_path is None and files:
                sl = selection.lower()
                # exact id
                for f in files:
                    if (f.get("id") or "").lower() == sl:
                        file_path = f.get("path"); break
                # name startswith
                if file_path is None:
                    for f in files:
                        if (f.get("name") or "").lower().startswith(sl):
                            file_path = f.get("path"); break
                # id startswith
                if file_path is None:
                    for f in files:
                        if (f.get("id") or "").lower().startswith(sl):
                            file_path = f.get("path"); break
                # fallback: assume teams/<id>.md
                if file_path is None:
                    file_path = f"teams/{selection}.md"

            # Read template
            read_res = await self._chatroom.read_template_file(file_path, resolve_refs=True)
            if not read_res.get("success"):
                self.console.print(f"[red]Template not found or unreadable: {selection}[/red]")
                err = read_res.get("error") or read_res.get("message")
                if err:
                    self.console.print(f"[dim]{err}[/dim]")
                self.console.print()
                return

            template = read_res.get("content") or {}
            # Validate
            val = await self._chatroom.validate_template(template)
            if not val.get("success") or (val.get("compatible") is False):
                self.console.print("[red]Template not compatible with current endpoint[/red]")
                msg = val.get("message") or "; ".join(val.get("validation_errors", []) or [])
                if msg:
                    self.console.print(f"[dim]{msg}[/dim]")
                self.console.print()
                return

            # Apply
            self.task_ui_renderer.reset()
            setup = await self._chatroom.setup_team_for_chat(self._chat_id, template)
            if not setup.get("success"):
                self.console.print(f"[red]Failed to apply team: {setup.get('message', 'Unknown error')}[/red]")
                self.console.print()
                return

            # Refresh local team cache and UI state
            self._team = await self._chatroom.get_team_for_chat(self._chat_id)
            self._is_multi_agent = len(self._team.agents) > 1
            self._current_agent_name = None
            self._last_printed_agent = None

            # Update status bar to first agent and model
            if self.prompt_app and self._team and self._team.agents:
                agent_names = list(self._team.agents.keys())
                if agent_names:
                    first_agent_name = agent_names[0]
                    agent = self._team.agents[first_agent_name]
                    model = getattr(agent, 'model', 'unknown')
                    if hasattr(agent, 'models'):
                        models = agent.models
                        if isinstance(models, list) and models:
                            model = models[0]
                        elif isinstance(models, str):
                            model = models
                    self.prompt_app.update_agent(first_agent_name)
                    self.prompt_app.update_model(model)

            # Confirmation and show agents
            tpl_name = template.get("name") or template.get("id") or file_path
            self.console.print(f"[green]✅ Switched team to:[/green] [bold]{tpl_name}[/bold]")
            await self._handle_show_agents()
            return

        except Exception as e:
            self.console.print(f"[red]Error handling /team: {e}[/red]")
            self.console.print()

    async def _handle_switch_agent(self, agent_arg: str):
        """Switch to a different agent by name or number."""
        if not self._team:
            self.console.print("[red]No team loaded[/red]")
            return

        if not self._is_multi_agent:
            self.console.print("[yellow]Single agent mode - no switching needed[/yellow]")
            return

        agent_names = list(self._team.agents.keys())

        # Try to parse as number first
        target_agent_name = None
        try:
            idx = int(agent_arg)
            if 1 <= idx <= len(agent_names):
                target_agent_name = agent_names[idx - 1]
            else:
                self.console.print(f"[red]Invalid agent number: {idx}. Valid range: 1-{len(agent_names)}[/red]")
                return
        except ValueError:
            # Try to match by name (case-insensitive, partial match)
            agent_arg_lower = agent_arg.lower()
            for name in agent_names:
                if name.lower() == agent_arg_lower or name.lower().startswith(agent_arg_lower):
                    target_agent_name = name
                    break

            if not target_agent_name:
                self.console.print(f"[red]Agent not found: {agent_arg}[/red]")
                self.console.print(f"[dim]Available agents: {', '.join(agent_names)}[/dim]")
                return

        # Check if already on this agent
        if target_agent_name == self._current_agent_name:
            self.console.print(f"[dim]Already on agent: {target_agent_name}[/dim]")
            return

        # Switch agent via chatroom
        result = await self._chatroom.set_active_agent(self._chat_id, target_agent_name)
        if result.get("success"):
            self._current_agent_name = target_agent_name
            
            # Reset task UI when switching agents
            self.task_ui_renderer.reset()
            
            # Update status bar agent display
            if self.prompt_app:
                self.prompt_app.update_agent(target_agent_name)
            self.console.print(f"[green]✅ Switched to:[/green] [bold cyan]{target_agent_name}[/bold cyan]")
        else:
            self.console.print(f"[red]Failed to switch agent: {result.get('message', 'Unknown error')}[/red]")

    def _handle_keys_command(self, args: str):
        """Handle /keys command - show or set LLM provider API keys.

        Usage:
            /keys                                              - List all providers and their status
            /keys 1 sk-xxx                                     - Set key by number
            /keys openai sk-xxx                                - Set key by provider name
            /keys openai <base_url> <api_key>                  - Set provider key plus optional base URL
            /keys 0 <base_url> <api_key>                       - Set OpenAI-compatible fallback
            /keys rm 0                                         - Remove OpenAI-compatible fallback
            /keys rm 1                                         - Remove provider key by number
            /keys rm openai                                    - Remove provider key by name
        """
        import os
        from .setup_wizard import (
            PROVIDER_MENU,
            _save_key_to_env_file,
            _remove_key_from_env_file,
        )
        from pantheon.utils.model_selector import reset_model_selector

        def _mask_value(env_var: str, value: str) -> str:
            if "KEY" in env_var:
                return value[:4] + "..." + value[-4:] if len(value) > 12 else "***"
            return value

        def _looks_like_base_url(value: str) -> bool:
            lowered = value.lower()
            return lowered.startswith("http://") or lowered.startswith("https://")

        if not args:
            self.console.print()
            self.console.print("[bold]LLM Provider API Keys[/bold]")
            self.console.print()

            self.console.print("  [cyan] 0[/cyan]  [bold]OpenAI-Compatible Fallback[/bold]")
            for env_var, label in [("LLM_API_BASE", "Base URL"), ("LLM_API_KEY", "API Key")]:
                val = os.environ.get(env_var, "")
                if val:
                    status = f"[green]{_mask_value(env_var, val)}[/green]"
                else:
                    status = "[dim]not set[/dim]"
                self.console.print(f"        {label:<10} {env_var:<24} {status}")

            for i, entry in enumerate(PROVIDER_MENU, 1):
                self.console.print(f"  [cyan]{i:>2}[/cyan]  [bold]{entry.display_name}[/bold]")
                key_value = os.environ.get(entry.env_var, "")
                if key_value:
                    key_status = f"[green]{_mask_value(entry.env_var, key_value)}[/green]"
                else:
                    key_status = "[dim]not set[/dim]"
                self.console.print(f"        API Key    {entry.env_var:<24} {key_status}")
                if entry.base_env_var:
                    base_value = os.environ.get(entry.base_env_var, "")
                    if base_value:
                        base_status = f"[green]{_mask_value(entry.base_env_var, base_value)}[/green]"
                    else:
                        base_status = "[dim]not set[/dim]"
                    self.console.print(f"        Base URL   {entry.base_env_var:<24} {base_status}")

            self.console.print()
            self.console.print("[dim]Usage: /keys <number|name> <api_key>[/dim]")
            self.console.print("[dim]       /keys <number|name> <base_url> <api_key>       (for providers with Base URL support)[/dim]")
            self.console.print("[dim]       /keys 0 <base_url> <api_key>                    (OpenAI-compatible fallback)[/dim]")
            self.console.print("[dim]       /keys rm <number|name>                           (remove key)[/dim]")
            self.console.print("[dim]Keys are saved to ~/.pantheon/.env[/dim]")
            self.console.print()
            return

        parts = args.split(None, 1)
        if len(parts) < 2:
            self.console.print("[yellow]Usage: /keys <number|name> <api_key>[/yellow]")
            self.console.print("[yellow]       /keys <number|name> <base_url> <api_key>       (for providers with Base URL support)[/yellow]")
            self.console.print("[yellow]       /keys 0 <base_url> <api_key>                    (OpenAI-compatible fallback)[/yellow]")
            self.console.print("[yellow]       /keys rm <number|name>                           (remove key)[/yellow]")
            return

        provider_arg, rest = parts[0], parts[1].strip()

        if provider_arg.lower() in ("rm", "del", "delete", "remove"):
            target_arg = rest.strip()
            if target_arg == "0":
                _remove_key_from_env_file("LLM_API_BASE")
                _remove_key_from_env_file("LLM_API_KEY")
                reset_model_selector()
                self.console.print("[green]\u2713[/green] OpenAI-compatible fallback removed from ~/.pantheon/.env")
                return

            target = None
            if target_arg.isdigit():
                idx = int(target_arg)
                if 1 <= idx <= len(PROVIDER_MENU):
                    target = PROVIDER_MENU[idx - 1]
            else:
                for entry in PROVIDER_MENU:
                    if entry.provider_key == target_arg.lower():
                        target = entry
                        break

            if not target:
                self.console.print(f"[red]Unknown provider: {target_arg}[/red]")
                return

            _remove_key_from_env_file(target.env_var)
            if target.base_env_var:
                _remove_key_from_env_file(target.base_env_var)
            reset_model_selector()
            self.console.print(f"[green]\u2713[/green] {target.display_name} removed from ~/.pantheon/.env")
            return

        if provider_arg == "0":
            custom_parts = rest.split(None, 1)
            if len(custom_parts) < 2:
                self.console.print("[yellow]Usage: /keys 0 <base_url> <api_key>[/yellow]")
                return
            base_url, api_key = custom_parts[0].strip(), custom_parts[1].strip()
            _save_key_to_env_file("LLM_API_BASE", base_url)
            os.environ["LLM_API_BASE"] = base_url
            _save_key_to_env_file("LLM_API_KEY", api_key)
            os.environ["LLM_API_KEY"] = api_key
            reset_model_selector()
            self.console.print(f"[green]\u2713[/green] OpenAI-compatible fallback saved to ~/.pantheon/.env")
            return

        target = None
        if provider_arg.isdigit():
            idx = int(provider_arg)
            if 1 <= idx <= len(PROVIDER_MENU):
                target = PROVIDER_MENU[idx - 1]
        else:
            provider_arg_lower = provider_arg.lower()
            for entry in PROVIDER_MENU:
                if entry.provider_key == provider_arg_lower:
                    target = entry
                    break

        if not target:
            self.console.print(f"[red]Unknown provider: {provider_arg}[/red]")
            self.console.print("[dim]Use /keys to see available providers[/dim]")
            return

        base_url = None
        api_key = rest
        custom_parts = rest.split()
        if len(custom_parts) >= 2 and _looks_like_base_url(custom_parts[0]):
            if not target.base_env_var:
                self.console.print(
                    f"[yellow]{target.display_name} does not support a provider-specific base URL here.[/yellow]"
                )
                return
            base_url = custom_parts[0]
            api_key = custom_parts[1]

        _save_key_to_env_file(target.env_var, api_key)
        os.environ[target.env_var] = api_key
        if target.base_env_var and base_url:
            _save_key_to_env_file(target.base_env_var, base_url)
            os.environ[target.base_env_var] = base_url
        reset_model_selector()

        detail = f" and {target.base_env_var}" if target.base_env_var and base_url else ""
        self.console.print(
            f"[green]\u2713[/green] {target.display_name} ({target.env_var}{detail}) saved to ~/.pantheon/.env"
        )

    async def _handle_model_command(self, args: str):
        """Handle /model command - list or set model."""
        if not args:
            await self._show_models()
        else:
            await self._set_current_agent_model(args)

    async def _show_models(self):
        """Show current model and available models."""
        self.console.print()
        self.console.print(
            "[dim][bold blue]-- MODEL INFO -------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()

        # Get current agent and its model
        current_agent_name = self._current_agent_name
        if not current_agent_name and self._team and self._team.agents:
            current_agent_name = list(self._team.agents.keys())[0]

        if current_agent_name and self._team:
            agent = self._team.agents.get(current_agent_name)
            if agent and hasattr(agent, "models") and agent.models:
                models_str = agent.models[0]
                if len(agent.models) > 1:
                    fallback = ", ".join(agent.models[1:3])
                    if len(agent.models) > 3:
                        fallback += ", ..."
                    models_str += f" [dim](fallback: {fallback})[/dim]"
                self.console.print(f"  [bold]Current Agent:[/bold] {current_agent_name}")
                self.console.print(f"  [bold]Current Model:[/bold] {models_str}")
            else:
                self.console.print(f"  [bold]Current Agent:[/bold] {current_agent_name}")
                self.console.print("  [bold]Current Model:[/bold] [dim]not set[/dim]")
        else:
            self.console.print("  [yellow]No agent loaded[/yellow]")

        self.console.print()
        self.console.print(
            "[dim]─────────────────────────────────────────────────────────────────[/dim]"
        )
        self.console.print()

        # Get available models from ChatRoom
        result = await self._chatroom.list_available_models()
        if not result.get("success", False):
            self.console.print(f"  [red]Failed to list models: {result.get('message', 'Unknown error')}[/red]")
            self.console.print()
            return

        current_provider = result.get("current_provider")
        models_by_provider = result.get("models_by_provider", {})
        supported_tags = result.get("supported_tags", [])

        self.console.print("  [bold]Available Models:[/bold]")
        self.console.print()

        for provider, models in models_by_provider.items():
            marker = "[green](current)[/green]" if provider == current_provider else ""
            self.console.print(f"  [cyan]{provider}[/cyan] {marker}")
            # Show first 5 models, truncate if more
            display_models = models[:5]
            models_line = ", ".join(display_models)
            if len(models) > 5:
                models_line += f", ... (+{len(models) - 5} more)"
            self.console.print(f"    [dim]{models_line}[/dim]")
            self.console.print()

        # Show supported tags
        if supported_tags:
            quality_tags = [t for t in supported_tags if t in ("high", "normal", "low")]
            capability_tags = [t for t in supported_tags if t not in ("high", "normal", "low")]
            self.console.print("  [bold]Supported Tags:[/bold]")
            self.console.print(f"    Quality: [cyan]{', '.join(quality_tags)}[/cyan]")
            self.console.print(f"    Capability: [cyan]{', '.join(capability_tags)}[/cyan]")
            self.console.print()

        self.console.print(
            "[dim]─────────────────────────────────────────────────────────────────[/dim]"
        )
        self.console.print()
        self.console.print("  [dim]Usage: /model <model_name> or /model <tag>[/dim]")
        self.console.print("  [dim]Examples: /model openai/gpt-4o, /model high, /model normal,vision[/dim]")
        self.console.print()

    async def _set_current_agent_model(self, model: str):
        """Set model for current agent."""
        # Get current agent name
        current_agent_name = self._current_agent_name
        if not current_agent_name and self._team and self._team.agents:
            current_agent_name = list(self._team.agents.keys())[0]

        if not current_agent_name:
            self.console.print("[red]No agent available to set model[/red]")
            return

        if not self._chat_id:
            self.console.print("[red]No active chat session[/red]")
            return

        # Call ChatRoom API to set model
        result = await self._chatroom.set_agent_model(
            chat_id=self._chat_id,
            agent_name=current_agent_name,
            model=model,
        )

        if result.get("success"):
            resolved_models = result.get("resolved_models", [])
            if len(resolved_models) > 3:
                models_display = ", ".join(resolved_models[:3]) + ", ..."
            else:
                models_display = ", ".join(resolved_models)
            self.console.print(
                f"[green]✅ Model set:[/green] {model} → [cyan][{models_display}][/cyan]"
            )
            # Update status bar
            if self.prompt_app and resolved_models:
                self.prompt_app.update_model(resolved_models[0])
        else:
            self.console.print(
                f"[red]Failed to set model: {result.get('message', 'Unknown error')}[/red]"
            )

    async def _handle_clear(self):
        """Clear screen and delete current chat (with confirmation).
        
        This command will:
        1. Clear the terminal screen
        2. Delete the current conversation memory
        3. Create a new chat session
        
        Requires confirmation to prevent accidental data loss.
        """
        # Show warning and set pending state
        self.console.print()
        self.console.print("[yellow]⚠️  Warning:[/yellow] This will delete the current conversation history.")
        self.console.print("[dim]To create a new chat without deleting history, use /new instead.[/dim]")
        self.console.print()
        self.console.print("[bold]Type 'yes' to confirm, or anything else to cancel:[/bold]")
        self.console.print()
        
        # Set pending confirmation state
        self._pending_clear_confirmation = True

    def _handle_save_command(self, command: str):
        """Handle /save command."""
        try:
            parts = command.split()
            if len(parts) > 1:
                filename = parts[1]
                if not filename.endswith(".json"):
                    filename += ".json"
            else:
                filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"

            # Save via ChatRoom's memory manager
            asyncio.create_task(self._save_chat(filename))
            self.console.print(f"[green]✅ Conversation saved to:[/green] {filename}")
        except Exception as e:
            self.console.print(f"[red]Error saving conversation: {str(e)}[/red]")
        self.console.print()

    async def _save_chat(self, filename: str):
        """Save current chat to file."""
        # Read-only: saving chat to file, no need to fix
        memory = self._chatroom.memory_manager.get_memory(self._chat_id)
        memory.save(filename)

    def _handle_load_command(self, command: str):
        """Handle /load command."""
        try:
            parts = command.split()
            filename = parts[1]
            self.console.print(
                f"[yellow]Note: /load is not fully supported in ChatRoom mode. Use /resume instead.[/yellow]"
            )
        except Exception as e:
            self.console.print(f"[red]Error loading conversation: {str(e)}[/red]")
        self.console.print()

    async def _handle_compress(self):
        """Trigger manual context compression."""
        # Update status bar to show compression in progress
        if self.prompt_app:
            self.prompt_app.update_processing(status="Compressing...")

        self.console.print()
        self.console.print(
            "[dim][bold blue]-- COMPRESS ---------------------------------------------------------[/bold blue][/dim]"
        )
        self.console.print()
        
        try:
            result = await self._chatroom.compress_chat(self._chat_id)
            
            if result.get("success"):
                # Show compression stats
                compressed_msgs = result.get("compressed_messages", 0)
                original_tokens = result.get("original_tokens", 0)
                new_tokens = result.get("new_tokens", 0)
                saved_tokens = original_tokens - new_tokens

                self.console.print("[green]✅ Context compression completed[/green]")
                if compressed_msgs:
                    self.console.print(f"[dim]  • Compressed {compressed_msgs} messages[/dim]")
                    self.console.print(f"[dim]  • Tokens: {original_tokens:,} → {new_tokens:,} (saved {saved_tokens:,})[/dim]")

                # Update status bar with post-compression estimate
                if self.prompt_app:
                    try:
                        # Read-only: getting pre-compression token statistics, no need to fix
                        memory = self._chatroom.memory_manager.get_memory(self._chat_id)
                        if memory:
                            # Find pre-compression total/max from last metadata
                            all_msgs = memory.get_messages(for_llm=False)
                            last_meta = next(
                                (m.get("_metadata") for m in reversed(all_msgs)
                                 if m.get("_metadata", {}).get("total_tokens")),
                                None,
                            )
                            if last_meta:
                                old_total = last_meta.get("total_tokens", 0)
                                max_tokens = last_meta.get("max_tokens", 200000)
                                new_total = max(0, old_total - saved_tokens)
                                usage_pct = (new_total / max_tokens * 100) if max_tokens > 0 else 0
                            else:
                                # No metadata found — just show low usage
                                usage_pct = 0.0

                            from pantheon.utils.llm import calculate_total_cost_from_messages
                            total_cost = calculate_total_cost_from_messages(all_msgs)
                            self.prompt_app.update_token_usage(usage_pct, total_cost)
                            # Prevent _update_status_bar_token_usage from overwriting
                            # with stale metadata from preserved messages
                            self._skip_token_update = True
                    except Exception as e:
                        logger.debug(f"Failed to update status bar after compression: {e}")
                
            else:
                msg = result.get("message", "Compression not available")
                self.console.print(f"[yellow]⚠ {msg}[/yellow]")
        
        except Exception as e:
            self.console.print(f"[red]Error: {str(e)}[/red]")
        
        self.console.print()


if __name__ == "__main__":
    agent = Agent("agent", "You are a helpful assistant.")
    repl = Repl(agent=agent)
    asyncio.run(repl.run())
