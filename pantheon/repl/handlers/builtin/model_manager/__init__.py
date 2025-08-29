from typing import TYPE_CHECKING

from rich.console import Console

from ...base import CommandHandler
from .model_manager import ModelManager
from .api_key_manager import APIKeyManager
from .....constant import CONFIG_FILE

if TYPE_CHECKING:
    from ....core import Repl


class ModelManagerCommandHandler(CommandHandler):
    def __init__(self, console: Console, parent: "Repl"):
        super().__init__(console, parent)
        api_key_manager = APIKeyManager(CONFIG_FILE)
        model_manager = ModelManager(CONFIG_FILE, api_key_manager)
        self.agent._model_manager = model_manager
        self.agent._api_key_manager = api_key_manager

    def match_command(self, command: str) -> bool:
        return (
            command.startswith("/model")
            or command.startswith("/api-key")
            or command.startswith("/api-endpoint")
        )

    async def handle_command(self, command: str):
        if command.startswith("/model"):
            return self._handle_model_command(command)
        elif command.startswith("/api-key"):
            return self._handle_api_key_command(command)
        elif command.startswith("/api-endpoint"):
            return self._handle_endpoint_command(command)
        return None

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

    def _handle_endpoint_command(self, command: str):
        """Handle /endpoint commands in REPL"""
        try:
            if hasattr(self.agent, '_api_key_manager') and self.agent._api_key_manager:
                result = self.agent._api_key_manager.handle_endpoint_command(command)
                # Print result as plain text to avoid formatting issues
                self.console.print(result)
            else:
                self.console.print("[red]Error: Endpoint manager not available[/red]")
        except Exception as e:
            self.console.print(f"[red]Error handling endpoint command: {str(e)}[/red]")
        self.console.print()  # Add spacing
