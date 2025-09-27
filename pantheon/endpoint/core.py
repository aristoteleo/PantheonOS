import os
import sys
import re
import uuid
import base64
import asyncio
from pathlib import Path
from typing import TypedDict

from executor.engine import Engine, LocalJob
from executor.engine.job.extend import SubprocessJob
import yaml

from ..toolset import tool
from ..remote import connect_remote
from ..toolsets.file_transfer import FileTransferToolSet
from ..utils.log import logger


def prepare_docker_env_vars() -> str:
    """Prepare environment variables for Docker container with localhost transformation."""
    relevant_env_vars = [
        "PANTHEON_REMOTE_BACKEND",
        "NATS_SERVERS",
        "MAGIQUE_SERVERS",
        "MAGIQUE_SERVER_URL",
    ]

    def transform_localhost_for_docker(value):
        if value and isinstance(value, str):
            return re.sub(
                r"localhost|127\.0\.0\.1|0\.0\.0\.0",
                "host.docker.internal",
                value,
            )
        return value

    env_vars = []
    for env_var in relevant_env_vars:
        if env_var in os.environ:
            original_value = os.environ[env_var]
            # Apply localhost transformation for server URL variables
            if env_var in ["NATS_SERVERS", "MAGIQUE_SERVERS", "MAGIQUE_SERVER_URL"]:
                transformed_value = transform_localhost_for_docker(original_value)
            else:
                transformed_value = original_value
            env_vars.append(f'-e {env_var}="{transformed_value}"')

    return " ".join(env_vars)


class EndpointConfig(TypedDict):
    service_name: str
    workspace_path: str
    log_level: str
    allow_file_transfer: bool
    builtin_services: list[str | dict]
    outer_services: list[str]
    docker_services: list[str]


class Endpoint(FileTransferToolSet):
    def __init__(
        self,
        config: EndpointConfig | None = None,
    ):
        if config is None:
            config = self.default_config()
        self.config = config
        name = self.config.get("service_name", "pantheon-chatroom-endpoint")
        workspace_path = self.config.get(
            "workspace_path", "./.pantheon-chatroom-workspace"
        )
        Path(workspace_path).mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(workspace_path) / ".endpoint-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.id_hash = self.config.get("id_hash", None)
        worker_params = self.config.get("worker_params", {})
        if self.id_hash is None:
            self.id_hash = str(uuid.uuid4())
        worker_params["id_hash"] = self.id_hash
        self.services: dict[str, dict] = {}
        self.allow_file_transfer = self.config.get("allow_file_transfer", True)
        self.redirect_log = self.config.get("redirect_log", False)
        self._services_to_start: list[str] = []

        # RAG manager will be started as separate process like other services

        super().__init__(
            name,
            workspace_path,
            worker_params,
            black_list=[".endpoint-logs", ".executor"],
        )
        self.report_service_id()

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
        super().setup_tools()

    @tool
    async def proxy_toolset(
        self,
        method_name: str,
        args: dict | None = None,
        toolset_name: str | None = None,
    ) -> dict:
        """Proxy call to any toolset method in the endpoint or specific toolset.

        Args:
            method_name: The name of the toolset method to call.
            args: Arguments to pass to the method.
            toolset_name: The name of the specific toolset to call. If None, calls endpoint directly.

        Returns:
            The result from the toolset method call.
        """
        try:
            if args is None:
                args = {}

            # Add debug logging
            logger.info(
                f"proxy_toolset called: method_name={method_name}, toolset_name={toolset_name}, args={args}"
            )

            if toolset_name is None or toolset_name == "":
                # Call endpoint method directly
                logger.info(f"Calling endpoint method: {method_name}")
                if hasattr(self, method_name):
                    method = getattr(self, method_name)
                    if hasattr(method, "_is_tool") and method._is_tool:
                        result = await method(**args)
                        return result
                    else:
                        raise Exception(f"Method '{method_name}' is not a tool method")
                else:
                    raise Exception(f"Method '{method_name}' not found on endpoint")
            else:
                # Call specific toolset method
                logger.info(f"Calling toolset '{toolset_name}' method: {method_name}")
                service_info = await self.get_service(toolset_name)

                if not service_info:
                    raise Exception(
                        f"Toolset '{toolset_name}' not found in endpoint services"
                    )

                # Connect to the specific toolset service
                from ..remote import connect_remote

                toolset_service = await connect_remote(service_info["id"])

                # Call the method on the toolset
                result = await toolset_service.invoke(method_name, args)
                return result

        except Exception as e:
            logger.error(
                f"Error calling toolset method {method_name} on {toolset_name or 'endpoint'}: {e}"
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
                }
            )
        return res

    @tool
    async def fetch_image_base64(self, image_path: str) -> dict:
        """Fetch an image and return the base64 encoded image."""
        if ".." in image_path:
            return {"success": False, "error": "Image path cannot contain '..'"}
        i_path = self.path / image_path
        if not i_path.exists():
            return {"success": False, "error": "Image does not exist"}
        format = i_path.suffix.lower()
        if format not in [".jpg", ".jpeg", ".png", ".gif"]:
            return {
                "success": False,
                "error": "Image format must be jpg, jpeg, png or gif",
            }
        with open(i_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
            data_uri = f"data:image/{format};base64,{b64}"
        return {
            "success": True,
            "image_path": image_path,
            "data_uri": data_uri,
        }

    @tool
    async def add_service(self, service_id: str):
        """Add a service to the endpoint."""
        try:
            s = await connect_remote(service_id)
            info = await s.fetch_service_info()
            self.services[service_id] = {
                "id": service_id,
                "name": info.service_name,
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
        """Check if all services are ready."""
        # First check if all expected services have been added
        if len(self._services_to_start) > 0:
            return False

        # Then verify that we actually have services running
        if len(self.services) == 0:
            return False

        for service_info in self.services.values():
            # Try to connect to each service to verify it's responsive
            service_id = service_info.get("id")
            if service_id:
                try:
                    # Test basic connectivity
                    await connect_remote(service_id)
                except Exception as e:
                    logger.error(f"Error checking service {service_id}: {e}")
                    # If any service is not responsive, not ready
                    return False

        return True

    @tool
    async def ensure_toolsets(self, required_toolsets: list[str]) -> dict:
        """Ensure required toolsets are available, starting them if needed."""
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
                    "started": [],
                    "failed": []
                }

            logger.info(f"Need to start {len(services_to_start)} toolsets: {services_to_start}")

            # Start services using unified batch logic
            successful, failed = await self.start_toolsets_batch(services_to_start, engine=None, retries=3)

            return {
                "success": True,
                "message": f"Started {successful} toolsets, {failed} failed, {len(already_running)} already running",
                "already_running": already_running,
                "started": successful,
                "failed": failed
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

    async def _start_single_toolset(self, service_config, engine=None, retries: int = 3) -> bool:
        """Start a single toolset service with given configuration."""
        try:
            if isinstance(service_config, str):
                service_type = service_config
                params = {"name": service_config}
            else:
                service_type = service_config.get("type", service_config)
                params = service_config.copy()
                if "type" in params:
                    del params["type"]

            # Generate command using existing logic
            cmd = self._get_cmd(service_type, params)

            # Handle docker and conda environments
            if params.get("docker_image"):
                docker_image_name = params.get("docker_image")
                data_dir = str(self.path.absolute())
                env_flags = prepare_docker_env_vars()
                docker_cmd = (
                    f"docker run "
                    f"{env_flags} "
                    f"--add-host=host.docker.internal:host-gateway "
                    f"-v {data_dir}:/data "
                    f"{docker_image_name}"
                )
                cmd = docker_cmd + " " + cmd
            elif params.get("conda_env"):
                conda_command = params.get("conda_command", "conda")
                cmd = f"{conda_command} run -n {params.get('conda_env')} {cmd}"

            # Setup logging and environment
            log_file = self.log_dir / f"{service_type}.log"
            env = os.environ.copy()

            if self.redirect_log:
                job = SubprocessJob(
                    cmd, retries=retries, redirect_out_err=str(log_file), env=env
                )
            else:
                job = SubprocessJob(cmd, retries=retries, env=env)

            # Handle engine lifecycle
            engine_cleanup_needed = False
            if engine is None:
                from executor.engine import Engine
                engine = Engine()
                engine_cleanup_needed = True

            # Start the service
            await engine.submit_async(job)
            await job.wait_until_status("running")

            # Add to services_to_start for tracking
            self._services_to_start.append(service_type)

            # Wait for service registration and detect it
            await asyncio.sleep(3)
            success = await self._detect_new_service(service_type)

            if success:
                logger.info(f"Successfully started toolset service: {service_type}")
            else:
                logger.warning(f"Service {service_type} started but detection failed")

            # Cleanup engine if we created it
            if engine_cleanup_needed:
                # Engine cleanup is handled automatically
                pass

            return success

        except Exception as e:
            logger.error(f"Failed to start toolset service {service_config}: {e}")
            return False


    async def _detect_new_service(self, expected_service: str):
        """Detect and register a newly started service."""
        try:
            # Map service name for detection
            mapped_service = self._map_toolset_name(expected_service)

            # Try to find the service by connecting to it
            # Generate the same full hash as NATS backend would create
            import hashlib
            id_hash_for_service = f"{self.id_hash}_{expected_service}"
            hash_obj = hashlib.sha256(id_hash_for_service.encode())
            full_hash_id = hash_obj.hexdigest()  # Full hash for new format
            service_id_suffix = hash_obj.hexdigest()[:8]  # Keep short hash for backward compatibility

            potential_service_ids = [
                full_hash_id,                               # New NATS backend format (full hash)
                f"{expected_service}_{service_id_suffix}",  # Old NATS backend format
                f"{mapped_service}_{service_id_suffix}",    # Old NATS backend format with mapped name
                f"{self.id_hash}_{expected_service}",
                f"{self.id_hash}_{mapped_service}",
                expected_service,
                mapped_service,
                f"{expected_service}_{self.id_hash}",
                f"{mapped_service}_{self.id_hash}",
            ]

            # Try multiple attempts with delays
            for attempt in range(3):
                for service_id in potential_service_ids:
                    try:
                        s = await connect_remote(service_id)
                        info = await s.fetch_service_info()

                        if info:
                            self.services[service_id] = {
                                "id": service_id,
                                "name": info.service_name or expected_service,
                            }
                            # Remove from services_to_start list
                            if expected_service in self._services_to_start:
                                self._services_to_start.remove(expected_service)
                            logger.info(
                                f"Detected and registered new service: {service_id} (attempt {attempt + 1})"
                            )
                            return True
                    except Exception as e:
                        # Log only on final attempt to reduce noise
                        if attempt == 2:
                            logger.debug(f"Failed to connect to {service_id}: {e}")
                        continue

                # Wait before retry
                if attempt < 2:
                    await asyncio.sleep(2)

            logger.warning(
                f"Could not detect service {expected_service} after 3 attempts"
            )
            return False

        except Exception as e:
            logger.error(f"Error detecting new service {expected_service}: {e}")
            return False

    @tool
    async def get_toolset_status(self) -> dict:
        """Get the status of all toolsets."""
        try:
            running_services = []
            for service_id, service_info in self.services.items():
                try:
                    # Quick health check
                    await connect_remote(service_id)
                    running_services.append(
                        {
                            "id": service_id,
                            "name": service_info.get("name", service_id),
                            "status": "running",
                        }
                    )
                except:
                    running_services.append(
                        {
                            "id": service_id,
                            "name": service_info.get("name", service_id),
                            "status": "unavailable",
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

    def _map_toolset_name(self, template_name: str) -> str:
        """Map template toolset names to actual toolset startup names."""
        toolset_name_mapping = {
            "r_interpreter": "r",
            "python_interpreter": "python",
            "julia_interpreter": "julia",
            "web_browse": "web",
        }
        return toolset_name_mapping.get(template_name, template_name)

    def _get_cmd(self, service_type: str, params: dict):
        # Map template toolset name to actual startup name
        startup_name = self._map_toolset_name(service_type)

        worker_params_str = f"\"{{'id_hash': '{self.id_hash + '_' + service_type}'}}\""
        cmd = [
            f"python -m pantheon.toolsets start {startup_name}",
            f"--service-name {params.get('name', service_type)}",
            f"--endpoint-service-id {self.service_id}",
            f"--worker-params {worker_params_str}",
        ]

        # Use mapped name for startup logic, but check both original and mapped names for parameters
        if startup_name == "python" or service_type in ["python", "python_interpreter"]:
            cmd.append(f"--workdir {str(self.path)}")
        elif service_type == "file_manager":
            cmd.append(f"--path {str(self.path)}")
        elif service_type == "vector_rag":
            db_path = params.get("db_path")
            if not db_path:
                raise ValueError("db_path is required for vector_rag service")
            if params.get("download_from_huggingface"):
                from ..rag.build import download_from_huggingface

                download_path = params.get("download_path", "tmp/db")
                if not os.path.exists(download_path):
                    logger.info(
                        f"Downloading vector database from Hugging Face to {download_path}"
                    )
                    download_from_huggingface(
                        download_path,
                        params.get("repo_id", "NaNg/pantheon_rag_db"),
                        params.get("filename", "latest.zip"),
                    )
                else:
                    logger.info(f"Vector database already exists in {download_path}")
            cmd.append(f"--db-path {db_path}")
        elif service_type == "workflow":
            # Add workflow path parameter - default to bio workflows
            workflow_path = params.get("workflow_path")
            if workflow_path:
                cmd.append(f"--workflow-path {workflow_path}")
            # Note: If no workflow_path specified, WorkflowToolSet will use default bio_workflows
        _cmd = " ".join(cmd)
        return _cmd

    async def run_builtin_services(self, engine: Engine):
        """Start all builtin services using unified service startup logic."""
        default_services = [
            "ragmanager",
            "python",
            "file_manager",
            "web",
        ]
        builtin_services = self.config.get("builtin_services", default_services)

        # Use batch startup for better performance
        await self.start_toolsets_batch(builtin_services, engine, retries=10)

    async def start_toolsets_batch(self, services: list, engine=None, retries: int = 10):
        """Start multiple toolsets in parallel with unified logic."""
        logger.info(f"Starting {len(services)} toolsets: {[s if isinstance(s, str) else s.get('type', s) for s in services]}")

        # Start all services in parallel
        tasks = []
        for service in services:
            task = asyncio.create_task(
                self._start_single_toolset(service, engine, retries=retries)
            )
            tasks.append(task)

        # Wait for all services to start
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log results
        successful = sum(1 for result in results if result is True)
        failed = len(results) - successful

        logger.info(f"Toolset startup completed: {successful} successful, {failed} failed")

        if failed > 0:
            for i, result in enumerate(results):
                if result is not True:
                    service_name = services[i] if isinstance(services[i], str) else services[i].get('type', services[i])
                    if isinstance(result, Exception):
                        logger.error(f"Service {service_name} failed: {result}")
                    else:
                        logger.warning(f"Service {service_name} startup returned: {result}")

        return successful, failed

    async def add_outer_services(self):
        for service_id in self.config.get("outer_services", []):
            logger.info(f"Adding outer service {service_id}")
            resp = await self.add_service(service_id)
            if not resp["success"]:
                logger.error(
                    f"Failed to add outer service {service_id}: {resp['error']}"
                )

    async def run(self):
        # Setup the endpoint toolset first
        await self.run_setup()

        engine = Engine()

        # Register the endpoint to remote server
        async def run_worker():
            return await super(Endpoint, self).run(self.config.get("log_level", "INFO"))

        job = LocalJob(run_worker)
        await engine.submit_async(job)
        await job.wait_until_status("running")

        # Wait a bit more for endpoint is registered
        await asyncio.sleep(3)

        # Start all services, registering the endpoint to remote server
        await self.run_builtin_services(engine)
        await self.add_outer_services()

        while True:
            ready = await self.services_ready()
            if ready:
                logger.info(f"Services are ready!!!")
                break
            await asyncio.sleep(1)

        logger.info(f"Endpoint started: {self.service_id}")
        await engine.wait_async()


async def wait_endpoint_ready(endpoint_service_id: str):
    s = await connect_remote(endpoint_service_id)
    while True:
        ready = await s.invoke("services_ready")
        logger.info(f"Services are ready: {ready}")
        if ready:
            logger.info(f"Services are ready!!!")
            break
        await asyncio.sleep(1)
