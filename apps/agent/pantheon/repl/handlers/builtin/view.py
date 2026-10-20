"""View command handler for REPL

Provides /view command for full-screen file viewing.

Usage:
    /view <filepath>   - View file in full-screen mode
"""

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from pantheon.repl.handlers.base import CommandHandler

if TYPE_CHECKING:
    from pantheon.repl.core import Repl


class ViewCommandHandler(CommandHandler):
    """Handle /view command"""
    
    def __init__(self, console: Console, parent: "Repl"):
        super().__init__(console, parent)
    
    def get_commands(self) -> list[tuple[str, str]]:
        """Return commands for autocomplete."""
        return [
            ("/view", "View file in full-screen mode"),
        ]
    
    def match_command(self, command: str) -> bool:
        """Check if this handler matches the command"""
        return command.startswith("/view ") or command.startswith("/test_notify")
    
    async def handle_command(self, command: str) -> str | None:
        """Execute the view command"""
        # Handle /test_notify command
        if command.startswith("/test_notify"):
            return await self._test_notify()
        
        parts = command.split(maxsplit=1)
        
        if len(parts) < 2:
            self.console.print("[yellow]Usage: /view <filepath>[/yellow]")
            return None
        
        file_path = parts[1].strip()
        
        # Resolve relative paths
        if not Path(file_path).is_absolute():
            workdir = getattr(self.parent, 'workdir', None) or Path.cwd()
            resolved = Path(workdir) / file_path
            if resolved.exists():
                file_path = str(resolved)
        
        # Check file exists
        if not Path(file_path).exists():
            self.console.print(f"[red]File not found: {file_path}[/red]")
            return None
        
        result = await self._view_file(file_path)
        
        if not result.get("success"):
            self.console.print(f"[red]{result.get('error', 'Unknown error')}[/red]")
        
        return None
    
    async def _view_file(self, file_path: str) -> dict:
        """View file using prompt_toolkit full_screen with proper REPL suspension"""
        from prompt_toolkit.application.run_in_terminal import in_terminal
        
        prompt_app = getattr(self.parent, 'prompt_app', None)
        
        try:
            if prompt_app and hasattr(prompt_app, 'app') and prompt_app.app.is_running:
                # Use in_terminal context manager to properly suspend REPL UI
                # This suspends the REPL app, runs viewer, then restores REPL
                async with in_terminal():
                    from pantheon.repl.viewers.file_viewer_ptk import PTKFileViewer
                    viewer = PTKFileViewer(file_path)
                    exit_code = await viewer.run_async()
                
                return {"success": exit_code == 0, "exit_code": exit_code}
            else:
                # No active prompt app, run viewer directly
                from pantheon.repl.viewers.file_viewer_ptk import view_file_ptk
                return await view_file_ptk(file_path)
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _test_notify(self) -> str | None:
        """Test the interactive notify dialog"""
        from prompt_toolkit.application.run_in_terminal import in_terminal
        from pantheon.repl.viewers.notify_dialog import (
            InteractiveNotifyDialog,
            NotifyAction,
        )
        
        # Test data
        message = "This is a test notification. Please review the files and approve or continue."
        
        # Use some common files for testing
        paths = [
            "README.md",
            "pyproject.toml", 
            "pantheon/__init__.py",
        ]
        
        # Filter to existing files
        from pathlib import Path
        existing_paths = [p for p in paths if Path(p).exists()]
        
        if not existing_paths:
            self.console.print("[yellow]No test files found. Using current directory files.[/yellow]")
            # Get first 3 Python files
            import os
            for f in os.listdir("."):
                if f.endswith(".py") or f.endswith(".md") or f.endswith(".toml"):
                    existing_paths.append(f)
                    if len(existing_paths) >= 3:
                        break
        
        try:
            prompt_app = getattr(self.parent, 'prompt_app', None)
            
            if prompt_app and hasattr(prompt_app, 'app') and prompt_app.app.is_running:
                async with in_terminal():
                    dialog = InteractiveNotifyDialog(message, existing_paths)
                    result = await dialog.run_async()
            else:
                dialog = InteractiveNotifyDialog(message, existing_paths)
                result = await dialog.run_async()
            
            # Show result (only for approve - continue planning is silent)
            if result.action == NotifyAction.APPROVE:
                self.console.print()
                self.console.print("[green]✓ Approved[/green]")
            if result.feedback:
                self.console.print(f"[bold]Feedback:[/bold] {result.feedback}")
            
        except Exception as e:
            import traceback
            self.console.print(f"[red]Error: {e}[/red]")
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
        
        return None



def get_view_help() -> str:
    """Get help text for view command"""
    return """
View Command:
  /view <file>    View file in full-screen mode

Controls:
  j/k or ↑/↓         Scroll up/down
  Space/Ctrl-F       Page down
  Ctrl-B             Page up
  g/G                Go to top/bottom
  q/ESC              Quit viewer
"""
