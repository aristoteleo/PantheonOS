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
        self._migration_settings = settings
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

    @tool(exclude=True)
    async def export_migration_environment(self, operation_id: str, servers: dict) -> dict:
        """Owner-only migration handoff from this gateway's stdio launch env.

        servers maps names to extra inherited variables to capture. Declared
        env fields are always included. Returns a private local file path only;
        no processes are started and no Agent data is admitted for migration.
        """
        from pantheon.chatroom.migration_mcp_handoff import export_mcp_handoff
        try:
            async with self._manager._lock:
                return {'success': True, **export_mcp_handoff(self._migration_settings, self._manager,
                    operation_id=operation_id, servers=servers)}
        except Exception:
            return {'success': False, 'error': 'Could not capture the MCP environment. Check the original gateway and private storage, then use a new operation.'}

    @tool
    async def get_uri(self) -> dict:
        """The unified gateway's HTTP URI (every mounted server, prefixed)."""
        return {"success": True, "uri": self._manager.get_unified_uri()}

    @tool(exclude=True)
    async def export_migration_configuration(self, operation_id: str, servers: dict, providers: list[str]) -> dict:
        """Privately capture effective MCP launch coordinates, environment and tool views.

        servers maps selected names to extra inherited environment fields;
        providers selects the original Agent's visible catalogs. Returns only
        the owner-private capture path. No migration or deployment is performed.
        """
        from pantheon.chatroom.migration_mcp_configuration import export_mcp_configuration
        try:
            async with self._manager._lock:
                result = await export_mcp_configuration(self._migration_settings, self._manager,
                    operation_id=operation_id, servers=servers, providers=providers)
            return {'success': True, **result}
        except Exception:
            return {'success': False, 'error': 'Could not capture the MCP runtime configuration. Check the original gateway, launch coordinates and private storage.'}

    @tool(exclude=True)
    async def export_migration_tools(self, providers: list[str]) -> dict:
        """Capture observed tool contracts for ordinary App dependency packages.

        Provider names retain the old Agent's selection (mcp for the unified
        catalog, or a server name for prefix filtering). This exports metadata,
        not credentials, commands, deployment authority or import admission.
        """
        from pantheon.chatroom.migration_mcp_tools import capture_mcp_tools
        try:
            async with self._manager._lock:
                contract = await capture_mcp_tools(self._manager, providers=providers)
            return {'success': True, 'contract': contract}
        except Exception:
            return {'success': False, 'error': 'Could not capture the MCP tool contract. Check the original gateway and selected providers.'}

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

    @tool
    async def restart_server(self, name: str) -> dict:
        """Restart one MCP server.

        Args:
            name: The server name from the pool config.
        """
        return await self._manager.restart_service(name)

    @tool
    async def add_server(
        self,
        config: dict,
        persist: bool = False,
        auto_start: bool = False,
    ) -> dict:
        """Add an MCP server to the pool.

        Args:
            config: Server config — name, type ("http"|"stdio"), and
                command (stdio) or uri (http); optional env, description,
                mount_prefix.
            persist: Save to mcp.json.
            auto_start: Also start it when the gateway boots.
        """
        from .manager import MCPServerConfig

        try:
            cfg = MCPServerConfig(**config)
        except (TypeError, ValueError) as e:
            return {"success": False, "message": str(e)}
        return await self._manager.add_config(cfg, persist=persist, auto_start=auto_start)

    @tool
    async def remove_server(self, name: str, persist: bool = False) -> dict:
        """Remove an MCP server from the pool.

        Args:
            name: The server to remove.
            persist: Also drop it from mcp.json.
        """
        return await self._manager.remove_config(name, persist=persist)
