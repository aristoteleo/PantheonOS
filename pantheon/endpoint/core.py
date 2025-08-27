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

from ..utils.toolset import tool
from ..utils.remote import connect_remote
from ..file_transfer import FileTransferToolSet
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
        config: EndpointConfig,
    ):
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
        super().__init__(
            name,
            workspace_path,
            worker_params,
            black_list=[".endpoint-logs", ".executor"],
        )
        self.report_service_id()

    def report_service_id(self):
        with open(self.log_dir / "service_id.txt", "w") as f:
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
            
        # Finally, check if all services are responsive
        try:
            for service_info in self.services.values():
                # Try to connect to each service to verify it's responsive
                service_id = service_info.get("id")
                if service_id:
                    try:
                        # Test basic connectivity
                        await connect_remote(service_id, timeout=2.0)
                    except Exception:
                        # If any service is not responsive, not ready
                        return False
        except Exception:
            # If we can't check services, assume not ready
            return False
            
        return True

    def _get_cmd(self, service_type: str, params: dict):
        worker_params_str = f"\"{{'id_hash': '{self.id_hash + '_' + service_type}'}}\""

        if service_type == "python_interpreter":
            cmd = (
                f"python -m pantheon.toolsets.python "
                f"--service-name {params.get('name', 'python_interpreter')} "
                f"--workdir {str(self.path)} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        elif service_type == "file_manager":
            cmd = (
                f"python -m pantheon.toolsets.file_manager "
                f"--service-name {params.get('name', 'file_manager')} "
                f"--path {str(self.path)} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        elif service_type == "web_browse":
            cmd = (
                f"python -m pantheon.toolsets.web_browse "
                f"--service-name {params.get('name', 'web_browse')} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        elif service_type == "r_interpreter":
            cmd = (
                f"python -m pantheon.toolsets.r "
                f"--service-name {params.get('name', 'r_interpreter')} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        elif service_type == "shell":
            cmd = (
                f"python -m pantheon.toolsets.shell "
                f"--service-name {params.get('name', 'shell')} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        elif service_type == "vector_rag":
            db_path = params.get("db_path")
            if not db_path:
                raise ValueError("db_path is required for vector_rag service")
            if params.get("download_from_huggingface"):
                from ..utils.rag.build import download_from_huggingface

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
            cmd = (
                f"python -m pantheon.toolsets.vector_rag "
                f"--service-name {params.get('name', 'vector_rag')} "
                f"--db-path {db_path} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        elif service_type == "scraper":
            cmd = (
                f"python -m pantheon.toolsets.scraper "
                f"--service-name {params.get('name', 'scraper')} "
                f"--endpoint-service-id {self.service_id} "
                f"--worker-params {worker_params_str}"
            )
        else:
            raise ValueError(f"Unknown service type: {service_type}")
        return cmd

    async def run_builtin_services(self, engine: Engine):
        default_services = [
            "python_interpreter",
            "file_manager",
            "web_browse",
        ]
        builtin_services = self.config.get("builtin_services", default_services)
        jobs = []
        for service in builtin_services:
            if isinstance(service, str):
                service_type = service
                params = {}
            else:
                service_type = service.get("type", service)
                params = service.copy()
                del params["type"]
            cmd = self._get_cmd(service_type, params)
            if params.get("docker_image"):
                docker_image_name = params.get("docker_image")
                data_dir = str(self.path.absolute())

                # Get environment variables for Docker container
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

            self._services_to_start.append(service_type)
            log_file = self.log_dir / f"{service_type}.log"

            # Inherit current environment variables for remote backend and server URLs
            env = os.environ.copy()

            if self.redirect_log:
                job = SubprocessJob(
                    cmd, retries=10, redirect_out_err=str(log_file), env=env
                )
            else:
                job = SubprocessJob(cmd, retries=10, env=env)
            jobs.append(job)

        for job in jobs:
            await engine.submit_async(job)
            await job.wait_until_status("running")
            await asyncio.sleep(1)

    async def add_outer_services(self):
        for service_id in self.config.get("outer_services", []):
            logger.info(f"Adding outer service {service_id}")
            resp = await self.add_service(service_id)
            if not resp["success"]:
                logger.error(
                    f"Failed to add outer service {service_id}: {resp['error']}"
                )

    async def run(self):
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level=self.config.get("log_level", "INFO"))

        # Setup the endpoint toolset first
        await self.run_setup()

        engine = Engine()

        # Start all services BEFORE registering the endpoint with magique
        await self.run_builtin_services(engine)
        await self.add_outer_services()
        
        # Wait a bit more for services to fully initialize
        await asyncio.sleep(3)

        # Only now register the endpoint with magique, making it available for API calls
        async def run_worker():
            return await super(Endpoint, self).run(self.config.get("log_level", "INFO"))

        job = LocalJob(run_worker)
        await engine.submit_async(job)
        await job.wait_until_status("running")
        
        logger.info(f"Endpoint started: {self.service_id}")
        await engine.wait_async()
