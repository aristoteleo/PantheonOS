from typing import TYPE_CHECKING

from rich.console import Console

from .base import CommandHandler
from ...utils.template import parse_items, load_template

if TYPE_CHECKING:
    from ..core import Repl


class TemplateHandler(CommandHandler):
    def __init__(self, console: Console, parent: "Repl", template: dict):
        super().__init__(console, parent)
        self.template = template
        self.items = parse_items(template, only_handler=True)

    def match_command(self, command: str) -> bool:
        parts = command.split()
        if len(parts) == 0:
            return False
        if not parts[0].startswith("/"):
            return False
        if parts[0].lstrip("/").lower() in self.template:
            return True
        return False

    async def handle_command(self, command: str):
        for item in self.items:
            args = item.match_command(command)
            if args is not None:
                # matched
                if len(args) == 0:
                    return item.content
                else:
                    return item.content.format(**args)
        self.print_help()
        return None

    def print_help(self):
        self.console.print("[bold]Available template commands:[/bold]")
        for item in self.items:
            cmd_line = ' '.join(item.command)
            for arg in item.args.keys():
                cmd_line += f" <{arg}>"
            self.console.print(f"/{cmd_line}\t- {item.description}")


if __name__ == "__main__":
    import sys
    t = load_template(sys.argv[1])
    d = parse_items(t)
    for item in d:
        print(item)
