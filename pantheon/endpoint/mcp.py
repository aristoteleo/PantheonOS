"""MCP (Model Context Protocol) Server Management Module

This module handles all MCP server lifecycle management with multi-port isolation:
- Configuration management (adding, removing, updating MCP servers)
- Server process management (starting, stopping, monitoring STDIO servers)
- Multi-port HTTP endpoint routing (each STDIO server on independent port)
- FastMCP proxy integration for STDIO servers
- Service discovery and status tracking

Architecture:
- Each STDIO server runs its own FastMCP HTTP proxy on a unique port
- Complete lifecycle isolation - stopping one server doesn't affect others
- HTTP servers use configured URIs directly (no process management)

Supported modes:
- http: Connect to existing HTTP endpoint (no process management)
- stdio: Launch subprocess with STDIO transport (FastMCP.run() converts to HTTP)
"""

import asyncio
import os
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict


from fastmcp import FastMCP


from ..utils.log import logger


class MCPServerType:
    """MCP server type constants"""

    HTTP = "http"  # Remote HTTP endpoint
    STDIO = "stdio"  # Local STDIO subprocess


class MCPPoolConfig(TypedDict, total=False):
    """MCP Server Pool Configuration."""

    mcp_servers: Dict[str, Dict[str, Any]]
    auto_start_mcp_servers: List[str]


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
    """Manages multiple MCP servers with independent HTTP endpoints

    Architecture:
    - Each STDIO server runs its own FastMCP HTTP proxy on unique ports
    - Each HTTP server is accessed directly at its configured URI
    - Routing URIs returned to clients:
      - STDIO: http://127.0.0.1:{base_port + index} (via FastMCP.run())
      - HTTP: configured URI directly
    - Complete lifecycle isolation - stopping one server doesn't affect others
    - No uvicorn management needed - FastMCP.run() handles HTTP server lifecycle
    """

    def __init__(self, log_dir: Optional[str] = None, base_port: int = 3000, host: str = "127.0.0.1"):
        """Initialize MCP Manager

        Args:
            log_dir: Directory for server logs
            base_port: Base port for STDIO servers (incremented per instance)
            host: Hostname/IP for generating client-accessible URIs (default: 127.0.0.1 for localhost)
        """
        self.instances: Dict[str, MCPServerInstance] = {}
        self._lock = asyncio.Lock()
        self.log_dir = log_dir
        self.base_port = base_port
        self.host = host
        self._next_port = base_port  # Track next available port for STDIO servers

        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            logger.info(f"MCP servers log directory: {log_dir}")

        logger.info(f"MCP Manager initialized with host: {self.host}, base_port: {self.base_port}")

    async def load_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Load MCP server configurations from config dictionary

        Args:
            config: Configuration dict with 'mcp_servers' key

        Returns:
            Result dict with loaded servers and any errors
        """
        results = {"success": True, "loaded": [], "errors": []}

        mcp_servers = config.get("mcp_servers", {})
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
        """Start specified MCP services

        Each STDIO server gets its own HTTP endpoint via FastMCP.run().
        HTTP servers use their configured URI directly.

        Lifecycle:
        1. Start subprocess (if STDIO)
        2. Set up HTTP endpoint (if STDIO)
        3. If any step fails, roll back all resources

        Args:
            names: List of service names to start

        Returns:
            Result dict with started services and errors
        """
        results = {"success": True, "started": [], "errors": []}

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
                # Set up HTTP endpoint based on server type
                try:
                    if instance.config.type == MCPServerType.STDIO:
                        await self._setup_stdio_http(name, instance)
                    # HTTP servers don't need setup, just use configured URI
                    results["started"].append(name)
                except Exception as e:
                    logger.error(f"Failed to setup HTTP endpoint for '{name}': {e}")
                    instance.status = "error"
                    instance.error_message = f"HTTP setup failed: {str(e)}"
                    # Rollback: clean up HTTP task and stop process
                    await instance._cleanup_http_task()
                    await instance.stop()
                    results["errors"].append(f"Failed to setup '{name}': {str(e)}")
                    results["success"] = False
            else:
                results["errors"].append(
                    f"Failed to start '{name}': {instance.error_message}"
                )
                results["success"] = False

        return results

    async def _setup_stdio_http(self, name: str, instance: MCPServerInstance) -> None:
        """Setup HTTP endpoint for a STDIO server via FastMCP proxy

        Each STDIO server gets its own FastMCP proxy with independent HTTP endpoint
        on a unique port. Uses the stdio_client already created by instance.start().

        Args:
            name: Server name
            instance: MCPServerInstance with stdio_client already created

        Raises:
            RuntimeError: If stdio_client not available or setup fails
        """
        if not instance.stdio_client:
            raise RuntimeError(f"STDIO server '{name}' client not initialized")

        try:
            # Allocate port for this server
            instance.http_port = self._next_port
            self._next_port += 1

            # Create a FastMCP proxy server that wraps the STDIO client
            proxy_mcp = FastMCP.as_proxy(instance.stdio_client, name=name)
            instance.fastmcp_proxy = proxy_mcp

            # Start HTTP server in background task using FastMCP's async method
            async def run_http_server():
                # Use run_http_async() which properly handles asyncio event loop
                # (unlike run() which tries to create its own event loop)
                # Note: path="/mcp" specifies the HTTP endpoint path for MCP requests
                await proxy_mcp.run_http_async(
                    host="0.0.0.0",
                    port=instance.http_port,
                    path="/mcp",
                    show_banner=False,
                )

            instance.http_server_task = asyncio.create_task(run_http_server())

            # Give server a moment to start
            await asyncio.sleep(0.5)

            logger.info(
                f"Started HTTP endpoint for STDIO server '{name}' at "
                f"http://0.0.0.0:{instance.http_port}"
            )

        except Exception as e:
            logger.error(f"Failed to setup HTTP endpoint for '{name}': {e}")
            # Clean up on failure
            if instance.http_server_task and not instance.http_server_task.done():
                instance.http_server_task.cancel()
            raise

    async def stop_services(self, names: List[str]) -> Dict[str, Any]:
        """Stop specified MCP services

        For STDIO servers: stops HTTP endpoint, then terminates subprocess
        For HTTP servers: just marks as stopped

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

            # instance.stop() handles all cleanup including HTTP tasks
            if await instance.stop():
                results["stopped"].append(name)
            else:
                results["errors"].append(
                    f"Failed to stop '{name}': {instance.error_message}"
                )
                results["success"] = False

        return results

    def _get_service_uri(self, instance: MCPServerInstance) -> Optional[str]:
        """Get the HTTP URI for a service based on its type

        Args:
            instance: MCPServerInstance

        Returns:
            HTTP URI string or None if not running
        """
        if instance.config.type == MCPServerType.STDIO:
            # STDIO servers have independent HTTP endpoints
            if instance.http_port:
                return f"http://{self.host}:{instance.http_port}/mcp"
            return None
        elif instance.config.type == MCPServerType.HTTP:
            # HTTP servers use configured URI directly
            return instance.config.uri
        return None

    def _build_service_info(self, name: str, instance: MCPServerInstance) -> Dict[str, Any]:
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
        """List all MCP services with their independent routing URIs

        Each STDIO server has its own HTTP endpoint at a unique port.
        HTTP servers use their configured URIs directly.

        Returns:
            Dict with services list including individual routing URIs
        """
        services = [
            self._build_service_info(name, instance)
            for name, instance in self.instances.items()
        ]

        return {
            "success": True,
            "services": services,
            "count": len(services),
            "base_port": self.base_port,
        }

    async def get_service(self, name: str) -> Dict[str, Any]:
        """Get details for a specific MCP service

        Returns the appropriate URI based on server type:
        - STDIO: Independent HTTP endpoint at allocated port
        - HTTP: Configured URI directly

        Args:
            name: Service name

        Returns:
            Service details with individual routing URI
        """
        if name not in self.instances:
            return {"success": False, "message": f"Service '{name}' not found"}

        instance = self.instances[name]

        return {
            "success": True,
            "service": self._build_service_info(name, instance),
        }
