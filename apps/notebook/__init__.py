"""Notebook toolsets for Jupyter integration"""

from .notebook_contents import NotebookContentsToolSet
from .integrated_notebook import IntegratedNotebookToolSet
from .jupyter_kernel import JupyterKernelToolSet
from .jedi_integration import EnhancedCompletionService

NotebookToolSet = IntegratedNotebookToolSet

__all__ = [
    "NotebookContentsToolSet",
    "IntegratedNotebookToolSet",
    "NotebookToolSet",
    "JupyterKernelToolSet",
    "EnhancedCompletionService",
]
