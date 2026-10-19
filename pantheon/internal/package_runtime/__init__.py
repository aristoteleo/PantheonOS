"""Shared package management helpers."""

from __future__ import annotations

from pathlib import Path

from .manager import PackageManager
from pantheonos.package_runtime.context import (
    build_context_payload,
    derive_packages_path,
    export_context,
    load_context,
)

_DEFAULT_MANAGER: PackageManager | None = None


def _default_path() -> Path:
    payload = load_context()
    workdir = payload.get("workdir")
    return Path(derive_packages_path(workdir))


def configure_package_manager(packages_path: str | Path | None = None) -> PackageManager:
    """(Re)configure the singleton manager with a specific path."""

    global _DEFAULT_MANAGER
    target = Path(packages_path).expanduser() if packages_path else _default_path()
    _DEFAULT_MANAGER = PackageManager(target)
    return _DEFAULT_MANAGER


def get_package_manager(packages_path: str | Path | None = None) -> PackageManager:
    """Return the global PackageManager instance, creating it if needed."""

    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        return configure_package_manager(packages_path)
    if packages_path is not None:
        target = Path(packages_path).expanduser()
        if target.resolve() != _DEFAULT_MANAGER.packages_path:
            return configure_package_manager(target)
    return _DEFAULT_MANAGER


def reset_package_manager() -> None:
    """Reset the global PackageManager cache (used for settings reload).

    Called during Settings.reload() to ensure PackageManager uses the latest configuration.
    """
    global _DEFAULT_MANAGER
    _DEFAULT_MANAGER = None


__all__ = [
    "PackageManager",
    "configure_package_manager",
    "get_package_manager",
    "reset_package_manager",
    "build_context_payload",
    "derive_packages_path",
    "export_context",
    "load_context",
]
