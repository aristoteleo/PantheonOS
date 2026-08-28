# Import commonly used toolsets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .python import PythonInterpreterToolSet
    from .shell import ShellToolSet
    from .file import FileManagerToolSet
    from .web import WebToolSet
    from .notebook import (
        IntegratedNotebookToolSet,
        JupyterKernelToolSet,
        NotebookToolSet,
    )
    from .scraper import ScraperToolSet
    from .task import TaskToolSet
    from .evolution import EvolutionToolSet, EvaluatorToolSet
    from .desktop import DesktopToolSet, LiveViewToolSet
    from .pty import PtyToolSet
    from .fleet import FleetToolSet

_TOOLSET_MAPPING = {
    "PythonInterpreterToolSet": ".python",
    "ShellToolSet": ".shell",
    "FileManagerToolSet": ".file",
    "WebToolSet": ".web",
    "IntegratedNotebookToolSet": ".notebook",
    "JupyterKernelToolSet": ".notebook",
    "NotebookToolSet": ".notebook",
    "ScraperToolSet": ".scraper",
    "TaskToolSet": ".task",
    "EvolutionToolSet": ".evolution",
    "EvaluatorToolSet": ".evolution",
    "DesktopToolSet": ".desktop",
    # The name before the toolset outgrew it. Kept so a saved agent
    # config naming the old class still resolves.
    "LiveViewToolSet": ".desktop",
    "PtyToolSet": ".pty",
    "FleetToolSet": ".fleet",
}

__all__ = list(_TOOLSET_MAPPING.keys())


def __getattr__(name: str):
    if name in _TOOLSET_MAPPING:
        import importlib

        module_path = _TOOLSET_MAPPING[name]
        module = importlib.import_module(module_path, package=__package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
