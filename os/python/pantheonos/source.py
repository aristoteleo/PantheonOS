"""Where this pantheonos was loaded from.

A source checkout keeps the package at <repo>/os/python/pantheonos, beside the
Go core in <repo>/os, with the first-party Apps at <repo>/apps. An installed
wheel or an App's vendored copy has no checkout around it. Code that needs
the checkout asks here instead of counting parent directories.
"""
from __future__ import annotations

from pathlib import Path

#: This package's directory.
PACKAGE = Path(__file__).resolve().parent


def checkout_root() -> Path | None:
    """The repository this package runs from, or None when it is installed."""
    python = PACKAGE.parent
    if python.name == "python" and python.parent.name == "os":
        repo = python.parent.parent
        if (repo / "apps").is_dir():
            return repo
    return None


def checkout_apps() -> Path | None:
    """The checkout's first-party App directories (<repo>/apps), or None."""
    repo = checkout_root()
    return repo / "apps" if repo is not None else None
