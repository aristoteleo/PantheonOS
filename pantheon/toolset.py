import inspect
import sys
import json
from abc import ABC
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import partial, wraps
from typing import Callable, Optional

from executor.engine import Engine, ProcessJob
from funcdesc import parse_func

from .remote import RemoteBackendFactory
from .utils.log import logger
from .utils.misc import run_func

# Global context variable for session_id
current_session_id: ContextVar[Optional[str]] = ContextVar(
    "current_session_id", default=None
)


def parse_tool_desc(func: Callable) -> dict:
    desc = parse_func(func)
    tool_dict = json.loads(desc.to_json())
    # Remove framework-only params from the cached description
    tool_dict["inputs"] = [
        inp
        for inp in tool_dict.get("inputs", [])
        if inp.get("name") not in ("self", "session_id")
    ]

    return tool_dict


def tool(func: Callable | None = None, *, exclude: bool = False, **kwargs):
    """
    Mark tool in a ToolSet class with automatic session_id injection

    The decorator automatically:
    1. Adds a 'session_id' parameter as the last argument
    2. Extracts session_id from kwargs before calling the function
    3. Injects it into contextvars for the function's execution
    4. Cleans up the context after execution

    This allows tools to access session_id via self.get_current_session_id()
    without declaring it as a parameter.

    Args:
        exclude: bool
            If True, this tool will not be exposed to LLM agents.
            Useful for tools that are only meant for frontend/API use.
            Default False
        **kwargs: Additional parameters for tool execution

    Example:
        @tool
        async def execute_cell(self, notebook_path: str, code: str):
            # No need to declare session_id parameter
            session_id = self.get_current_session_id()
            # session_id is automatically available via context
            ...

        # Usage (session_id automatically injected):
        await toolset.execute_cell(
            notebook_path="test.ipynb",
            code="x = 1",
            session_id="chat-123"  # <- automatically injected into context
        )
    """
    if func is None:
        return partial(tool, exclude=exclude, **kwargs)

    # Unified wrapper for both sync and async functions
    @wraps(func)
    async def wrapper(*args, **func_kwargs):
        # Extract session_id from kwargs (not passed to original function)
        session_id = func_kwargs.pop("session_id", None)

        # Set session_id to contextvars
        token = None
        if session_id is not None:
            token = current_session_id.set(session_id)

        try:
            # Call original function (handles both sync and async via run_func)
            result = await run_func(func, *args, **func_kwargs)
            return result
        finally:
            # Clean up context
            if token is not None:
                current_session_id.reset(token)

    # Mark as tool and keep reference to original function
    wrapper._is_tool = True
    wrapper._exclude = exclude
    wrapper._tool_params = kwargs
    tool_desc = None
    try:
        tool_desc = parse_tool_desc(func)
    except Exception:
        pass
    wrapper._tool_desc = tool_desc

    return wrapper


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

    @functions.setter
    def functions(self, value):
        """Set functions dictionary"""
        self._functions = value

    @property
    def service_id(self):
        return self.worker.service_id if self.worker else None

    def get_current_session_id(self) -> Optional[str]:
        """
        Get current session ID from context (similar to getting HTTP header in MCP)

        Returns:
            Session ID if set in current context, None otherwise
        """
        return current_session_id.get()

    async def run_setup(self):
        """Setup the toolset before running it. Can be overridden by subclasses."""
        pass

    @tool(exclude=True)
    async def list_tools(self) -> dict:
        """
        List all available tools in this toolset (for LLM consumption)

        This method is used by ToolsetProxy to discover available tools.
        Uses funcdesc for unified type extraction (same as local tools).
        Named to match MCP's list_tools convention.

        Note:
            - Parses the original function signature (before @tool wrapping)
            - Does NOT include session_id parameter (transparent to LLM)
            - session_id is automatically injected by the framework

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

        tools = []

        # Use tool_functions which already filters out exclude=True tools
        for name, (method, tool_kwargs) in self.tool_functions.items():
            # Skip list_tools itself to avoid recursion
            if name == "list_tools":
                continue

            try:
                cached = getattr(method, "_tool_desc", None)
                if cached is not None:
                    tools.append(cached)
                else:
                    tools.append(parse_tool_desc(method))

            except Exception as e:
                logger.warning(f"Failed to parse tool '{name}': {e}")
                continue

        return {"success": True, "tools": tools}

    async def cleanup(self):
        pass

    async def run(self, log_level: str | None = None, remote: bool = True):
        """
        Run the ToolSet.

        Args:
            log_level: Log level for this run
            remote: Whether to start RemoteWorker and register as service
                - True (default): Start RemoteWorker, register as service, blocking run
                - False: Only run setup, no worker, return immediately

        Returns:
            self: The ToolSet instance
        """
        if log_level is not None:
            logger.set_level(log_level)

        if remote:
            # ===== Remote mode: Start RemoteWorker =====
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
            try:
                await self.worker.run()
            finally:
                # Cleanup on shutdown
                await self.cleanup()
        else:
            # ===== Embed mode: Only setup, no worker =====
            await self.run_setup()
            self._setup_completed = True
            logger.info(
                f"Embed mode initialized: {self._service_name} (service_id={self.service_id})"
            )

        return self

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
