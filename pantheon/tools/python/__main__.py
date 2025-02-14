from .python_interpreter import PythonInterpreterToolSet
from ...remote.toolset import toolset_cli


toolset_cli(PythonInterpreterToolSet, "python_interpreter")
