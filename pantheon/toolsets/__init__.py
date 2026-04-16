# Import commonly used toolsets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .python import PythonInterpreterToolSet
    from .r import RInterpreterToolSet
    from .julia import JuliaInterpreterToolSet
    from .shell import ShellToolSet
    from .file import FileManagerToolSet
    from .web import WebToolSet
    from .notebook import (
        IntegratedNotebookToolSet,
        JupyterKernelToolSet,
        NotebookToolSet,
    )
    from .scraper import ScraperToolSet
    from .rag import VectorRAGToolSet
    from .package import PackageToolSet
    from .database_api import DatabaseAPIQueryToolSet
    from .task import TaskToolSet
    from .knowledge import KnowledgeToolSet
    from .evolution import EvolutionToolSet, EvaluatorToolSet
    from .skillbook import SkillbookToolSet
    from .scfm import SCFMToolSet

_TOOLSET_MAPPING = {
    "PythonInterpreterToolSet": ".python",
    "RInterpreterToolSet": ".r",
    "JuliaInterpreterToolSet": ".julia",
    "ShellToolSet": ".shell",
    "FileManagerToolSet": ".file",
    "WebToolSet": ".web",
    "IntegratedNotebookToolSet": ".notebook",
    "JupyterKernelToolSet": ".notebook",
    "NotebookToolSet": ".notebook",
    "ScraperToolSet": ".scraper",
    "VectorRAGToolSet": ".rag",
    "PackageToolSet": ".package",
    "DatabaseAPIQueryToolSet": ".database_api",
    "TaskToolSet": ".task",
    "KnowledgeToolSet": ".knowledge",
    "EvolutionToolSet": ".evolution",
    "EvaluatorToolSet": ".evolution",
    "SkillbookToolSet": ".skillbook",
    "SCFMToolSet": ".scfm",
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
