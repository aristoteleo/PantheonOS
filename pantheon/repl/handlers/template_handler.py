from .base import CommandHandler
from rich.console import Console
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import Repl


class TemplateHandler(CommandHandler):
    def __init__(self, console: Console, parent: "Repl", template: dict):
        super().__init__(console, parent)
        self.template = template

    def match_command(self, command: str) -> bool:
        pass

    async def handle_command(self, command: str):
        pass
