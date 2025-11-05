"""ToolSet Lifecycle Management Module

This module handles all ToolSet-related lifecycle management:
- Service registration and discovery
- Method execution routing (LOCAL direct/thread vs REMOTE)
- Batch toolset startup and detection
- Health monitoring and status reporting
"""

import asyncio
import os
import uuid
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from executor.engine import Engine
from executor.engine.job import ThreadJob
from executor.engine.job.extend import SubprocessJob

from ..remote import connect_remote
from ..toolset import ToolSet, tool
from ..utils.log import logger


class ToolSetMode(Enum):
    """ToolSet execution mode"""

    REMOTE = "remote"  # Via remote module communication (separate process)
    LOCAL = "local"  # In-process management (direct call)


class ToolSetManager:
    """Manages toolset lifecycle for an endpoint instance.

    Responsibilities:
    - Service registration and discovery
    - Method execution routing (LOCAL direct/thread vs REMOTE)
    - Batch toolset startup
    - Health monitoring and status reporting
    """

    def __init__(
        self,
        config: Dict,
        id_hash: str,
        endpoint_path: Path,
        log_dir: Path,
    ):
        """Initialize ToolSet Manager.

        Args:
            config: EndpointConfig dict containing:
                - service_modes: Dict[str, str] - service_name -> "local" | "remote"
                - local_toolset_timeout: int - timeout in seconds (default: 60)
                - local_toolset_execution_mode: str - "thread" | "direct" (default: "direct")
                - redirect_log: bool - whether to redirect logs (default: False)
            id_hash: Unique endpoint identifier
            endpoint_path: Path to endpoint workspace
            log_dir: Path to log directory
        """
        # Service registry (service_id -> service_info)
        self.services: Dict[str, Dict] = {}
        # Local toolset instances (service_id -> toolset_instance)
        self.local_toolsets: Dict[str, ToolSet] = {}

        # Configuration from EndpointConfig (ToolSet-specific settings)
        self.service_modes: Dict[str, str] = config.get("service_modes", {})
        self.default_service_mode: str = self.service_modes.get("default", "local")
        self.local_toolset_timeout: int = config.get("local_toolset_timeout", 60)
        self.local_toolset_execution_mode: str = config.get(
            "local_toolset_execution_mode", "direct"
        )
        self.redirect_log: bool = config.get("redirect_log", False)

        # Execution engines (remote_engine is None at init, set later in Endpoint.run())
        self._local_engine: Engine = Engine()
        self._remote_engine: Optional[Engine] = None

        # Identifiers
        self.id_hash: str = id_hash
        self.path: Path = endpoint_path
        self.log_dir: Path = log_dir

        # State tracking
        self._services_to_start: List[str] = []

        logger.info(
            f"ToolSetManager initialized: timeout={self.local_toolset_timeout}s, "
            f"execution_mode={self.local_toolset_execution_mode}"
        )

    # ========== UTILITY METHODS ==========

    def _get_tool_method(self, obj, method_name: str, context: str):
        """Get and validate a tool method from an object.

        Args:
            obj: Object containing the method
            method_name: Name of the method to retrieve
            context: Context description for error messages

        Returns:
            The method if valid

        Raises:
            Exception: If method not found or not a tool method
        """
        if not hasattr(obj, method_name):
            raise Exception(f"Method '{method_name}' not found on {context}")

        method = getattr(obj, method_name)
        if not (hasattr(method, "_is_tool") and method._is_tool):
            raise Exception(f"Method '{method_name}' is not a tool method")

        return method

    # ========== CORE INTERFACE METHODS ==========

    async def proxy_toolset_method(
        self,
        method_name: str,
        args: Dict | None = None,
        toolset_name: str | None = None,
    ) -> Dict:
        """Proxy call to a toolset method (LOCAL or REMOTE mode).
        Internal method used by Endpoint.proxy_toolset.

        Args:
            method_name: The name of the toolset method to call.
            args: Arguments to pass to the method.
            toolset_name: The name of the specific toolset to call.

        Returns:
            The result from the toolset method call.
        """
        try:
            args = args or {}
            logger.debug(
                f"proxy_toolset_method: method={method_name}, toolset={toolset_name}, args={args}"
            )

            service_info = await self.get_service(toolset_name)

            if not service_info:
                raise Exception(
                    f"Toolset '{toolset_name}' not found in endpoint services"
                )

            logger.debug(
                f"Service info for '{toolset_name}': id={service_info.get('id')}, name={service_info.get('name')}, mode={service_info.get('mode')}"
            )

            # Route based on mode
            if service_info.get("mode") == ToolSetMode.LOCAL:
                # LOCAL mode: use global execution mode setting
                toolset_instance = service_info.get("instance")
                if not toolset_instance:
                    raise Exception(
                        f"No instance found for local toolset '{toolset_name}'"
                    )

                method = self._get_tool_method(
                    toolset_instance, method_name, f"toolset '{toolset_name}'"
                )

                # Use global execution mode for all LOCAL toolsets
                if self.local_toolset_execution_mode == "direct":
                    logger.debug(
                        f"Using LOCAL mode (direct) for {toolset_name}.{method_name}"
                    )
                    return await self._execute_local_method_direct(method, args)
                else:  # "thread"
                    logger.debug(
                        f"Using LOCAL mode (thread) for {toolset_name}.{method_name}"
                    )
                    return await self._execute_local_method(method, args)
            else:
                # REMOTE mode: call via remote service
                logger.debug(f"Using REMOTE mode for {toolset_name}")
                toolset_service = await connect_remote(service_info["id"])
                return await toolset_service.invoke(method_name, args)

        except Exception as e:
            logger.error(f"Error calling {method_name} on {toolset_name}: {e}")
            return {"success": False, "error": str(e)}

    @tool
    async def add_service(self, service_id: str):
        """Add a service to the endpoint."""
        try:
            s = await connect_remote(service_id)
            info = await s.fetch_service_info()
            self.services[service_id] = {
                "id": service_id,
                "name": info.service_name,
                "mode": ToolSetMode.REMOTE,
                "instance": None,
            }
            if service_id in self._services_to_start:
                self._services_to_start.remove(service_id)
            elif info.service_name in self._services_to_start:
                self._services_to_start.remove(info.service_name)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def get_service(self, service_id_or_name: str) -> dict | None:
        """Get a service by id or name."""
        for s in self.services.values():
            if s["id"] == service_id_or_name or s["name"] == service_id_or_name:
                return s
        return None

    async def start_services(
        self,
        required_services: list[str],
        local_retries: int = 3,
        remote_retries: int = 10,
    ) -> dict:
        """Start required ToolSet services (unified interface with MCPManager).
        Respects service_modes configuration for local/remote mode selection.

        Args:
            required_services: List of service names to start
            local_retries: Number of retries for local mode services (default: 3)
            remote_retries: Number of retries for remote mode services (default: 10)

        Returns:
            Dict with success status, started count, and errors
        """
        try:
            # Filter out already running services
            services_to_start = []
            already_running = []

            for service_name in required_services:
                if await self._is_service_running(service_name):
                    already_running.append(service_name)
                else:
                    services_to_start.append(service_name)

            if not services_to_start:
                return {
                    "success": True,
                    "message": f"All {len(required_services)} services already running",
                    "started": [],
                    "errors": [],
                }

            logger.info(
                f"Starting {len(services_to_start)} services: {services_to_start}"
            )

            # Start all services in one batch
            total_successful, total_failed = await self.start_toolsets_batch(
                services_to_start,
                local_retries=local_retries,
                remote_retries=remote_retries,
            )

            return {
                "success": total_failed == 0,
                "started": services_to_start[:total_successful],
                "errors": []
                if total_failed == 0
                else [f"Failed to start {total_failed} services"],
            }

        except Exception as e:
            logger.error(f"Error starting services: {e}")
            return {"success": False, "errors": [str(e)]}

    async def stop_services(self, names: list[str]) -> dict:
        """Stop ToolSet services (unified interface with MCPManager).

        Args:
            names: List of service names to stop

        Returns:
            Dict with success status and errors
        """
        return {"success": False, "errors": ["stop_services not yet implemented"]}

    async def list_services(self) -> dict:
        """List all ToolSet services (unified interface with MCPManager).

        Returns status of all toolsets (both local and remote).

        Returns:
            Dict with success status and services list:
                {
                    "success": bool,
                    "services": [
                        {
                            "id": service_id,
                            "name": service_name,
                            "status": "running" | "unavailable",
                            "mode": "local" | "remote"
                        },
                        ...
                    ],
                    "total_services": int
                }
        """
        try:
            running_services = []
            for service_id, service_info in self.services.items():
                mode = service_info.get("mode", ToolSetMode.REMOTE)
                mode_str = mode.value if isinstance(mode, ToolSetMode) else mode

                # Determine status based on mode
                status = "unavailable"
                try:
                    if mode == ToolSetMode.LOCAL:
                        status = (
                            "running" if service_info.get("instance") else "unavailable"
                        )
                    else:
                        await connect_remote(service_id)
                        status = "running"
                except Exception:
                    status = "unavailable"

                running_services.append(
                    {
                        "id": service_id,
                        "name": service_info.get("name", service_id),
                        "status": status,
                        "mode": mode_str,
                    }
                )

            return {
                "success": True,
                "services": running_services,
                "total_services": len(self.services),
            }
        except Exception as e:
            logger.error(f"Error listing services: {e}")
            return {"success": False, "error": str(e)}

    # ========== INTERNAL HELPER METHODS ==========

    async def _is_service_running(self, service_name: str) -> bool:
        """Check if a service is currently running."""

        for service_info in self.services.values():
            if (
                service_info.get("name") == service_name
                or service_info.get("id") == service_name
            ):
                return True
        return False

    def _parse_service_config(self, service_config) -> tuple[str, dict]:
        """Parse service config into (service_type, params)."""
        if isinstance(service_config, str):
            service_type = service_config
            params = {"name": service_config}
        else:
            service_type = service_config.get("type", service_config)
            params = service_config.copy()
            if "type" in params:
                del params["type"]

        return service_type, params

    def _generate_cmd_from_args(
        self, service_type: str, toolset_args: dict, params: dict
    ) -> str:
        """Generate command-line string from toolset arguments."""
        cmd_parts = [
            f"python -m pantheon.toolsets start {service_type}",
            f"--id-hash {self.id_hash}_{service_type}",
            f"--endpoint-service-id {self.id_hash}",
        ]

        for key, value in toolset_args.items():
            cli_key = key.replace("_", "-")
            cmd_parts.append(f"--{cli_key} {value}")

        return " ".join(cmd_parts)

    async def _start_toolset_unified(
        self, service_config, mode: str, retries: int = 3
    ) -> bool:
        """Unified toolset startup for both local and remote modes."""
        try:
            service_type, params = self._parse_service_config(service_config)
            service_name = params.get("name", service_type)
            toolset_args = self._prepare_toolset_args(service_type, params)

            if mode == "local":
                # LOCAL MODE: Instantiate and register locally
                toolset_class = self._get_toolset_class(service_type)
                toolset_instance = toolset_class(**toolset_args)
                await toolset_instance.run_setup()

                service_id = f"local_{service_name}_{uuid.uuid4().hex[:8]}"
                self.local_toolsets[service_id] = toolset_instance
                self.services[service_id] = {
                    "id": service_id,
                    "name": service_name,
                    "mode": ToolSetMode.LOCAL,
                    "instance": toolset_instance,
                }

                logger.info(f"Started local toolset: {service_name} (id: {service_id})")
                return True

            else:
                # REMOTE MODE: Generate cmd and launch subprocess
                cmd = self._generate_cmd_from_args(service_type, toolset_args, params)

                log_file = self.log_dir / f"{service_type}.log"
                env = os.environ.copy()
                env["PANTHEON_WORKSPACE"] = os.getcwd()

                if self.redirect_log:
                    job = SubprocessJob(
                        cmd, retries=retries, redirect_out_err=str(log_file), env=env
                    )
                else:
                    job = SubprocessJob(cmd, retries=retries, env=env)

                await self._remote_engine.submit_async(job)
                self._services_to_start.append(service_type)

                success = await self._detect_new_service(service_type)

                if success:
                    logger.info(f"Successfully started toolset service: {service_type}")
                else:
                    logger.warning(
                        f"Service {service_type} started but detection failed"
                    )

                return success

        except Exception as e:
            logger.error(
                f"Failed to start toolset {service_config} in {mode} mode: {e}"
            )
            import traceback

            logger.error(traceback.format_exc())
            return False

    def _generate_potential_service_ids(self, expected_service: str) -> list[str]:
        """Generate list of potential service IDs for detection."""
        import hashlib

        id_hash_for_service = f"{self.id_hash}_{expected_service}"
        hash_obj = hashlib.sha256(id_hash_for_service.encode())
        full_hash = hash_obj.hexdigest()
        short_hash = full_hash[:8]

        return [
            full_hash,
            f"{expected_service}_{short_hash}",
            f"{self.id_hash}_{expected_service}",
            expected_service,
            f"{expected_service}_{self.id_hash}",
        ]

    async def _try_connect_service(
        self, service_id: str, expected_service: str
    ) -> bool:
        """Try to connect to a service and register it if successful."""
        try:
            s = await connect_remote(service_id)
            info = await s.fetch_service_info()

            if not info:
                return False

            self.services[service_id] = {
                "id": service_id,
                "name": info.service_name or expected_service,
                "mode": ToolSetMode.REMOTE,
                "instance": None,
            }

            if expected_service in self._services_to_start:
                self._services_to_start.remove(expected_service)

            return True
        except Exception:
            return False

    async def _detect_new_service(self, expected_service: str):
        """Detect and register a newly started service."""
        potential_service_ids = self._generate_potential_service_ids(expected_service)

        for attempt in range(3):
            for service_id in potential_service_ids:
                if await self._try_connect_service(service_id, expected_service):
                    logger.info(
                        f"Detected service: {service_id} (attempt {attempt + 1})"
                    )
                    return True

            if attempt < 2:
                await asyncio.sleep(2)

        logger.warning(f"Could not detect service {expected_service} after 3 attempts")
        return False

    def _get_toolset_class(self, service_type: str):
        """Get ToolSet class by service type (snake_case → PascalCase)."""
        import pantheon.toolsets as toolsets

        def capitalize_word(word: str) -> str:
            acronyms = {"rag": "RAG", "api": "API"}
            return acronyms.get(word.lower(), word.capitalize())

        class_name = (
            "".join(capitalize_word(word) for word in service_type.split("_"))
            + "ToolSet"
        )

        try:
            cls = getattr(toolsets, class_name, None)
            if not cls:
                import importlib

                # try to load from module not exposed in toolsets.__init__.py
                module = importlib.import_module(f"pantheon.toolsets.{service_type}")
                cls = getattr(module, class_name)
            return cls
        except AttributeError:
            available_classes = [
                name
                for name in dir(toolsets)
                if name.endswith("ToolSet") and not name.startswith("_")
            ]

            for available_class in available_classes:
                if available_class.lower() == class_name.lower():
                    return getattr(toolsets, available_class)

            raise ValueError(
                f"ToolSet class '{class_name}' not found for service type '{service_type}'. "
                f"Make sure it's exported in pantheon.toolsets.__init__.py. "
                f"Available classes: {', '.join(available_classes)}"
            )

    def _prepare_toolset_args(self, service_type: str, params: dict) -> dict:
        """Prepare ToolSet instantiation arguments."""
        service_name = params.get("name", service_type)
        args = {"name": service_name}

        if service_type == "python_interpreter":
            args["workdir"] = str(self.path)
        elif service_type == "file_manager":
            args["path"] = str(self.path)
        elif service_type == "vector_rag":
            db_path = params.get("db_path")
            if not db_path:
                raise ValueError("db_path is required for vector_rag service")
            args["db_path"] = db_path
        elif service_type == "workflow":
            workflow_path = params.get("workflow_path")
            if workflow_path:
                args["workflow_path"] = workflow_path
        elif service_type == "rag_manager":
            args["workspace_path"] = str(self.path)

        return args

    async def _execute_local_method_direct(self, method: Callable, args: dict) -> dict:
        """Execute a local method directly with timeout."""
        try:
            result = await asyncio.wait_for(
                method(**args), timeout=self.local_toolset_timeout
            )
            return result
        except asyncio.TimeoutError:
            error_msg = f"Execution timeout after {self.local_toolset_timeout}s"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except Exception as e:
            import traceback

            logger.error(f"Local method execution error: {e}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    async def _execute_local_method(self, method: Callable, args: dict) -> dict:
        """Execute a local method via ThreadJob."""
        job = None
        try:
            job = ThreadJob(
                func=method,
                kwargs=args,
                name=f"local_{method.__name__}",
                retries=0,
            )

            await self._local_engine.submit_async(job)
            await asyncio.wait_for(job.join(), timeout=self.local_toolset_timeout)

            if job.status == "done":
                return job.result()
            elif job.status == "failed":
                exc = job.exception()
                error_msg = str(exc) if exc else "Unknown error"
                logger.error(f"ThreadJob failed: {error_msg}")
                return {"success": False, "error": error_msg}
            else:
                return {
                    "success": False,
                    "error": f"Unexpected job status: {job.status}",
                }

        except asyncio.TimeoutError:
            if job is not None:
                try:
                    await job.cancel()
                except Exception as cancel_error:
                    logger.warning(f"Failed to cancel job: {cancel_error}")

            error_msg = f"Execution timeout after {self.local_toolset_timeout}s"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        except Exception as e:
            import traceback

            logger.error(f"Local method execution error: {e}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    async def start_toolsets_batch(
        self, services: list, local_retries: int = 3, remote_retries: int = 10
    ):
        """Start multiple toolsets in parallel."""
        if not services:
            return 0, 0

        logger.info(f"Starting {len(services)} toolsets")

        local_services = []
        remote_services = []

        for service in services:
            service_name = (
                service
                if isinstance(service, str)
                else service.get("type", service.get("name", ""))
            )
            mode = self.service_modes.get(service_name, self.default_service_mode)

            if mode == "local":
                local_services.append(service)
            else:
                remote_services.append(service)

        tasks = []

        for service in local_services:
            task = asyncio.create_task(
                self._start_toolset_unified(service, "local", local_retries)
            )
            tasks.append(task)

        for service in remote_services:
            task = asyncio.create_task(
                self._start_toolset_unified(service, "remote", remote_retries)
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successful = sum(1 for result in results if result is True)
        failed = len(results) - successful

        logger.info(
            f"Toolset startup complete: {successful} successful, {failed} failed "
            f"({len(local_services)} local, {len(remote_services)} remote)"
        )

        if failed > 0:
            all_services = local_services + remote_services
            for i, result in enumerate(results):
                if result is not True:
                    service_name = (
                        all_services[i]
                        if isinstance(all_services[i], str)
                        else all_services[i].get("type", all_services[i])
                    )
                    if isinstance(result, Exception):
                        logger.error(f"Service {service_name} failed: {result}")
                    else:
                        logger.warning(
                            f"Service {service_name} startup returned: {result}"
                        )

        return successful, failed


__all__ = ["ToolSetMode", "ToolSetManager"]
