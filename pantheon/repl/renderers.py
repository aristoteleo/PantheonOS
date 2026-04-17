"""Tool call and result renderers for REPL UI.

This module provides enhanced rendering for tool calls and results,
supporting both compact and verbose display modes.
"""

import os
import sys
import base64
import subprocess
from enum import Enum
from pathlib import Path
from dataclasses import dataclass
from difflib import unified_diff
from typing import Optional, Any, List

from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.table import Table
from rich.markup import escape


class DisplayMode(Enum):
    """Display mode for tool output"""
    COMPACT = "compact"
    VERBOSE = "verbose"


@dataclass
class DisplayConfig:
    """Configuration for tool display"""
    mode: DisplayMode = DisplayMode.COMPACT

    # Compact mode limits
    compact_code_lines: int = 30
    compact_file_lines: int = 20
    compact_output_lines: int = 10
    compact_search_results: int = 5

    # Verbose mode: None = show all
    verbose_code_lines: Optional[int] = None
    verbose_file_lines: Optional[int] = None
    verbose_output_lines: Optional[int] = None
    verbose_search_results: Optional[int] = None

    def get_limit(self, limit_type: str) -> Optional[int]:
        """Get the limit based on current mode"""
        if self.mode == DisplayMode.VERBOSE:
            return getattr(self, f"verbose_{limit_type}", None)
        return getattr(self, f"compact_{limit_type}", 20)


class ImageDisplay:
    """Handle image display in terminal"""

    @staticmethod
    def detect_terminal() -> str:
        """Detect terminal type for image support"""
        term_program = os.environ.get("TERM_PROGRAM", "")
        term = os.environ.get("TERM", "")

        if "iTerm" in term_program:
            return "iterm2"
        if "kitty" in term.lower() or "KITTY_WINDOW_ID" in os.environ:
            return "kitty"
        if "WezTerm" in term_program:
            return "wezterm"
        return "fallback"

    @staticmethod
    def display_iterm2(path: str, console: Console):
        """Display image using iTerm2 protocol"""
        try:
            with open(path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode()

            # iTerm2 inline image protocol
            osc = f"\033]1337;File=inline=1;preserveAspectRatio=1:{image_data}\a"
            sys.stdout.write(osc)
            sys.stdout.flush()
            console.print()
        except Exception as e:
            console.print(f"[dim]Failed to display image: {e}[/dim]")

    @staticmethod
    def display_kitty(path: str, console: Console):
        """Display image using Kitty protocol"""
        try:
            subprocess.run(["kitty", "+kitten", "icat", path], check=True)
        except Exception:
            console.print(f"[dim][Image] Image: {path}[/dim]")

    @classmethod
    def display(cls, path: str, console: Console, auto_open: bool = False):
        """Display image with best available method"""
        terminal = cls.detect_terminal()

        if terminal == "iterm2":
            cls.display_iterm2(path, console)
        elif terminal == "kitty":
            cls.display_kitty(path, console)
        else:
            # Fallback: show path with option to open
            console.print(f"  [dim][Image] Image saved:[/dim] {path}")
            if auto_open and sys.platform == "darwin":
                console.print(f"     [dim]Tip: run 'open {path}' to view[/dim]")


def get_bullet_char() -> str:
    """Get a bullet character that works on the current terminal"""
    # Try to use fancy Unicode bullet, fallback to ASCII
    try:
        # Test if stdout can handle Unicode
        if sys.stdout.encoding and sys.stdout.encoding.lower() in ['utf-8', 'utf8']:
            return "⏺"
        # On Windows with non-UTF8, use ASCII
        return ">"
    except Exception:
        return ">"


class ToolCallRenderer:
    """Render tool calls with rich formatting.

    Design principles:
    - Always use 'toolset > function' format for headers
    - No special name mappings (e.g., don't convert run_python_code to "Python")
    - Smart parameter rendering based on parameter type
    - Verbose mode shows all parameters
    """

    # Bullet character for tool calls
    BULLET = get_bullet_char()

    # File extension to language mapping for syntax highlighting
    FILE_LANG_MAP = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".json": "json",
        ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".md": "markdown", ".html": "html", ".css": "css",
        ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
        ".r": "r", ".R": "r", ".jl": "julia",
        ".sql": "sql", ".xml": "xml", ".csv": "text",
        ".txt": "text", ".log": "text", ".ini": "ini",
        ".cfg": "ini", ".conf": "ini",
    }

    # Function name to syntax language mapping
    FUNC_LANG_MAP = {
        "run_python_code": "python",
        "run_r_code": "r",
        "run_julia_code": "julia",
        "run_command": "bash",
        "run_command_in_shell": "bash",
        "execute_cell": "python",
    }

    # Parameters that contain code (should be rendered with syntax highlighting)
    CODE_PARAMS = {"code", "command", "source", "content"}

    # Parameters that are file paths (should be displayed prominently)
    PATH_PARAMS = {"file_path", "path", "notebook_path", "sub_dir"}

    def __init__(self, console: Console, config: DisplayConfig):
        self.console = console
        self.config = config

    def render(self, tool_name: str, args: dict):
        """Unified tool call renderer"""
        # Parse toolset and function name
        if "__" in tool_name:
            toolset_name, function_name = tool_name.split("__", 1)
        else:
            toolset_name, function_name = None, tool_name

        # Extract _background flag (without mutating original args)
        is_background = bool(args.get("_background")) if args else False

        # Render header: toolset > function
        self._render_header(toolset_name, function_name)

        # Show background indicator
        if is_background:
            self.console.print(f"  | [dim italic]-> running in background[/dim italic]")

        # Render arguments (filter out _background)
        if args:
            display_args = {k: v for k, v in args.items() if k != "_background"}
            if display_args:
                self._render_args(function_name, display_args)

    def _render_header(self, toolset_name: Optional[str], function_name: str):
        """Render tool header in format: toolset > function"""
        if toolset_name:
            self.console.print(f"{self.BULLET} [grey50]{toolset_name} >[/grey50] [cyan]{function_name}[/cyan]")
        else:
            self.console.print(f"{self.BULLET} [cyan]{function_name}[/cyan]")

    def _render_args(self, function_name: str, args: dict):
        """Render tool arguments with smart formatting"""
        # Separate args into categories
        code_args = {}
        path_args = {}
        diff_args = {}
        other_args = {}

        for key, value in args.items():
            if key in self.CODE_PARAMS and value:
                code_args[key] = value
            elif key in self.PATH_PARAMS and value:
                path_args[key] = value
            elif key in {"old_string", "new_string"}:
                diff_args[key] = value
            else:
                other_args[key] = value

        # 1. Render path parameters first
        for key, value in path_args.items():
            self.console.print(f"  | [dim]{key}=[/dim]'{value}'")

        # 2. Render diff if present (for update_file)
        if "old_string" in diff_args and "new_string" in diff_args:
            self._render_diff(diff_args["old_string"], diff_args["new_string"])

        # 3. Render code parameters with syntax highlighting
        for key, value in code_args.items():
            lang = self._detect_language(function_name, key)
            self._render_code_block(key, value, lang)

        # 4. Render other parameters (compact: key params only, verbose: all)
        self._render_other_args(other_args)

    def _detect_language(self, function_name: str, param_name: str) -> str:
        """Detect syntax language for code highlighting"""
        # First check function name mapping
        if function_name in self.FUNC_LANG_MAP:
            return self.FUNC_LANG_MAP[function_name]
        # Default to text
        return "text"

    def _render_code_block(self, param_name: str, code: str, lang: str):
        """Render code with syntax highlighting"""
        if not code or not code.strip():
            return

        limit = self.config.get_limit("code_lines")
        truncated, shown, total = self._truncate_lines(code, limit)

        syntax = Syntax(
            truncated,
            lang,
            theme="monokai",
            line_numbers=True,
            word_wrap=True
        )

        title = None
        if shown < total:
            title = f"[dim]{shown}/{total} lines[/dim]"

        self.console.print(Panel(syntax, border_style="dim", title=title, title_align="right"))

    def _render_diff(self, old_string: str, new_string: str):
        """Render diff between old and new strings"""
        old_lines = old_string.splitlines(keepends=True)
        new_lines = new_string.splitlines(keepends=True)

        diff_lines = list(unified_diff(
            old_lines, new_lines,
            fromfile="before", tofile="after",
            lineterm=""
        ))

        if not diff_lines:
            # No unified diff, show simple comparison
            # Escape dynamic content to prevent Rich tag mismatch errors
            old_display = escape(old_string[:200] + "..." if len(old_string) > 200 else old_string)
            new_display = escape(new_string[:200] + "..." if len(new_string) > 200 else new_string)
            for line in old_display.split('\n'):
                self.console.print(f"  [red]- {line}[/red]")
            for line in new_display.split('\n'):
                self.console.print(f"  [green]+ {line}[/green]")
            return

        # Format diff with colors
        diff_formatted = []
        for line in diff_lines:
            line = line.rstrip('\n')
            if line.startswith('+++') or line.startswith('---'):
                continue
            # Escape line content to prevent Rich tag errors from dynamic content
            safe_line = escape(line)
            if line.startswith('@@'):
                diff_formatted.append(f"[cyan]{safe_line}[/cyan]")
            elif line.startswith('+'):
                diff_formatted.append(f"[green]{safe_line}[/green]")
            elif line.startswith('-'):
                diff_formatted.append(f"[red]{safe_line}[/red]")
            else:
                diff_formatted.append(f"[dim]{safe_line}[/dim]")

        diff_text = '\n'.join(diff_formatted)
        self.console.print(Panel(
            diff_text,
            title="[bold yellow]Diff[/bold yellow]",
            border_style="yellow",
            title_align="left"
        ))

    def _render_other_args(self, args: dict):
        """Render other (non-code, non-path) arguments"""
        if not args:
            return

        # In verbose mode, show all args
        if self.config.mode == DisplayMode.VERBOSE:
            for key, value in args.items():
                value_str = str(value)
                if len(value_str) > 200:
                    value_str = value_str[:200] + "..."
                self.console.print(f"  | [dim]{key}=[/dim]{value_str}")
        else:
            # Compact mode: show only important args
            important_keys = {"query", "pattern", "cell_id", "cell_type", "recursive", "urls"}
            for key, value in args.items():
                if key in important_keys:
                    value_str = str(value)
                    if len(value_str) > 80:
                        value_str = value_str[:80] + "..."
                    self.console.print(f"  | [dim]{key}=[/dim]{value_str}")

    def _truncate_lines(self, text: str, limit: Optional[int]) -> tuple[str, int, int]:
        """Truncate text to limit lines. Returns (truncated_text, shown, total)"""
        lines = text.split('\n')
        total = len(lines)

        if limit is None or total <= limit:
            return text, total, total

        truncated = '\n'.join(lines[:limit])
        return truncated, limit, total



class ToolResultRenderer:
    """Render tool results with rich formatting.

    Design principles:
    - Core content (output, stdout, content) displayed first
    - Verbose mode shows all metadata fields
    - Unified rendering approach for all tools
    """

    FILE_LANG_MAP = ToolCallRenderer.FILE_LANG_MAP

    # Core content fields (displayed with special formatting)
    CORE_CONTENT_FIELDS = {"output", "stdout", "stderr", "content", "result", "message"}

    # Fields to skip in metadata display
    SKIP_METADATA_FIELDS = {
        "output", "stdout", "stderr", "content", "result", "message",
        "fig_storage_path", "base64_uri", "hidden_to_model"
    }

    def __init__(self, console: Console, config: DisplayConfig):
        self.console = console
        self.config = config
        self.image_display = ImageDisplay()

    def render(self, tool_name: str, result: Any) -> bool:
        """
        Unified result renderer.
        Returns True if result was rendered, False to use default handling.
        """
        # Handle non-dict results
        if isinstance(result, list):
            self._render_list_result(result)
            self._render_metadata({"_list_length": len(result)})
            return True

        if not isinstance(result, dict):
            self.console.print(f"  | {result}")
            return True

        # Check for error first
        if not result.get("success", True) and "error" in result:
            self._render_error(result)
            self._render_metadata(result)
            return True

        # Render core content
        core_displayed = self._render_core_content(result)

        # Render images if present
        self._render_images(result)

        # Render metadata in verbose mode
        self._render_metadata(result)

        return core_displayed

    def _truncate_lines(self, text: str, limit: Optional[int]) -> tuple[str, int, int]:
        """Truncate text to limit lines"""
        lines = text.split('\n')
        total = len(lines)

        if limit is None or total <= limit:
            return text, total, total

        truncated = '\n'.join(lines[:limit])
        return truncated, limit, total

    def _render_error(self, result: dict):
        """Render error result"""
        error = result.get("error", "Unknown error")
        self.console.print(f"[red]X Error:[/red] {error}")

    def _render_core_content(self, result: dict) -> bool:
        """Render core content fields (output, stdout, stderr, content, result)"""
        displayed = False

        # Priority order for core content
        content_priority = [
            ("output", "Output", "dim"),
            ("stdout", "stdout", "green"),
            ("stderr", "stderr", "red"),
            ("content", "Content", "blue"),
        ]

        for field, title, color in content_priority:
            value = result.get(field)
            if value and str(value).strip():
                self._render_content_panel(title, value, color)
                displayed = True

        # Handle 'result' field specially (not in a panel)
        exec_result = result.get("result")
        if exec_result is not None:
            result_str = str(exec_result)
            limit = 500 if self.config.mode == DisplayMode.COMPACT else None
            if limit and len(result_str) > limit:
                result_str = result_str[:limit] + "..."
            self.console.print(f"  | [dim]result=[/dim]{result_str}")
            displayed = True

        return displayed

    def _render_content_panel(self, title: str, value: Any, color: str):
        """Render content in a panel with truncation"""
        # Clean the content
        text = str(value).replace('\r\n', '\n').strip()

        if not text:
            return

        # Truncate if needed
        limit = self.config.get_limit("output_lines")
        truncated, shown, total = self._truncate_lines(text, limit)

        # Build title with line count
        display_title = title
        if shown < total:
            display_title += f" ({shown}/{total} lines)"

        self.console.print(Panel(
            truncated,
            title=f"[{color}]{display_title}[/{color}]",
            border_style=color,
            title_align="left"
        ))

    def _render_images(self, result: dict):
        """Render image outputs"""
        fig_path = result.get("fig_storage_path")
        base64_uris = result.get("base64_uri", [])

        if fig_path:
            self.image_display.display(fig_path, self.console)
        elif base64_uris:
            self.console.print(f"  | [dim][Image] Generated {len(base64_uris)} image(s)[/dim]")

    def _render_metadata(self, result: dict):
        """Render metadata fields in verbose mode"""
        if self.config.mode != DisplayMode.VERBOSE:
            return

        # Filter out core content and skip fields
        metadata = {
            k: v for k, v in result.items()
            if k not in self.SKIP_METADATA_FIELDS and v is not None
        }

        if not metadata:
            return

        self.console.print("[dim]Metadata:[/dim]")
        for key, value in metadata.items():
            value_str = str(value)
            if len(value_str) > 100:
                value_str = value_str[:100] + "..."
            self.console.print(f"  | [dim]{key}=[/dim]{value_str}")

    def _render_list_result(self, result: list):
        """Render list results (e.g., search results)"""
        if not result:
            self.console.print("[dim]  | (empty list)[/dim]")
            return

        # Check if it looks like search results
        if result and isinstance(result[0], dict) and ("title" in result[0] or "href" in result[0]):
            self._render_search_results(result)
        else:
            # Generic list rendering
            limit = self.config.get_limit("search_results") or len(result)
            for i, item in enumerate(result[:limit], 1):
                item_str = str(item)
                if len(item_str) > 100:
                    item_str = item_str[:100] + "..."
                self.console.print(f"  | [{i}] {item_str}")
            if len(result) > limit:
                self.console.print(f"  | [dim]... and {len(result) - limit} more[/dim]")

    def _format_size(self, size: int) -> str:
        """Format file size"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    def _render_search_results(self, result: dict):
        """Render search results"""
        # Handle different result formats
        results = result if isinstance(result, list) else result.get("organic_results", [])

        if not results:
            self.console.print("[dim]  |No results found[/dim]")
            return

        limit = self.config.get_limit("search_results") or len(results)

        self.console.print(f"[bold]Search Results[/bold] ({len(results)} found)")

        for i, item in enumerate(results[:limit], 1):
            title = item.get("title", "No title")
            link = item.get("link") or item.get("href", "")
            snippet = item.get("snippet") or item.get("body", "")

            self.console.print(f"\n  [cyan]{i}.[/cyan] [bold]{title}[/bold]")
            if link:
                self.console.print(f"     [dim]-> {link}[/dim]")
            if snippet and self.config.mode == DisplayMode.VERBOSE:
                # Truncate snippet
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                self.console.print(f"     [dim]{snippet}[/dim]")

        if len(results) > limit:
            self.console.print(f"\n[dim]  ... and {len(results) - limit} more results[/dim]")

    def _render_crawl_results(self, result: dict):
        """Render web crawl results"""
        contents = result if isinstance(result, list) else [result]

        success_count = sum(1 for c in contents if c)
        self.console.print(f"[green]OK[/green] Crawled {success_count}/{len(contents)} pages")

        if self.config.mode == DisplayMode.VERBOSE:
            for i, content in enumerate(contents):
                if content:
                    preview = content[:300] + "..." if len(content) > 300 else content
                    self.console.print(f"\n  [dim]Page {i+1}:[/dim]")
                    self.console.print(f"  {preview}")

    def _render_cell_output(self, result: dict):
        """Render notebook cell execution output"""
        outputs = result.get("outputs", [])
        execution_count = result.get("execution_count")

        if execution_count:
            self.console.print(f"[green]OK[/green] Cell [{execution_count}] executed")

        if not outputs:
            return

        for output in outputs:
            output_type = output.get("output_type")

            if output_type == "stream":
                text = output.get("text", "")
                if isinstance(text, list):
                    text = "".join(text)
                stream_name = output.get("name", "stdout")
                color = "green" if stream_name == "stdout" else "red"

                limit = self.config.get_limit("output_lines")
                truncated, shown, total = self._truncate_lines(text.strip(), limit)

                title = stream_name
                if shown < total:
                    title += f" ({shown}/{total} lines)"

                self.console.print(Panel(truncated, border_style=color, title=title))

            elif output_type == "execute_result":
                data = output.get("data", {})
                if "text/plain" in data:
                    text = data["text/plain"]
                    if isinstance(text, list):
                        text = "".join(text)
                    self.console.print(f"  |{text}")

            elif output_type == "display_data":
                data = output.get("data", {})
                if "image/png" in data:
                    self.console.print("  |[dim][Image] Image output[/dim]")
                elif "text/html" in data:
                    self.console.print("  |[dim][F] HTML output[/dim]")

            elif output_type == "error":
                ename = output.get("ename", "Error")
                evalue = output.get("evalue", "")
                self.console.print(f"  [red]X {ename}: {evalue}[/red]")


__all__ = [
    "DisplayMode",
    "DisplayConfig",
    "ToolCallRenderer",
    "ToolResultRenderer",
    "ImageDisplay",
]
