import inspect
import json
import sys
from abc import ABC
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import partial, wraps
from typing import Callable, Optional

from executor.engine import Engine, ProcessJob
from funcdesc import parse_func

import pantheon.utils.log as log
from pantheon.utils.log import logger

from .remote import RemoteBackendFactory
from .utils.misc import run_func


class ExecutionContext(dict):
    """
    Execution context dict for tools.

    Stores metadata like client_id, session_id, and agent_name.
    Both session_id and client_id are stored as explicit keys for compatibility.

    Automatically created and set to contextvars in the @tool decorator.
    """

    async def call_agent(
        self,
        messages: list,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Call the LLM agent during tool execution for intermediate sampling.
        """

        if not self.get("_call_agent"):
            logger.warning(f"No call_agent callback available in context: {self}")
            raise RuntimeError("No call_agent callback available in context")

        try:
            # Call the agent callback with the sampling request
            result = await self["_call_agent"](
                messages=messages,
                system_prompt=system_prompt,
                model=model,
            )

            if isinstance(result, str):
                return result
            elif isinstance(result, dict):
                return result.get("response") or result.get("text") or str(result)
            else:
                return str(result)

        except Exception as e:
            logger.error(f"Error calling agent:{e}")
            raise


# Global ContextVar for implicit access to context_variables within tools
_current_context_variables: ContextVar[Optional[ExecutionContext]] = ContextVar(
    "_current_context_variables", default=ExecutionContext()
)


def get_current_context_variables() -> Optional[ExecutionContext]:
    """Get the current context_variables."""
    return _current_context_variables.get()


def set_current_context_variables(context: ExecutionContext) -> object:
    """Set current context_variables, returns token for later reset"""
    return _current_context_variables.set(context)


def reset_current_context_variables(token: object) -> None:
    """Reset context_variables to previous state"""
    _current_context_variables.reset(token)


def parse_tool_desc(func: Callable) -> dict:
    desc = parse_func(func)
    tool_dict = json.loads(desc.to_json())
    # Remove framework-only params from the cached description
    tool_dict["inputs"] = [
        inp
        for inp in tool_dict.get("inputs", [])
        if inp.get("name") not in ("self", "session_id", "context_variables")
    ]

    return tool_dict


def tool(func: Callable | None = None, *, exclude: bool = False, **kwargs):
    """
    Mark tool in a ToolSet class with automatic context_variables and session_id injection.

    The decorator automatically:
    1. Extracts context_variables from kwargs (passed by Agent)
    2. Converts it to ExecutionContext instance (dict subclass for compatibility)
    3. Injects it into contextvars for the function's execution
    4. Removes context_variables from kwargs if function doesn't declare it as parameter
    5. Cleans up the context after execution

    This allows tools to access context via multiple methods:
    - Explicit parameter: def tool_func(self, arg, context_variables/ctx/context): ...
    - Implicit access: self.get_context_variables() or get_current_context_variables()

    Args:
        exclude: bool
            If True, this tool will not be exposed to LLM agents.
            Useful for tools that are only meant for frontend/API use.
            Default False
        **kwargs: Additional parameters for tool execution

    Example (explicit parameter):
        @tool
        async def my_tool(self, code: str, context_variables/ctx/context: ExecutionContext):
            session_id = context_variables['session_id']
            session_id = context_variables.session_id  # attribute access

        # Example (implicit access):
        @tool
        async def my_tool(self, code: str):
            ctx = self.get_context_variables()
            session_id = ctx.get('session_id')
    """
    if func is None:
        return partial(tool, exclude=exclude, **kwargs)

    # Check function signature for context parameter names
    sig = inspect.signature(func)
    params = sig.parameters

    # Support multiple parameter names for context: context_variables, ctx, or context
    context_param_name = None
    if "context_variables" in params:
        context_param_name = "context_variables"
    elif "ctx" in params:
        context_param_name = "ctx"
    elif "context" in params:
        context_param_name = "context"

    # Unified wrapper for both sync and async functions
    @wraps(func)
    async def wrapper(*args, **func_kwargs):
        # 1. Extract context_variables from kwargs
        context_variables = func_kwargs.pop("context_variables", {})
        # backward compatibility: session_id is now an alias for client_id
        session_id = func_kwargs.pop("session_id", None)
        if session_id:
            context_variables["client_id"] = session_id
        # 2. Convert to ExecutionContext (dict subclass for compatibility)

        context_variables = ExecutionContext(context_variables)

        # 3. If function declares a context parameter, re-inject it with appropriate name
        if context_param_name is not None:
            func_kwargs[context_param_name] = context_variables

        # 4. Set to contextvars for implicit access
        token = set_current_context_variables(context_variables)

        try:
            # Call original function (handles both sync and async via run_func)
            result = await run_func(func, *args, **func_kwargs)
            return result
        finally:
            # 5. Clean up contextvars
            reset_current_context_variables(token)

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

    def get_session_id(self) -> Optional[str]:
        """
        Get current session ID from context (similar to getting HTTP header in MCP)

        Returns:
            Session ID if set in current context, None otherwise
        """
        ctx = get_current_context_variables()
        if ctx:
            return ctx.get("client_id")
        return None

    def get_context(self) -> Optional[ExecutionContext]:
        """Get current execution context"""
        return get_current_context_variables()

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
            log.set_level(log_level)

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
            log.set_level(log_level)
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
