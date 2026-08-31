"""Import-compat bridge into the App tree, wherever this install keeps it.

The first-party App directories live at <repo>/apps — OUTSIDE the pantheon
package, where the design wants them (each one a future git repo of its
own). In a source checkout this module points the package's search path
there: `pantheon.apps.builtin.file` resolves to <repo>/apps/file. In an
installed wheel there is no repo root — setup.py copied the tree's runtime
face INTO this package at build time, so the default search path (this
directory) already resolves the same names.
"""

import os as _os
from pathlib import Path as _Path

_APPS_ROOT = _os.environ.get("PANTHEON_APPS_ROOT") or str(
    _Path(__file__).resolve().parents[3] / "apps"
)
if _Path(_APPS_ROOT).is_dir():
    __path__ = [_APPS_ROOT]

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .python import PythonInterpreterToolSet
    from .file import FileManagerToolSet
    from .web import WebToolSet
    from .notebook import (
        IntegratedNotebookToolSet,
        JupyterKernelToolSet,
        NotebookContentsToolSet,
    )
    from .image import ImageGenerationToolSet
    from .file_transfer import FileTransferToolSet
    from .scraper import ScraperToolSet
    from .task import TaskToolSet
    from .evolution import EvolutionToolSet, EvaluatorToolSet
    from .desktop import DesktopToolSet, LiveViewToolSet
    from .fleet import FleetToolSet

_TOOLSET_MAPPING = {
    "PythonInterpreterToolSet": ".python",
    "FileManagerToolSet": ".file",
    "WebToolSet": ".web",
    "IntegratedNotebookToolSet": ".notebook",
    "JupyterKernelToolSet": ".notebook",
    "NotebookContentsToolSet": ".notebook",
    "ImageGenerationToolSet": ".image",
    "FileTransferToolSet": ".file_transfer",
    "ScraperToolSet": ".scraper",
    "TaskToolSet": ".task",
    "EvolutionToolSet": ".evolution",
    "EvaluatorToolSet": ".evolution",
    "DesktopToolSet": ".desktop",
    # The name before the toolset outgrew it. Kept so a saved agent
    # config naming the old class still resolves.
    "LiveViewToolSet": ".desktop",
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
