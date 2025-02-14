from .shell import ShellToolSet
from ...remote.toolset import toolset_cli


toolset_cli(ShellToolSet, "shell")
