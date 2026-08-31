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


# Global MCP log directory (can be set by MCPManager)
_MCP_LOG_DIR: Optional[Path] = None


def _get_mcp_log_path(command: str) -> Path:
    """Get the log file path for a specific MCP server command.
    
    Args:
        command: The server command string (e.g., "uvx context7")
        
    Returns:
        Path to the server's log file
    """
    import hashlib
    
    # Use global log dir (set by MCPManager) or default to /tmp
    log_dir = _MCP_LOG_DIR or Path("/tmp/mcp")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize command for filename (replace spaces and special chars)
    # Use hash suffix to avoid collisions
    safe_name = command.replace(" ", "_").replace("/", "_").replace("\\", "_")[:50]
    cmd_hash = hashlib.md5(command.encode()).hexdigest()[:8]
    
    return log_dir / f"{safe_name}_{cmd_hash}.log"


def _patch_stdio_client_errlog():
    """Patch MCP SDK's stdio_client to redirect subprocess stderr to per-server log files.

    Each STDIO MCP server gets its own log file: {pantheon_dir}/logs/mcp/{server_name}.log
    
    This makes it easy to:
    1. Debug individual servers
    2. Display server-specific errors in REPL
    3. Avoid log mixing between servers

    Must be called before any STDIO MCP servers are started.
    """
    try:
        import mcp.client.stdio as stdio_module

        # Check if already patched
        if getattr(stdio_module, "_errlog_patched", False):
            return

        # Store original function
        original_stdio_client = stdio_module.stdio_client

        # Store open log files to prevent GC
        if not hasattr(stdio_module, "_mcp_log_files"):
            stdio_module._mcp_log_files = {}

        # Create patched version
        def patched_stdio_client(server, errlog=None):
            if errlog is None:
                # Build full command string for log file identification
                cmd_str = f"{server.command} {' '.join(server.args)}" if server.args else server.command
                
                # Get or create log file for this command
                log_path = _get_mcp_log_path(cmd_str)
                
                # Open in append mode, line buffered
                log_file = open(log_path, "a", buffering=1, encoding="utf-8")
                stdio_module._mcp_log_files[cmd_str] = log_file
                
                # Write session separator
                from datetime import datetime
                log_file.write(f"\n{'='*60}\n")
                log_file.write(f"Session started at {datetime.now().isoformat()}\n")
                log_file.write(f"{'='*60}\n")
                log_file.flush()
                
                errlog = log_file
            
            return original_stdio_client(server, errlog=errlog)

        # Apply patch
        stdio_module.stdio_client = patched_stdio_client
        stdio_module._errlog_patched = True

        logger.debug("Patched MCP stdio_client to use per-server log files")
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

    # Runtime state (backing field)
    _status: str = "stopped"  # "running", "starting", "stopped", "error"
    started_at: Optional[datetime] = None

    # STDIO server lifecycle (FastMCP managed)
    stdio_transport: Optional[Any] = None  # StdioTransport instance
    stdio_client: Optional[Any] = None  # FastMCP Client wrapping transport
    stdio_log_file: Optional[Any] = None  # Log file handle for stderr redirection

    # HTTP server lifecycle
    http_client: Optional[Any] = None  # FastMCP Client for remote HTTP server
    http_port: Optional[int] = None  # Allocated port number
    http_server_task: Optional[asyncio.Task] = None  # proxy_mcp.run() async task
    fastmcp_proxy: Optional[Any] = None  # FastMCP proxy server
    
    @property
    def status(self) -> str:
        """Get current status.
        
        Status is managed by:
        - start(): sets to 'starting'
        - _prewarm_server(): sets to 'running' on success, 'error' on failure
        - stop(): sets to 'stopped' (preserves 'error')
        
        Note: is_connected() is only True during active async with context,
        so we rely on _status as the source of truth.
        """
        return self._status
    
    @status.setter
    def status(self, value: str) -> None:
        """Set status backing field."""
        self._status = value
    
    @property
    def logs(self) -> List[str]:
        """Get recent stderr log lines from this server's log file.
        
        Log file is identified by command string (e.g., "uvx context7").
        """
        try:
            # Use command string to find log file
            log_path = _get_mcp_log_path(self.config.command or "")
            if not log_path.exists():
                return []
            
            # Read last 50 lines
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-50:]
            
            # Return non-empty lines (stripped)
            return [line.rstrip() for line in lines if line.strip()]
        except Exception:
            return []

    async def start(self) -> bool:
        """Start the MCP server

        For HTTP type: just mark as running (no process management)
        For STDIO type: create StdioTransport and Client (FastMCP manages process)
        
        Logs are automatically written to per-server files via stdio_client patch.

        Returns:
            True if started successfully, False otherwise
        """
        if self.status in ("running", "starting"):
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

                # Use posix=False on Windows to handle backslashes correctly
                import os
                cmd = shlex.split(self.config.command, posix=(os.name != 'nt'))
                # On Windows with posix=False, quotes are preserved - strip them
                if os.name == 'nt':
                    cmd = [arg.strip('"').strip("'") for arg in cmd]

                # Get or create log file for this server (reuse existing mechanism)
                log_path = _get_mcp_log_path(self.config.command)
                log_file = open(log_path, "a", buffering=1, encoding="utf-8")

                # Write session separator
                log_file.write(f"\n{'='*60}\n")
                log_file.write(f"Session started at {datetime.now().isoformat()}\n")
                log_file.write(f"Command: {self.config.command}\n")
                log_file.write(f"{'='*60}\n")
                log_file.flush()

                # Store log file reference for cleanup
                self.stdio_log_file = log_file

                # Create StdioTransport with log_file to redirect stderr
                # This suppresses uvx dependency resolution and FastMCP startup logs
                self.stdio_transport = StdioTransport(
                    command=cmd[0], args=cmd[1:], env=self._prepare_env(), log_file=log_file
                )

                # Create FastMCP client wrapping the transport
                self.stdio_client = FastMCPClient(self.stdio_transport)

                # Set to 'starting' - will become 'running' after pre-warm succeeds
                self.status = "starting"
                self.started_at = datetime.now()

                logger.info(
                    f"Starting STDIO MCP server '{self.config.name}'..."
                )
                return True

        except Exception as e:
            self.status = "error"
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
                # HTTP type: no process to stop, preserve error status
                if self._status != "error":
                    self.status = "stopped"
                self.http_client = None
                logger.info(f"HTTP MCP server '{self.config.name}' disconnected")
                return True

            # STDIO type: cleanup in proper order
            if self.config.type == MCPServerType.STDIO:
                # Step 1: Stop HTTP server task
                await self._cleanup_http_task()

                # Step 2: Disconnect STDIO transport
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

                # Step 3: Close log file
                if self.stdio_log_file:
                    try:
                        self.stdio_log_file.close()
                    except Exception:
                        pass
                    self.stdio_log_file = None

                # Step 4: Clear any remaining client references to avoid session leaks
                if self.http_client:
                    try:
                        # FastMCP client is an async context manager
                        await self.http_client.__aexit__(None, None, None)
                    except Exception:
                        pass
                    self.http_client = None
                self.stdio_client = None

                # Preserve error status if it was set
                if self._status != "error":
                    self.status = "stopped"
                logger.info(f"STDIO MCP server '{self.config.name}' stopped")

            return True

        except Exception as e:
            self.status = "error"
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
                            f"Environment variable '{env_var_name}' not found for '{self.config.name}'"
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
        config_path: Optional[Path] = None,
    ):
        """Initialize MCP Manager with unified gateway.

        Args:
            log_dir: Directory for MCP server logs (default: {pantheon_dir}/logs/mcp)
            port: Port for unified gateway (default: 3100)
            host: Host address to bind to (default: 127.0.0.1)
            config_path: Path to mcp.json for config persistence (optional)
        """
        from pantheon.apps.builtin.mcp.gateway import UnifiedMCPGateway
        global _MCP_LOG_DIR

        self.instances: Dict[str, MCPServerInstance] = {}
        self._lock = asyncio.Lock()
        self.host = host
        self.port = port
        self._config_path = config_path

        # Set global log directory for _get_mcp_log_path
        if log_dir:
            _MCP_LOG_DIR = Path(log_dir)
            _MCP_LOG_DIR.mkdir(parents=True, exist_ok=True)
            logger.debug(f"MCP log directory: {_MCP_LOG_DIR}")

        # Unified gateway replaces multi-port architecture
        self._gateway = UnifiedMCPGateway(port=port, host=host)

        logger.info(f"MCP Manager initialized with unified gateway at {host}:{port}")

    def _save_config(self, auto_start_add: Optional[str] = None, auto_start_remove: Optional[str] = None) -> bool:
        """Save current server configs to mcp.json.
        
        Writes to project-level config (.pantheon/mcp.json).
        Preserves existing settings (host, port) and updates servers + auto_start.
        
        Args:
            auto_start_add: Server name to add to auto_start list
            auto_start_remove: Server name to remove from auto_start list
        
        Returns:
            True if saved successfully, False otherwise
        """
        if not self._config_path:
            logger.warning("No config path set, cannot persist MCP config")
            return False
        
        try:
            import json
            from pantheon.settings import load_jsonc
            
            # Load existing config to preserve other settings
            existing = load_jsonc(self._config_path) if self._config_path.exists() else {}
            
            # Build servers dict from current instances
            servers = {}
            for name, instance in self.instances.items():
                cfg = instance.config
                server_entry: Dict[str, Any] = {"type": cfg.type}
                
                if cfg.type == MCPServerType.STDIO:
                    server_entry["command"] = cfg.command
                    if cfg.env:
                        server_entry["env"] = cfg.env
                elif cfg.type == MCPServerType.HTTP:
                    server_entry["uri"] = cfg.uri
                
                if cfg.description:
                    server_entry["description"] = cfg.description
                if cfg.mount_prefix and cfg.mount_prefix != name:
                    server_entry["mount_prefix"] = cfg.mount_prefix
                
                servers[name] = server_entry
            
            # Update servers section
            existing["servers"] = servers
            
            # Update auto_start list if requested
            auto_start = existing.get("auto_start", [])
            if auto_start_add and auto_start_add not in auto_start:
                auto_start.append(auto_start_add)
                existing["auto_start"] = auto_start
            if auto_start_remove and auto_start_remove in auto_start:
                auto_start.remove(auto_start_remove)
                existing["auto_start"] = auto_start
            
            # Write back as formatted JSON
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=4, ensure_ascii=False)
            
            logger.info(f"Saved MCP config to {self._config_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save MCP config: {e}")
            return False

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
        logger.info(f"DEBUG: MCPManager.load_config called with servers: {list(mcp_servers.keys())}")
        if not mcp_servers:
            logger.warning("DEBUG: mcp_servers is empty/None")
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

    async def add_config(self, config: MCPServerConfig, persist: bool = False, auto_start: bool = False) -> Dict[str, Any]:
        """Add a new MCP server configuration

        Args:
            config: MCPServerConfig instance
            persist: If True, save to mcp.json (default: False)
            auto_start: If True, add to auto_start list in mcp.json (default: False)

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

            result = {
                "success": True,
                "message": f"MCP server '{config.name}' added successfully",
                "name": config.name,
                "persisted": False,
                "auto_start": auto_start,
            }
            
            if persist:
                auto_start_add = config.name if auto_start else None
                result["persisted"] = self._save_config(auto_start_add=auto_start_add)
            
            return result

    async def remove_config(self, name: str, persist: bool = False) -> Dict[str, Any]:
        """Remove an MCP server configuration

        Args:
            name: Server name
            persist: If True, save to mcp.json (default: False)

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

            result = {
                "success": True,
                "message": f"MCP server '{name}' removed successfully",
                "persisted": False,
            }
            
            if persist:
                # Also remove from auto_start list
                result["persisted"] = self._save_config(auto_start_remove=name)
            
            return result

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

        # Check for missing services and reload config if needed
        # This supports dynamic addition of services to mcp.json without restarting everything
        missing = [n for n in names if n not in self.instances]
        if missing:
            from pantheon.settings import get_settings
            logger.info(f"Services {missing} not found, attempting to reload MCP config...")
            try:
                # Reload config from settings
                await self.load_config(get_settings().get_mcp_config())
            except Exception as e:
                logger.warning(f"Failed to reload MCP config: {e}")

        for name in names:
            if name not in self.instances:
                results["errors"].append(f"Service '{name}' not found")
                results["success"] = False
                continue

            instance = self.instances[name]

            if await instance.start():
                # Mount to unified gateway
                try:
                    await self._mount_to_gateway(name, instance)
                    results["started"].append(name)
                    
                    # Background pre-warm MCP to verify it's working
                    # Status will be updated async: starting -> running | error
                    asyncio.create_task(self._prewarm_server(name, instance))
                        
                except Exception as e:
                    logger.error(f"Failed to mount '{name}' to gateway: {e}")
                    instance.status = "error"
                    await instance.stop()
                    results["errors"].append(f"Failed to mount '{name}': {str(e)}")
                    results["success"] = False
            else:
                # Get error from logs
                error_info = "\n".join(instance.logs[-3:]) if instance.logs else "Unknown error"
                results["errors"].append(
                    f"Failed to start '{name}': {error_info}"
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

            logger.debug(
                f"Mounted STDIO server '{name}' to gateway with prefix '{prefix}'"
            )

        elif instance.config.type == MCPServerType.HTTP:
            from fastmcp import Client
            import asyncio as _asyncio

            # Create client for remote HTTP server
            remote_client = Client(instance.config.uri)
            instance.http_client = remote_client  # Store for status checks
            
            # Validate connection before mounting
            async def _validate_connection():
                async with remote_client:
                    # Try to list tools to verify connection
                    await remote_client.list_tools()

            try:
                await _asyncio.wait_for(_validate_connection(), timeout=10)  # 10 second timeout
            except _asyncio.TimeoutError:
                raise RuntimeError(f"Connection timeout: {instance.config.uri} (10s)")
            except Exception as e:
                raise RuntimeError(f"Cannot connect to {instance.config.uri}: {e}")

            # Create proxy wrapping the remote client
            proxy = FastMCP.as_proxy(remote_client, name=name)
            instance.fastmcp_proxy = proxy

            # Mount to gateway
            await self._gateway.mount_server(prefix, proxy, remote_client)
            instance.http_port = self.port

            logger.debug(
                f"Mounted HTTP server '{name}' to gateway with prefix '{prefix}'"
            )

    async def _prewarm_server(self, name: str, instance: MCPServerInstance) -> None:
        """Pre-warm an MCP server by calling list_tools.
        
        This triggers internal initialization (loading dependencies, etc.)
        early in background, reducing first-call latency for agents.
        
        Also serves as health check - updates status:
        - Success: 'starting' -> 'running'
        - Failure: 'starting' -> 'error'
        """
        try:
            import asyncio as _asyncio

            # Give the client a moment to initialize
            await _asyncio.sleep(0.1)

            if instance.stdio_client:
                # Python 3.10 compatible timeout
                async def _prewarm_client():
                    async with instance.stdio_client:
                        await instance.stdio_client.list_tools()

                await _asyncio.wait_for(_prewarm_client(), timeout=30)  # 30 second timeout for startup

            # Success - update status to running
            instance.status = "running"
            logger.info(f"MCP server '{name}' started successfully")

        except Exception as e:
            logger.warning(f"[PREWARM] MCP '{name}' pre-warm failed: {str(e)}")
            
            # Mark server as error since it failed to start properly
            # Detailed error info is available via instance.logs property
            instance.status = "error"
            
            # Try to stop the failed process
            try:
                await instance.stop()
            except Exception:
                pass
    

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
                error_info = "\n".join(instance.logs[-3:]) if instance.logs else "Unknown error"
                results["errors"].append(
                    f"Failed to stop '{name}': {error_info}"
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
        # Gateway URI (where this service is mounted on the gateway)
        gateway_uri = self._get_service_uri(instance)
        
        # For HTTP type, also include the original remote URI from config
        remote_uri = None
        if instance.config.type == MCPServerType.HTTP:
            remote_uri = instance.config.uri

        return {
            "name": name,
            "type": instance.config.type,
            "status": instance.status,
            "uri": gateway_uri,  # Gateway mount point
            "remote_uri": remote_uri,  # Original HTTP server URI (for HTTP type)
            "http_port": instance.http_port
            if instance.config.type == MCPServerType.STDIO
            else None,
            "command": instance.config.command
            if instance.config.type == MCPServerType.STDIO
            else None,
            "description": instance.config.description,
            "logs": instance.logs,  # Recent stderr log lines
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
    async def cleanup(self):
        """Clean up all MCP resources."""
        async with self._lock:
            # Stop all running instances
            running_names = [name for name, inst in self.instances.items() if inst.is_running()]
            if running_names:
                logger.info(f"Stopping {len(running_names)} MCP servers during cleanup...")
                try:
                    await self.stop_services(running_names)
                except Exception as e:
                    logger.error(f"Error stopping MCP services during cleanup: {e}")
            
            # Stop the gateway
            try:
                await self._gateway.stop_gateway()
                logger.info("MCP Gateway stopped")
            except Exception as e:
                logger.error(f"Error stopping MCP gateway: {e}")

            # Clear references
            self.instances.clear()
