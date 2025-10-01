from typing import Callable
from functools import partial
import inspect
import sys
from abc import ABC
from contextlib import asynccontextmanager

from executor.engine import Engine, ProcessJob
from .remote import RemoteBackendFactory

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
    def tool_functions(self):
        return self._functions

    @property
    def functions(self):
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
        It works for both LOCAL and REMOTE toolsets through proxy_toolset.
        Named to match MCP's list_tools convention.

        Returns:
            dict: {
                "success": True,
                "tools": [
                    {
                        "name": "method_name",
                        "description": "Method docstring summary",
                        "parameters": {
                            "param_name": {
                                "type": "string|integer|number|boolean|array|object|any",
                                "required": True|False,
                                "default": value  # if not required
                            }
                        }
                    },
                    ...
                ]
            }
        """
        tools = []

        for name, (method, tool_kwargs) in self._functions.items():
            # Skip list_tools itself to avoid recursion
            if name == "list_tools":
                continue

            try:
                # Get function signature
                sig = inspect.signature(method)
                doc = inspect.getdoc(method) or ""

                # Parse parameters
                parameters = {}
                for param_name, param in sig.parameters.items():
                    # Skip self/cls
                    if param_name in ["self", "cls"]:
                        continue

                    param_info = {
                        "type": self._get_param_type_str(param),
                        "required": param.default == inspect.Parameter.empty,
                    }

                    # Add default value if exists
                    if param.default != inspect.Parameter.empty:
                        param_info["default"] = param.default

                    parameters[param_name] = param_info

                # Add to tools list
                tools.append(
                    {
                        "name": name,
                        "description": doc if doc else "",
                        "parameters": parameters,
                    }
                )

            except Exception as e:
                logger.warning(f"Failed to extract tool info for '{name}': {e}")
                continue

        return {"success": True, "tools": tools}

    def _get_param_type_str(self, param) -> str:
        """Get parameter type as string."""
        if param.annotation == inspect.Parameter.empty:
            return "any"

        # Simple type mapping
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        # Direct match
        if param.annotation in type_map:
            return type_map[param.annotation]

        # Handle typing module types
        annotation_str = str(param.annotation)
        if "List" in annotation_str or "list" in annotation_str:
            return "array"
        elif "Dict" in annotation_str or "dict" in annotation_str:
            return "object"
        elif "str" in annotation_str:
            return "string"
        elif "int" in annotation_str:
            return "integer"
        elif "float" in annotation_str:
            return "number"
        elif "bool" in annotation_str:
            return "boolean"

        return "any"

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
        for func, _ in self._functions.values():
            mcp.tool(func)
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
