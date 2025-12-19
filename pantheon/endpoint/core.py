import asyncio
import os
import uuid
from pathlib import Path
from typing import TypedDict

from executor.engine import Engine, LocalJob


from pantheon.settings import get_settings
from pantheon.toolset import tool
from pantheon.toolsets.file_transfer import FileTransferToolSet
from pantheon.utils.log import logger
from .mcp import MCPManager
from .toolsets import ToolSetManager
from .mcp import MCPServerConfig


class EndpointConfig(TypedDict, total=False):
    """Endpoint configuration.

    Contains both core endpoint settings and delegated manager configurations.
    Manager-specific settings are passed through to their respective managers.
    """

    # ===== Core Endpoint Settings =====
    service_name: str
    workspace_path: str
    log_level: str
    allow_file_transfer: bool
    id_hash: str

    # ===== ToolSet Manager Configuration =====
    # Service startup and mode configuration
    builtin_services: list[str | dict]
    service_modes: dict[str, str]  # service_name -> "local" | "remote"
    # Local toolset execution settings
    local_toolset_timeout: int  # Timeout in seconds (default: 60)
    local_toolset_execution_mode: str  # "thread" | "direct" (default: "direct")




class Endpoint(FileTransferToolSet):
    def __init__(
        self,
        config: EndpointConfig | None = None,
        workspace_path: str | None = None,
        **kwargs,
    ):
        if config is None:
            config = self.default_config()
        self.config = config
        name = self.config.get("service_name", "pantheon-chatroom-endpoint")

        # Priority: parameter > config > default
        if workspace_path is None:
            workspace_path = self.config.get(
                "workspace_path", str(get_settings().pantheon_dir)
            )
        # Convert to absolute path BEFORE chdir to avoid path resolution issues
        workspace_path = str(Path(workspace_path).resolve())
        Path(workspace_path).mkdir(parents=True, exist_ok=True)

        # Switch to workspace directory for this Endpoint instance
        os.chdir(workspace_path)

        self.log_dir = Path(workspace_path) / ".endpoint-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Generate id_hash if not provided in kwargs or config
        if "id_hash" not in kwargs:
            kwargs["id_hash"] = self.config.get("id_hash") or str(uuid.uuid4())
        self.id_hash = kwargs["id_hash"]

        self.allow_file_transfer = self.config.get("allow_file_transfer", True)

        # Remote engine (will be initialized in run())
        self._remote_engine = None

        # Initialize ToolSet Manager (manages all toolset state and lifecycle)
        self.toolset_manager = ToolSetManager(
            config=self.config,
            id_hash=self.id_hash,
            endpoint_path=Path(workspace_path),
            log_dir=self.log_dir,
        )

        # Initialize MCP Pool with log directory and hostname
        mcp_log_dir = str(self.log_dir / "mcp-servers")
        # Get MCP config directly from settings (not mixed into EndpointConfig)
        settings = get_settings()
        mcp_config = settings.get_mcp_config()
        self.mcp_manager: MCPManager = MCPManager(
            log_dir=mcp_log_dir,
            port=mcp_config.get("port", 3100),
            host=mcp_config.get("host", "localhost"),
        )

        super().__init__(
            name,
            workspace_path,
            black_list=[".endpoint-logs", ".executor"],
            **kwargs,
        )

    @staticmethod
    def default_config() -> EndpointConfig:
        """Get default endpoint configuration from Settings."""
        settings = get_settings()
        return settings.get_endpoint_config()

    def report_service_id(self):
        with open(self.log_dir / "service_id.txt", "w", encoding="utf-8") as f:
            f.write(self.service_id)

    def setup_tools(self):
        if not self.allow_file_transfer:
            self.fetch_image_base64._is_tool = False
            self.open_file_for_write._is_tool = False
            self.write_chunk._is_tool = False
            self.close_file._is_tool = False
            self.read_file._is_tool = False

    async def run_setup(self):
        """Setup endpoint before running.

        Unified startup sequence for MCP servers and builtin services.
        """
        # ===== Phase 1: Load MCP Config and Start Gateway =====
        logger.info("Phase 1: Loading MCP config and starting gateway...")
        mcp_config = get_settings().get_mcp_config()
        result = await self.mcp_manager.load_config(mcp_config)
        if result.get("errors"):
            logger.warning(f"MCP configuration loading had errors: {result['errors']}")

        # Always start gateway (for Package Runtime, dynamic server addition, etc.)
        await self.mcp_manager._gateway.start_gateway()
        unified_uri = self.mcp_manager.get_unified_uri()
        os.environ["ENDPOINT_MCP_URI"] = unified_uri
        logger.info(f"MCP Gateway started at {unified_uri}")

        # Auto-start configured MCP servers
        auto_start_mcp = mcp_config.get("auto_start", [])
        if auto_start_mcp:
            logger.info(f"Auto-starting MCP servers: {auto_start_mcp}")
            result = await self.mcp_manager.start_services(auto_start_mcp)
            if not result.get("success"):
                logger.warning(
                    f"Some MCP servers failed to start: {result.get('errors', [])}"
                )
            else:
                logger.info(
                    f"MCP servers started successfully: {result.get('started', [])}"
                )
        else:
            logger.info("No MCP servers configured for auto-start")

        # ===== Phase 2: Start Builtin ToolSet Services =====
        logger.info("Phase 2: Starting builtin ToolSet services...")
        builtin_services = self.config.get("builtin_services", [])
        result = await self.toolset_manager.start_services(
            builtin_services, local_retries=10, remote_retries=10
        )
        logger.info(f"Builtin services startup result: {result}")

        while True:
            ready = await self.services_ready()
            if ready:
                logger.info("All services are ready!")
                break
            await asyncio.sleep(1)

        # ===== Phase 3: Health checks are now handled asynchronously =====
        logger.info("Phase 3: MCP servers initialized with async health monitoring")

        # ===== Phase 4: Expose Endpoint as MCP Server =====
        logger.info("Phase 4: Starting Endpoint MCP server for package API access...")
        await self._start_endpoint_mcp_server()

    async def _start_endpoint_mcp_server(self):
        """Mount Endpoint tools to the unified MCP gateway.

        This allows package API (running in separate Python/shell/Jupyter processes)
        to access Endpoint tools via the unified gateway.

        Note: Gateway is already started in run_setup(). This only mounts tools.
        Endpoint tools are hidden from list_tools but still callable by Package Runtime.
        """
        # Mount Endpoint tools to gateway with 'endpoint_' prefix
        # Use 'internal' tag to mark as hidden (filtered by middleware)
        endpoint_mcp = self.to_mcp(tags={"internal"})
        await self.mcp_manager._gateway.mount_server("endpoint", endpoint_mcp)

        self.endpoint_mcp_port = self.mcp_manager.port
        logger.info(
            f"Endpoint tools mounted to gateway (prefix: endpoint_, hidden)"
        )

    def _get_tool_method(self, obj, method_name: str, context: str):
        """Get and validate a tool method from an object."""
        if not hasattr(obj, method_name):
            raise Exception(f"Method '{method_name}' not found on {context}")

        method = getattr(obj, method_name)
        if not (hasattr(method, "_is_tool") and method._is_tool):
            raise Exception(f"Method '{method_name}' is not a tool method")

        return method

    # ===== ToolSet Management (delegated to ToolSetManager) =====

    @tool
    async def services_ready(self) -> bool:
        """Check if endpoint and all builtin services are ready.

        Returns:
            True if endpoint setup is completed AND all builtin services are running.
        """

        # Then check if all builtin services are running
        builtin_services = self.config.get("builtin_services", [])
        for service_name in builtin_services:
            if not await self.toolset_manager._is_service_running(service_name):
                logger.debug(
                    f"services_ready: waiting for builtin service '{service_name}'"
                )
                return False

        return True

    @tool
    async def proxy_toolset(
        self,
        method_name: str,
        args: dict | None = None,
        toolset_name: str | None = None,
    ) -> dict:
        """Proxy call to endpoint methods or toolset methods.

        Routes to:
        - Endpoint methods when toolset_name is None
        - ToolSet methods when toolset_name is specified (delegates to toolset_manager)

        Args:
            method_name: The name of the method to call
            args: Arguments to pass to the method
            toolset_name: The name of the specific toolset. If None, calls endpoint method.

        Returns:
            The result from the method call
        """
        try:
            args = args or {}
            logger.debug(
                f"proxy_toolset: method={method_name}, toolset={toolset_name}, args={args}"
            )

            # Call endpoint method directly
            if not toolset_name:
                logger.debug(f"Calling endpoint method: {method_name}")
                method = self._get_tool_method(self, method_name, "endpoint")
                return await method(**args)

            # Call toolset method (delegate to toolset_manager)
            logger.debug(f"Calling toolset '{toolset_name}' method: {method_name}")
            return await self.toolset_manager.proxy_toolset_method(
                method_name=method_name,
                args=args,
                toolset_name=toolset_name,
            )

        except Exception as e:
            logger.error(
                f"Error calling {method_name} on {toolset_name or 'endpoint'}: {e}"
            )
            return {"success": False, "error": str(e)}

    # ===== Unified Service Management =====

    @tool
    async def manage_service(
        self,
        action: str,
        service_type: str,
        name: str | list[str] | None = None,
        config: dict | None = None,
    ) -> dict:
        """Unified service management interface for MCP and ToolSet services.

        Args:
            action: "list", "get", "add", "remove", "update", "start", "stop"
            service_type: "mcp" or "toolset"
            name: Service name(s) - string for single, list for multiple (required for most actions)
            config: Service configuration (required for "add" and "update")

        Returns:
            Dict with operation result
        """
        try:
            # Validate service_type
            if service_type not in ("mcp", "toolset"):
                return {
                    "success": False,
                    "error": f"Unknown service_type: {service_type}",
                }

            # Normalize name to list for uniform handling
            names = [name] if isinstance(name, str) else (name if name else [])
            config = config or {}

            # Get the appropriate manager
            manager = (
                self.mcp_manager if service_type == "mcp" else self.toolset_manager
            )

            if action == "list":
                return await manager.list_services()
            elif action == "get":
                if not names:
                    return {"success": False, "error": "name required for 'get' action"}
                srv = await manager.get_service(names[0])
                return srv or {
                    "success": False,
                    "error": f"Service '{names[0]}' not found",
                }
            elif action == "add":
                # Only support add for MCP services
                if not names:
                    return {"success": False, "error": "name required for 'add' action"}
                try:
                    mcp_config = MCPServerConfig(name=names[0], **config)
                    return await self.mcp_manager.add_config(mcp_config)
                except Exception as e:
                    return {"success": False, "error": f"Invalid MCP config: {str(e)}"}
            elif action == "remove":
                # Only support remove for MCP services
                if not names:
                    return {
                        "success": False,
                        "error": "name required for 'remove' action",
                    }
                # Remove one at a time (support batch)
                results = {"success": True, "removed": [], "errors": []}
                for service_name in names:
                    result = await self.mcp_manager.remove_config(service_name)
                    if result.get("success"):
                        results["removed"].append(service_name)
                    else:
                        results["errors"].append(
                            result.get("message", f"Failed to remove {service_name}")
                        )
                        results["success"] = False
                return results
            elif action == "update":
                # Only support update for MCP services
                if not names:
                    return {
                        "success": False,
                        "error": "name required for 'update' action",
                    }
                return await self.mcp_manager.update_config(names[0], config)
            elif action == "start":
                if not names:
                    return {
                        "success": False,
                        "error": "name required for 'start' action",
                    }
                return await manager.start_services(names)
            elif action == "stop":
                if not names:
                    return {
                        "success": False,
                        "error": "name required for 'stop' action",
                    }
                return await manager.stop_services(names)
            elif action == "restart":
                if not names:
                    return {
                        "success": False,
                        "error": "name required for 'restart' action",
                    }
                # Restart one at a time
                results = {"success": True, "restarted": [], "errors": []}
                for service_name in names:
                    result = await self.mcp_manager.restart_service(service_name)
                    if result.get("success"):
                        results["restarted"].append(service_name)
                    else:
                        results["errors"].extend(result.get("errors", []))
                        results["success"] = False
                return results
            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error(f"Error managing {service_type} service: {e}")
            return {"success": False, "error": str(e)}

    async def cleanup(self):
        """Clean up Endpoint resources (local and remote toolset engines)"""
        try:
            if hasattr(self, "toolset_manager"):
                self.toolset_manager._local_engine.stop()
                logger.info("Local toolset engine stopped")
            if hasattr(self, "_remote_engine") and self._remote_engine is not None:
                self._remote_engine.stop()
                logger.info("Remote toolset engine stopped")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


async def wait_endpoint_ready(endpoint_service_id: str):
    from pantheon.remote import connect_remote
    s = await connect_remote(endpoint_service_id)
    while True:
        ready = await s.invoke("services_ready")
        logger.info(f"Services are ready: {ready}")
        if ready:
            logger.info("Services are ready!!!")
            break
        await asyncio.sleep(1)
