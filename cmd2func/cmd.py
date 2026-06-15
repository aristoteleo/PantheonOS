import typing as T
import re

from .config import CLIConfig, ArgDef


class Command(object):
    def __init__(self, template: str):
        self.template = template
        self.placeholders: T.List[str] = [
            p.strip("{}") for p in
            re.findall(r"\{.*?\}", self.template)
        ]

    @property
    def main_command(self) -> str:
        return self.template.split()[0]

    def check_placeholder(self, arg_names: T.List[str]):
        for arg in arg_names:
            if arg not in self.placeholders:
                raise ValueError(
                    f"The argument {arg} is not in command templates.")

    def format(self, vals: dict):
        for ph in self.placeholders:
            if ph not in vals:
                raise ValueError(
                    f"The value of placeholder {ph} is not provided.")
        cmd = self.template.format(**vals)
        return cmd

    def infer_config(self) -> CLIConfig:
        """Infer the config from the command template."""
        args = {}
        for ph in self.placeholders:
            arg: ArgDef = {
                "type": "str",
                "default": None,
            }
            args[ph] = arg
        config: CLIConfig = {
            "name": self.main_command,
            "inputs": args,
            "inputs_order": self.placeholders,
        }
        return config

    def complete_config(self, config: CLIConfig) -> CLIConfig:
        """Complete the config from the command template."""
        if 'name' not in config:
            config["name"] = self.main_command
        if 'inputs_order' not in config:
            config["inputs_order"] = self.placeholders
        args = config['inputs']
        for ph in self.placeholders:
            if ph not in args:
                arg: ArgDef = {
                    "type": "str",
                }
                args[ph] = arg
        return config
