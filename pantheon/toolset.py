from typing import Callable
from functools import partial
import inspect
import os
import sys
from abc import ABC
from contextlib import asynccontextmanager

from executor.engine import Engine, ProcessJob
from .remote import RemoteBackendFactory, RemoteConfig
from .remote import connect_remote

from .utils.log import logger


def tool(func: Callable | None = None, **kwargs):
    """Mark tool in a ToolSet class

    Args:
        job_type: "local", "thread" or "process"
            Different job types will be executed in different ways.
            Default "local"
    """
    if func is None:
        return partial(tool, **kwargs)
    func._is_tool = True
    func._tool_params = kwargs
    return func


class ToolSet(ABC):
    def __init__(
        self,
        name: str,
        worker_params: dict | None = None,
        endpoint_service_id: str | None = None,
    ):
        # Setup remote backend configuration using centralized resolver
        # Extract backend and server configuration from worker_params if present
        backend = None
        backend_config = None

        if worker_params is not None:
            backend = worker_params.pop("backend", None)
            server_urls = worker_params.pop("server_urls", None)
            if server_urls:
                backend_config = {"server_urls": server_urls}

        # Use standardized configuration pattern
        config = RemoteConfig.from_config(
            backend=backend, backend_config=backend_config
        )
        backend_instance = RemoteBackendFactory.create_backend(config)

        # Prepare worker parameters (remaining params after extraction)
        worker_kwargs = worker_params or {}

        # Create remote worker synchronously
        self.worker = backend_instance.create_worker(name, **worker_kwargs)
        self._worker_config = config
        self._backend = backend_instance
        self._service_name = name
        self.endpoint_service_id = endpoint_service_id
        self._setup_completed = False

        # Register tools immediately during initialization
        self.setup_tools()

    def setup_tools(self):
        """Register all tool methods with the worker"""
        methods = inspect.getmembers(self, inspect.ismethod)
        for _, method in methods:
            if hasattr(method, "_is_tool"):
                _kwargs = getattr(method, "_tool_params", {})
                self.worker.register(method, **_kwargs)

    @property
    def tool_functions(self):
        return self.worker.functions

    @property
    def service_id(self):
        return self.worker.service_id if self.worker else None

    async def run_setup(self):
        """Setup the toolset before running it."""
        if not self._setup_completed:
            # Tools are already registered in __init__, just mark as completed
            self._setup_completed = True

    async def after_worker_register(self, _):
        """Handle endpoint register after worker register."""
        if self.endpoint_service_id:
            endpoint = await connect_remote(
                self.endpoint_service_id, self.worker.servers
            )
            resp = await endpoint.invoke(
                "add_service",
                {
                    "service_id": self.service_id,
                },
            )
            if not resp["success"]:
                logger.error(f"Failed to add service to endpoint: {resp['error']}")
            else:
                logger.info(
                    f"Added service({self.service_id}) to endpoint({self.endpoint_service_id})"
                )

    async def run_as_hypha_service(self, hypha_server_url, **hypha_kwargs):
        # Create hypha backend config
        backend_config = {"server_url": hypha_server_url, **hypha_kwargs}
        config = RemoteConfig(backend="hypha", backend_config=backend_config)
        backend = RemoteBackendFactory.create_backend(config)

        # Create hypha worker
        worker = backend.create_worker(self._service_name, **hypha_kwargs)

        # Register all tools
        methods = inspect.getmembers(self, inspect.ismethod)
        for _, method in methods:
            if hasattr(method, "_is_tool"):
                worker.register(method)

        logger.info(f"Starting Hypha Service: {worker.service_name}")
        logger.info(f"Service ID: {worker.service_id}")
        await worker.run()

    async def run(self, log_level: str | None = None):
        if log_level is not None:
            logger.remove()
            logger.add(sys.stderr, level=log_level)
        await self.run_setup()
        logger.info(f"Remote Server: {getattr(self.worker, 'servers', 'N/A')}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.service_id}")

        # For MagiqueRemoteWorker, pass the after_register callback
        if hasattr(self.worker, "_worker") and hasattr(self.worker._worker, "run"):
            return await self.worker._worker.run(
                after_register=self.after_worker_register,
            )
        else:
            # For other backends, just run normally
            return await self.worker.run()

    def to_mcp(self, mcp_kwargs: dict = {}):
        from fastmcp import FastMCP

        mcp = FastMCP(self.worker.service_name, **mcp_kwargs)
        for func, _ in self.worker.functions.values():
            mcp.tool(func)
        return mcp

    async def run_as_mcp(self, log_level: str | None = None, **mcp_kwargs):
        if log_level is not None:
            logger.remove()
            logger.add(sys.stderr, level=log_level)
        mcp = self.to_mcp(mcp_kwargs)
        transport = mcp_kwargs.get("transport", "http")
        show_banner = mcp_kwargs.get("show_banner", True)
        await mcp.run_async(transport=transport, show_banner=show_banner)


async def _run_toolset(toolset: ToolSet, log_level: str = "WARNING"):
    await toolset.run(log_level)


@asynccontextmanager
async def run_toolsets(
    toolsets: list[ToolSet],
    engine: Engine | None = None,
    log_level: str = "WARNING",
):
    logger.remove()
    logger.add(sys.stderr, level=log_level)
    if engine is None:
        engine = Engine()
    jobs = []
    for toolset in toolsets:
        job = ProcessJob(
            _run_toolset,
            args=(toolset, log_level),
        )
        jobs.append(job)
    await engine.submit_async(*jobs)
    for job in jobs:
        await job.wait_until_status("running")
    yield
    for job in jobs:
        await job.cancel()
    await engine.wait_async()
    engine.stop()


def toolset_cli(toolset_type: type[ToolSet], default_service_name: str):
    import fire

    async def main(
        service_name: str = default_service_name,
        mcp: bool = False,
        mcp_kwargs: dict = {},
        hypha: bool = False,
        hypha_server_url: str | None = None,
        hypha_kwargs: dict = {},
        **kwargs,
    ):
        """
        Start a toolset.

        Args:
            service_name: The name of the toolset.
            mcp: Whether to run the toolset as an MCP server.
            mcp_kwargs: The keyword arguments for the MCP server.
            toolset_kwargs: The keyword arguments for the toolset.
        """
        toolset = toolset_type(service_name, **kwargs)
        if mcp:
            await toolset.run_as_mcp(**mcp_kwargs)
        elif hypha:
            if hypha_server_url is None:
                hypha_server_url = os.getenv(
                    "HYPHA_SERVER_URL", "https://hypha.aristoteleo.com"
                )
            await toolset.run_as_hypha_service(hypha_server_url, **hypha_kwargs)
        else:
            await toolset.run()

    fire.Fire(main)
