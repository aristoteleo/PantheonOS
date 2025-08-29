from typing import TYPE_CHECKING
from pathlib import Path
import json

from rich.console import Console
import tomli
import yaml
from collections import OrderedDict

from .base import CommandHandler

if TYPE_CHECKING:
    from ..core import Repl


class TemplateItem:
    def __init__(
        self,
        command: list[str],
        description: str,
        content: str,
        args: OrderedDict | list[str] | None = None,
    ):
        self.command = command
        self.description = description
        self.content = content
        if args is None:
            args = OrderedDict()
        elif isinstance(args, list):
            args = OrderedDict([(i, i) for i in args])
        elif isinstance(args, dict):
            args = OrderedDict(args)
        self.args = args

    def __repr__(self):
        return f"TemplateItem(command={self.command}, description={self.description}, args={self.args}, content={self.content[:20]}...)"

    def match(self, command: str) -> dict | None:
        """ Match the command to the template item.
        If the command is matched, return the args.

        Returns:
            dict | None: The args if the command is matched, None otherwise
        """
        parts = split_command(command)
        for i in range(len(parts)):
            if parts[:i+1] == self.command:
                args = parts[i+1:]
                if len(args) == 0:
                    if len(self.args) == 0:
                        return {}
                    else:
                        return None
                else:
                    if len(args) != len(self.args):
                        return None
                    else:
                        return {
                            k: v for k, v in zip(self.args.keys(), args)
                        }
        return None


def parse_items(template: dict) -> list[TemplateItem]:
    items = []
    
    def traverse_tree(node: dict, path: list[str] = None):
        if path is None:
            path = []
        
        for key, value in node.items():
            current_path = path + [key]
            
            if isinstance(value, dict):
                if "content" in value:
                    item = TemplateItem(
                        command=current_path,
                        description=value.get("description", ""),
                        content=value["content"],
                        args=value.get("args", None)
                    )
                    items.append(item)
                else:
                    traverse_tree(value, current_path)
    
    traverse_tree(template)
    return items


def load_template(file_path: str | Path) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if path.suffix == ".toml":
        with open(path, "rb") as f:
            return tomli.load(f)
    elif path.suffix == ".yaml":
        with open(path, "r") as f:
            return yaml.safe_load(f)
    elif path.suffix == ".json":
        with open(path, "r") as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")


def split_command(command: str) -> list[str]:
    """
    Split the command into parts.
    """
    parts = command.split()
    parts = [part.lower() for part in parts]
    parts[0] = parts[0].lstrip("/")
    return parts


class TemplateHandler(CommandHandler):
    def __init__(self, console: Console, parent: "Repl", template: dict):
        super().__init__(console, parent)
        self.template = template
        self.items = parse_items(template)

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
            args = item.match(command)
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
