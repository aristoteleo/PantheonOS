"""User-facing runtime shim for Pantheon packages.

Typical usage inside python/shell/notebook interpreters::

    from pantheon import packages as pp
    pp.packages.sales_report.generate(date="2025-12-01")
"""

from __future__ import annotations

import sys
from pathlib import Path

from pantheon.internal.package_runtime import derive_packages_path, load_context
from pantheon.internal.package_runtime.runtime import get_runtime


def _ensure_packages_path():
    payload = load_context()
    workdir = payload.get("workdir")
    packages_path = Path(derive_packages_path(workdir))
    try:
        resolved = packages_path.resolve()
    except FileNotFoundError:
        resolved = packages_path
    if resolved.exists():
        path_str = str(resolved)
        if path_str not in sys.path:
            sys.path.append(path_str)


_ensure_packages_path()

packages = get_runtime()
list_packages = packages.list_packages
describe = packages.describe
reload = packages.reload

__all__ = ["packages", "list_packages", "describe", "reload"]
