from pathlib import Path
import asyncio
import yaml
import os
import subprocess

from executor.engine import Engine
from executor.engine.job.extend import SubprocessJob

from ..utils.toolset import ToolSet, tool
from ..utils.log import logger


class EndpointHub(ToolSet):
    def __init__(
        self,
        config_dir: str | Path,
        workspace_base_path: str | Path,
        worker_params: dict | None = None,
    ):
        self.config_dir = Path(config_dir)
        self.endpoint_config_paths: dict[str, str] = {}
        self.endpoint_configs: dict[str, str] = {}
        self.endpoints: dict[str, dict] = {}
        self.load_endpoint_config_paths()
        self.workspace_base_path = Path(workspace_base_path)
        self.workspace_base_path.mkdir(parents=True, exist_ok=True)
        self.engine = Engine()
        self.jobs: dict[str, SubprocessJob] = {}
        super().__init__("endpoint-hub", worker_params=worker_params)

    def load_endpoint_config_paths(self):
        print(self.config_dir)
        for config_file in self.config_dir.glob("*.yaml"):
            with open(config_file, "r") as f:
                self.endpoint_config_paths[config_file.stem] = config_file
                print(f"Add endpoint config: {config_file}")

    @tool
    async def list_configs(self) -> list[str]:
        return list(self.endpoint_configs.keys())

    @tool
    async def get_config(self, config_name: str) -> dict:
        return self.endpoint_configs[config_name]

    @tool
    async def new_endpoint(
        self, id_hash: str, config_name: str = None, custom_config: dict = None
    ) -> dict:
        logger.info(f"New endpoint for id_hash {id_hash}")

        if id_hash in self.endpoints:
            return {
                "success": False,
                "error": f"Endpoint {id_hash} already exists",
            }

        # Use config_name if provided, otherwise use custom_config
        if config_name:
            if config_name not in self.endpoint_config_paths:
                return {
                    "success": False,
                    "error": f"Static config '{config_name}' not found",
                }
            with open(self.endpoint_config_paths[config_name], "r") as f:
                config = yaml.safe_load(f)
        else:
            # Validate custom_config
            if not custom_config or not isinstance(custom_config, dict):
                return {
                    "success": False,
                    "error": f"custom_config not valid: {custom_config}",
                }

            config = custom_config

        # Set common configuration
        config["id_hash"] = id_hash
        workspace_path = self.workspace_base_path / id_hash
        workspace_path.mkdir(parents=True, exist_ok=True)
        config["workspace_path"] = str(workspace_path)
        self.endpoint_configs[id_hash] = config

        # Create log directory and config file
        (workspace_path / ".endpoint-logs").mkdir(parents=True, exist_ok=True)
        tmp_config_file = workspace_path / ".endpoint-logs" / "endpoint_config.yaml"
        with open(tmp_config_file, "w") as f:
            yaml.dump(config, f)
        cmd = (
            f"python -m pantheon.toolsets.endpoint start "
            f"--config-path {tmp_config_file} "
        )
        log_file = workspace_path / ".endpoint-logs" / "endpoint.log"

        # Inherit current environment variables for remote backend and server URLs
        env = os.environ.copy()

        job = SubprocessJob(cmd, retries=10, redirect_out_err=str(log_file), env=env)
        await self.engine.submit_async(job)
        await job.wait_until_status("running")
        await asyncio.sleep(1)
        self.jobs[id_hash] = job
        with open(workspace_path / ".endpoint-logs" / "service_id.txt", "r") as f:
            service_id = f.read().strip()

        logger.info(f"Endpoint: {service_id} started with config: {config}")
        self.endpoints[id_hash] = {
            "service_id": service_id,
            "log_file": str(log_file),
        }
        return {
            "success": True,
            "service_id": service_id,
        }

    @tool
    async def get_endpoint(self, id_hash: str) -> dict:
        endpoint = self.endpoints.get(id_hash)
        logger.info(f"Getting endpoint: id_hash{id_hash}")
        if endpoint:
            return {
                "success": True,
                "service_id": endpoint["service_id"],
            }
        return {
            "success": False,
            "error": f"Endpoint {id_hash} not found",
        }

    async def _cleanup_docker_containers(self, id_hash: str):
        """Clean up Docker containers associated with the endpoint."""
        try:
            # Get all running containers
            result = subprocess.run(
                ["docker", "ps", "-q", "--format", "{{.ID}}:{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.warning(f"Failed to list Docker containers: {result.stderr}")
                return
                
            containers_to_kill = []
            for line in result.stdout.strip().split('\n'):
                if line and ':' in line:
                    container_id, container_name = line.split(':', 1)
                    # Check if container name contains the id_hash
                    if id_hash in container_name:
                        containers_to_kill.append(container_id)
            
            # Kill matching containers
            for container_id in containers_to_kill:
                try:
                    logger.info(f"Stopping Docker container {container_id} for id_hash: {id_hash}")
                    subprocess.run(
                        ["docker", "stop", container_id],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    logger.info(f"Removing Docker container {container_id} for id_hash: {id_hash}")
                    subprocess.run(
                        ["docker", "rm", container_id],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    logger.info(f"Successfully cleaned up Docker container {container_id}")
                except subprocess.TimeoutExpired:
                    logger.warning(f"Timeout stopping Docker container {container_id}, force killing")
                    try:
                        subprocess.run(
                            ["docker", "kill", container_id],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        subprocess.run(
                            ["docker", "rm", container_id],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        logger.info(f"Force killed and removed Docker container {container_id}")
                    except Exception as e:
                        logger.error(f"Failed to force kill Docker container {container_id}: {e}")
                except Exception as e:
                    logger.error(f"Failed to cleanup Docker container {container_id}: {e}")
                    
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout listing Docker containers for id_hash: {id_hash}")
        except Exception as e:
            logger.error(f"Error during Docker cleanup for id_hash {id_hash}: {e}")

    @tool
    async def delete_endpoint(self, id_hash: str) -> dict:
        job = self.jobs.get(id_hash)
        logger.info(f"Deleting endpoint id_hash: {id_hash}")
        if job:
            logger.info(f"Start Deleting endpoint id_hash: {id_hash}")
            endpoint_id = self.endpoints[id_hash]["service_id"]
            
            # Clean up Docker containers first
            await self._cleanup_docker_containers(id_hash)
            
            try:
                await asyncio.wait_for(job.cancel(), timeout=5.0)
                logger.info(f"Job cancelled for endpoint id_hash: {id_hash}")
            except Exception as e:
                logger.warning(
                    f"Job cancel failed/timeout for id_hash: {id_hash} error:{e}, killing subprocess"
                )
                if hasattr(job, "process") and job.process:
                    job.process.kill()
                    logger.info(f"Subprocess killed for id_hash: {id_hash}")

            # Clean up engine
            if hasattr(self.engine, "jobs") and job in self.engine.jobs:
                self.engine.jobs.remove(job)

            # Clean up tracking dictionaries regardless of cancellation success
            del self.jobs[id_hash]
            del self.endpoints[id_hash]
            logger.info(f"Deleted endpoint id_hash:{id_hash} endpoint_id:{endpoint_id}")
            return {
                "success": True,
            }
        else:
            return {
                "success": False,
                "error": f"Endpoint {id_hash} not found",
            }

    async def run(self, log_level: str | None = "INFO"):
        while True:
            try:
                await super().run(log_level)
            except Exception as e:
                logger.error(f"Error running endpoint hub: {e}")
                await asyncio.sleep(1)
                logger.info("Restarting endpoint hub")
