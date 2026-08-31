"""The mcp-gateway App — the unified MCP gateway as its own service.

Historically the endpoint owned the gateway (construct MCPManager, start the
FastMCP HTTP server, auto-start configured servers, answer manage_service
queries for the URI). This class is that whole lifecycle as a supervisable
App: `python -m pantheon.apphost --app-id mcp-gateway --workdir W` boots the
gateway and serves its coordinates over the bus, no endpoint involved.

Consumers ask `get_uri` and connect their MCP client straight to the HTTP
URI — the gateway's data plane stays HTTP exactly as before; only the
control plane (who starts it, who answers "where is it") moves to the App
model.
"""

from __future__ import annotations

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger


class MCPGatewayToolSet(ToolSet):
    """Unified MCP gateway: configured MCP servers behind one HTTP URI.

    Args:
        name: The name of the toolset.
        workdir: Project directory whose .pantheon config (mcp.json) governs
            the server pool. Defaults to the process cwd.
        port: Gateway port override (default from config, then 3100).
        host: Gateway host override (default from config, then localhost).
        **kwargs: Additional keyword arguments.
    """

    def __init__(
        self,
        name: str,
        workdir: str | None = None,
        port: int | None = None,
        host: str | None = None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self.workdir = workdir
        self._port = port
        self._host = host
        self._manager = None

    async def run_setup(self):
        """Construct the manager, start the gateway, auto-start servers.

        Mirrors the endpoint's phase-1 MCP startup (config load → gateway →
        auto_start) so behavior is identical either side of the migration.
        """
        from pantheon.settings import get_settings
        from pantheon.apps.builtin.mcp.manager import MCPManager

        settings = get_settings()
        mcp_config = settings.get_mcp_config()
        self._manager = MCPManager(
            log_dir=str(settings.pantheon_dir / "logs" / "mcp"),
            port=self._port or mcp_config.get("port", 3100),
            host=self._host or mcp_config.get("host", "localhost"),
            config_path=settings.pantheon_dir / "mcp.json",
        )
        result = await self._manager.load_config(mcp_config)
        if result.get("errors"):
            logger.warning(f"[mcp-gateway] config load errors: {result['errors']}")
        await self._manager._gateway.start_gateway()
        logger.info(f"[mcp-gateway] gateway up at {self._manager.get_unified_uri()}")
        auto_start = mcp_config.get("auto_start", [])
        if auto_start:
            started = await self._manager.start_services(auto_start)
            logger.info(f"[mcp-gateway] auto-start {auto_start}: {started}")

    async def cleanup(self):
        if self._manager is not None:
            names = list(self._manager.instances)
            if names:
                await self._manager.stop_services(names)
            await self._manager._gateway.stop_gateway()

    @tool
    async def get_uri(self) -> dict:
        """The unified gateway's HTTP URI (every mounted server, prefixed)."""
        return {"success": True, "uri": self._manager.get_unified_uri()}

    @tool
    async def list_servers(self) -> dict:
        """List the gateway's MCP servers and their status."""
        return await self._manager.list_services()

    @tool
    async def get_server(self, name: str) -> dict:
        """One MCP server's status and connection info.

        Args:
            name: The server name from the pool config.
        """
        return await self._manager.get_service(name)

    @tool
    async def start_servers(self, names: list[str]) -> dict:
        """Start (mount) MCP servers by name.

        Args:
            names: Server names from the pool config.
        """
        return await self._manager.start_services(names)

    @tool
    async def stop_servers(self, names: list[str]) -> dict:
        """Stop (unmount) MCP servers by name.

        Args:
            names: Server names to stop.
        """
        return await self._manager.stop_services(names)
