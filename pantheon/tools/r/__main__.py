from .r_interpreter import RInterpreterToolSet
from ...remote.toolset import toolset_cli


toolset_cli(RInterpreterToolSet, "r_interpreter")
