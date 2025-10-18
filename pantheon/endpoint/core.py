import os
import sys
import uuid
import base64
import asyncio
import importlib
from pathlib import Path
from typing import TypedDict, Callable
from enum import Enum

from executor.engine import Engine, LocalJob
from executor.engine.job.extend import SubprocessJob
import yaml

from ..toolset import tool, ToolSet
from ..remote import connect_remote
from ..toolsets.file_transfer import FileTransferToolSet
from ..utils.log import logger


class ToolSetMode(Enum):
    """ToolSet运行模式"""

    REMOTE = "remote"  # 通过remote模块通信（独立进程）
    LOCAL = "local"  # 本地进程内管理（直接调用）


class EndpointConfig(TypedDict):
    service_name: str
    workspace_path: str
    log_level: str
    allow_file_transfer: bool
    builtin_services: list[str | dict]
    # 支持混合模式配置
    service_modes: dict[
        str, str
    ]  # 服务名 -> "local" | "remote"，指定每个服务的运行模式
    # 特殊键 "default" -> 未指定服务的默认模式（默认为"local"）
    # Local toolset 配置
    local_toolset_timeout: int  # Local toolset方法调用的超时时间（秒），默认60
    local_toolset_execution_mode: (
        str  # Local toolset全局执行模式："thread" | "direct"，默认"direct"
    )


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
                "workspace_path", "./.pantheon-chatroom-workspace"
            )

        Path(workspace_path).mkdir(parents=True, exist_ok=True)

        # Switch to workspace directory for this Endpoint instance
        os.chdir(str(workspace_path))

        self.log_dir = Path(workspace_path) / ".endpoint-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Generate id_hash if not provided in kwargs or config
        if "id_hash" not in kwargs:
            kwargs["id_hash"] = self.config.get("id_hash") or str(uuid.uuid4())
        self.id_hash = kwargs["id_hash"]

        self.services: dict[str, dict] = {}
        self.allow_file_transfer = self.config.get("allow_file_transfer", True)
        self.redirect_log = self.config.get("redirect_log", False)
        self._services_to_start: list[str] = []

        # Local toolset management
        self.local_toolsets: dict[str, ToolSet] = {}  # service_id -> ToolSet instance
        self.service_modes: dict[str, str] = self.config.get(
            "service_modes", {}
        )  # service_name -> "local"/"remote"
        # Extract default mode from service_modes, default to "local" if not specified
        self.default_service_mode: str = self.service_modes.get("default", "local")

        # Local toolset execution configuration
        self.local_toolset_timeout = self.config.get("local_toolset_timeout", 60)
        self.local_toolset_execution_mode = self.config.get(
            "local_toolset_execution_mode", "direct"
        )  # "thread" or "direct" - applies to all LOCAL mode toolsets

        # Executor engines for toolset execution
        self._local_engine = (
            Engine()
        )  # For LOCAL mode ThreadJob execution (when using "thread" mode)
        self._remote_engine = (
            None  # For REMOTE mode SubprocessJob execution (created in run())
        )

        logger.info(
            f"Local toolset config: timeout={self.local_toolset_timeout}s, "
            f"execution_mode={self.local_toolset_execution_mode} (global)"
        )

        super().__init__(
            name,
            workspace_path,
            black_list=[".endpoint-logs", ".executor"],
            **kwargs,
        )

    @staticmethod
    def default_config() -> EndpointConfig:
        with open(
            os.path.join(os.path.dirname(__file__), "endpoint.yaml"),
            "r",
            encoding="utf-8",
        ) as f:
            return yaml.safe_load(f)

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

    def _get_tool_method(self, obj, method_name: str, context: str):
        """Get and validate a tool method from an object."""
        if not hasattr(obj, method_name):
            raise Exception(f"Method '{method_name}' not found on {context}")

        method = getattr(obj, method_name)
        if not (hasattr(method, "_is_tool") and method._is_tool):
            raise Exception(f"Method '{method_name}' is not a tool method")

        return method

    @tool
    async def proxy_toolset(
        self,
        method_name: str,
        args: dict | None = None,
        toolset_name: str | None = None,
    ) -> dict:
        """Proxy call to any toolset method in the endpoint or specific toolset.
        Supports both local and remote toolset modes.

        Args:
            method_name: The name of the toolset method to call.
            args: Arguments to pass to the method.
            toolset_name: The name of the specific toolset to call. If None, calls endpoint directly.

        Returns:
            The result from the toolset method call.
        """
        try:
            args = args or {}
            logger.debug(
                f"proxy_toolset: method={method_name}, toolset={toolset_name}, args={args}"
            )

            # Call endpoint method directly
            if not toolset_name:
                logger.info(f"Calling endpoint method: {method_name}")
                method = self._get_tool_method(self, method_name, "endpoint")
                return await method(**args)

            # Call specific toolset method
            logger.debug(f"Calling toolset '{toolset_name}' method: {method_name}")
            service_info = await self.get_service(toolset_name)

            if not service_info:
                raise Exception(
                    f"Toolset '{toolset_name}' not found in endpoint services"
                )

            # Debug logging
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
            logger.error(
                f"Error calling {method_name} on {toolset_name or 'endpoint'}: {e}"
            )
            return {"success": False, "error": str(e)}

    @tool
    async def list_services(self) -> list[dict]:
        res = []
        for s in self.services.values():
            res.append(
                {
                    "name": s["name"],
                    "id": s["id"],
                    "mode": s.get("mode", ToolSetMode.REMOTE).value
                    if isinstance(s.get("mode"), ToolSetMode)
                    else s.get("mode", "remote"),
                }
            )
        return res

    @tool
    async def fetch_image_base64(self, image_path: str) -> dict:
        """Fetch an image and return the base64 encoded image.

        Args:
            image_path: Path to the image file (relative to workspace)

        Returns:
            Dict with success status and either data_uri or error message

        Raises:
            Returns error dict for invalid paths, unsupported formats, or file issues
        """
        # Security: Maximum image size (10MB)
        MAX_IMAGE_SIZE = 10 * 1024 * 1024

        # Security: Validate path doesn't contain directory traversal
        if ".." in image_path:
            return {"success": False, "error": "Image path cannot contain '..'"}

        try:
            i_path = self.path / image_path

            # Security: Check if path is within allowed workspace
            try:
                resolved_path = i_path.resolve()
                allowed_path = self.path.resolve()
                if not str(resolved_path).startswith(str(allowed_path)):
                    return {"success": False, "error": "Path outside allowed workspace"}
            except Exception as e:
                return {"success": False, "error": "Invalid path"}

            # Security: Reject symbolic links
            if resolved_path.is_symlink():
                return {"success": False, "error": "Symbolic links are not allowed"}

            # Check file existence
            if not resolved_path.exists():
                return {"success": False, "error": "Image does not exist"}

            # Check if it's a file (not directory)
            if not resolved_path.is_file():
                return {"success": False, "error": "Path is not a file"}

            # Validate format
            format = resolved_path.suffix.lower()
            if format not in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"]:
                return {
                    "success": False,
                    "error": "Image format must be jpg, jpeg, png, gif, webp, bmp, or svg",
                }

            # Check file size
            try:
                file_size = resolved_path.stat().st_size
                if file_size == 0:
                    return {"success": False, "error": "Image file is empty"}
                if file_size > MAX_IMAGE_SIZE:
                    return {
                        "success": False,
                        "error": f"Image size ({file_size / 1024 / 1024:.1f}MB) exceeds maximum ({MAX_IMAGE_SIZE / 1024 / 1024:.0f}MB)",
                    }
            except OSError as e:
                return {"success": False, "error": f"Cannot access file: {str(e)}"}

            # Encode image to base64
            try:
                with open(resolved_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
            except PermissionError:
                return {"success": False, "error": "Permission denied reading image"}
            except IOError as e:
                return {"success": False, "error": f"IO error reading image: {str(e)}"}

            # Map format to MIME type
            mime_format = format.lstrip('.')
            if mime_format == 'jpg':
                mime_format = 'jpeg'

            data_uri = f"data:image/{mime_format};base64,{b64}"
            return {
                "success": True,
                "image_path": image_path,
                "data_uri": data_uri,
            }

        except Exception as e:
            logger.error(f"Error fetching image base64 for {image_path}: {str(e)}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    @tool
    async def add_service(self, service_id: str):
        """Add a service to the endpoint."""
        try:
            s = await connect_remote(service_id)
            info = await s.fetch_service_info()
            self.services[service_id] = {
                "id": service_id,
                "name": info.service_name,
                "mode": ToolSetMode.REMOTE,  # Remote services
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

    @tool
    async def services_ready(self) -> bool:
        """Check if endpoint and all builtin services are ready.

        Returns:
            True if endpoint setup is completed AND all builtin services are running.
        """
        # First check if endpoint itself is ready
        if not self._setup_completed:
            return False

        # Then check if all builtin services are running
        builtin_services = self.config.get("builtin_services", [])
        for service_name in builtin_services:
            if not await self._is_service_running(service_name):
                logger.debug(
                    f"services_ready: waiting for builtin service '{service_name}'"
                )
                return False

        return True

    @tool
    async def ensure_toolsets(
        self,
        required_toolsets: list[str],
        local_retries: int = 3,
        remote_retries: int = 10,
    ) -> dict:
        """Ensure required toolsets are available, starting them if needed.
        Respects service_modes configuration for local/remote mode selection.

        Args:
            required_toolsets: List of toolset names to ensure are running
            local_retries: Number of retries for local mode services (default: 3)
            remote_retries: Number of retries for remote mode services (default: 10)

        Returns:
            Dict with success status, message, and counts
        """
        try:
            # Filter out already running services
            services_to_start = []
            already_running = []

            for toolset_id in required_toolsets:
                if await self._is_service_running(toolset_id):
                    already_running.append(toolset_id)
                else:
                    services_to_start.append(toolset_id)

            if not services_to_start:
                return {
                    "success": True,
                    "message": f"All {len(required_toolsets)} toolsets already running",
                    "already_running": already_running,
                    "started": 0,
                    "failed": 0,
                }

            logger.info(
                f"Need to start {len(services_to_start)} toolsets: {services_to_start}"
            )

            # Start all services in one batch (will auto-separate by mode internally)
            total_successful, total_failed = await self.start_toolsets_batch(
                services_to_start,
                local_retries=local_retries,
                remote_retries=remote_retries,
            )

            return {
                "success": True,
                "message": f"Started {total_successful} toolsets, {total_failed} failed, {len(already_running)} already running",
                "already_running": already_running,
                "started": total_successful,
                "failed": total_failed,
            }

        except Exception as e:
            logger.error(f"Error ensuring toolsets: {e}")
            return {"success": False, "error": str(e)}

    async def _is_service_running(self, service_name: str) -> bool:
        """Check if a service is currently running."""
        # Check by service name or ID
        for service_info in self.services.values():
            if (
                service_info.get("name") == service_name
                or service_info.get("id") == service_name
            ):
                return True
        return False

    def _parse_service_config(self, service_config) -> tuple[str, dict]:
        """Parse service config into (service_type, params).

        Args:
            service_config: Either a string (service type) or dict with 'type' key

        Returns:
            Tuple of (service_type, params_dict)
        """
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
        """Generate command-line string from toolset arguments.

        Args:
            service_type: The type of toolset
            toolset_args: Arguments dict used for toolset instantiation (same as LOCAL mode)
            params: Original configuration parameters

        Returns:
            Command string for subprocess execution
        """
        cmd_parts = [
            f"python -m pantheon.toolsets start {service_type}",
            # Pass id_hash and endpoint_service_id as kwargs (will be passed to create_worker)
            f"--id-hash {self.id_hash}_{service_type}",
            f"--endpoint-service-id {self.service_id}",
        ]

        # Convert toolset_args to command-line arguments
        # This uses the SAME args prepared by _prepare_toolset_args
        for key, value in toolset_args.items():
            # Convert snake_case to kebab-case for CLI
            cli_key = key.replace("_", "-")
            cmd_parts.append(f"--{cli_key} {value}")

        return " ".join(cmd_parts)

    async def _start_toolset_unified(
        self, service_config, mode: str, retries: int = 3
    ) -> bool:
        """Unified toolset startup for both local and remote modes.

        Args:
            service_config: Service configuration (string or dict)
            mode: "local" or "remote"
            retries: Number of retries for remote mode

        Returns:
            True if startup succeeded, False otherwise
        """
        try:
            # 1. Parse configuration (shared for both modes)
            service_type, params = self._parse_service_config(service_config)
            service_name = params.get("name", service_type)

            # 2. Prepare toolset arguments (shared for both modes)
            toolset_args = self._prepare_toolset_args(service_type, params)

            # 3. Mode-specific execution
            if mode == "local":
                # LOCAL MODE: Instantiate, run setup and register instance locally
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

                # Setup logging and environment
                log_file = self.log_dir / f"{service_type}.log"
                env = os.environ.copy()
                # Store current working directory in environment variable for subprocess
                # Endpoint instance chdir to workspace_path in __init__
                # Subprocess will inherit parent's cwd by default
                env["PANTHEON_WORKSPACE"] = os.getcwd()

                if self.redirect_log:
                    job = SubprocessJob(
                        cmd, retries=retries, redirect_out_err=str(log_file), env=env
                    )
                else:
                    job = SubprocessJob(cmd, retries=retries, env=env)

                # Start the service using endpoint's remote engine
                await self._remote_engine.submit_async(job)

                # Add to services_to_start for tracking
                self._services_to_start.append(service_type)

                # Wait for service registration and detect it
                # Note: We don't wait for job.status=="running" because:
                # 1. wait_until_status uses polling (inefficient)
                # 2. "running" status doesn't guarantee service is registered in NATS
                # Instead, _detect_new_service does retry logic with proper delays
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
            full_hash,  # New NATS backend format (full hash)
            f"{expected_service}_{short_hash}",  # Old format
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

            # Remove from services_to_start list
            if expected_service in self._services_to_start:
                self._services_to_start.remove(expected_service)

            return True
        except Exception:
            return False

    async def _detect_new_service(self, expected_service: str):
        """Detect and register a newly started service."""
        potential_service_ids = self._generate_potential_service_ids(expected_service)

        # Try multiple attempts with delays
        for attempt in range(3):
            for service_id in potential_service_ids:
                if await self._try_connect_service(service_id, expected_service):
                    logger.info(
                        f"Detected service: {service_id} (attempt {attempt + 1})"
                    )
                    return True

            # Wait before retry (except on last attempt)
            if attempt < 2:
                await asyncio.sleep(2)

        logger.warning(f"Could not detect service {expected_service} after 3 attempts")
        return False

    @tool
    async def get_toolset_status(self) -> dict:
        """Get the status of all toolsets (both local and remote)."""
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
            logger.error(f"Error getting toolset status: {e}")
            return {"success": False, "error": str(e)}

    def _get_toolset_class(self, service_type: str):
        """根据service_type动态获取ToolSet类

        自动将 snake_case service_type 转换为 PascalCase ToolSet 类名。
        例如: python_interpreter → PythonInterpreterToolSet
             file_manager → FileManagerToolSet
        """
        import pantheon.toolsets as toolsets

        # Convert snake_case to PascalCase and add ToolSet suffix
        # Handle special capitalization rules for acronyms
        def capitalize_word(word: str) -> str:
            # Special acronym handling: rag → RAG, api → API
            acronyms = {"rag": "RAG", "api": "API"}
            return acronyms.get(word.lower(), word.capitalize())

        class_name = (
            "".join(capitalize_word(word) for word in service_type.split("_"))
            + "ToolSet"
        )

        # Try to get the class from toolsets module
        try:
            return getattr(toolsets, class_name)
        except AttributeError:
            # Fallback: Try case-insensitive matching
            # Get all available toolset classes
            available_classes = [
                name
                for name in dir(toolsets)
                if name.endswith("ToolSet") and not name.startswith("_")
            ]

            # Try to find a case-insensitive match
            for available_class in available_classes:
                if available_class.lower() == class_name.lower():
                    return getattr(toolsets, available_class)

            # If still not found, raise error with suggestions
            raise ValueError(
                f"ToolSet class '{class_name}' not found for service type '{service_type}'. "
                f"Make sure it's exported in pantheon.toolsets.__init__.py. "
                f"Available classes: {', '.join(available_classes)}"
            )

    def _prepare_toolset_args(self, service_type: str, params: dict) -> dict:
        """准备ToolSet实例化所需的参数"""
        service_name = params.get("name", service_type)
        args = {"name": service_name}

        # 根据不同的toolset类型准备特定参数
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
        job = None
        try:
            from executor.engine.job import ThreadJob

            # 创建ThreadJob（自动处理同步和异步方法）
            job = ThreadJob(
                func=method,
                kwargs=args,
                name=f"local_{method.__name__}",
                retries=0,  # 不重试，失败即返回
            )

            # 提交到local engine
            await self._local_engine.submit_async(job)

            # 等待完成（带超时）
            # 使用 job.join() 而非 wait_until() 以避免轮询开销
            # join() 使用 asyncio.wait() 实现真正的事件驱动等待
            await asyncio.wait_for(job.join(), timeout=self.local_toolset_timeout)

            # 检查结果
            if job.status == "done":
                # Success - return result (result is a method, need to call it)
                return job.result()
            elif job.status == "failed":
                # Failed - get exception message (exception is also a method)
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
            # 超时，尝试取消job
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
        """Start multiple toolsets in parallel (automatically handles mixed local/remote modes).

        Args:
            services: List of service configs to start
            local_retries: Number of retries for local mode services (default: 3)
            remote_retries: Number of retries for remote mode services (default: 10)

        Returns:
            Tuple of (successful_count, failed_count)
        """
        if not services:
            return 0, 0

        logger.info(f"Starting {len(services)} toolsets")

        # Separate services by mode based on service_modes configuration
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

        # Start all services in parallel (both local and remote)
        tasks = []

        # Add local service tasks
        for service in local_services:
            task = asyncio.create_task(
                self._start_toolset_unified(service, "local", local_retries)
            )
            tasks.append(task)

        # Add remote service tasks
        for service in remote_services:
            task = asyncio.create_task(
                self._start_toolset_unified(service, "remote", remote_retries)
            )
            tasks.append(task)

        # Wait for all services to start
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count results
        successful = sum(1 for result in results if result is True)
        failed = len(results) - successful

        logger.info(
            f"Toolset startup complete: {successful} successful, {failed} failed "
            f"({len(local_services)} local, {len(remote_services)} remote)"
        )

        # Detailed failure logging
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

    async def cleanup(self):
        """清理Endpoint资源，包括local和remote toolset engines"""
        try:
            if hasattr(self, "_local_engine"):
                self._local_engine.stop()
                logger.info("Local toolset engine stopped")
            if hasattr(self, "_remote_engine") and self._remote_engine is not None:
                self._remote_engine.stop()
                logger.info("Remote toolset engine stopped")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    async def run(self):
        # Setup the endpoint toolset first
        await self.run_setup()

        # Create remote engine for REMOTE mode toolset execution
        self._remote_engine = Engine()

        # Register the endpoint to remote server
        async def run_worker():
            return await super(Endpoint, self).run(self.config.get("log_level", "INFO"))

        job = LocalJob(run_worker)
        await self._remote_engine.submit_async(job)
        await job.wait_until_status("running")

        # Wait a bit more for endpoint is registered
        await asyncio.sleep(3)

        # Report service_id after worker is created
        self.report_service_id()

        # Start all builtin services using ensure_toolsets
        default_services = [
            "rag_manager",
            "python_interpreter",
            "file_manager",
            "web",
        ]
        builtin_services = self.config.get("builtin_services", default_services)
        result = await self.ensure_toolsets(
            builtin_services, local_retries=10, remote_retries=10
        )
        logger.info(
            f"Builtin services startup result: {result.get('message', 'unknown')}"
        )

        while True:
            ready = await self.services_ready()
            if ready:
                logger.info(f"Services are ready!!!")
                break
            await asyncio.sleep(1)

        logger.info(f"Endpoint started: {self.service_id}")

        try:
            await self._remote_engine.wait_async()
        finally:
            # Cleanup on shutdown
            await self.cleanup()


async def wait_endpoint_ready(endpoint_service_id: str):
    s = await connect_remote(endpoint_service_id)
    while True:
        ready = await s.invoke("services_ready")
        logger.info(f"Services are ready: {ready}")
        if ready:
            logger.info(f"Services are ready!!!")
            break
        await asyncio.sleep(1)
