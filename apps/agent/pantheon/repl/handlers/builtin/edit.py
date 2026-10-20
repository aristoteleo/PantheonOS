"""
/edit command handler - Edit files in external editor (vim/nvim/$EDITOR).

Usage:
    /edit <filepath>   - Edit file in external editor
    /edit              - Show usage

The editor is determined by (in order):
1. $EDITOR environment variable
2. nvim (if available)
3. vim (if available)
4. nano (fallback)

After editing, you'll return to the REPL automatically.
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from prompt_toolkit.application.run_in_terminal import run_in_terminal

from pantheon.repl.handlers.base import CommandHandler

if TYPE_CHECKING:
    from pantheon.repl.core import Repl


class EditHandler(CommandHandler):
    """Handle /edit command to open files in external editor"""

    def __init__(self, console: Console, parent: "Repl"):
        super().__init__(console, parent)

    def get_commands(self):
        return [
            ("/edit", "Edit file in external editor (vim/nvim/$EDITOR)"),
        ]

    def match_command(self, command: str) -> bool:
        return command.startswith("/edit")

    def _find_editor(self) -> tuple[str, str]:
        """Find available editor.
        
        Returns:
            (editor_path, editor_name) tuple
        """
        # 1. Check $EDITOR environment variable
        editor_env = os.environ.get("EDITOR")
        if editor_env:
            editor_path = shutil.which(editor_env)
            if editor_path:
                return editor_path, editor_env
        
        # 2. Try nvim
        nvim_path = shutil.which("nvim")
        if nvim_path:
            return nvim_path, "nvim"
        
        # 3. Try vim
        vim_path = shutil.which("vim")
        if vim_path:
            return vim_path, "vim"
        
        # 4. Fallback to nano
        nano_path = shutil.which("nano")
        if nano_path:
            return nano_path, "nano"
        
        # 5. Try vi (should exist on most Unix systems)
        vi_path = shutil.which("vi")
        if vi_path:
            return vi_path, "vi"
        
        return None, None

    async def handle_command(self, command: str) -> str | None:
        """Handle /edit command"""
        parts = command.split(maxsplit=1)
        
        if len(parts) < 2:
            self.console.print("[yellow]Usage: /edit <filepath>[/yellow]")
            self.console.print("\n[dim]Examples:[/dim]")
            self.console.print("  /edit script.py")
            self.console.print("  /edit /path/to/file.md")
            self.console.print("\n[dim]The editor is determined by:[/dim]")
            self.console.print("  1. $EDITOR environment variable")
            self.console.print("  2. nvim (if available)")
            self.console.print("  3. vim (if available)")
            self.console.print("  4. nano (fallback)")
            return None
        
        file_path = parts[1].strip()
        path = Path(file_path).expanduser().resolve()
        
        # Find editor
        editor_path, editor_name = self._find_editor()
        if not editor_path:
            self.console.print(
                "[red]Error: No editor found. Please set $EDITOR environment variable.[/red]"
            )
            return None
        
        # Check if file exists (create if not)
        if not path.exists():
            self.console.print(f"[yellow]File does not exist: {path}[/yellow]")
            self.console.print("[yellow]Creating new file...[/yellow]")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            except Exception as e:
                self.console.print(f"[red]Error creating file: {e}[/red]")
                return None
        
        # Check if it's a file (not directory)
        if path.is_dir():
            self.console.print(f"[red]Error: {path} is a directory, not a file[/red]")
            return None
        
        self.console.print(f"[dim]Opening {path.name} in {editor_name}...[/dim]")
        
        # Run editor in terminal
        # We need to use run_in_terminal to properly suspend the REPL
        def open_editor():
            """Open editor in the terminal"""
            try:
                subprocess.run(
                    [editor_path, str(path)],
                    stdin=None,  # Use terminal stdin
                    stdout=None,  # Use terminal stdout
                    stderr=None,  # Use terminal stderr
                )
            except Exception as e:
                print(f"\n[Error running editor: {e}]")
        
        # Run in terminal context (suspends prompt_toolkit)
        await run_in_terminal(open_editor, in_executor=True)
        
        return None


def get_handler():
    """Factory function to get handler instance"""
    return EditHandler()
