"""MCP (Model Context Protocol) Server Management Module

This module handles all MCP server lifecycle management with unified gateway:
- Configuration management (adding, removing, updating MCP servers)
- Server process management (starting, stopping, monitoring STDIO servers)
- Unified gateway routing via UnifiedMCPGateway
- FastMCP proxy integration for STDIO servers
- Service discovery and status tracking

Architecture:
- All MCP servers are mounted to a single UnifiedMCPGateway on one port
- Unified endpoint (/mcp) exposes all tools with prefixes (e.g., context7_resolve_library_id)
- Path endpoints (/mcp/{server}) expose tools without prefixes
- Complete lifecycle isolation - stopping one server doesn't affect others

Supported modes:
- http: Connect to existing HTTP endpoint (no process management)
- stdio: Launch subprocess with STDIO transport (FastMCP proxy to gateway)
"""

import asyncio
import os
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict




from pantheon.utils.log import logger


def _patch_stdio_client_errlog():
    """Patch MCP SDK's stdio_client to redirect subprocess stderr to log file.
    
    This redirects STDIO MCP server stderr (welcome messages, logs) to a dedicated
    log file instead of Python's sys.stderr, preventing noise in REPL interface
    while keeping logs accessible for debugging.
    
    Log file location: {pantheon_dir}/logs/mcp_stdio.log
    
    Must be called before any STDIO MCP servers are started.
    """
    try:
        import mcp.client.stdio as stdio_module
        
        # Check if already patched
        if getattr(stdio_module, '_errlog_patched', False):
            return
        
        # Store original function
        original_stdio_client = stdio_module.stdio_client
        
        # Prepare log file
        from pantheon.settings import get_settings
        log_dir = get_settings().pantheon_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        mcp_log_file = log_dir / "mcp_stdio.log"
        
        # Open log file in append mode (shared across all STDIO servers)
        _mcp_errlog = open(mcp_log_file, 'a', buffering=1)  # Line buffered
        
        # Store file handle to prevent GC
        stdio_module._mcp_errlog_file = _mcp_errlog
        
        # Create patched version
        def patched_stdio_client(server, errlog=None):
            # Default to log file instead of sys.stderr
            if errlog is None:
                errlog = _mcp_errlog
            return original_stdio_client(server, errlog=errlog)
        
        # Apply patch
        stdio_module.stdio_client = patched_stdio_client
        stdio_module._errlog_patched = True
        
        logger.debug(f"Patched MCP stdio_client to redirect stderr to {mcp_log_file}")
    except Exception as e:
        logger.warning(f"Failed to patch MCP stdio_client: {e}")


# Apply patch on module import
_patch_stdio_client_errlog()


class MCPServerType:
    """MCP server type constants"""

    HTTP = "http"  # Remote HTTP endpoint
    STDIO = "stdio"  # Local STDIO subprocess


class MCPPoolConfig(TypedDict, total=False):
    """MCP Server Pool Configuration (matches mcp.json structure)."""

    servers: Dict[str, Dict[str, Any]]
    auto_start: List[str]


@dataclass
class MCPServerConfig:
    """MCP Server Configuration

    Supports two types:
    - http: Remote HTTP endpoint (uri required)
    - stdio: Local STDIO subprocess (command required)
    """

    name: str
    type: str  # "http" or "stdio"
    command: Optional[str] = None  # For stdio: full command line
    uri: Optional[str] = None  # For http: remote HTTP endpoint
    env: Optional[Dict[str, str]] = None  # Environment variables (stdio only)
    description: str = ""
    mount_prefix: Optional[str] = None  # Prefix for unified gateway (default: name)

    def __post_init__(self):
        """Validate configuration"""
        if self.type not in (MCPServerType.HTTP, MCPServerType.STDIO):
            raise ValueError(
                f"MCP server '{self.name}': unknown type '{self.type}'. "
                f"Must be 'http' or 'stdio'"
            )

        if self.type == MCPServerType.STDIO:
            if not self.command:
                raise ValueError(
                    f"MCP server '{self.name}': stdio type requires 'command'"
                )
        elif self.type == MCPServerType.HTTP:
            if not self.uri:
                raise ValueError(f"MCP server '{self.name}': http type requires 'uri'")

        if not self.env:
            self.env = {}


@dataclass
class MCPServerInstance:
    """Runtime MCP server instance with status tracking"""

    config: MCPServerConfig

    # Runtime state
    status: str = "stopped"  # "running", "stopped", "error"
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None

    # STDIO server lifecycle (FastMCP managed)
    # StdioTransport handles process creation/termination automatically
    stdio_transport: Optional[Any] = None  # StdioTransport instance
    stdio_client: Optional[Any] = None  # FastMCP Client wrapping transport

    # HTTP endpoint for STDIO servers
    http_port: Optional[int] = None  # Allocated port number
    http_server_task: Optional[asyncio.Task] = None  # proxy_mcp.run() async task
    fastmcp_proxy: Optional[Any] = None  # FastMCP proxy server

    async def start(self, log_dir: Optional[str] = None) -> bool:
        """Start the MCP server

        For HTTP type: just mark as running (no process management)
        For STDIO type: create StdioTransport and Client (FastMCP manages process)

        Args:
            log_dir: Directory for stdout/stderr logging (currently unused for STDIO)

        Returns:
            True if started successfully, False otherwise
        """
        if self.status == "running":
            return True

        try:
            if self.config.type == MCPServerType.HTTP:
                # HTTP type: no subprocess, just use URI directly
                self.status = "running"
                logger.info(
                    f"HTTP MCP server '{self.config.name}' connected to {self.config.uri}"
                )
                return True

            # STDIO type: create transport and client (StdioTransport manages process)
            if self.config.type == MCPServerType.STDIO:
                from fastmcp.client.transports import StdioTransport
                from fastmcp import Client as FastMCPClient

                cmd = shlex.split(self.config.command)

                # Create StdioTransport - it will manage subprocess lifecycle
                # Note: subprocess stderr (welcome messages) is handled by FastMCP
                # and may appear in console. Use FASTMCP_LOG_LEVEL=WARNING to suppress.
                self.stdio_transport = StdioTransport(
                    command=cmd[0], args=cmd[1:], env=self._prepare_env()
                )

                # Create FastMCP client wrapping the transport
                self.stdio_client = FastMCPClient(self.stdio_transport)

                self.status = "running"
                self.started_at = datetime.now()
                self.error_message = None

                logger.info(
                    f"Started STDIO MCP server '{self.config.name}' (process managed by FastMCP)"
                )
                return True

        except Exception as e:
            self.status = "error"
            self.error_message = str(e)
            logger.error(f"Failed to start MCP server '{self.config.name}': {e}")
            return False

    async def stop(self) -> bool:
        """Stop the MCP server

        For http type: just mark as stopped (no process to kill)
        For stdio type:
          1. Stop HTTP server task (if running)
          2. Terminate subprocess
          3. Clean up all resources

        Returns:
            True if stopped successfully, False otherwise
        """
        if self.status == "stopped":
            return True

        try:
            if self.config.type == MCPServerType.HTTP:
                # HTTP type: no process to stop
                self.status = "stopped"
                logger.info(f"HTTP MCP server '{self.config.name}' disconnected")
                return True

            # STDIO type: cleanup in proper order
            if self.config.type == MCPServerType.STDIO:
                # Step 1: Stop HTTP server task
                await self._cleanup_http_task()

                # Step 2: Disconnect STDIO transport (FastMCP will manage process cleanup)
                if self.stdio_transport:
                    try:
                        await self.stdio_transport.disconnect()
                        logger.info(
                            f"STDIO MCP server '{self.config.name}' transport disconnected"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error disconnecting STDIO transport for '{self.config.name}': {e}"
                        )
                    finally:
                        self.stdio_transport = None
                        self.stdio_client = None

                self.status = "stopped"
                logger.info(f"STDIO MCP server '{self.config.name}' stopped")

            return True

        except Exception as e:
            self.status = "error"
            self.error_message = str(e)
            logger.error(f"Failed to stop MCP server '{self.config.name}': {e}")
            return False

    async def _cleanup_http_task(self) -> None:
        """Clean up HTTP server task

        Stops the FastMCP.run() task running the HTTP endpoint.
        """
        if self.http_server_task and not self.http_server_task.done():
            logger.debug(f"Cleaning up HTTP server task for '{self.config.name}'...")
            self.http_server_task.cancel()
            try:
                await asyncio.wait_for(self.http_server_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        self.http_server_task = None
        self.fastmcp_proxy = None

    def _prepare_env(self) -> Dict[str, str]:
        """Prepare environment variables with variable resolution"""
        env = os.environ.copy()

        if self.config.env:
            for key, value in self.config.env.items():
                if (
                    isinstance(value, str)
                    and value.startswith("${")
                    and value.endswith("}")
                ):
                    # Resolve environment variable reference
                    env_var_name = value[2:-1]
                    resolved_value = os.environ.get(env_var_name)
                    if resolved_value:
                        env[key] = resolved_value
                    else:
                        logger.warning(
                            f"Environment variable '{env_var_name}' not found"
                        )
                else:
                    env[key] = value

        return env

    def is_running(self) -> bool:
        """Check if server is running"""
        return self.status == "running"


class MCPManager:
    """Manages multiple MCP servers through a unified gateway.

    Architecture:
    - All MCP servers are mounted to a single UnifiedMCPGateway
    - Unified endpoint (/mcp) exposes all tools with prefixes
    - Path endpoints (/mcp/{server}) expose tools without prefixes
    - Complete lifecycle isolation - stopping one server doesn't affect others
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        port: int = 3100,
        host: str = "127.0.0.1",
    ):
        """Initialize MCP Manager with unified gateway.

        Args:
            log_dir: Directory for server logs
            port: Port for unified gateway (default: 3100)
            host: Host address to bind to (default: 127.0.0.1)
        """
        from pantheon.endpoint.gateway import UnifiedMCPGateway

        self.instances: Dict[str, MCPServerInstance] = {}
        self._lock = asyncio.Lock()
        self.log_dir = log_dir
        self.host = host
        self.port = port

        # Unified gateway replaces multi-port architecture
        self._gateway = UnifiedMCPGateway(port=port, host=host)

        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            logger.info(f"MCP servers log directory: {log_dir}")

        logger.info(
            f"MCP Manager initialized with unified gateway at {host}:{port}"
        )

    async def load_config(self, config: MCPPoolConfig) -> Dict[str, Any]:
        """Load MCP server configurations from config dictionary

        Args:
            config: Configuration dict with 'mcp_servers' key

        Returns:
            Result dict with loaded servers and any errors
        """
        results = {"success": True, "loaded": [], "errors": []}

        # mcp.json uses 'servers' key directly
        mcp_servers = config.get("servers", {})
        if not mcp_servers:
            return results

        for server_name, server_config in mcp_servers.items():
            try:
                server_type = server_config.get("type", "stdio")

                if server_type == MCPServerType.STDIO:
                    command = server_config.get("command")
                    # Convert list to string if needed
                    if isinstance(command, list):
                        command = " ".join(command)

                    config_obj = MCPServerConfig(
                        name=server_name,
                        type=MCPServerType.STDIO,
                        command=command,
                        env=server_config.get("env", {}),
                        description=server_config.get("description", ""),
                    )

                elif server_type == MCPServerType.HTTP:
                    config_obj = MCPServerConfig(
                        name=server_name,
                        type=MCPServerType.HTTP,
                        uri=server_config.get("uri"),
                        description=server_config.get("description", ""),
                    )
                else:
                    results["errors"].append(
                        f"Unknown MCP type for '{server_name}': {server_type}"
                    )
                    results["success"] = False
                    continue

                await self.add_config(config_obj)
                results["loaded"].append(server_name)

            except Exception as e:
                results["errors"].append(f"Failed to load '{server_name}': {str(e)}")
                results["success"] = False
                logger.error(f"Error loading MCP server '{server_name}': {e}")

        return results

    async def add_config(self, config: MCPServerConfig) -> Dict[str, Any]:
        """Add a new MCP server configuration

        Args:
            config: MCPServerConfig instance

        Returns:
            Result dict with success status
        """
        async with self._lock:
            if config.name in self.instances:
                return {
                    "success": False,
                    "message": f"MCP server '{config.name}' already exists",
                }

            instance = MCPServerInstance(config=config)
            self.instances[config.name] = instance
            logger.info(f"Added MCP server configuration: {config.name}")

            return {
                "success": True,
                "message": f"MCP server '{config.name}' added successfully",
                "name": config.name,
            }

    async def remove_config(self, name: str) -> Dict[str, Any]:
        """Remove an MCP server configuration

        Args:
            name: Server name

        Returns:
            Result dict with success status
        """
        async with self._lock:
            if name not in self.instances:
                return {"success": False, "message": f"MCP server '{name}' not found"}

            instance = self.instances[name]

            # Stop if running
            if instance.is_running():
                await instance.stop()

            del self.instances[name]
            logger.info(f"Removed MCP server: {name}")

            return {
                "success": True,
                "message": f"MCP server '{name}' removed successfully",
            }

    async def update_config(self, name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update an MCP server configuration

        Args:
            name: Server name
            updates: Dictionary of fields to update

        Returns:
            Result dict with success status
        """
        async with self._lock:
            if name not in self.instances:
                return {"success": False, "message": f"MCP server '{name}' not found"}

            instance = self.instances[name]

            # Don't allow updates while running
            if instance.is_running():
                return {
                    "success": False,
                    "message": "Cannot update configuration while server is running",
                }

            # Update configuration
            config_dict = {
                "name": instance.config.name,
                "type": instance.config.type,
                "command": instance.config.command,
                "uri": instance.config.uri,
                "env": instance.config.env,
                "description": instance.config.description,
            }
            config_dict.update(updates)

            instance.config = MCPServerConfig(**config_dict)
            logger.info(f"Updated MCP server configuration: {name}")

            return {
                "success": True,
                "message": f"MCP server '{name}' configuration updated",
            }

    async def start_services(self, names: List[str]) -> Dict[str, Any]:
        """Start specified MCP services and mount to unified gateway.

        Lifecycle:
        1. Ensure gateway is running
        2. Start subprocess (if STDIO) or connect (if HTTP)
        3. Create FastMCP proxy and mount to gateway
        4. If any step fails, roll back all resources

        Args:
            names: List of service names to start

        Returns:
            Result dict with started services and errors
        """
        results = {"success": True, "started": [], "errors": []}

        # Ensure gateway is running
        await self._gateway.start_gateway()

        for name in names:
            if name not in self.instances:
                results["errors"].append(f"Service '{name}' not found")
                results["success"] = False
                continue

            instance = self.instances[name]

            # For stdio servers, pass log directory
            log_dir = (
                self.log_dir if instance.config.type == MCPServerType.STDIO else None
            )

            if await instance.start(log_dir=log_dir):
                # Mount to unified gateway
                try:
                    await self._mount_to_gateway(name, instance)
                    results["started"].append(name)
                except Exception as e:
                    logger.error(f"Failed to mount '{name}' to gateway: {e}")
                    instance.status = "error"
                    instance.error_message = f"Gateway mount failed: {str(e)}"
                    await instance.stop()
                    results["errors"].append(f"Failed to mount '{name}': {str(e)}")
                    results["success"] = False
            else:
                results["errors"].append(
                    f"Failed to start '{name}': {instance.error_message}"
                )
                results["success"] = False

        return results

    async def _mount_to_gateway(self, name: str, instance: MCPServerInstance) -> None:
        """Mount an MCP server to the unified gateway.

        Creates a FastMCP proxy and mounts it to the gateway with the configured
        prefix (or server name as default).

        Args:
            name: Server name
            instance: MCPServerInstance with client already created

        Raises:
            RuntimeError: If client not available or mount fails
        """
        from fastmcp import FastMCP

        prefix = instance.config.mount_prefix or name

        if instance.config.type == MCPServerType.STDIO:
            if not instance.stdio_client:
                raise RuntimeError(f"STDIO server '{name}' client not initialized")

            # Create FastMCP proxy wrapping the STDIO client
            proxy = FastMCP.as_proxy(instance.stdio_client, name=name)
            instance.fastmcp_proxy = proxy

            # Mount to gateway
            await self._gateway.mount_server(prefix, proxy, instance.stdio_client)
            instance.http_port = self.port  # All servers share gateway port

            logger.info(
                f"Mounted STDIO server '{name}' to gateway with prefix '{prefix}'"
            )

        elif instance.config.type == MCPServerType.HTTP:
            from fastmcp import Client

            # Create client for remote HTTP server
            remote_client = Client(instance.config.uri)

            # Create proxy wrapping the remote client
            proxy = FastMCP.as_proxy(remote_client, name=name)
            instance.fastmcp_proxy = proxy

            # Mount to gateway
            await self._gateway.mount_server(prefix, proxy, remote_client)
            instance.http_port = self.port

            logger.info(
                f"Mounted HTTP server '{name}' to gateway with prefix '{prefix}'"
            )

    async def stop_services(self, names: List[str]) -> Dict[str, Any]:
        """Stop specified MCP services.

        Disables the service in the gateway and stops the underlying process.

        Args:
            names: List of service names to stop

        Returns:
            Result dict with stopped services and errors
        """
        results = {"success": True, "stopped": [], "errors": []}

        for name in names:
            if name not in self.instances:
                results["errors"].append(f"Service '{name}' not found")
                results["success"] = False
                continue

            instance = self.instances[name]
            prefix = instance.config.mount_prefix or name

            # Disable in gateway first
            await self._gateway.disable_server(prefix)

            # Stop the underlying process
            if await instance.stop():
                results["stopped"].append(name)
            else:
                results["errors"].append(
                    f"Failed to stop '{name}': {instance.error_message}"
                )
                results["success"] = False

        return results

    async def restart_service(self, name: str) -> Dict[str, Any]:
        """Restart a specific MCP service.

        Args:
            name: Service name to restart

        Returns:
            Result dict with restart status
        """
        stop_result = await self.stop_services([name])
        if not stop_result["success"]:
            return stop_result
        return await self.start_services([name])

    def get_unified_uri(self) -> str:
        """Get the unified gateway URI.

        Returns:
            Full URI string for the unified endpoint
        """
        return self._gateway.get_unified_uri()

    def _get_service_uri(self, instance: MCPServerInstance) -> Optional[str]:
        """Get the HTTP URI for a service (path endpoint on unified gateway).

        Args:
            instance: MCPServerInstance

        Returns:
            HTTP URI string for the service's path endpoint
        """
        if not instance.is_running():
            return None

        prefix = instance.config.mount_prefix or instance.config.name
        return self._gateway.get_server_uri(prefix)

    def _build_service_info(
        self, name: str, instance: MCPServerInstance
    ) -> Dict[str, Any]:
        """Build service information dict

        Consolidates service info building to avoid duplication between
        list_services and get_service.

        Args:
            name: Service name
            instance: MCPServerInstance

        Returns:
            Service info dict
        """
        uri = self._get_service_uri(instance)

        return {
            "name": name,
            "type": instance.config.type,
            "status": instance.status,
            "uri": uri,  # Per-server independent URI
            "http_port": instance.http_port
            if instance.config.type == MCPServerType.STDIO
            else None,
            "command": instance.config.command
            if instance.config.type == MCPServerType.STDIO
            else None,
            "description": instance.config.description,
            "error_message": instance.error_message,
            "started_at": instance.started_at.isoformat()
            if instance.started_at
            else None,
        }

    async def list_services(self) -> Dict[str, Any]:
        """List all MCP services with their status and URIs.

        All services share the unified gateway endpoint.

        Returns:
            Dict with services list and gateway info
        """
        services = [
            self._build_service_info(name, instance)
            for name, instance in self.instances.items()
        ]

        return {
            "success": True,
            "services": services,
            "count": len(services),
            "unified_uri": self.get_unified_uri(),
            "port": self.port,
        }

    async def get_service(self, name: str) -> Dict[str, Any]:
        """Get details for a specific MCP service.

        Args:
            name: Service name, or "mcp" to get unified gateway info

        Returns:
            Service details with path endpoint URI
        """
        # Special case: "mcp" returns unified gateway info
        if name == "mcp":
            return {
                "success": True,
                "service": {
                    "name": "mcp",
                    "uri": self.get_unified_uri(),
                    "description": "Unified MCP Gateway",
                },
            }

        if name not in self.instances:
            return {"success": False, "message": f"Service '{name}' not found"}

        instance = self.instances[name]

        return {
            "success": True,
            "service": self._build_service_info(name, instance),
        }
