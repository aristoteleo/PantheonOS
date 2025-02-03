from .python_interpreter import PythonInterpreterToolSet
from ...remote import toolset_cli


toolset_cli(PythonInterpreterToolSet, "python_interpreter")
