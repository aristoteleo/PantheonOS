from typing import Callable
from functools import partial
import inspect
import sys
from abc import ABC
from contextlib import asynccontextmanager

from executor.engine import Engine, ProcessJob
from .remote import RemoteBackendFactory

from .utils.log import logger


def tool(func: Callable | None = None, *, exclude: bool = False, **kwargs):
    """Mark tool in a ToolSet class

    Args:
        exclude: bool
            If True, this tool will not be exposed to LLM agents.
            Useful for tools that are only meant for frontend/API use.
            Default False
        job_type: "local", "thread" or "process"
            Different job types will be executed in different ways.
            Default "local"
        **kwargs: Additional parameters for tool execution
    """
    if func is None:
        return partial(tool, exclude=exclude, **kwargs)
    func._is_tool = True
    func._exclude = exclude
    func._tool_params = kwargs
    return func


class ToolSet(ABC):
    def __init__(self, name: str, **kwargs):
        self._service_name = name
        self._worker_kwargs = kwargs
        self._setup_completed = False
        self.worker = None
        self._backend = None

        # Collect tool functions internally
        self._functions = {}
        methods = inspect.getmembers(self, inspect.ismethod)
        for name, method in methods:
            if hasattr(method, "_is_tool"):
                _kwargs = getattr(method, "_tool_params", {})
                self._functions[name] = (method, _kwargs)

    @property
    def toolset_name(self):
        return self._service_name

    @property
    def tool_functions(self):
        """Get tool functions available to LLM (exclude=True filtered out)"""
        return {
            name: (method, kwargs)
            for name, (method, kwargs) in self._functions.items()
            if not getattr(method, "_exclude", False)
        }

    @property
    def functions(self):
        """Get all functions (including excluded ones)"""
        return self._functions

    @property
    def service_id(self):
        return self.worker.service_id if self.worker else None

    async def run_setup(self):
        """Setup the toolset before running it. Can be overridden by subclasses."""
        pass

    @tool
    async def list_tools(self) -> dict:
        """List all available tools in this toolset.

        This method is used by ToolsetProxy to discover available tools.
        Uses funcdesc for unified type extraction (same as local tools).
        Named to match MCP's list_tools convention.

        Returns:
            dict: {
                "success": True,
                "tools": [
                    {
                        "name": "method_name",
                        "doc": "Method docstring",
                        "inputs": [
                            {
                                "name": "param_name",
                                "type": {"type": "str"} or {"type": "list", "args": [...]},
                                "default": value,
                                "doc": ""
                            }
                        ]
                    },
                    ...
                ]
            }
        """
        import json
        from funcdesc import parse_func

        tools = []

        # Use tool_functions which already filters out exclude=True tools
        for name, (method, tool_kwargs) in self.tool_functions.items():
            # Skip list_tools itself to avoid recursion
            if name == "list_tools":
                continue

            try:
                # Use funcdesc.parse_func() - same as local tools
                desc = parse_func(method)

                # Serialize using Description's built-in to_json()
                tool_dict = json.loads(desc.to_json())

                tools.append(tool_dict)

            except Exception as e:
                logger.warning(f"Failed to parse tool '{name}': {e}")
                continue

        return {"success": True, "tools": tools}

    async def run(self, log_level: str | None = None):
        if log_level is not None:
            logger.set_level(log_level)

        # Create backend and worker in run method
        self._backend = RemoteBackendFactory.create_backend()
        self.worker = self._backend.create_worker(
            self._service_name, **self._worker_kwargs
        )

        # Register all tools with the worker
        for name, (method, tool_kwargs) in self._functions.items():
            self.worker.register(method, **tool_kwargs)

        # Run custom setup
        await self.run_setup()
        self._setup_completed = True

        logger.info(f"Remote Server: {getattr(self.worker, 'servers', 'N/A')}")
        logger.info(f"Service Name: {self.worker.service_name}")
        logger.info(f"Service ID: {self.service_id}")

        return await self.worker.run()

    def to_mcp(self, mcp_kwargs: dict = {}):
        from fastmcp import FastMCP

        mcp = FastMCP(self._service_name, **mcp_kwargs)
        for method, kwargs in self._functions.values():
            mcp.tool(method)
        return mcp

    async def run_as_mcp(self, log_level: str | None = None, **mcp_kwargs):
        if log_level is not None:
            logger.set_level(log_level)
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
        else:
            await toolset.run()

    fire.Fire(main)
