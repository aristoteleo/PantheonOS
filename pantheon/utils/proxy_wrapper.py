"""Proxy wrapper builder for ToolsetProxy tools.

This module provides utilities for creating wrapper functions around remote toolset tools,
enabling them to be used as regular Agent tools while handling RPC calls and callbacks.
"""

import json
from typing import Callable, Protocol, Optional, Any, TYPE_CHECKING

from funcdesc import Description

from pantheon.endpoint import ToolsetProxy

from .log import logger
from .misc import run_func

if TYPE_CHECKING:
    from ..agent import Agent


class AgentCallback(Protocol):
    """Protocol for Agent callback interface (dependency inversion).

    This protocol defines the minimal interface that an Agent must provide
    for proxy wrapper callbacks (inner_call mechanism).
    """

    async def run(
        self, msg: Any, allow_transfer: bool = False, update_memory: bool = False
    ) -> Any:
        """Run agent with a message and return response.

        Args:
            msg: Input message
            allow_transfer: Whether to allow agent transfer
            update_memory: Whether to update memory

        Returns:
            Agent response content
        """
        ...

    @property
    def functions(self) -> dict[str, Callable]:
        """Get agent's available functions."""
        ...


# Constants for special parameters
_CTX_VARS_NAME = "context_variables"
_AGENT_RUN_NAME = "agent_run"
_SKIP_PARAMS = [_CTX_VARS_NAME, _AGENT_RUN_NAME]


class ProxyWrapperBuilder:
    """Builder for creating wrapper functions around ToolsetProxy tools.

    This class provides a stateless utility for creating wrapper functions that:
    1. Call remote toolset methods via ToolsetProxy.invoke()
    2. Handle inner_call callbacks (agent.run or local function calls)
    3. Attach proper function metadata (function_descriptions)

    The wrapper is designed to work seamlessly with Agent.tool() for unified
    tool management.
    """

    @staticmethod
    async def wrap_tools(
        proxy: "ToolsetProxy", agent: AgentCallback, chat_id: Optional[str] = None
    ):
        tools = await proxy.list_tools()
        wrapped_tools = {}
        for tool_desc in tools:
            tool_name = tool_desc["name"]
            wrapped_tools[tool_name] = ProxyWrapperBuilder.create_wrapper(
                proxy, tool_desc, agent, chat_id
            )

        return wrapped_tools

    @staticmethod
    def create_wrapper(
        proxy: "ToolsetProxy",
        tool_desc: dict,
        agent_callback: AgentCallback,
        chat_id: Optional[str] = None,
    ) -> Callable:
        """Create a wrapper function for a remote toolset tool.

        Args:
            proxy: ToolsetProxy instance for RPC calls
            tool_desc: Tool description dict (funcdesc format) from proxy.list_tools()
            agent_callback: Agent instance implementing AgentCallback protocol
            chat_id: Optional chat_id to auto-inject (ChatRoom-specific logic)

        Returns:
            Async callable wrapper function with function_descriptions attribute

        Example:
            >>> proxy = ToolsetProxy.from_endpoint(endpoint, "python")
            >>> tools = await proxy.list_tools()
            >>> for tool_desc in tools:
            ...     wrapper = ProxyWrapperBuilder.create_wrapper(
            ...         proxy, tool_desc, agent, chat_id="chat-123"
            ...     )
            ...     agent.tool(wrapper, key=tool_desc["name"])
        """
        tool_name = tool_desc["name"]
        param_names = [inp["name"] for inp in tool_desc.get("inputs", [])]
        has_chat_id_param = "chat_id" in param_names

        # Create async wrapper function
        async def proxy_tool_wrapper(**kwargs):
            """Wrapper that calls through ToolsetProxy.invoke()."""
            # 1. Auto-inject chat_id if needed (ChatRoom-specific logic)
            if has_chat_id_param and "chat_id" not in kwargs and chat_id is not None:
                kwargs["chat_id"] = chat_id
                logger.debug(
                    f"Auto-injected chat_id={chat_id} for remote tool {tool_name}"
                )

            # 2. Parameter filtering (extract remote parameters)
            remote_params = {
                k: v for k, v in kwargs.items() if k in param_names or k in _SKIP_PARAMS
            }

            # 3. Call remote tool via proxy
            result = await proxy.invoke(tool_name, remote_params)

            # 4. Handle inner_call callback mechanism
            # Some toolsets use this to call back to agent or local functions
            if isinstance(result, dict) and "inner_call" in result:
                result = await ProxyWrapperBuilder._handle_inner_call(
                    result, kwargs, agent_callback
                )

            return result

        # Set function metadata
        proxy_tool_wrapper.__name__ = tool_name
        proxy_tool_wrapper.__doc__ = tool_desc.get("doc", "")
        # Filter chat_id from function description if auto-inject is enabled
        filtered_tool_desc = {
            **tool_desc,
            "inputs": [
                inp for inp in tool_desc.get("inputs", []) if inp["name"] != "chat_id"
            ],
        }

        proxy_tool_wrapper.function_descriptions = Description.from_json(
            json.dumps(filtered_tool_desc)
        )

        return proxy_tool_wrapper

    @staticmethod
    async def _handle_inner_call(
        result: dict, kwargs: dict, agent_callback: AgentCallback
    ) -> dict:
        """Handle inner_call callback mechanism."""
        inner_call = result.pop("inner_call")
        name = inner_call["name"]
        args = inner_call["args"]
        result_field = inner_call["result_field"]

        # Case 1: Agent callback (__agent_run__)
        if name == "__agent_run__":
            # Try using injected agent_run callback first
            agent_run_func = kwargs.get(_AGENT_RUN_NAME)
            if agent_run_func:
                callback_result = await agent_run_func(args)
            else:
                # Fallback: use AgentCallback protocol
                logger.info(f"Running agent callback with message: {args}")
                resp = await agent_callback.run(
                    args, allow_transfer=False, update_memory=False
                )
                callback_result = resp.content if hasattr(resp, "content") else resp

            result[result_field] = callback_result

        # Case 2: Local function callback
        else:
            local_func = agent_callback.functions.get(name)
            if local_func:
                callback_result = await run_func(local_func, **args)
                result[result_field] = callback_result
            else:
                raise RuntimeError(
                    f"Local function '{name}' not found in agent functions"
                )

        return result


async def add_remote_toolset(
    agent: "Agent",
    endpoint_service,
    toolset_name: str,
    chat_id: str | None = None,
) -> int:
    """Add remote toolset tools to an agent."""
    from ..endpoint import ToolsetProxy
    from .proxy_wrapper import ProxyWrapperBuilder

    proxy = ToolsetProxy.from_endpoint(endpoint_service, toolset_name)

    wrapped_tools = await ProxyWrapperBuilder.wrap_tools(proxy, agent, chat_id)
    for tool_name, tool in wrapped_tools.items():
        agent.tool(tool, key=tool_name)

    logger.info(
        f"Agent '{agent.name}': Added {len(wrapped_tools.keys())} tools from remote toolset '{toolset_name}'"
        + (f" (chat_id={chat_id})" if chat_id else "")
    )

    return len(tools)
