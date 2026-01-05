"""Prompt application for REPL with prompt_toolkit integration.

This module provides a prompt_toolkit-based input session with:
- Fixed input box at bottom with horizontal line borders (top/bottom only)
- Dynamic status bar below input
- Command completion
- Async status bar refresh during processing
- Concurrent input processing via Application.run_async()
- Merged command completer logic
"""

import re
import sys
import time
import shutil
import asyncio
import unicodedata
from collections import deque

from pathlib import Path
from typing import TYPE_CHECKING, Optional, Callable, Awaitable, Tuple, List, Iterator

from prompt_toolkit import Application
from prompt_toolkit.layout import (
    Layout,
    HSplit,
    FloatContainer,
    Float,
    DynamicContainer,
    ConditionalContainer,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.auto_suggest import load_auto_suggest_bindings
from prompt_toolkit.styles import Style
from prompt_toolkit.filters import Condition
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML

from .utils import get_animation_frames, get_separator, get_wave_color
from pantheon.constant import FILE_COMPLETION_IGNORED, PROJECT_ROOT
from pantheon.utils.log import logger

if TYPE_CHECKING:
    from .core import Repl


# Image file extensions for @image: completion
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}


def subsequence_match(pattern: str, target: str) -> Tuple[bool, int]:
    """Subsequence fuzzy matching with scoring.

    Matches if all characters in pattern appear in target in order.
    Score is higher for better matches (consecutive, prefix, word boundary).

    Args:
        pattern: User input pattern (e.g., "rdme")
        target: Target filename (e.g., "README.md")

    Returns:
        (matched, score) - matched is True if pattern is subsequence of target
    """
    if not pattern:
        return True, 0

    pattern_lower = pattern.lower()
    target_lower = target.lower()

    p_idx = 0
    score = 0
    consecutive = 0
    prev_match_pos = -2

    for t_idx, char in enumerate(target_lower):
        if p_idx >= len(pattern_lower):
            break

        if char == pattern_lower[p_idx]:
            score += 1

            # Consecutive match bonus
            if t_idx == prev_match_pos + 1:
                consecutive += 1
                score += consecutive * 2
            else:
                consecutive = 0

            # Prefix match bonus
            if t_idx == 0:
                score += 15
            # Word boundary bonus (after _, -, ., or uppercase)
            elif t_idx > 0 and (target[t_idx - 1] in "_-." or target[t_idx].isupper()):
                score += 5

            prev_match_pos = t_idx
            p_idx += 1

    matched = p_idx == len(pattern_lower)

    # Exact prefix match bonus
    if matched and target_lower.startswith(pattern_lower):
        score += 20

    # Shorter filename bonus (prefer concise names)
    if matched:
        score -= len(target) // 10

    return matched, score


class FileSearchCache:
    """Cache for directory contents to avoid repeated I/O."""

    def __init__(self, ttl_seconds: float = 5.0, max_entries: int = 500):
        self._cache: dict[str, List[Path]] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._max_entries = max_entries

    def get_entries(self, directory: Path) -> Optional[List[Path]]:
        """Get cached directory contents if not expired."""
        key = str(directory.resolve())
        if key not in self._cache:
            return None

        if time.time() - self._timestamps[key] > self._ttl:
            del self._cache[key]
            del self._timestamps[key]
            return None

        return self._cache[key]

    def set_entries(self, directory: Path, entries: List[Path]):
        """Cache directory contents."""
        # Simple LRU: clear half when full
        if len(self._cache) >= self._max_entries:
            sorted_keys = sorted(self._timestamps.items(), key=lambda x: x[1])
            for k, _ in sorted_keys[: len(sorted_keys) // 2]:
                self._cache.pop(k, None)
                self._timestamps.pop(k, None)

        key = str(directory.resolve())
        self._cache[key] = entries
        self._timestamps[key] = time.time()

    def invalidate(self):
        """Clear all cache."""
        self._cache.clear()
        self._timestamps.clear()


class ReplCompleter(Completer):
    """Completer that provides completions for REPL commands and file paths.

    Supports:
    - Built-in commands starting with /
    - Custom handler commands
    - File path completions starting with @ (code/text files)
    - Image completions starting with @image: (images only)
    """

    # Built-in commands (sync with core.py run() command handling)
    BUILTIN_COMMANDS = [
        # Basic commands
        ("/help", "Show available commands"),
        ("/status", "Session info"),
        ("/history", "Command history"),
        ("/tokens", "Token usage analysis"),
        ("/save", "Save conversation"),
        ("/clear", "Clear/new chat"),
        ("/exit", "Exit REPL"),
        # Chat management
        ("/new", "New chat session"),
        ("/list", "List chat sessions"),
        ("/resume", "Resume another chat"),
        # Agent/Team
        ("/agents", "Show agents in team"),
        ("/agent", "Switch to specific agent"),
        ("/team", "Switch team: /team list | /team <id>"),
        ("/model", "Show/set model: /model [name|tag]"),
        # MCP server management
        ("/mcp", "MCP servers: /mcp [start|stop|add|remove]"),
        # Display modes
        ("/verbose", "Verbose output mode"),
        ("/v", "Verbose (short)"),
        ("/compact", "Compact output mode"),
        ("/c", "Compact (short)"),
        # Context management
        ("/compress", "Force context compression"),
    ]

    # File search configuration
    MAX_SEARCH_DEPTH = 4  # Maximum recursion depth
    MAX_RESULTS = 20  # Maximum completion results
    SEARCH_TIMEOUT_MS = 100  # Search timeout in milliseconds

    # Shared cache across instances
    _file_cache = FileSearchCache(ttl_seconds=5.0)

    def __init__(self, repl: "Repl" = None):
        """Initialize completer.

        Args:
            repl: Reference to Repl instance for accessing custom handlers.
        """
        self.repl = repl

    def get_completions(self, document, complete_event):
        """Generate completions for the current input.

        Args:
            document: The current document being edited.
            complete_event: Information about how completion was triggered.

        Yields:
            Completion objects for matching commands or file paths.
        """
        text = document.text_before_cursor.lstrip()

        # Image completion: triggered by @image:
        if "@image:" in text:
            yield from self._get_file_completions(document, images_only=True)
            return

        # File path completion: triggered by @
        if "@" in text:
            yield from self._get_file_completions(document, images_only=False)
            return

        # Command completion: starts with /
        if not text.startswith("/"):
            return

        # Built-in command completions
        for cmd, desc in self.BUILTIN_COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display_meta=desc)

        # Custom handler command completions
        if self.repl and hasattr(self.repl, "handlers"):
            for handler in self.repl.handlers:
                if hasattr(handler, "get_commands"):
                    for cmd, desc in handler.get_commands():
                        if cmd.startswith(text):
                            yield Completion(
                                cmd, start_position=-len(text), display_meta=desc
                            )

    def _get_file_completions(self, document, images_only: bool = False):
        """Generate file path completions for @ or @image: mentions.

        Supports two modes:
        1. Navigate mode: @docs/ → show contents of docs directory
        2. Search mode: @readme → recursive fuzzy search for matching files

        Args:
            images_only: If True, only show image files (for @image: prefix)
        """
        text = document.text_before_cursor

        # Determine prefix type and extract path
        if images_only:
            at_pos = text.rfind("@image:")
            if at_pos == -1:
                return
            path_prefix = text[at_pos + 7 :]
            start_position = -(len(path_prefix) + 7)
            completion_prefix = "@image:"
        else:
            at_pos = text.rfind("@")
            if at_pos == -1:
                return
            path_prefix = text[at_pos + 1 :]
            start_position = -(len(path_prefix) + 1)
            completion_prefix = "@"

        workspace = self._get_workspace()

        # Mode selection: "/" means navigate mode, otherwise search mode
        if "/" in path_prefix:
            yield from self._navigate_mode(
                path_prefix, workspace, start_position, completion_prefix, images_only
            )
        else:
            yield from self._search_mode(
                path_prefix, workspace, start_position, completion_prefix, images_only
            )

    def _navigate_mode(
        self,
        path_prefix: str,
        workspace: Path,
        start_position: int,
        completion_prefix: str,
        images_only: bool,
    ) -> Iterator[Completion]:
        """Navigate mode - show contents of specified directory (original behavior)."""
        dir_part, name_prefix = path_prefix.rsplit("/", 1)
        search_dir = workspace / dir_part

        if not search_dir.exists() or not search_dir.is_dir():
            return

        # Use cache
        entries = self._file_cache.get_entries(search_dir)
        if entries is None:
            try:
                entries = sorted(
                    list(search_dir.iterdir()),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
                self._file_cache.set_entries(search_dir, entries)
            except PermissionError:
                return

        count = 0
        for entry in entries:
            if count >= self.MAX_RESULTS:
                break

            if entry.name in FILE_COMPLETION_IGNORED:
                continue

            # Prefix match
            if name_prefix and not entry.name.lower().startswith(name_prefix.lower()):
                continue

            # Image filter
            if images_only and not entry.is_dir():
                if entry.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue

            rel_path = f"{dir_part}/{entry.name}"

            if entry.is_dir():
                yield Completion(
                    f"{completion_prefix}{rel_path}/",
                    start_position=start_position,
                    display=f"{entry.name}/",
                    display_meta="dir",
                )
            else:
                file_type = "img" if images_only else self._get_file_type(entry)
                yield Completion(
                    f"{completion_prefix}{rel_path}",
                    start_position=start_position,
                    display=entry.name,
                    display_meta=file_type,
                )
            count += 1

    def _search_mode(
        self,
        pattern: str,
        workspace: Path,
        start_position: int,
        completion_prefix: str,
        images_only: bool,
    ) -> Iterator[Completion]:
        """Search mode - recursive fuzzy search for files."""
        # Empty pattern: show root directory contents
        if not pattern:
            yield from self._navigate_mode(
                "/", workspace, start_position, completion_prefix, images_only
            )
            return

        start_time = time.time()
        results: List[Tuple[Path, int]] = []

        # BFS recursive search
        queue = deque([(workspace, 0)])

        while queue:
            # Timeout check
            if (time.time() - start_time) * 1000 > self.SEARCH_TIMEOUT_MS:
                break

            # Enough results
            if len(results) >= self.MAX_RESULTS * 2:
                break

            current_dir, depth = queue.popleft()

            if depth > self.MAX_SEARCH_DEPTH:
                continue

            # Get directory contents (with cache)
            entries = self._file_cache.get_entries(current_dir)
            if entries is None:
                try:
                    entries = list(current_dir.iterdir())
                    self._file_cache.set_entries(current_dir, entries)
                except (PermissionError, OSError):
                    continue

            for entry in entries:
                # Skip blacklisted directories
                if entry.name in FILE_COMPLETION_IGNORED:
                    continue

                # Image filter
                if images_only and entry.is_file():
                    if entry.suffix.lower() not in IMAGE_EXTENSIONS:
                        continue

                # Fuzzy match
                matched, score = subsequence_match(pattern, entry.name)
                if matched:
                    results.append((entry, score))

                # Recurse into subdirectories
                if entry.is_dir() and depth < self.MAX_SEARCH_DEPTH:
                    queue.append((entry, depth + 1))

        # Sort by score (descending), then by name
        results.sort(key=lambda x: (-x[1], x[0].name.lower()))

        # Generate completions
        for entry, score in results[: self.MAX_RESULTS]:
            try:
                rel_path = str(entry.relative_to(workspace))
            except ValueError:
                rel_path = entry.name

            if entry.is_dir():
                # Show parent path in meta for context
                parent = (
                    str(entry.parent.relative_to(workspace))
                    if entry.parent != workspace
                    else "."
                )
                yield Completion(
                    f"{completion_prefix}{rel_path}/",
                    start_position=start_position,
                    display=f"{entry.name}/",
                    display_meta=f"dir @ {parent}",
                )
            else:
                file_type = "img" if images_only else self._get_file_type(entry)
                parent = (
                    str(entry.parent.relative_to(workspace))
                    if entry.parent != workspace
                    else "."
                )
                yield Completion(
                    f"{completion_prefix}{rel_path}",
                    start_position=start_position,
                    display=entry.name,
                    display_meta=f"{file_type} @ {parent}",
                )

    def _get_workspace(self) -> Path:
        """Get workspace path - uses launch directory (PROJECT_ROOT)."""
        return PROJECT_ROOT

    def _get_file_type(self, path: Path) -> str:
        """Get file type description based on extension."""
        ext_map = {
            ".py": "py",
            ".js": "js",
            ".ts": "ts",
            ".tsx": "tsx",
            ".md": "md",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".txt": "txt",
            ".sh": "sh",
            ".css": "css",
            ".html": "html",
            ".sql": "sql",
            ".rs": "rs",
            ".go": "go",
        }
        return ext_map.get(path.suffix.lower(), "file")


def create_key_bindings(app_instance: "PantheonInputApp") -> KeyBindings:
    """Create key bindings for app input handling.

    Args:
        app_instance: The PantheonInputApp instance.
    """
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        """Ctrl+C to clear/cancel or exit."""
        # Calculate how many extra lines need to be cleared
        prev_lines = app_instance._prev_line_count

        # Reset state first
        app_instance._prev_line_count = 1

        # Clean current input buffer
        event.current_buffer.text = ""

        # Force complete re-render by resetting renderer state
        try:
            renderer = event.app.renderer
            # Reset the renderer's height tracking
            renderer.reset()
            event.app.invalidate()
        except Exception:
            pass

        # Check if we should exit (double press check via repl)
        repl = app_instance.repl
        if hasattr(repl, "handle_interrupt"):
            should_exit = repl.handle_interrupt()
            if should_exit:
                # Signal app to exit
                event.app.exit(exception=EOFError())
        else:
            pass

    @kb.add("enter")
    def _(event):
        """Enter to submit or accept completion."""
        buffer = event.current_buffer

        # If completion menu is open
        if buffer.complete_state:
            completion = buffer.complete_state.current_completion
            if completion:
                # Accept the selected completion
                buffer.apply_completion(completion)
                return
            else:
                # No completion selected - close menu and submit input
                buffer.cancel_completion()
                # Fall through to submit

        # Submit the input
        text = buffer.text
        if text.strip():
            app_instance.accept_input(buffer)

    # Condition: check if processing
    is_processing = Condition(lambda: getattr(app_instance, "_is_processing", False))

    @kb.add("escape", "enter")  # Alt+Enter to insert newline
    @kb.add("c-j")  # Ctrl+J as reliable fallback
    def _(event):
        """Insert newline."""
        event.current_buffer.insert_text("\n")

    @kb.add("c-d")
    def _(event):
        """Ctrl+D to exit (EOF)."""
        # Print simple session summary (sync - can't await in key binding)
        repl = app_instance.repl
        if hasattr(repl, "console") and hasattr(repl, "message_count"):
            repl.console.print(f"\n[dim]Session: {repl.message_count} messages[/dim]")
            repl.console.print("[dim]Goodbye![/dim]")
        # Signal app to exit
        event.app.exit(exception=EOFError())

    @kb.add("escape", eager=True, filter=is_processing)
    def _(event):
        """Escape to cancel operation immediately (only when processing)."""
        # Cancel any running agent task
        repl = app_instance.repl
        if hasattr(repl, "_current_agent_task") and repl._current_agent_task:
            if not repl._current_agent_task.done():
                repl._current_agent_task.cancel()
                print("\n[Cancelled]")

    @kb.add("escape", filter=~is_processing)
    def _(event):
        """Escape to clear input (when idle)."""
        event.current_buffer.text = ""

    @kb.add("c-t")
    def _(event):
        """Ctrl+T to toggle display mode (COMPACT <-> VERBOSE).

        Works both during processing and when idle.
        """
        from .renderers import DisplayMode

        repl = app_instance.repl
        if hasattr(repl, "display_config"):
            current = repl.display_config.mode
            if current == DisplayMode.COMPACT:
                repl.display_config.mode = DisplayMode.VERBOSE
                print("\n[Switched to VERBOSE mode]")
            else:
                repl.display_config.mode = DisplayMode.COMPACT
                print("\n[Switched to COMPACT mode]")

    return kb


class PantheonInputApp:
    """prompt_toolkit Application based input session.

    Uses standard Application + Layout + Frame + TextArea to achieve:
    1. Bordered Input Box
    2. Fixed placement at bottom (full_screen=False)
    3. Concurrent input usage

    Features:
    - Bordered input frame with dynamic status bar title
    - Command history and auto-suggest
    - Async status bar refresh during processing
    """

    def __init__(
        self,
        history_file: str,
        completer: ReplCompleter,
        repl: "Repl",
        message_queue: asyncio.Queue,
    ):
        """Initialize prompt app.

        Args:
            history_file: Path to command history file.
            completer: ReplCompleter instance for tab completion.
            repl: Reference to parent Repl instance.
            message_queue: Queue to put submitted messages into.
        """
        self.repl = repl
        self.message_queue = message_queue

        # Unicode compatibility - detect and fallback to ASCII if needed
        self.SPINNER_FRAMES = get_animation_frames()
        self._separator = get_separator()

        # Status information
        self._model_name = ""
        self._current_agent = ""
        self._status_text = "ready"
        self._is_processing = False
        self._start_time = 0
        self._processing_start_time = 0  # For real-time animation calculation
        self._input_tokens = 0
        self._output_tokens = 0
        self._refresh_task = None
        self._wave_offset = 0
        self._token_usage_pct = 0.0  # Token usage percentage for status bar
        self._total_cost = 0.0  # Total session cost

        # Task panel state
        self._task_panel_visible = False
        self._task_panel_content = ""  # Pre-rendered ANSI content

        # Style
        self.style = Style.from_dict(
            {
                "frame.border": "fg:ansiblue",
                "frame.label": "fg:ansiwhite bold",
                "input-area": "",  # Default text style
            }
        )

        # Input Widget (TextArea)
        self.text_area = TextArea(
            multiline=True,
            wrap_lines=True,  # Enable auto line wrap
            completer=completer,
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
            focusable=True,
            style="class:input-area",
            prompt="> ",
            height=Dimension(
                min=1, max=10, preferred=1
            ),  # Start with 1 line, expand as needed
        )

        # Track line count for dynamic height adjustment
        self._prev_line_count = 1

        def on_text_changed(buffer):
            """Handle text changes - force redraw when lines decrease."""
            if not hasattr(self, "app"):
                return

            # Calculate current line count including wrapped lines
            current_lines = self._calculate_visual_lines(buffer.text)

            if current_lines < self._prev_line_count:
                # Lines decreased - erase and request full redraw
                try:
                    self.app.renderer.erase()
                except Exception:
                    pass

            self._prev_line_count = current_lines
            self.app.invalidate()

        self.text_area.buffer.on_text_changed += on_text_changed

        # Key bindings
        self.kb = create_key_bindings(self)
        # Apply key bindings to the text area's buffer
        # Note: TextArea creates its own Buffer/KeyBindings, we merge ours globally or attached to Window
        # The easiest way for TextArea is to handle accept_handler, but we want custom Enter logic
        # So we attach our KB to the Layout or Application

        # Main Layout Structure
        # 1. Task Panel (when active task)
        # 2. Processing status line (above input, only visible when processing)
        # 3. Input Area (TextArea) with top/bottom borders
        # 4. Status Bar (model/agent info) below Input
        # All wrapped in FloatContainer to support CompletionsMenu (dropdown)

        self.processing_control = FormattedTextControl(
            text=self.get_processing_formatted_text
        )
        self.status_control = FormattedTextControl(text=self.get_status_formatted_text)
        self.task_panel_control = FormattedTextControl(text=self._get_task_panel_text)

        self.root_container = HSplit(
            [
                # Dynamic Task Panel (only visible when there's an active task)
                ConditionalContainer(
                    Window(
                        content=self.task_panel_control,
                        height=Dimension(
                            min=6,
                            max=20,  # Increased max height
                            preferred=15,  # Prefer max height if content available
                        ),
                        style="class:task-panel",
                    ),
                    filter=Condition(lambda: self._task_panel_visible),
                ),
                # Empty line for spacing (only when processing)
                ConditionalContainer(
                    Window(height=1), filter=Condition(lambda: self._is_processing)
                ),
                # Processing status line (directly above input)
                ConditionalContainer(
                    Window(
                        content=self.processing_control,
                        height=1,
                        style="class:processing-bar",
                    ),
                    filter=Condition(lambda: self._is_processing),
                ),
                # Dynamic Input Container
                DynamicContainer(self._get_input_container),
                # Status Bar below input (model/agent info)
                Window(content=self.status_control, height=1, style="class:status-bar"),
            ]
        )

        self.layout = Layout(
            FloatContainer(
                content=self.root_container,
                floats=[
                    Float(
                        xcursor=True,
                        ycursor=False,
                        bottom=2,  # Anchor above input line (Status=1 + BottomLine=1 => 2 from bottom)
                        content=CompletionsMenu(max_height=16, scroll_offset=1),
                    ),
                ],
            )
        )

        # Initialize Application
        # Exception handling: set_exception_handler=False in run_async() suppresses
        # "Press ENTER to continue..." prompts. Additional catch in core.py logs exceptions.
        # Merge custom key bindings with auto-suggest bindings (for right arrow to accept suggestion)
        auto_suggest_bindings = load_auto_suggest_bindings()
        merged_kb = merge_key_bindings([auto_suggest_bindings, self.kb])

        self.app = Application(
            layout=self.layout,
            style=self.style,
            key_bindings=merged_kb,
            mouse_support=False,  # Disable mouse support to allow terminal scrolling
            full_screen=False,
            refresh_interval=0.125,  # 8 fps for smooth animation
        )

    def _create_horizontal_line(self, char: str = "─", style: str = "fg:ansiblue"):
        """Create a horizontal line that spans terminal width."""

        def get_line():
            # Return a long line, prompt_toolkit will auto-truncate to terminal width
            return [(style, char * 500)]

        return Window(
            content=FormattedTextControl(get_line),
            height=1,
        )

    def _get_display_width(self, text: str) -> int:
        """Calculate display width of text, accounting for wide characters.

        CJK characters and other wide characters take 2 columns in terminal.

        Args:
            text: Input text string

        Returns:
            Display width in terminal columns
        """
        width = 0
        for char in text:
            # Get East Asian Width property
            ea_width = unicodedata.east_asian_width(char)
            if ea_width in ("W", "F"):
                # Wide or Fullwidth characters take 2 columns
                width += 2
            elif ea_width == "A":
                # Ambiguous width - treat as wide in CJK context
                width += 2
            else:
                # Narrow, Halfwidth, Neutral - 1 column
                width += 1
        return width

    def _calculate_visual_lines(self, text: str) -> int:
        """Calculate visual line count including wrapped lines.

        Args:
            text: Input text content

        Returns:
            Number of visual lines (accounting for wrap)
        """
        if not text:
            return 1

        # Get terminal width, subtract prompt width ("> " = 2 chars) and some margin
        try:
            terminal_width = shutil.get_terminal_size().columns
        except Exception:
            terminal_width = 80

        # Available width for text (subtract prompt "> " and margin)
        available_width = max(20, terminal_width - 4)

        visual_lines = 0
        for line in text.split("\n"):
            if not line:
                visual_lines += 1
            else:
                # Calculate display width accounting for wide characters
                line_width = self._get_display_width(line)
                # Calculate how many visual lines this logical line takes
                visual_lines += max(
                    1, (line_width + available_width - 1) // available_width
                )

        return min(visual_lines, 10)  # Cap at max height

    def _get_input_container(self):
        """Return input container with top/bottom horizontal lines (no side borders).

        Height is dynamically calculated based on actual content to prevent
        prompt_toolkit from allocating excess space on freshly cleared terminals.
        """
        # Calculate exact height needed: content lines + 2 (top/bottom borders)
        content_lines = self._calculate_visual_lines(self.text_area.buffer.text)
        total_height = content_lines + 2  # +2 for border lines

        return HSplit(
            [
                self._create_horizontal_line("─", "fg:ansiblue"),  # Top line
                self.text_area,
                self._create_horizontal_line("─", "fg:ansiblue"),  # Bottom line
            ],
            height=total_height,
        )

    def accept_input(self, buffer: Buffer):
        """Handle input submission from TextArea."""
        text = buffer.text

        # Add to history (TextArea handles file history automatically on submit usually,
        # but since we manual handle Enter, we should optimize)
        buffer.append_to_history()

        # Print to stdout so it appears in scrollback (show original text with @)
        # We must use print WITHOUT redirecting to the app buffer, so use proper stdout
        # But we are likely inside patch_stdout, so standard print works well to put text 'above'
        # Format: "> message" with light gray background (ANSI 256-color: 238)
        bg_color = "\033[48;5;238m"  # Light gray background
        reset = "\033[0m"
        # Handle multiline: prefix each line with "> "
        lines = text.split("\n")
        formatted_lines = [f"{bg_color}> {line}{reset}" for line in lines]
        print("\n" + "\n".join(formatted_lines))

        # Expand @path mentions to absolute paths before sending to agent
        text = self._expand_file_mentions(text)

        # Put into queue
        self.message_queue.put_nowait(text)

        # Reset buffer
        buffer.reset()

    def _expand_file_mentions(self, text: str) -> str:
        """Convert @relative/path to '/absolute/path' in text."""
        workspace = self._get_workspace()

        # Pattern: @ followed by a path (not starting with /)
        # Match @word or @path/to/file but not @/absolute/path
        pattern = r"@(?!/)([\w./\-]+)"

        def replace_path(match):
            rel_path = match.group(1)
            abs_path = (workspace / rel_path).resolve()
            if abs_path.exists():
                return f"'{abs_path}'"
            # Keep original if path doesn't exist
            return match.group(0)

        return re.sub(pattern, replace_path, text)

    def _get_workspace(self) -> Path:
        """Get workspace path from repl or fallback to PROJECT_ROOT."""
        if self.repl:
            try:
                endpoint = self.repl._chatroom._endpoint
                if endpoint and hasattr(endpoint, "path"):
                    return endpoint.path
            except AttributeError:
                pass
        return PROJECT_ROOT

    async def run_async(self):
        """Run the application asynchronously."""
        # Ensure text area is focused
        self.app.layout.focus(self.text_area)
        # Force initial renderer reset for clean state (fixes height issues on fresh terminals)
        try:
            self.app.renderer.reset()
        except Exception:
            pass
        # set_exception_handler=False prevents "Press ENTER to continue..." prompts
        # when unhandled exceptions occur in the event loop
        await self.app.run_async(set_exception_handler=False)

    def get_processing_formatted_text(self) -> HTML:
        """Generate processing status line content (above input) with wave animation."""
        # Always use real-time calculation for smooth animation
        # This avoids visual "jumps" when switching between real-time and external updates
        if self._processing_start_time > 0:
            elapsed = time.time() - self._processing_start_time
            spinner_idx = int(elapsed * 8) % len(self.SPINNER_FRAMES)
            spinner = self.SPINNER_FRAMES[spinner_idx]
            wave_offset = int(elapsed * 4)
        else:
            spinner = self.SPINNER_FRAMES[0]
            wave_offset = 0
            elapsed = 0.0

        # Create wave text
        wave_text_parts = []
        clean_status = self._status_text

        # Apply wave color to each character
        for i, char in enumerate(clean_status):
            if char.isspace():
                wave_text_parts.append(char)
                continue
            color = get_wave_color(i, wave_offset)
            wave_text_parts.append(f'<style fg="{color}">{char}</style>')

        wave_html = "".join(wave_text_parts)

        # Only show token counts when we have output tokens
        if self._output_tokens > 0:
            token_info = (
                f"{self._separator} {self._input_tokens} in, {self._output_tokens} out "
            )
        else:
            token_info = ""

        return HTML(
            f"{spinner} {wave_html} {token_info}"
            f"{self._separator} {elapsed:.1f}s "
            f'{self._separator} <style fg="#888888">[Esc] cancel</style>'
        )

    def get_status_formatted_text(self) -> HTML:
        """Generate bottom status bar content (model/agent info) in muted gray."""
        # Show 1 decimal place for values < 1%, otherwise show integer
        if self._token_usage_pct <= 0:
            usage_display = "ctx: 0%"
        elif self._token_usage_pct < 1:
            usage_display = f"ctx: {self._token_usage_pct:.1f}%"
        else:
            usage_display = f"ctx: {self._token_usage_pct:.0f}%"
        if self._total_cost and self._total_cost > 0:
            usage_display += f" │ cost: ${self._total_cost:.4f}"
        status = "processing..." if self._is_processing else "ready"

        return HTML(
            f'<style fg="#666666">⏺ {self._model_name} │ agent: {self._current_agent} │ {usage_display} │ {status}</style>'
        )

    def start_processing(self, input_tokens: int = 0):
        """Mark processing start."""
        self._is_processing = True
        self._input_tokens = input_tokens
        self._output_tokens = 0
        self._processing_start_time = time.time()  # Record start time for animation
        self._current_elapsed = 0.0
        self._current_spinner = self.SPINNER_FRAMES[0]
        self._wave_offset = 0
        self._status_text = "Processing..."
        self.app.invalidate()

    def update_processing(
        self,
        status: str = None,
        output_tokens: int = None,
        tool_name: str = None,
        spinner: str = None,
        elapsed: float = None,
        wave_offset: int = None,
    ):
        """Update processing status from external driver."""
        if tool_name:
            self._status_text = f"Running {tool_name}..."
        elif status:
            self._status_text = status

        if output_tokens is not None:
            self._output_tokens = output_tokens

        if spinner:
            self._current_spinner = spinner

        if elapsed is not None:
            self._current_elapsed = elapsed

        if wave_offset is not None:
            self._wave_offset = wave_offset

        # Trigger redraw immediately
        self.app.invalidate()

    def stop_processing(self):
        """Mark processing complete."""
        self._is_processing = False
        self._processing_start_time = 0  # Reset for next processing
        self._current_elapsed = 0.0
        self._status_text = "ready"
        # Force renderer reset to clear processing status line space
        try:
            self.app.renderer.erase()
        except Exception:
            pass
        self.app.invalidate()

    def update_model(self, model_name: str):
        self._model_name = model_name
        self.app.invalidate()

    def update_agent(self, agent_name: str):
        self._current_agent = agent_name
        self.app.invalidate()

    def update_token_usage(self, usage_pct: float, total_cost: float = 0.0):
        """Update token usage percentage for status bar display."""
        self._token_usage_pct = usage_pct
        self._total_cost = total_cost
        self.app.invalidate()

    # === Task Panel Methods ===

    def _get_task_panel_text(self):
        """Generate task panel content for prompt_toolkit."""
        if not self._task_panel_content:
            return []
        # Return ANSI formatted text
        from prompt_toolkit.formatted_text import ANSI

        return ANSI(self._task_panel_content)

    def update_task_panel(self, ansi_content: str):
        """Update task panel content (called by TaskUIRenderer).

        Args:
            ansi_content: Pre-rendered ANSI string content
        """
        self._task_panel_content = ansi_content
        self.app.invalidate()

    def show_task_panel(self):
        """Show task panel (when there's an active task)."""
        self._task_panel_visible = True
        self.app.invalidate()

    def hide_task_panel(self):
        """Hide task panel (no active task)."""
        self._task_panel_visible = False
        self._task_panel_content = ""
        self.app.invalidate()
