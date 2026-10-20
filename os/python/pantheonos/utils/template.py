import json
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib
import yaml
from collections import OrderedDict


def split_command(command: str) -> list[str]:
    """
    Split the command into parts.
    """
    parts = command.split()
    parts = [part.lower() for part in parts]
    parts[0] = parts[0].lstrip("/")
    return parts


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

    def match_command(self, command: str) -> dict | None:
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


def parse_items(template: dict, only_handler: bool = False) -> list[TemplateItem]:
    items = []
    
    def traverse_tree(node: dict, path: list[str] = None):
        if path is None:
            path = []
        
        for key, value in node.items():
            current_path = path + [key]
            
            if isinstance(value, dict):
                if ("content" in value) and (value.get("as_handler", False) or not only_handler):
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


def load_single_template(file_path: str | Path) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if path.suffix == ".toml":
        with open(path, "rb") as f:
            return tomllib.load(f)
    elif path.suffix == ".yaml" or path.suffix == ".yml":
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    elif path.suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")


def load_template(path: str | Path) -> dict:
    path = Path(path)
    if path.is_dir():
        template = {}
        files = list(path.glob("*.toml"))
        files.extend(list(path.glob("*.yaml")))
        files.extend(list(path.glob("*.yml")))
        files.extend(list(path.glob("*.json")))
        for file in files:
            t = load_single_template(file)
            for k, v in t.items():
                if k not in template:
                    template[k] = v
                else:
                    template[k].update(v)
        return template
    else:
        return load_single_template(path)
