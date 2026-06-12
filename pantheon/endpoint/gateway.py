"""Unified MCP Gateway - Single endpoint for all MCP servers.

This module provides a unified HTTP endpoint that aggregates multiple MCP servers
behind a single port. All tools are exposed with prefixes (e.g., context7_resolve_library_id).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Optional

from pantheon.utils.log import log_startup_profile, logger

if TYPE_CHECKING:
    from fastmcp import Client, FastMCP


@dataclass
class MountedServerInfo:
    """Metadata for a mounted MCP server."""

    name: str
    server: "FastMCP"
    client: Optional["Client"] = None
    mounted_at: datetime = field(default_factory=datetime.now)
    status: str = "healthy"  # healthy, unhealthy, disabled


class UnifiedMCPGateway:
    """Unified MCP Gateway - single endpoint for all MCP servers.

    All tools are accessible at /mcp with prefixed names.
    Example: context7_resolve_library_id, biomcp_search_genes
    """

    def __init__(self, port: int = 3100, host: str = "127.0.0.1"):
        """Initialize the gateway.

        Args:
            port: HTTP port to listen on
            host: Host address to bind to
        """
        self.port = port
        self.host = host

        # Lazily imported to avoid circular dependencies
        self._unified_mcp: Optional["FastMCP"] = None
        self._mounted_servers: Dict[str, MountedServerInfo] = {}

        self._lock = asyncio.Lock()
        self._mcp_init_lock = asyncio.Lock()
        self._server_task: Optional[asyncio.Task] = None

    def _ensure_unified_mcp(self) -> "FastMCP":
        """Lazily create the unified FastMCP instance with filtering middleware."""
        if self._unified_mcp is None:
            ensure_t0 = time.perf_counter()
            from fastmcp import FastMCP
            from fastmcp.server.middleware import Middleware, MiddlewareContext

            class HideInternalToolsMiddleware(Middleware):
                """Filter out tools with 'internal' tag from list_tools response.
                
                Tools with 'internal' tag are hidden from discovery but remain callable.
                This is used for endpoint tools that should only be called by Package Runtime.
                """

                async def on_list_tools(self, context: MiddlewareContext, call_next):
                    result = await call_next(context)
                    # Filter tools that have 'internal' in their tags
                    return [
                        t for t in result
                        if "internal" not in getattr(t, "tags", set())
                    ]

            self._unified_mcp = FastMCP("Pantheon Unified Gateway")
            self._unified_mcp.add_middleware(HideInternalToolsMiddleware())
            log_startup_profile(
                "MCP gateway FastMCP object initialized in "
                f"{time.perf_counter() - ensure_t0:.3f}s"
            )
        return self._unified_mcp

    async def _ensure_unified_mcp_async(self) -> "FastMCP":
        """Create the unified FastMCP instance without blocking the event loop."""
        if self._unified_mcp is not None:
            return self._unified_mcp

        async with self._mcp_init_lock:
            if self._unified_mcp is None:
                await asyncio.to_thread(self._ensure_unified_mcp)

            assert self._unified_mcp is not None
            return self._unified_mcp

    async def start_gateway(self) -> None:
        """Start the HTTP gateway server using FastMCP native HTTP.

        If the configured port is in use, automatically finds an available port.
        """
        if self._server_task is not None:
            logger.debug("Gateway already running")
            return

        start_t0 = time.perf_counter()

        # Try configured port first, fallback to find available if in use
        from pantheon.utils.misc import find_free_port
        port_t0 = time.perf_counter()
        actual_port = find_free_port(self.port, self.host)
        log_startup_profile(
            "MCP gateway port check finished in "
            f"{time.perf_counter() - port_t0:.3f}s (port={actual_port})"
        )
        if actual_port != self.port:
            logger.info(
                f"Port {self.port} in use, using port {actual_port} instead"
            )
            self.port = actual_port

        # Use FastMCP's native HTTP server (supports SSE properly)
        ensure_t0 = time.perf_counter()
        mcp = await self._ensure_unified_mcp_async()
        log_startup_profile(
            "MCP gateway ensure_unified_mcp returned in "
            f"{time.perf_counter() - ensure_t0:.3f}s"
        )

        async def run_server():
            await mcp.run_http_async(
                host=self.host,
                port=self.port,
                path="/mcp",
                show_banner=False,
                log_level="warning",
            )

        task_t0 = time.perf_counter()
        self._server_task = asyncio.create_task(run_server())
        log_startup_profile(
            "MCP gateway server task scheduled in "
            f"{time.perf_counter() - task_t0:.3f}s"
        )

        # Wait for server to be ready (with health check)
        ready_t0 = time.perf_counter()
        await self._wait_until_ready()
        log_startup_profile(
            "MCP gateway readiness wait finished in "
            f"{time.perf_counter() - ready_t0:.3f}s; total="
            f"{time.perf_counter() - start_t0:.3f}s"
        )

    async def _wait_until_ready(
        self, timeout: float = 30.0, interval: float = 0.05
    ) -> None:
        """Wait until gateway HTTP server is ready to accept connections."""
        import time

        start_time = time.time()
        attempts = 0
        while time.time() - start_time < timeout:
            attempts += 1
            # Check if server task crashed
            if self._server_task and self._server_task.done():
                exc = self._server_task.exception()
                if exc:
                    raise RuntimeError(
                        f"Gateway server task failed: {exc}"
                    ) from exc
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=0.5
                )
                writer.close()
                await writer.wait_closed()
                logger.info(
                    f"Unified MCP Gateway started at http://{self.host}:{self.port}/mcp"
                )
                log_startup_profile(
                    "MCP gateway readiness probe connected after "
                    f"{attempts} attempt(s) in {time.time() - start_time:.3f}s"
                )
                return
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                await asyncio.sleep(interval)

        raise RuntimeError(
            f"Gateway failed to start within {timeout}s on {self.host}:{self.port}"
        )

    async def stop_gateway(self) -> None:
        """Stop the HTTP gateway server."""
        if self._server_task is not None:
            # Cancel the task
            self._server_task.cancel()
            try:
                # Wait for task to finish cleanup (Python 3.10 compatible)
                await asyncio.wait_for(
                    asyncio.shield(self._server_task),
                    timeout=5.0
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.error(f"Error during gateway task cleanup: {e}")
            finally:
                self._server_task = None
                logger.info("Unified MCP Gateway stopped")

    async def mount_server(
        self,
        name: str,
        proxy: "FastMCP",
        client: Optional["Client"] = None,
    ) -> bool:
        """Mount an MCP server to the unified gateway.

        Tools will be prefixed with the server name (e.g., name_tool_name).

        Args:
            name: Server name (used as prefix)
            proxy: FastMCP proxy instance
            client: Optional underlying client for health checks
            hidden: If True, tools are hidden from list_tools but still callable

        Returns:
            True if mounted successfully, False if already mounted
        """
        async with self._lock:
            if name in self._mounted_servers:
                logger.debug(f"Server '{name}' already mounted")
                return False

            # Mount to unified endpoint with namespace
            # fastmcp 3.x uses 'namespace', 2.x used 'prefix' (deprecated)
            unified = self._ensure_unified_mcp()
            try:
                unified.mount(proxy, namespace=name)
            except TypeError:
                unified.mount(proxy, prefix=name)

            # Track metadata
            self._mounted_servers[name] = MountedServerInfo(
                name=name,
                server=proxy,
                client=client,
            )

            logger.info(f"Mounted '{name}' to /mcp (prefix={name}_)")
            return True

    async def disable_server(self, name: str) -> bool:
        """Disable a server's tools (without unmounting)."""
        async with self._lock:
            if name not in self._mounted_servers:
                return False

            self._mounted_servers[name].status = "disabled"
            logger.info(f"Server '{name}' disabled")
            return True

    async def enable_server(self, name: str) -> bool:
        """Re-enable a disabled server."""
        async with self._lock:
            if name not in self._mounted_servers:
                return False

            self._mounted_servers[name].status = "healthy"
            logger.info(f"Server '{name}' enabled")
            return True

    def get_server_status(self, name: str) -> Optional[str]:
        """Get the status of a mounted server."""
        info = self._mounted_servers.get(name)
        return info.status if info else None

    def list_mounted_servers(self) -> Dict[str, dict]:
        """List all mounted servers with their status."""
        return {
            name: {
                "status": info.status,
                "mounted_at": info.mounted_at.isoformat(),
            }
            for name, info in self._mounted_servers.items()
        }

    def get_unified_uri(self) -> str:
        """Get the URI for the unified endpoint."""
        return f"http://{self.host}:{self.port}/mcp"

    def get_server_uri(self, name: str) -> Optional[str]:
        """Get the unified URI if server is mounted.

        Note: All servers share the same URI, tools are distinguished by prefix.

        Args:
            name: Server name

        Returns:
            Unified URI if mounted, None otherwise
        """
        if name not in self._mounted_servers:
            return None
        return self.get_unified_uri()
