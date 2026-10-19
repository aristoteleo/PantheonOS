"""Runtime helpers exposed inside python/shell interpreters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from . import get_package_manager
from pantheonos.package_runtime.context import load_context
from .manager import PackageManager

_RUNTIME: "PackageRuntime" | None = None


def get_runtime(packages_path: str | None = None) -> "PackageRuntime":
    global _RUNTIME
    manager = get_package_manager(packages_path)
    if _RUNTIME is None or _RUNTIME.manager is not manager:
        _RUNTIME = PackageRuntime(manager)
    return _RUNTIME


async def _ensure_awaitable(result: Any) -> Any:
    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
        return await result
    return result


@dataclass
class PackageRuntimeMethod:
    manager: PackageManager
    package_name: str
    method_name: str

    def _is_local_package(self) -> bool:
        """Quick check if package is local/system without loading."""
        return (
            self.package_name in self.manager._system_names or
            (self.manager.packages_path / self.package_name).exists()
        )

    def _invoke(self, *args, **kwargs):
        # Ensure MCP packages are loaded if this might be an MCP package
        if not self.manager._mcp_packages and not self._is_local_package():
            self.manager._auto_refresh_mcp_packages()
        
        # Route to MCP if it's an MCP package
        if self.manager.is_mcp_package(self.package_name):
            # MCP calls are always async, return coroutine
            return self.manager.call_mcp_tool(
                self.package_name,
                self.method_name,
                **kwargs,
            )
        
        # Local/system package: prepare context and call manager
        context = kwargs.pop("context_variables", None)
        if context is None:
            context_payload = load_context()
            context = dict(context_payload.get("context_variables") or {})
        return self.manager.call(
            self.package_name,
            self.method_name,
            *args,
            context_variables=context,
            **kwargs,
        )

    def __call__(self, *args, **kwargs):
        """Call the package method, preserving its original sync/async nature.

        If the underlying method is async, this returns a coroutine that
        the caller should await. If sync, returns the result directly.
        """
        return self._invoke(*args, **kwargs)

    def sync_call(self, *args, **kwargs):
        """Force synchronous execution of the method.

        Use this when you need to call an async method from a sync context.
        If already inside an event loop, runs the coroutine in a separate
        thread to avoid nested loop issues (no external dependencies).

        Returns:
            The result of the method execution (not a coroutine).
        """
        result = self._invoke(*args, **kwargs)
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            try:
                asyncio.get_running_loop()
                # Already in an event loop - run in a separate thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, result)
                    return future.result()
            except RuntimeError:
                # No running loop, asyncio.run() is safe
                return asyncio.run(result)
        return result

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<PackageRuntimeMethod {self.package_name}.{self.method_name}>"


class PackageProxy:
    def __init__(self, name: str, manager: PackageManager):
        self._name = name
        self._manager = manager

    def __getattr__(self, item: str) -> PackageRuntimeMethod:
        return PackageRuntimeMethod(self._manager, self._name, item)

    def __dir__(self):  # pragma: no cover - UX helper
        record = None
        try:
            record = self._manager.describe_package(self._name)["package"]
        except Exception:
            return []
        return record.get("methods", [])


class PackageRuntime:
    def __init__(self, manager: PackageManager):
        self.manager = manager

    async def list_packages(self):
        return await self.manager.list_packages()

    def describe(self, name: str):
        return self.manager.describe_package(name)

    def reload(self, name: str | None = None):
        return self.manager.reload_package(name)

    async def refresh_mcp_packages(self):
        """Discover and register MCP servers as packages.
        
        Call this to sync MCP servers from the Endpoint.
        Requires endpoint_mcp_uri to be set in context.
        
        Returns:
            dict with success status and list of registered package names
        """
        return await self.manager.refresh_mcp_packages()

    def __getattr__(self, item: str) -> PackageProxy:
        return PackageProxy(item, self.manager)

    def __dir__(self):  # pragma: no cover
        return self.manager.discover()


__all__ = ["PackageRuntime", "get_runtime"]
