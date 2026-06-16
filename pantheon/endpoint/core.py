import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import TypedDict

from executor.engine import Engine, LocalJob


from pantheon.settings import get_settings
from pantheon.toolset import tool
from pantheon.toolsets.file_transfer import FileTransferToolSet
from pantheon.utils.log import log_startup_profile, logger
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
    # IntegratedNotebook streaming control
    enable_notebook_streaming: bool  # Enable NATS streaming for integrated_notebook (default: False)





class Endpoint(FileTransferToolSet):
    def __init__(
        self,
        config: EndpointConfig | None = None,
        workspace_path: str | None = None,
        **kwargs,
    ):
        # Load default config first, then merge with user-provided config
        default_config = self.default_config()
        if config is not None:
            # Merge user config with defaults (user config takes precedence)
            default_config.update(config)
        self.config = default_config
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

        # Generate id_hash if not provided in kwargs or config
        if "id_hash" not in kwargs:
            kwargs["id_hash"] = self.config.get("id_hash") or str(uuid.uuid4())
        self.id_hash = kwargs["id_hash"]

        self.log_dir = get_settings().pantheon_dir / "logs" / "endpoint" / self.id_hash
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.allow_file_transfer = self.config.get("allow_file_transfer", True)

        # Remote engine (will be initialized in run())
        self._remote_engine = None

        # Initialize ToolSet Manager (manages all toolset state and lifecycle)
        self.toolset_manager = ToolSetManager(
            config=self.config,
            id_hash=self.id_hash,
            endpoint_path=Path(workspace_path),
            log_dir=self.log_dir,
            endpoint=self,  # Pass self for remote backend status checking
        )

        # Initialize MCP Pool with log directory
        # Get MCP config directly from settings (not mixed into EndpointConfig)
        settings = get_settings()
        mcp_log_dir = str(settings.pantheon_dir / "logs" / "mcp")
        mcp_config = settings.get_mcp_config()
        # Config path for persistence (project-level .pantheon/mcp.json)
        mcp_config_path = settings.pantheon_dir / "mcp.json"
        self.mcp_manager: MCPManager = MCPManager(
            log_dir=mcp_log_dir,
            port=mcp_config.get("port", 3100),
            host=mcp_config.get("host", "localhost"),
            config_path=mcp_config_path,
        )

        super().__init__(
            name,
            workspace_path,
            black_list=[".endpoint-logs", ".executor"],
            **kwargs,
        )

        # Dual-channel: the Endpoint serves ChatRoom over the primary backend
        # (e.g. local TCP) and the frontend over NATS simultaneously. ToolSet.run
        # honors this when PANTHEON_FRONTEND_BACKEND differs from the primary.
        self._enable_frontend_channel = True

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
        startup_t0 = time.perf_counter()

        # ===== Phase 1: Load MCP Config, pre-set URI, start gateway off critical path =====
        phase1_t0 = time.perf_counter()
        logger.info("Phase 1: Loading MCP config and starting gateway...")
        mcp_config = get_settings().get_mcp_config()
        result = await self.mcp_manager.load_config(mcp_config)
        log_startup_profile(
            "Endpoint phase1 MCP config loaded in "
            f"{time.perf_counter() - phase1_t0:.3f}s "
            f"(servers={len(mcp_config.get('servers', {}))}, "
            f"auto_start={mcp_config.get('auto_start', [])})"
        )
        if result.get("errors"):
            logger.warning(f"MCP configuration loading had errors: {result['errors']}")

        # Pre-set ENDPOINT_MCP_URI immediately — get_unified_uri() is a pure string
        # computation that does not require the gateway to be running.
        unified_uri = self.mcp_manager.get_unified_uri()
        os.environ["ENDPOINT_MCP_URI"] = unified_uri

        # Start gateway and mount endpoint tools as a background task so they don't
        # block NATS subscription. Package Runtime only needs the gateway when a tool
        # call is actually executed (seconds into the session, after the LLM responds),
        # so there is no practical race condition.
        auto_start_mcp = mcp_config.get("auto_start", [])

        async def _wait_for_worker_ready_before_background_startup():
            if getattr(self, "worker", None) is None:
                log_startup_profile(
                    "Endpoint post-ready background startup not waiting for NATS worker "
                    "(no remote worker)"
                )
                return

            wait_t0 = time.perf_counter()
            log_startup_profile(
                "Endpoint post-ready background startup waiting for NATS worker readiness"
            )
            await self._worker_ready.wait()
            log_startup_profile(
                "Endpoint post-ready background startup "
                f"released after worker ready in {time.perf_counter() - wait_t0:.3f}s"
            )

        async def _start_gateway_background():
            gateway_t0 = time.perf_counter()
            try:
                log_startup_profile("Endpoint MCP gateway background begin")
                gateway_start_t0 = time.perf_counter()
                await self.mcp_manager._gateway.start_gateway()
                log_startup_profile(
                    "Endpoint MCP gateway start_gateway finished in "
                    f"{time.perf_counter() - gateway_start_t0:.3f}s"
                )
                logger.info(f"MCP Gateway started at {unified_uri}")
                if auto_start_mcp:
                    logger.info(f"Auto-starting MCP servers: {auto_start_mcp}")
                    mcp_services_t0 = time.perf_counter()
                    result = await self.mcp_manager.start_services(auto_start_mcp)
                    log_startup_profile(
                        "Endpoint MCP auto-start services finished in "
                        f"{time.perf_counter() - mcp_services_t0:.3f}s: {result}"
                    )
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
                endpoint_mcp_t0 = time.perf_counter()
                await self._start_endpoint_mcp_server()
                log_startup_profile(
                    "Endpoint MCP endpoint server mounted in "
                    f"{time.perf_counter() - endpoint_mcp_t0:.3f}s"
                )
                log_startup_profile(
                    "Endpoint MCP gateway background finished in "
                    f"{time.perf_counter() - gateway_t0:.3f}s"
                )
            except Exception as e:
                logger.error(f"MCP gateway background startup failed: {e}")
                log_startup_profile(
                    "Endpoint MCP gateway background failed after "
                    f"{time.perf_counter() - gateway_t0:.3f}s"
                )

        async def _start_post_ready_background():
            await _wait_for_worker_ready_before_background_startup()
            asyncio.create_task(self._warmup_llm_connection())
            await _start_gateway_background()

        # ===== Phase 2: Start Builtin ToolSet Services =====
        phase2_t0 = time.perf_counter()
        builtin_services = self.config.get("builtin_services", [])
        log_startup_profile(
            "Endpoint phase2 starting builtin ToolSet services: "
            f"{builtin_services}"
        )
        result = await self.toolset_manager.start_services(
            builtin_services, local_retries=10, remote_retries=10
        )
        log_startup_profile(
            "Endpoint phase2 builtin ToolSet services finished in "
            f"{time.perf_counter() - phase2_t0:.3f}s: {result}"
        )

        ready_t0 = time.perf_counter()
        ready_checks = 0
        while True:
            ready_checks += 1
            ready = await self.services_ready()
            if ready:
                log_startup_profile(
                    "Endpoint services_ready passed in "
                    f"{time.perf_counter() - ready_t0:.3f}s "
                    f"after {ready_checks} check(s)"
                )
                break
            await asyncio.sleep(1)

        # ===== Phase 3: Health checks are now handled asynchronously =====
        logger.info("Phase 3: MCP servers initialized with async health monitoring")
        log_startup_profile(
            "Endpoint run_setup blocking phases completed in "
            f"{time.perf_counter() - startup_t0:.3f}s"
        )

        # Post-ready background work waits until the Endpoint's NATS worker has
        # subscribed, so optional warmup/gateway startup cannot delay service
        # registration on the critical path.
        asyncio.create_task(_start_post_ready_background())
        log_startup_profile("Endpoint post-ready background task scheduled")

    async def _warmup_llm_connection(self):
        """Best-effort warm of the sandbox→LLM-proxy path at boot.

        A fresh sandbox's first real message pays ~10-13s of cold path: Modal
        egress + proxy ingress + TCP/TLS, PLUS the LiteLLM proxy spinning up the
        *target model's* deployment. This absorbs that here, before the user's
        first message. Two things are load-bearing:

          * Model = ``LLM_WARMUP_MODEL`` (the Hub sets this to the app's leader
            model, e.g. opus for VE). Warming a cheaper/different model does NOT
            warm the leader's per-model deployment — that was why a successful
            haiku warmup still left opus's first call cold at ~8-12s.
          * RETRY. The fresh-sandbox first outbound connection frequently fails
            with APIConnectionError (egress/ingress not ready yet) — that IS the
            cold path. The old ``num_retries=0`` gave up on the first failure and
            warmed nothing; we retry across the cold-egress window until an
            attempt connects and absorbs the cold model-route setup.

        Never raises — boot must not depend on it.
        """
        import time as _time

        try:
            model = os.environ.get("LLM_WARMUP_MODEL")
            if model and model.strip().lower() in {"0", "false", "off", "none", "disabled"}:
                logger.info("[WARMUP] LLM warmup disabled by LLM_WARMUP_MODEL")
                return
            if not model:
                from pantheon.utils.model_selector import get_model_selector

                chain = get_model_selector().resolve_model("low")
                model = chain[0] if chain else None
            if not model:
                logger.warning("[WARMUP] no model resolved; skipping LLM warmup")
                return

            from pantheon.utils.llm import acompletion

            t0 = _time.time()
            last_err = None
            # First 1-2 attempts often hit APIConnectionError before egress/ingress
            # are ready; a later attempt connects and absorbs the ~10-13s cold
            # model-route. Each attempt does num_retries=0 (we own the retry loop)
            # with a per-attempt cap so a genuine hang can't pin the task.
            for attempt in range(1, 9):
                try:
                    await asyncio.wait_for(
                        acompletion(
                            messages=[{"role": "user", "content": "ping"}],
                            model=model,
                            model_params={"max_tokens": 1},
                            num_retries=0,
                        ),
                        timeout=40,
                    )
                    logger.warning(
                        f"[WARMUP] LLM path warmed via {model} in "
                        f"{_time.time() - t0:.2f}s (attempt {attempt})"
                    )
                    return
                except Exception as e:
                    last_err = e
                    logger.info(
                        f"[WARMUP] attempt {attempt} not warm yet "
                        f"({type(e).__name__}); retrying"
                    )
                    await asyncio.sleep(2)
            logger.warning(
                f"[WARMUP] gave up after {_time.time() - t0:.2f}s / 8 attempts; "
                f"last error: {type(last_err).__name__}: {str(last_err)[:120]}"
            )
        except Exception as e:
            # Never let warmup break boot.
            logger.warning(
                f"[WARMUP] LLM warmup non-fatal error: "
                f"{type(e).__name__}: {str(e)[:120]}"
            )

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

            # ===== Parse mcp:* patterns for "start" action =====
            if action == "start" and service_type == "toolset":
                toolset_names = []
                mcp_names = []

                for svc in names:
                    if svc == "mcp":
                        # "mcp" is any/unified gateway, handled separately or already running.
                        # Just ensure it doesn't fall into toolset_names.
                        pass
                    elif svc.startswith("mcp:"):
                        mcp_names.append(svc[4:])  # Extract: "mcp:context7" -> "context7"
                    else:
                        toolset_names.append(svc)

                # Start both toolsets and MCP servers
                results = {"success": True, "started": [], "errors": []}

                if toolset_names:
                    ts_result = await self.toolset_manager.start_services(toolset_names)
                    if not ts_result.get("success"):
                        results["success"] = False
                    results["started"].extend(ts_result.get("started", []))
                    results["errors"].extend(ts_result.get("errors", []))

                if mcp_names:
                    # Async task for MCP to prevent blocking
                    asyncio.create_task(self.mcp_manager.start_services(mcp_names))
                    
                    # Return success immediately for MCP part (fire and return)
                    logger.info(f"Backgrounded MCP startup for: {mcp_names}")
                    results["started"].extend(mcp_names)

                return results

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
                
                # Check for MCP type and background it
                if service_type == "mcp":
                    asyncio.create_task(manager.start_services(names))
                    logger.info(f"Backgrounded MCP startup for: {names}")
                    return {
                        "success": True, 
                        "started": names, 
                        "message": "MCP startup running in background"
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
        """Clean up Endpoint resources (toolsets and MCP servers)"""
        if hasattr(self, "toolset_manager"):
            try:
                await self.toolset_manager.cleanup()
                logger.info("ToolSet Manager cleanup complete")
            except Exception as e:
                logger.error(f"Error during ToolSet Manager cleanup: {e}")
        
        if hasattr(self, "mcp_manager"):
            try:
                await self.mcp_manager.cleanup()
                logger.info("MCP Manager cleanup complete")
            except Exception as e:
                logger.error(f"Error during MCP Manager cleanup: {e}")



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
