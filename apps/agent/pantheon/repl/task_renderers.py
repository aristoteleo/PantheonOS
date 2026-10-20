"""Task UI Renderers for REPL.

This module provides Task UI and Notify UI rendering for the REPL,
supporting both static (scrollback) and dynamic (real-time) display modes.
"""

from dataclasses import dataclass, field
from collections import deque
from io import StringIO
import shutil
from datetime import datetime
from collections import deque
import shutil
from typing import TYPE_CHECKING, Optional
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from .prompt_app import PantheonInputApp


@dataclass
class ToolCallInfo:
    """Info about a single tool call."""
    name: str           # Full tool name (e.g., 'file_manager__read_file')
    key_param: str      # Key parameter value for display
    is_running: bool    # Whether tool is currently running


@dataclass
class MessageInfo:
    """Info about an assistant message."""
    content: str        # Truncated message content for display
    is_current: bool    # Whether this is in the current (in-progress) step


# Union type for step items (message or tool call)
StepItem = ToolCallInfo | MessageInfo


@dataclass
class AssistantStep:
    """An assistant step containing ordered items (messages and tool calls).
    
    Items are stored in chronological order to preserve the sequence of
    messages and tool calls within a single status phase.
    """
    items: list[StepItem] = field(default_factory=list)


@dataclass
class TaskUIState:
    """Track current task state for UI rendering.
    
    Attributes:
        task_name: Current task name
        mode: Current mode (PLANNING/EXECUTION/VERIFICATION etc.)
        summary: Task summary (what has been done)
        current_status: Current status (what is being done next)
        status_history: List of previous status updates
        recent_steps: Recent assistant steps (bounded deque for automatic LRU)
    """
    task_name: str = ""
    mode: str = ""
    summary: str = ""
    current_status: str = ""
    status_history: list[str] = field(default_factory=list)
    # Use deque with maxlen for automatic bounded storage
    recent_steps: deque = field(default_factory=lambda: deque(maxlen=7))
    
    # Internal: current step being built (accumulates content + tools)
    _current_step: Optional[AssistantStep] = field(default=None, repr=False)
    
    def reset(self):
        """Reset state for new task."""
        self.task_name = ""
        self.mode = ""
        self.summary = ""
        self.current_status = ""
        self.status_history = []
        self.recent_steps = deque(maxlen=7)
        self._current_step = None


class TaskUIRenderer:
    """Render Task UI in both static and dynamic forms.
    
    Static: Printed to scrollback when task ends (via notify_user or new task)
    Dynamic: Real-time updates in fixed position above input area
    """
    
    MAX_RECENT_ITEMS = 7  # Number of recent items to display
    MAX_STATUS_HISTORY = 10
    
    # Key param value truncation (only the value, not the whole param)
    KEY_PARAM_MAX_LEN = 50  # Max length for key param value display
    
    # Spinner animation frames for active status
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
    # Mapping of tool functions to their key parameter for display
    # Format: function_name -> param_name (or list of param names to try in order)
    KEY_PARAMS = {
        # File operations
        "read_file": "file_path",
        "write_file": "file_path",
        "update_file": "file_path",
        "delete_file": "file_path",
        "create_directory": "directory_path",
        "delete_path": "path",
        "move_file": "source_path",
        "manage_path": "path",
        "apply_patch": "file_path",
        "glob": "pattern",
        "grep": "pattern",
        # Shell
        "run_command": "command",
        "run_command_async": "command",
        # Python
        "run_python_code": None,  # Code is too long, skip
        "execute_cell": None,
        # Web
        "fetch_url": "url",
        "search_web": "query",
        # Notebook
        "open_notebook": "notebook_path",
        "save_notebook": "notebook_path",
        # Think
        "think": "thought",
    }
    
    # Generic key params to try if function not in KEY_PARAMS
    GENERIC_KEY_PARAMS = ["file_path", "path", "url", "query", "command", "pattern"]
    
    def __init__(self, console: Console, prompt_app: Optional["PantheonInputApp"] = None):
        """Initialize TaskUIRenderer.
        
        Args:
            console: Rich console for output
            prompt_app: Optional PantheonInputApp for dynamic panel updates
        """
        self.console = console
        self.prompt_app = prompt_app
        self.state = TaskUIState()
        self._previous_states: list[TaskUIState] = []
        self._spinner_idx = 0  # Current spinner frame index
    
    def reset(self):
        """Reset state cleanly (e.g., for /clear or new session).
        
        Differs from on_notify_user in that it does NOT print the static
        task panel to scrollback. It simply wipes the state and hides the UI.
        """
        self.state = TaskUIState()
        self._previous_states = []
        if self.prompt_app:
            self.prompt_app.hide_task_panel()
    
    def set_prompt_app(self, prompt_app: "PantheonInputApp"):
        """Set the prompt app reference (for late binding)."""
        self.prompt_app = prompt_app
    
    def has_active_task(self) -> bool:
        """Check if there's an active task to display."""
        return bool(self.state.task_name)
    
    def advance_spinner(self):
        """Advance spinner to next frame (called by animation timer)."""
        self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNER_FRAMES)
        self._update_dynamic_panel()
    
    def update_task_boundary(self, args: dict):
        """Handle task_boundary tool call.
        
        Args:
            args: Tool call arguments containing task_name, mode, task_status, task_summary
        """
        new_name = args.get("task_name") or args.get("TaskName", "")
        
        # Handle %SAME% substitution
        if "%SAME%" in new_name:
            new_name = self.state.task_name
        
        # New task?
        if new_name != self.state.task_name:
            # Save old task to history if it existed
            if self.state.task_name:
                self._previous_states.append(self.state)
                # Print static version to scrollback
                self.render_static_task_panel(self.state)
            
            # Reset for new task (clears any pre-task accumulated steps)
            self.state = TaskUIState()
        
        # Update state
        self.state.task_name = new_name
        
        # Handle mode
        new_mode = args.get("mode") or args.get("Mode", "")
        if "%SAME%" in new_mode:
            new_mode = self.state.mode or (self._previous_states[-1].mode if self._previous_states else "")
        self.state.mode = new_mode.upper() if new_mode else self.state.mode
        
        # Finalize current step when status changes (new phase)
        new_status = args.get("task_status") or args.get("TaskStatus", "")
        if "%SAME%" not in new_status and new_status and new_status != self.state.current_status:
            self._finalize_current_step()
            if self.state.current_status:
                self.state.status_history.append(self.state.current_status)
                # Keep only recent history
                if len(self.state.status_history) > self.MAX_STATUS_HISTORY:
                    self.state.status_history = self.state.status_history[-self.MAX_STATUS_HISTORY:]
            
            # Clear recent steps to prevent leakage into new status context
            self.state.recent_steps.clear()
            
            self.state.current_status = new_status
        
        # Handle summary
        new_summary = args.get("task_summary") or args.get("TaskSummary", "")
        if "%SAME%" not in new_summary and new_summary:
            self.state.summary = new_summary
        
        # Update dynamic panel
        self._update_dynamic_panel()
    

    def add_tool_call(self, tool_name: str, args: dict = None, is_running: bool = True):
        """Add tool call to current assistant step.
        
        Args:
            tool_name: Full tool name (e.g., 'file_manager__read_file')
            args: Tool arguments dict
            is_running: Whether the tool is currently running
        """
        # Ensure we have a current step
        if self.state._current_step is None:
            self.state._current_step = AssistantStep()
        
        # Get key param value
        func_name = self._get_func_name(tool_name)
        key_param = self._get_key_param_value(func_name, args) if args else ""
        
        tool_info = ToolCallInfo(
            name=tool_name,
            key_param=key_param,
            is_running=is_running
        )
        self.state._current_step.items.append(tool_info)
        self._update_dynamic_panel()
    
    def update_tool_complete(self, tool_name: str, args: dict = None):
        """Mark a tool as complete (update running status to done)."""
        if self.state._current_step is None:
            return
        
        func_name = self._get_func_name(tool_name)
        # Search in items list for matching ToolCallInfo
        for item in self.state._current_step.items:
            if isinstance(item, ToolCallInfo) and func_name in item.name and item.is_running:
                item.is_running = False
                break
        self._update_dynamic_panel()
    
    def add_message(self, content: str):
        """Add assistant message content to current step.
        
        Messages are added in order alongside tool calls to preserve
        the chronological sequence of events.
        
        Note: Messages are NOT truncated - Rich Panel handles automatic
        text wrapping for long content.
        
        Args:
            content: Message content (will be displayed in full, with wrapping)
        """
        # Replace newlines with spaces for single-line display, but keep full content
        snippet = content.replace("\n", " ").strip()
        
        if self.state._current_step is None:
            self.state._current_step = AssistantStep()
        
        msg_info = MessageInfo(content=snippet, is_current=True)
        self.state._current_step.items.append(msg_info)
        self._update_dynamic_panel()
    
    def _finalize_current_step(self):
        """Finalize current step and add to recent_steps."""
        if self.state._current_step is not None:
            # Mark all items as not current
            for item in self.state._current_step.items:
                if isinstance(item, ToolCallInfo):
                    item.is_running = False
                elif isinstance(item, MessageInfo):
                    item.is_current = False
            
            # deque with maxlen automatically discards oldest items
            self.state.recent_steps.append(self.state._current_step)
            self.state._current_step = None
    
    def render_static_task_panel(self, state: TaskUIState):
        """Render static task panel (printed to scrollback when task ends).
        
        Args:
            state: TaskUIState to render
        """
        if not state.task_name:
            return
        
        # Build status history lines
        status_lines = []
        for s in state.status_history[-5:]:  # Last 5 historical statuses
            status_lines.append(f"  [green]✓[/green] {s}")
        if state.current_status:
            status_lines.append(f"  [green]✓[/green] {state.current_status}")
        
        # Build content
        content_parts = []
        if state.summary:
            content_parts.append(f"[bold]Summary:[/bold] {state.summary}")
        if status_lines:
            content_parts.append("\n[bold]Status History:[/bold]")
            content_parts.extend(status_lines)
        
        content = "\n".join(content_parts)
        
        # Mode badge color
        mode_color = self._get_mode_color(state.mode)
        
        panel = Panel(
            content,
            title=f"[bold green]✓ Task: {state.task_name}[/bold green] [{mode_color}]{state.mode}[/{mode_color}]",
            border_style="green",
            padding=(0, 1)
        )
        self.console.print(panel)
    
    def render_dynamic_task_panel(self, max_height: Optional[int] = None) -> Optional[Panel]:
        """Render dynamic task panel (real-time updates at fixed position).
        
        Args:
            max_height: Optional maximum height for the panel (to enforce borders)
        
        Returns:
            Rich Panel object, or None if no active task
        """
        if not self.state.task_name:
            return None
            
        # 1. Gather all potential content blocks
        # -------------------------------------
        summary_line = None
        if self.state.summary:
            summary_line = f"[dim]Summary:[/dim] {self.state.summary}"
            
        history_lines = []
        for status in self.state.status_history[-5:]:
            history_lines.append(f"[green]✓[/green] [dim]{status}[/dim]")
            
        current_status_line = None
        if self.state.current_status:
            spinner = self.SPINNER_FRAMES[self._spinner_idx]
            current_status_line = f"[cyan]{spinner}[/cyan] [bold]{self.state.current_status}[/bold]"
            
        # Collect all items (flattened)
        item_lines = []
        for step in self.state.recent_steps:
            item_lines.extend(self._flatten_step(step, is_current=False))
        if self.state._current_step:
            item_lines.extend(self._flatten_step(self.state._current_step, is_current=True))
        item_lines = [f"    {item}" for item in item_lines]


        # 2. Simplified Block-Based Truncation (User Requested)
        # -----------------------------------------------------
        # Strategy: 
        # - Bottom Block (Priority 1): Current Status + Last 7 Items
        # - Top Block (Priority 2): Summary + Last 5 History
        # - Combine and truncate Top Block first if needed.
        
        # Define limits
        LIMIT_ITEMS = 7
        LIMIT_HISTORY = 5
        
        # Construct Bottom Block
        bottom_block = []
        if current_status_line:
            bottom_block.append(current_status_line)
        # Add items (up to limit)
        recent_items = item_lines[-LIMIT_ITEMS:]
        bottom_block.extend(recent_items)
        
        # Construct Top Block
        top_block = []
        if summary_line:
            top_block.append(summary_line)
            top_block.append("") # Spacer
            
        # Add history (up to limit)
        recent_history = []
        for status in self.state.status_history[-LIMIT_HISTORY:]:
             recent_history.append(f"[green]✓[/green] [dim]{status}[/dim]")
        top_block.extend(recent_history)
        
        
        # Determine final content based on budget
        final_lines = []
        
        if not max_height:
             # Show everything (limited by block limits defined above for consistency)
             final_lines = top_block + bottom_block
        else:
            budget = max_height - 2
            if budget < 1: budget = 1
            
            # Try full content
            full_content = top_block + bottom_block
            if len(full_content) <= budget:
                final_lines = full_content
            else:
                # Overflow! Prioritize Bottom Block (Context)
                # Check if Bottom Block alone fits
                if len(bottom_block) <= budget:
                    # Bottom fits. Fill remaining budget with Top Block (from bottom up)
                    remaining = budget - len(bottom_block)
                    if remaining > 0:
                        final_lines = top_block[-remaining:] + bottom_block
                    else:
                        final_lines = bottom_block
                else:
                    # Even Bottom Block is too big. Truncate it (keep Status + as many items as fit)
                    # Status is at index 0 of bottom_block (if it exists)
                    # We want to keep the bottom-most items? Or Top-most items (closest to Status)?
                    # User said: "Current Status + Fixed items"
                    # status line is usually index 0 of bottom_block. Items follow.
                    # Wait, logic above: bottom_block = [Status, Item1, Item2...]
                    # So if budget=3, we show [Status, Item1, Item2].
                    final_lines = bottom_block[:budget]

        # Mode badge color
        mode_color = self._get_mode_color(self.state.mode)
        
        # Pass height to Panel if provided to ensure borders are preserved
        return Panel(
            "\n".join(final_lines),
            title=f"[bold cyan]📌 {self.state.task_name}[/bold cyan] [{mode_color}]{self.state.mode}[/{mode_color}]",
            border_style="cyan",
            padding=(0, 1),
            height=max_height
        )
    
    def _flatten_step(self, step: AssistantStep, is_current: bool = False) -> list[str]:
        """Flatten an AssistantStep into display lines.
        
        Iterates over step.items in order, formatting each MessageInfo or
        ToolCallInfo appropriately.
        
        Args:
            step: The AssistantStep to flatten
            is_current: Whether this is the current (in-progress) step
            
        Returns:
            List of formatted strings for display
        """
        result = []
        
        for item in step.items:
            if isinstance(item, MessageInfo):
                # Format message
                is_msg_current = item.is_current if is_current else False
                icon = "💬" if is_msg_current else "[dim]💬[/dim]"
                content = item.content if is_msg_current else f"[dim]{item.content}[/dim]"
                result.append(f"{icon} {content}")
            elif isinstance(item, ToolCallInfo):
                # Format tool call
                result.append(self._format_tool_info(item))
        
        return result
    
    def _format_tool_info(self, tool_info: ToolCallInfo) -> str:
        """Format a ToolCallInfo for display.

        Note: key_param is already truncated in _get_key_param_value.

        Args:
            tool_info: The ToolCallInfo to format

        Returns:
            Formatted string with Rich markup
        """
        func_name = self._get_func_name(tool_info.name)

        # Special rendering for think tool
        if func_name == "think":
            icon = "💭" if not tool_info.is_running else "⟳"
            thought = tool_info.key_param or ""
            # Show first line only, clean up
            first_line = thought.split("\n")[0].strip()
            if first_line:
                return f"{icon} [dim italic]{first_line}[/dim italic]"
            return f"{icon} [dim italic]thinking...[/dim italic]"

        status = "⟳" if tool_info.is_running else "✓"
        status_color = "cyan" if tool_info.is_running else "green"

        # Use common formatting
        base = self._format_tool_base(tool_info.name)

        # Add key param (already truncated in _get_key_param_value)
        if tool_info.key_param:
            return f"[{status_color}]{status}[/{status_color}] {base} : [dim]{tool_info.key_param}[/dim]"

        return f"[{status_color}]{status}[/{status_color}] {base}"
    
    def on_notify_user(self):
        """Handle notify_user (ends current task context display)."""
        if self.state.task_name:
            # Print static version to scrollback
            self.render_static_task_panel(self.state)
        
        # Reset state and hide dynamic panel
        self.state = TaskUIState()
        if self.prompt_app:
            self.prompt_app.hide_task_panel()
    
    def _update_dynamic_panel(self, height: Optional[int] = None):
        """Refresh dynamic panel content.
        
        Args:
            height: Optional explicit height for the rendered panel
        """
        if not self.prompt_app:
            return
        
        # Pull dynamic height from prompt_app if not provided explicit
        if height is None and hasattr(self.prompt_app, "_get_task_panel_height"):
            try:
                # _get_task_panel_height returns prompt_toolkit Dimension
                dim = self.prompt_app._get_task_panel_height()
                if hasattr(dim, "max"):
                    height = dim.max
            except Exception:
                pass

        if self.has_active_task():
            self.prompt_app.show_task_panel()
            
            # Render panel to ANSI string for prompt_toolkit
            panel = self.render_dynamic_task_panel(max_height=height)
            if panel:
                string_io = StringIO()
                # Use actual terminal width
                terminal_width = shutil.get_terminal_size().columns
                temp_console = Console(
                    file=string_io, 
                    force_terminal=True, 
                    width=terminal_width,
                    no_color=False
                )
                temp_console.print(panel)
                ansi_content = string_io.getvalue()
                self.prompt_app.update_task_panel(ansi_content)
        else:
            self.prompt_app.hide_task_panel()
    
    def _format_tool_base(self, tool_name: str) -> str:
        """Format tool name base (toolset > function format).
        
        Args:
            tool_name: Full tool name (e.g., 'file_manager__read_file')
            
        Returns:
            Formatted base string with Rich markup (no key param)
        """
        func_name = self._get_func_name(tool_name)
        if "__" in tool_name:
            toolset = tool_name.split("__", 1)[0]
            return f"[grey50]{toolset}[/grey50] > [cyan]{func_name}[/cyan]"
        return f"[cyan]{func_name}[/cyan]"
    
    def _get_func_name(self, tool_name: str) -> str:
        """Extract function name from full tool name."""
        if "__" in tool_name:
            return tool_name.split("__", 1)[1]
        return tool_name
    
    def _get_key_param_value(self, func_name: str, args: dict) -> str:
        """Get the key parameter value for a function.
        
        The value is truncated to KEY_PARAM_MAX_LEN if too long.
        Truncation preserves the end of the value (e.g., file paths show filename).
        
        Args:
            func_name: Function name (e.g., 'read_file')
            args: Tool arguments dict
            
        Returns:
            Key parameter value (truncated if needed) or empty string
        """
        if not args:
            return ""
        
        value = ""
        
        # Check specific mapping first
        if func_name in self.KEY_PARAMS:
            param_name = self.KEY_PARAMS[func_name]
            if param_name is None:  # Explicitly skip (e.g., code blocks)
                return ""
            value = args.get(param_name, "")
            if value:
                value = str(value)
        
        # Fallback to generic params
        if not value:
            for param in self.GENERIC_KEY_PARAMS:
                if param in args and args[param]:
                    value = str(args[param])
                    break
        
        # Truncate value if too long (keep the end, useful for file paths)
        if value and len(value) > self.KEY_PARAM_MAX_LEN:
            value = "..." + value[-(self.KEY_PARAM_MAX_LEN - 3):]
        
        return value
    
    def _get_mode_color(self, mode: str) -> str:
        """Get color for mode badge.
        
        Args:
            mode: Mode string (PLANNING, EXECUTION, VERIFICATION, etc.)
            
        Returns:
            Rich color name
        """
        mode_upper = mode.upper()
        if mode_upper in ("PLANNING", "RESEARCH", "DESIGN"):
            return "blue"
        elif mode_upper in ("EXECUTION", "ANALYSIS", "IMPLEMENTATION"):
            return "yellow"
        elif mode_upper in ("VERIFICATION", "INTERPRETATION", "TESTING"):
            return "green"
        return "white"


class NotifyUIRenderer:
    """Render Notify User UI with approval flow."""
    
    def __init__(self, console: Console):
        """Initialize NotifyUIRenderer.
        
        Args:
            console: Rich console for output
        """
        self.console = console
    
    def render_notification(self, result: dict) -> bool:
        """Render notification panel.
        
        Args:
            result: notify_user tool result dict
            
        Returns:
            True if blocked on user (needs approval), False otherwise
        """
        message = result.get("message", "")
        paths = result.get("paths", [])
        blocked = result.get("interrupt", False)
        
        if isinstance(paths, str):
            paths = [paths]
        
        # Build content — render message as Markdown
        renderables = []

        if message:
            renderables.append(Markdown(message))

        if paths:
            path_parts = ["[bold]📄 Files to review:[/bold]"]
            for p in paths:
                path_parts.append(f"  • {p}")
            renderables.append(Text(""))
            renderables.append(Text.from_markup("\n".join(path_parts)))

        content = Group(*renderables) if renderables else Text("")
        
        # Panel styling - simpler for blocked notifications (interactive dialog follows)
        if blocked:
            # Just show a minimal notification, interactive dialog will follow
            panel = Panel(
                content,
                title="[bold cyan]📋 Review Required[/bold cyan]",
                border_style="cyan",
                padding=(0, 1)
            )
        else:
            panel = Panel(
                content,
                title="[bold dim]📬 Notification[/bold dim]",
                border_style="dim",
                padding=(0, 1)
            )
        self.console.print(panel)
        
        return blocked
    
    def render_approval_result(self, approved: bool, feedback: str = ""):
        """Render approval result.
        
        Args:
            approved: Whether user approved
            feedback: Optional feedback message
        """
        if approved:
            self.console.print("[green]✓ Approved[/green]")
        else:
            self.console.print("[yellow]→ Rejected with feedback[/yellow]")
            if feedback:
                self.console.print(f"[dim]Feedback: {feedback}[/dim]")


__all__ = [
    "ToolCallInfo",
    "MessageInfo",
    "AssistantStep",
    "TaskUIState",
    "TaskUIRenderer", 
    "NotifyUIRenderer",
]
