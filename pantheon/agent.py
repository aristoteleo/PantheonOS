import asyncio
import copy
import json
import os
import sys
import time
from typing import Any, Callable, Optional, Union, Protocol
from uuid import uuid4

from funcdesc import Description, Value, parse_func
from pydantic import BaseModel, create_model
from fastmcp import Client
from magique.worker import ReverseCallable

from .toolset import ToolSet

from .remote import (
    RemoteBackendFactory,
    RemoteConfig,
    RemoteWorker,
    RemoteService,
)

from .endpoint import ToolsetProxy

from .memory import Memory
from .utils.llm import (
    acompletion_litellm,
    acompletion_openai,
    acompletion_zhipu,
    process_messages_for_hook_func,
    process_messages_for_model,
    remove_hidden_fields,
)
from .utils.log import logger
from .utils.misc import desc_to_openai_dict, run_func
from .utils.vision import VisionInput, vision_to_openai


DEFAULT_MODEL = "gpt-5-mini"


# Protocol for legacy toolset compatibility (objects with tool_functions attribute)
class ToolsetLike(Protocol):
    """Protocol for objects that can be used as toolsets.

    Any object with a tool_functions attribute is considered toolset-compatible.
    """

    tool_functions: dict[str, tuple[Callable, dict]]


__CTX_VARS_NAME__ = "context_variables"
__AGENT_RUN_NAME__ = "agent_run"
__SKIP_PARAMS__ = [__CTX_VARS_NAME__, __AGENT_RUN_NAME__]
__CLIENT_ID_NAME__ = "client_id"


def _convert_tool_desc_to_description(tool_desc: dict) -> Description:
    """Convert tool description dict to funcdesc.Description.

    Args:
        tool_desc: Tool description dict with format:
            {
                "name": "tool_name",
                "description": "Tool description",
                "parameters": {
                    "param1": {"type": "string", "required": True},
                    ...
                }
            }

    Returns:
        funcdesc.Description object
    """
    parameters = tool_desc.get("parameters", {})

    inputs = []
    for param_name, param_info in parameters.items():
        # Map type string to Python type
        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
            "any": Any,
        }
        param_type = type_map.get(param_info.get("type", "any"), Any)

        # Create Value object
        value = Value(
            type_=param_type,
            name=param_name,
            default=(
                param_info.get("default", None)
                if not param_info.get("required", False)
                else ...
            ),
        )
        inputs.append(value)

    return Description(
        name=tool_desc["name"], inputs=inputs, doc=tool_desc.get("description", "")
    )


class AgentService:
    def __init__(
        self,
        agent: "Agent",
        backend_config: Optional[RemoteConfig] = None,
        worker_params: dict | None = None,
    ):
        self.agent = agent
        # Merge worker parameters
        default_params = {"service_name": f"remote_agent_{self.agent.name}"}
        if worker_params:
            default_params.update(worker_params)
        self.worker_params = default_params

        self.backend = RemoteBackendFactory.create_backend(backend_config)
        self.worker: RemoteWorker = self.backend.create_worker(**self.worker_params)
        self.setup_worker()

    async def response(self, msg, **kwargs):
        resp = await self.agent.run(msg, **kwargs)
        return resp

    async def get_info(self):
        return {
            "name": self.agent.name,
            "instructions": self.agent.instructions,
            "models": self.agent.models,
            "functions_names": list(self.agent.functions.keys()),
            "toolset_proxies_names": list(self.agent.toolset_services.keys()),
        }

    async def get_message_queue(self):
        return await self.agent.events_queue.get()

    async def check_message_queue(self):
        # check if there is a message in the queue
        return not self.agent.events_queue.empty()

    async def add_tool(self, func: Callable):
        self.agent.tool(func)
        return {"success": True}

    def setup_worker(self):
        """Register methods with the worker"""
        self.worker.register(self.response)
        self.worker.register(self.get_info)
        self.worker.register(self.get_message_queue)
        self.worker.register(self.check_message_queue)
        self.worker.register(self.add_tool)

    async def run(self, log_level: str = "INFO"):
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level=log_level)

        logger.info(f"Remote Backend: {self.backend_config.backend}")
        logger.info(f"Service Name: {self.worker_params['service_name']}")
        if hasattr(self.worker, "service_id"):
            logger.info(f"Service ID: {self.worker.service_id}")
        if hasattr(self.worker, "servers"):
            logger.info(f"Remote Servers: {self.worker.servers}")

        return await self.worker.run()


class RemoteAgent:
    def __init__(
        self,
        service_id_or_name: str,
        backend_config: Optional[RemoteConfig] = None,
        **remote_kwargs,
    ):
        self.service_id_or_name = service_id_or_name
        self.backend = RemoteBackendFactory.create_backend(backend_config)
        self.remote_kwargs = remote_kwargs
        self.name = None
        self.instructions = None
        self.model = None
        self.events_queue = RemoteAgentMessageQueue(self)

    async def _connect(self):
        return await self.backend.connect(self.service_id_or_name, **self.remote_kwargs)

    async def fetch_info(self):
        service = await self._connect()
        info = await service.invoke("get_info")
        self.name = info["name"]
        self.instructions = info["instructions"]
        self.models = info["models"]
        self.functions_names = info["functions_names"]
        self.toolset_proxies_names = info["toolset_proxies_names"]
        await service.close()
        return info

    async def run(self, msg: "AgentInput", **kwargs):
        await self.fetch_info()
        service = await self._connect()
        try:
            return await service.invoke("response", {"msg": msg, **kwargs})
        finally:
            await service.close()

    async def tool(self, func: Callable):
        service = await self._connect()
        try:
            func_arg = {"func": func}
            await service.invoke("add_tool", func_arg)
        finally:
            await service.close()

    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        await self.fetch_info()
        from ..repl.core import Repl

        repl = Repl(self)
        await repl.run(message)


class RemoteAgentMessageQueue:
    def __init__(self, agent: "RemoteAgent"):
        self.agent = agent

    async def get(self, interval: float = 0.2):
        service = await self.agent._connect()
        try:
            while True:
                res = await service.invoke("check_message_queue")
                if res:
                    return await service.invoke("get_message_queue")
                await asyncio.sleep(interval)
        finally:
            await service.close()


class ResponseDetails(BaseModel):
    """
    The ResponseDetails class is used to store the details of the agent response.

    Args:
        messages: The messages of the agent response.
        context_variables: The context variables of the agent response.
    """

    messages: list[dict]
    context_variables: dict


class AgentResponse(BaseModel):
    """
    The AgentResponse class is used to store the agent response.

    Args:
        agent_name: The name of the agent.
        content: The main final content of the agent response.
        details: The details of the agent response, which contains the history of the agent response.
        interrupt: Whether the agent is interrupted.
    """

    agent_name: str
    content: Any
    details: ResponseDetails | None
    interrupt: bool = False


class AgentTransfer(BaseModel):
    """
    The AgentTransfer class is used to transfer the agent response to another agent.

    Args:
        from_agent: The name of the agent that is transferring.
        to_agent: The name of the agent that is receiving the transfer.
        history: The history of the agent response.
        context_variables: The context variables of the agent response.
        init_message_length: The length of the initial message.
    """

    from_agent: str
    to_agent: str
    history: list[dict]
    context_variables: dict
    init_message_length: int


AgentInput = (
    str
    | BaseModel
    | AgentResponse
    | list[str | BaseModel | dict]
    | AgentTransfer
    | VisionInput
)


class StopRunning(Exception):
    pass


class Agent:
    """
    The Agent class is the core component of Pantheon,
    providing a flexible interface for creating AI-powered agents with tools,
    memory, and collaboration capabilities.

    Args:
        name: The name of the agent.
        instructions: The instructions for the agent.
            The instructions are the system instructions that the agent will follow.
        model: The model to use for the agent.
            Can be a single model or list of fallback models.
        icon: The icon to use for the agent.
        tools: The tools to use for the agent.
        response_format: The response format to use for the agent.
            It can be a Pydantic model or a function that returns a Pydantic model.
        use_memory: Whether to use memory for the agent. (default: True)
        memory: The memory to use for the agent.
            If not provided, a new memory will be created.
        tool_timeout: The timeout for the tool. (default: 10 minutes)
        force_litellm: Whether to force using LiteLLM. (default: False)
        max_tool_content_length: The maximum length of the tool content. (default: 100000)
        max_consecutive_messages: Max consecutive assistant messages without tools to prevent API loops. (default: 2)
        max_total_messages: Max total assistant messages per conversation to prevent excessive API usage. (default: 10)
    """

    def __init__(
        self,
        name: str,
        instructions: str,
        model: str | list[str] = DEFAULT_MODEL,
        icon: str = "🤖",
        tools: list[Callable] | None = None,
        response_format: Any | None = None,
        use_memory: bool = True,
        memory: Memory | None = None,
        tool_timeout: int = 10 * 60,
        force_litellm: bool = False,
        max_tool_content_length: int | None = 100000,
        # Safety limits to prevent API drain
        max_consecutive_messages: int = 2,
        max_total_messages: int = 20,
    ):
        self.id = uuid4()
        self.name = name
        self.instructions = instructions
        if isinstance(model, str):
            self.models = [model]
            if model != DEFAULT_MODEL:
                self.models.append(DEFAULT_MODEL)
        else:
            self.models = model
        self.functions: dict[str, Callable] = {}
        # NOTE: toolset_services kept for cleanup/compatibility, but functions are now unified in self.functions
        self.toolset_services: dict[str, RemoteService] = {}
        # NOTE: _func_to_proxy is now obsolete with unified approach, but kept for backwards compatibility
        self._func_to_proxy: dict[str, str] = {}

        # Lazy loading for ToolsetProxy
        self.toolset_proxies: dict[str, "ToolsetProxy"] = {}  # Proxy references
        self._proxy_loaded: set[str] = set()  # Track loaded proxies

        # Performance optimization: Cache tool definitions
        self._tool_definitions_cache: dict[str, dict] = {}
        self._cache_dirty = True

        if tools:
            for func in tools:
                self.tool(func)
        self.response_format = response_format
        self.use_memory = use_memory
        self.memory = memory or Memory(str(uuid4()))
        self.tool_timeout = tool_timeout
        self.events_queue: asyncio.Queue = asyncio.Queue()
        self.force_litellm = force_litellm
        self.icon = icon
        self.max_tool_content_length = max_tool_content_length
        # Safety limits to prevent API drain
        self.max_consecutive_messages = max_consecutive_messages
        self.max_total_messages = max_total_messages
        self.enhanced_flow = False

    def tool(self, func: Callable, key: str | None = None):
        """
        Add a tool to the agent.

        Args:
            func: The tool function to add to the agent.
            key: The key to use for the tool. If not provided, the name of the function will be used.

        Returns:
            The agent instance.
        """
        if key is None:
            if hasattr(func, "__name__"):
                key = func.__name__
            elif hasattr(func, "name"):
                key = func.name
            else:
                raise ValueError(f"Invalid tool: {func}")
        self.functions[key] = func
        # Mark cache as dirty when tools are added
        self._cache_dirty = True
        return self

    def enable_rich_conversations(self):
        """Enable rich conversation flow"""
        self.enhanced_flow = True
        return self

    def disable_rich_conversations(self):
        """Disable rich conversation flow"""
        self.enhanced_flow = False
        return self

    async def toolset(
        self, toolset: Union["ToolSet", "ToolsetProxy", "ToolsetLike"]
    ) -> "Agent":
        """Add a toolset to the agent.

        Supports three types of toolsets:
        1. ToolSet instance (local toolset) - loaded immediately
        2. ToolsetProxy instance (remote toolset) - lazy loaded on first conversation
        3. Any object with tool_functions attribute - legacy compatibility

        For ToolsetProxy, this method only stores the proxy reference.
        Tools are loaded lazily during the first conversation via _ensure_proxy_tools_loaded().

        Args:
            toolset: The toolset to add. Accepted types:
                - ToolSet: Local toolset instance (loaded immediately)
                - ToolsetProxy: Remote toolset proxy (lazy loading)
                - ToolsetLike: Any object with tool_functions attribute (legacy)

        Returns:
            Agent: The agent instance for method chaining.

        Raises:
            TypeError: If toolset is not one of the accepted types.

        Example:
            ```python
            # Local toolset (immediate loading)
            from pantheon.toolset import ToolSet
            local_ts = ToolSet()
            await agent.toolset(local_ts)

            # Remote toolset (lazy loading)
            from pantheon.endpoint import ToolsetProxy
            proxy = ToolsetProxy.from_toolset("service_id")
            await agent.toolset(proxy)  # ← Instant return

            # Tools loaded on first conversation
            await agent.run("Hello")
            ```
        """
        # Check if it's a ToolsetProxy
        if isinstance(toolset, ToolsetProxy):
            # Lazy loading: only store proxy reference, don't load tools yet
            toolset_name = toolset.toolset_name

            # Store proxy (no await, instant return)
            self.toolset_proxies[toolset_name] = toolset

            logger.info(
                f"Agent '{self.name}' registered toolset '{toolset_name}' "
                f"(lazy loading - tools will be loaded on first conversation)"
            )

        # Check if it's a regular ToolSet or compatible object
        elif hasattr(toolset, "tool_functions"):
            # Use local ToolSet pattern - load immediately
            for name, (func, _) in toolset.tool_functions.items():
                self.tool(func, key=name)
            # Cache will be marked dirty by individual tool() calls
        else:
            raise TypeError(
                f"Invalid toolset type: {type(toolset)}. "
                "Expected ToolSet, ToolsetProxy, or object with tool_functions."
            )

        return self

    async def mcp(self, client: Client):
        """Add a MCP toolset to the agent.

        Args:
            client: The FastMCP client to add to the agent.

        Returns:
            The agent instance.
        """
        tools = await client.list_tools()
        for tool in tools:

            async def _wrap_tool(**kwargs):
                res = await client.call_tool(tool.name, kwargs)
                return res.structured_output["result"]

            params = tool.inputSchema
            params["additionalProperties"] = False
            _wrap_tool.__schema__ = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "strict": True,
                    "parameters": params,
                },
            }
            self.tool(_wrap_tool, key=tool.name)
        return self

    async def _ensure_proxy_tools_loaded(self):
        """Ensure all ToolsetProxy tools are loaded (lazy loading on first conversation).

        This method is called before each conversation to:
        1. Load tools from proxies that haven't been loaded yet
        2. Retry loading from proxies that failed before (auto-healing)
        3. Create wrapper functions and add them to self.functions

        This is idempotent - already loaded proxies are skipped.
        Failed proxies are logged but don't block agent operation.
        """
        if not self.toolset_proxies:
            return  # No proxies registered

        for toolset_name, proxy in self.toolset_proxies.items():
            # Skip if already loaded
            if toolset_name in self._proxy_loaded:
                continue

            try:
                # Attempt to load tools (uses proxy's cache if available)
                logger.debug(f"Loading tools from toolset '{toolset_name}'...")
                tools = await proxy.list_tools()

                # Create wrapper for each tool and add to self.functions
                for tool_desc in tools:
                    tool_name = tool_desc["name"]
                    wrapper = self._create_proxy_wrapper(proxy, tool_desc)
                    self.tool(wrapper, key=tool_name)

                # Mark as loaded
                self._proxy_loaded.add(toolset_name)
                logger.info(
                    f"Successfully loaded {len(tools)} tools from toolset '{toolset_name}'"
                )

            except Exception as e:
                # Failed to load - skip this toolset, will retry next conversation
                logger.warning(
                    f"Failed to load toolset '{toolset_name}': {e}. "
                    f"Tools from this toolset will not be available for this conversation. "
                    f"Will retry on next conversation."
                )
                continue  # Don't block other toolsets

    def _create_proxy_wrapper(self, proxy: "ToolsetProxy", tool_desc: dict):
        """Create a wrapper function for a proxy tool.

        This wrapper:
        1. Calls proxy.invoke() when invoked
        2. Has function_descriptions attribute for _convert_functions()
        3. Handles errors gracefully (proxy.invoke() returns error dict)
        4. Handles inner_call callback mechanism (for agent_run and local functions)

        Args:
            proxy: ToolsetProxy instance
            tool_desc: Tool description dict from proxy.list_tools()

        Returns:
            Callable wrapper function with function_descriptions attribute
        """
        tool_name = tool_desc["name"]
        agent_context = self  # Reference to agent for callbacks

        # Create async wrapper function
        async def proxy_tool_wrapper(**kwargs):
            """Wrapper that calls through ToolsetProxy.invoke()."""
            # Filter parameters based on tool description
            param_names = list(tool_desc.get("parameters", {}).keys())
            remote_params = {
                k: v
                for k, v in kwargs.items()
                if k in param_names or k in __SKIP_PARAMS__
            }

            # proxy.invoke() handles all errors and returns dict (never raises)
            result = await proxy.invoke(tool_name, remote_params)

            # Handle inner calls (callback mechanism)
            # Some toolsets use this to call back to agent or local functions
            if isinstance(result, dict) and "inner_call" in result:
                inner_call = result.pop("inner_call")
                name = inner_call["name"]
                args = inner_call["args"]
                result_field = inner_call["result_field"]

                # Handle callback using the agent context
                if name == "__agent_run__":
                    agent_run = kwargs.get(__AGENT_RUN_NAME__)
                    if agent_run:
                        callback_result = await agent_run(args)
                        result[result_field] = callback_result
                    else:
                        raise RuntimeError("Agent callback required but not provided")
                else:
                    # Local function callback - use agent's functions
                    if name in agent_context.functions:
                        callback_result = await run_func(
                            agent_context.functions[name], **args
                        )
                        result[result_field] = callback_result
                    else:
                        raise RuntimeError(
                            f"Local function '{name}' not found in agent"
                        )

            return result

        # Set function metadata
        proxy_tool_wrapper.__name__ = tool_name
        proxy_tool_wrapper.__doc__ = tool_desc.get("description", "")

        # Convert tool_desc to Description for _convert_functions()
        proxy_tool_wrapper.function_descriptions = _convert_tool_desc_to_description(
            tool_desc
        )

        return proxy_tool_wrapper

    def _convert_functions(
        self, litellm_mode: bool, allow_transfer: bool
    ) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []
        # Fully unified function handling - all functions are now identical
        for func in self.functions.values():
            if hasattr(func, "__schema__"):
                # directly use the schema of the tool
                schema = copy.deepcopy(func.__schema__)
                if litellm_mode:
                    schema["function"]["strict"] = False
                    del schema["function"]["parameters"]["additionalProperties"]
                functions.append(func.__schema__)
                continue

            if hasattr(func, "function_descriptions"):
                # This is a remote function wrapper with stored descriptions
                desc = func.function_descriptions
            elif isinstance(func, ReverseCallable):
                # This is a magique reverse callable
                desc = Description(
                    inputs=[Value(type_=str, name=p) for p in func.parameters],
                    name=func.name,
                )
            elif hasattr(func, "parameters") and isinstance(func.parameters, list):
                # This is a legacy reverse callable in magique
                desc = Description(
                    inputs=[Value(type_=str, name=p) for p in func.parameters],
                    name=func.name,
                )
            else:
                # All other functions (local) can be parsed normally
                desc = parse_func(func)

            assert isinstance(desc.name, str), "Function name must be a string"
            if not allow_transfer:
                if desc.name.startswith("transfer_to_") or desc.name.startswith(
                    "call_agent_"
                ):
                    # NOTE: transfer function should start with `transfer_to_`
                    continue
            func_dict = desc_to_openai_dict(
                desc,
                skip_params=__SKIP_PARAMS__,
                litellm_mode=litellm_mode,
            )
            functions.append(func_dict)

        return functions

    async def _handle_tool_calls(
        self,
        tool_calls: list,
        context_variables: dict,
        timeout: float,
        time_delta: float = 0.5,
        check_stop: Callable | None = None,
    ) -> list[dict]:
        messages = []

        async def agent_run(msg: AgentInput):
            """Remote function for toolset call agent."""
            logger.info(f"Running agent {self.name} with message {msg}")
            resp = await self.run(
                msg,
                allow_transfer=False,
                update_memory=False,
            )
            return resp.content

        # Fully unified tool calling - all functions handled identically
        for call in tool_calls:
            try:
                func_name = call["function"]["name"]
                params = json.loads(call["function"]["arguments"])

                # All functions are now in self.functions (unified approach)
                assert func_name in self.functions, (
                    f"Function `{func_name}` is not found in self.functions"
                )

                func = self.functions[func_name]

                # Handle parameter injection - check function type
                if hasattr(func, "function_descriptions"):
                    # Remote function wrapper - check parameter names from description
                    var_names = [v.name for v in func.function_descriptions.inputs]
                elif isinstance(func, ReverseCallable):
                    # This is a magique reverse callable
                    var_names = func.parameters
                elif hasattr(func, "parameters") and isinstance(func.parameters, list):
                    # Legacy reverse callable in magique
                    var_names = func.parameters
                else:
                    # Regular local function
                    var_names = func.__code__.co_varnames

                if __CTX_VARS_NAME__ in var_names:
                    params[__CTX_VARS_NAME__] = context_variables
                if __AGENT_RUN_NAME__ in var_names:
                    params[__AGENT_RUN_NAME__] = agent_run
                start_time = time.time()
                task = asyncio.create_task(run_func(func, **params))
                while True:
                    if task.done() or (task.cancelled()):
                        result = task.result()
                        break
                    else:
                        logger.debug("Check stop when tool calling")
                        if timeout is not None:
                            if time.time() - start_time > timeout:
                                raise asyncio.TimeoutError()
                        if check_stop is not None:
                            if check_stop(time.time() - start_time):
                                raise StopRunning()
                        await asyncio.sleep(time_delta)
            except StopRunning:
                raise
            except Exception as e:
                result = repr(e)

            context_variables[call["id"]] = result
            # Generate unique id for each tool message
            tool_message_id = str(uuid4())
            end_timestamp = time.time()
            execution_duration = end_timestamp - start_time

            if isinstance(result, (Agent, RemoteAgent)):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "tool_name": func_name,
                        "content": result.name,
                        "transfer": True,
                        "id": tool_message_id,
                        "timestamp": end_timestamp,  # 保持兼容性
                        "start_timestamp": start_time,  # 新增：开始时间
                        "end_timestamp": end_timestamp,  # 新增：结束时间
                        "execution_duration": execution_duration,  # 新增：执行时长（秒）
                    }
                )
            else:
                if isinstance(result, dict):
                    processed_result = remove_hidden_fields(result)
                else:
                    processed_result = result
                content = repr(processed_result)
                if self.max_tool_content_length is not None:
                    content = content[: self.max_tool_content_length]
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "tool_name": func_name,
                        "raw_content": result,
                        "content": content,
                        "id": tool_message_id,
                        "timestamp": end_timestamp,  # 保持兼容性
                        "start_timestamp": start_time,  # 新增：开始时间
                        "end_timestamp": end_timestamp,  # 新增：结束时间
                        "execution_duration": execution_duration,  # 新增：执行时长（秒）
                    }
                )
        return messages

    async def _acompletion(
        self,
        messages: list[dict],
        model: str,
        tool_use: bool = True,
        response_format: Any | None = None,
        process_chunk: Callable | None = None,
        allow_transfer: bool = True,
    ) -> dict:
        import time

        # Start timing
        request_start_time = time.time()
        logger.info(f"🚀 [Agent:{self.name}] Starting LLM request for model: {model}")

        # Time message processing
        msg_start = time.time()
        messages = process_messages_for_model(messages, model)
        msg_time = time.time() - msg_start

        force_litellm = self.force_litellm

        provider = "openai"
        if "/" in model:
            provider = model.split("/")[0]
            model_name = model.split("/")[1]
        else:
            model_name = model
        litellm_mode = (provider != "openai") or force_litellm

        # Get custom base_url from environment variable if set
        base_url = None
        env_var = f"{provider.upper()}_API_BASE"
        if env_var in os.environ:
            base_url = os.environ[env_var]

        # Ensure proxy tools are loaded (lazy loading on first conversation)
        if tool_use:
            load_start = time.time()
            await self._ensure_proxy_tools_loaded()
            load_time = time.time() - load_start
            if load_time > 0.01:  # Only log if took significant time
                logger.info(
                    f"📦 [Agent:{self.name}] Proxy tools loaded: {load_time:.3f}s"
                )

        # Time tool conversion
        tools = None
        if tool_use:
            tools_start = time.time()
            tools = self._convert_functions(litellm_mode, allow_transfer) or None
            tools_time = time.time() - tools_start
            tool_count = len(tools) if tools else 0
            logger.info(
                f"⚙️  [Agent:{self.name}] Tool conversion: {tools_time:.3f}s for {tool_count} tools"
            )
        else:
            tools_time = 0
            tool_count = 0

        # Generate unique message_id for this LLM request
        message_id = str(uuid4())
        chunk_index = 0

        # Create enhanced process_chunk for this specific request
        enhanced_process_chunk = None
        if process_chunk:

            async def enhanced_process_chunk(chunk: dict):
                nonlocal chunk_index
                enhanced_chunk = {
                    **chunk,
                    "message_id": message_id,
                    "chunk_index": chunk_index,
                    "timestamp": time.time(),
                }
                chunk_index += 1
                await run_func(process_chunk, enhanced_chunk)

        # Send begin chunk and time it
        if enhanced_process_chunk:
            begin_start = time.time()
            await enhanced_process_chunk({"begin": True})
            begin_time = time.time() - begin_start
            logger.info(f"📤 [Agent:{self.name}] Begin chunk sent: {begin_time:.3f}s")
        else:
            begin_time = 0

        # Start LLM API timing
        llm_start_time = time.time()
        logger.info(
            f"🤖 [Agent:{self.name}] Calling {provider} API (model: {model_name})"
        )

        if provider == "zhipu":
            # Use dedicated Zhipu AI function
            complete_resp = await acompletion_zhipu(
                messages=messages,
                model=model_name,
                tools=tools,
                response_format=response_format,
                process_chunk=enhanced_process_chunk,
                base_url=base_url or "https://open.bigmodel.cn/api/paas/v4/",
            )
            if (
                complete_resp
                and hasattr(complete_resp, "choices")
                and complete_resp.choices
                and len(complete_resp.choices) > 0
            ):
                message = complete_resp.choices[0].message.model_dump()
                if "parsed" in message:
                    message.pop("parsed")
                if "tool_calls" in message:
                    if message["tool_calls"] == []:
                        message["tool_calls"] = None
            else:
                message = {
                    "role": "assistant",
                    "content": "Error: Empty response from Zhipu AI",
                }
        elif not litellm_mode:
            complete_resp = await acompletion_openai(
                messages=messages,
                model=model_name,
                tools=tools,
                response_format=response_format,
                process_chunk=enhanced_process_chunk,
                base_url=base_url,
            )
            if (
                complete_resp
                and hasattr(complete_resp, "choices")
                and complete_resp.choices
                and len(complete_resp.choices) > 0
            ):
                message = complete_resp.choices[0].message.model_dump()
                if "parsed" in message:
                    message.pop("parsed")
                if "tool_calls" in message:
                    if message["tool_calls"] == []:
                        message["tool_calls"] = None
            else:
                message = {
                    "role": "assistant",
                    "content": "Error: Empty response from API",
                }
        else:
            complete_resp = await acompletion_litellm(
                messages=messages,
                model=model,
                tools=tools,
                response_format=response_format,
                process_chunk=enhanced_process_chunk,
                base_url=base_url,
            )
            if (
                complete_resp
                and hasattr(complete_resp, "choices")
                and complete_resp.choices
                and len(complete_resp.choices) > 0
            ):
                message = complete_resp.choices[0].message.model_dump()
            else:
                message = {
                    "role": "assistant",
                    "content": "Error: Empty response from API",
                }

        # Log total LLM timing
        llm_total_time = time.time() - llm_start_time
        request_total_time = time.time() - request_start_time

        logger.info(
            f"⏱️  [Agent:{self.name}] LLM response received: {llm_total_time:.3f}s"
        )
        logger.info(
            f"📊 [Agent:{self.name}] Request breakdown - Total: {request_total_time:.3f}s | "
            f"Message: {msg_time:.3f}s | Tools: {tools_time:.3f}s | "
            f"Begin: {begin_time:.3f}s | LLM: {llm_total_time:.3f}s"
        )

        # Add streaming fields directly to step_message
        end_timestamp = time.time()
        generation_duration = end_timestamp - request_start_time

        message["id"] = message_id
        message["timestamp"] = end_timestamp  # 保持兼容性
        message["start_timestamp"] = request_start_time  # 新增：开始时间
        message["end_timestamp"] = end_timestamp  # 新增：结束时间
        message["generation_duration"] = generation_duration  # 新增：生成时长（秒）
        return message

    async def _acompletion_with_models(
        self,
        history,
        tool_use,
        response_format,
        process_chunk,
        allow_transfer,
        model: str | list[str] | None = None,
    ):
        error_count = 0
        if model is None:
            models = self.models
        else:
            if isinstance(model, str):
                models = [model] + self.models
            else:
                models = model + self.models
        for model in models:
            if error_count > 0:
                logger.warning(
                    f"Try to use {model}, because of the error of the previous model."
                )
            try:
                message = await self._acompletion(
                    history,
                    model=model,
                    tool_use=tool_use,
                    response_format=response_format,
                    process_chunk=process_chunk,
                    allow_transfer=allow_transfer,
                )
                return message
            except StopRunning:
                raise
            except Exception as e:
                logger.error(f"Error completing with model {model}: {e}")
                error_count += 1
                continue
        else:
            return {}

    def get_system_prompt(self):
        if self.enhanced_flow:
            return create_enhanced_instructions(self.instructions)
        return self.instructions

    async def _run_stream(
        self,
        messages: list[dict],
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        check_stop: Callable | None = None,
        max_turns: int | float = float("inf"),
        context_variables: dict | None = None,
        response_format: Any | None = None,
        tool_use: bool = True,
        tool_timeout: int | None = None,
        model: str | list[str] | None = None,
        allow_transfer: bool = True,
    ) -> ResponseDetails | AgentTransfer:
        response_format = response_format or self.response_format
        history = copy.deepcopy(messages)
        tool_timeout = tool_timeout or self.tool_timeout
        system_prompt = self.get_system_prompt()
        current_timestamp = time.time()

        if (len(history) > 0) and (history[0]["role"] == "system"):
            history[0]["content"] = system_prompt
            history[0]["timestamp"] = current_timestamp  # 添加时间戳
            if "id" not in history[0]:
                history[0]["id"] = str(uuid4())  # 添加唯一ID（如果没有的话）
        else:
            history.insert(
                0,
                {
                    "role": "system",
                    "content": system_prompt,
                    "timestamp": current_timestamp,  # 添加时间戳
                    "id": str(uuid4()),  # 添加唯一ID
                },
            )
        init_len = len(history)
        context_variables = context_variables or {}

        if response_format:
            Response = create_model("Response", result=(response_format, ...))
        else:
            Response = None

        if check_stop is not None:

            async def _process_chunk(chunk: dict):
                if process_chunk is not None:
                    await run_func(process_chunk, chunk)
                if check_stop():
                    raise StopRunning()
        else:
            _process_chunk = process_chunk

        while len(history) - init_len < max_turns:
            message = await self._acompletion_with_models(
                history,
                tool_use,
                Response,
                _process_chunk,
                allow_transfer,
                model=model,
            )

            if Response is not None:
                content = message.get("content")
                if content:
                    parsed = Response.model_validate_json(content)
                    message["parsed"] = parsed.result

            message["agent_name"] = self.name

            history.append(message)
            self.events_queue.put_nowait(message)
            if process_step_message:
                await run_func(process_step_message, message)

            # Enhanced conversation flow (opt-in)
            if not message.get("tool_calls"):
                if self.enhanced_flow:
                    if self._should_continue_conversation(message, history):
                        continue
                break

            tool_messages = await self._handle_tool_calls(
                message["tool_calls"],
                context_variables=context_variables,
                timeout=tool_timeout,
                check_stop=check_stop,
            )
            history.extend(tool_messages)
            for msg in tool_messages:
                self.events_queue.put_nowait(msg)

            if process_step_message:
                for msg in process_messages_for_hook_func(tool_messages):
                    await run_func(process_step_message, msg)

            for msg in tool_messages:
                if msg.get("transfer"):
                    return AgentTransfer(
                        from_agent=self.name,
                        to_agent=msg["content"],
                        history=history,
                        context_variables=context_variables,
                        init_message_length=init_len,
                    )

        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
        )

    def _input_to_openai_messages(
        self,
        msg: AgentInput,
    ) -> list[dict]:
        assert isinstance(
            msg, (list, str, BaseModel, AgentResponse, AgentTransfer, VisionInput)
        ), (
            "Message must be a list, string, BaseModel or AgentResponse, AgentTransfer, VisionInput"
        )
        if isinstance(msg, AgentResponse):
            # For acceping the result of previous run or other agent
            msg = msg.content

        # Convert message to the openai message format
        if isinstance(msg, AgentTransfer):
            messages = msg.history
        elif isinstance(msg, VisionInput):
            messages = vision_to_openai(msg)
        elif isinstance(msg, BaseModel):
            messages = [
                {
                    "role": "user",
                    "content": msg.model_dump_json(),
                    "timestamp": time.time(),
                    "id": str(uuid4()),
                }
            ]
        elif isinstance(msg, str):
            messages = [
                {
                    "role": "user",
                    "content": msg,
                    "timestamp": time.time(),
                    "id": str(uuid4()),
                }
            ]
        elif isinstance(msg, list):
            new_messages = []
            for m in msg:
                if isinstance(m, str):
                    new_messages.append(
                        {
                            "role": "user",
                            "content": m,
                            "timestamp": time.time(),
                            "id": str(uuid4()),
                        }
                    )
                elif isinstance(m, VisionInput):
                    new_messages.extend(vision_to_openai(m))
                elif isinstance(m, BaseModel):
                    new_messages.append(
                        {
                            "role": "user",
                            "content": m.model_dump_json(),
                            "timestamp": time.time(),
                            "id": str(uuid4()),
                        }
                    )
                else:
                    assert isinstance(m, dict), (
                        "Message must be a string, BaseModel or dict"
                    )
                    new_messages.append(m)
            messages = new_messages
        return messages

    def _should_continue_conversation(self, message: dict, history: list) -> bool:
        """Simple conversation continuation logic - only enhanced messages reach here"""
        from .utils.log import logger

        # SAFETY FIRST: Basic limits to prevent API drain - count truly consecutive messages from THIS agent
        consecutive_no_tools = 0
        for msg in reversed(history):
            if (
                msg.get("role") == "assistant"
                and not msg.get("tool_calls")
                and msg.get("agent_name") == self.name
            ):
                consecutive_no_tools += 1
            else:
                # Stop counting when we hit a non-matching message (breaks the consecutive chain)
                break

        if consecutive_no_tools >= self.max_consecutive_messages:
            logger.warning(
                f"Agent {self.name}: Too many consecutive messages without tools ({consecutive_no_tools})"
            )
            return False

        if (
            len([m for m in history if m.get("role") == "assistant"])
            >= self.max_total_messages
        ):
            logger.warning(f"Agent {self.name}: Reached message limit")
            return False

        # CORE LOGIC: Simple state-based decisions
        state = (
            message.get("conversation_state")
            or _detect_conversation_state_static(message)["state"]
        )

        # These always stop (completed tasks or user questions)
        if state in ["completed", "executed"]:
            return False

        # These always continue (reasoning leads to action, executing continues the work)
        if state in ["reasoning", "executing"]:
            return True

        # Default: stop
        return False

    async def run(
        self,
        msg: AgentInput,
        response_format: Any | None = None,
        tool_use: bool = True,
        context_variables: dict | None = None,
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        check_stop: Callable | None = None,
        memory: Memory | None = None,
        use_memory: bool | None = None,
        update_memory: bool = True,
        tool_timeout: int | None = None,
        model: str | list[str] | None = None,
        allow_transfer: bool = True,
    ) -> AgentResponse | AgentTransfer:
        """Run the agent.

        Args:
            msg: The input message to the agent.
            response_format: The response format to use.
            tool_use: Whether to use tools.
            context_variables: The context variables to use.
            process_chunk: The function to process the chunk.
            process_step_message: The function to process the step message.
            check_stop: The function to check if the agent should stop.
            memory: The memory to use.
            use_memory: Whether to use short term memory.
            update_memory: Whether to update the short term memory.
            tool_timeout: The timeout for the tool.
            model: The model to use in this run.
                Could be a list of models for fallback.
                If not provided, the model will be selected from the agent's models.
            allow_transfer: Whether to allow transfer to another agent.

        Returns:
            The agent response. Either an AgentResponse or an AgentTransfer.
            If the agent is interrupted, the AgentResponse will have the interrupt flag set to True.
            If the agent is transferring to another agent, the AgentTransfer will be returned.
        """
        _use_m = self.use_memory
        if use_memory is not None:
            _use_m = use_memory
        new_input_messages = self._input_to_openai_messages(msg)
        memory = memory or self.memory
        if _use_m:
            memory.cleanup()
            old_messages = memory.get_messages()
            messages = old_messages + new_input_messages
        else:
            messages = new_input_messages
        response_format = response_format or self.response_format
        context_variables = context_variables or {}
        if isinstance(msg, AgentTransfer):
            new_input_messages = []
            context_variables = msg.context_variables
        context_variables[__CLIENT_ID_NAME__] = memory.id

        if update_memory:
            memory.add_messages(new_input_messages)
            if process_step_message is not None:

                async def _process_step_message(step_message: dict):
                    memory.add_messages([step_message])
                    await run_func(process_step_message, step_message)
            else:

                async def _process_step_message(step_message: dict):
                    memory.add_messages([step_message])
        else:
            _process_step_message = process_step_message

        async def enhanced_step_processor(step_message):
            enhancer = SmartMessageEnhancer()
            enhancer.enhance_message(step_message)
            if _process_step_message:
                await run_func(_process_step_message, step_message)

        if self.enhanced_flow:
            step_processor = enhanced_step_processor
        else:
            step_processor = _process_step_message

        try:
            details = await self._run_stream(
                messages=messages,
                response_format=response_format,
                tool_use=tool_use,
                context_variables=context_variables,
                process_chunk=process_chunk,
                process_step_message=step_processor,
                check_stop=check_stop,
                tool_timeout=tool_timeout,
                model=model,
                allow_transfer=allow_transfer,
            )
        except StopRunning:
            logger.info("StopRunning")
            if update_memory:
                memory.cleanup()
            return AgentResponse(
                agent_name=self.name,
                content="",
                details=None,
                interrupt=True,
            )

        if isinstance(details, AgentTransfer):
            return details
        else:
            final_msg = details.messages[-1]
            if response_format:
                content = final_msg.get("parsed")
            else:
                content = final_msg.get("content")
            return AgentResponse(
                agent_name=self.name,
                content=content,
                details=details,
            )

    # FIX: agent should not call REPL, REPL call agent instead
    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        from .repl.core import Repl

        repl = Repl(self)
        await repl.run(message)

    async def serve(self, **kwargs):
        """Serve the agent to a remote server."""

        service = AgentService(self, **kwargs)
        return await service.run()


def create_enhanced_instructions(base_instructions: str) -> str:
    """Add rich response capability with Biomni-inspired structured format"""

    enhanced_format = f"""{base_instructions}

ENHANCED CONVERSATION FLOW:

At each turn, you must include EXACTLY ONE tag with title and content. Choose from these 3 options:

1) For reasoning and analysis:
<thinking>Brief title of your thinking focus
Your detailed reasoning, planning, or analysis process goes here.

2) For taking actions or using tools:
<execute>Brief title of the action you're taking
Explanation of what you're doing and why, then call your tools.

3) For completion - either finished work OR asking user questions:
<complete>Brief title of what you're concluding
Final results, answers, questions to user, or task completion summary.

CRITICAL RULES:
- Use EXACTLY ONE tag per response - no exceptions
- Each tag must have: Brief title on first line + detailed content below
- NEVER combine multiple tags: ✗ <thinking>...</thinking> <execute>...</execute>
- In each response, you must include one of the three tags. No messages without tags.
- If you need to think AND act, use <thinking> first, then <execute> in next response

TAG USAGE GUIDE:
- <thinking> = internal reasoning, planning, analyzing requirements
- <execute> = calling tools, performing actions, working on tasks
- <complete> = final answers, results, OR questions to user

EXAMPLES OF CORRECT USAGE:
✓ "<thinking>Analyzing user requirements
Let me break down what the user is asking for: 1) They want X, 2) They need Y..."

✓ "<execute>Running data analysis
I'll call the analysis function with these parameters and process the results.
[calls tools here]"

✓ "<complete>Task completed successfully
Here are the results: ... What would you like me to focus on next?"

EXAMPLES OF WRONG USAGE:
✗ "<thinking>Planning... <execute>Now running..."
✗ "Let me <thinking>think and then <execute>act"
✗ Any response with more than one tag

"""

    return enhanced_format


def _detect_conversation_state_static(message: dict) -> dict:
    """Standalone function for conversation state detection using loop-based patterns"""
    import re

    # Tool responses are always "executed"
    if message.get("role") == "tool":
        return {
            "state": "executed",
            "description": f"Tool {message.get('tool_name', 'response')} executed",
            "title": "🔹 Tool Response",
        }

    content = message.get("content", "")
    has_tool_calls = bool(message.get("tool_calls"))

    # Define state patterns - simplified 3-tag system with title + content (single tag format)
    patterns = [
        # (regex_pattern, state, emoji)
        (
            r"<thinking>\s*([^\n<]*?)(?:\n(.*?))?(?=<(?:execute|complete)|$)",
            "reasoning",
            "🧠",
        ),
        (
            r"<execute>\s*([^\n<]*?)(?:\n(.*?))?(?=<(?:thinking|complete)|$)",
            "executing",
            "⚡",
        ),
        (
            r"<complete>\s*([^\n<]*?)(?:\n(.*?))?(?=<(?:thinking|execute)|$)",
            "completed",
            "✅",
        ),
    ]

    # Check content for explicit state markers - find first match
    if content:
        for pattern, state, emoji in patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                title = match.group(1).strip() if match.group(1) else ""
                raw_description = match.group(2).strip() if match.group(2) else ""

                # Clean description by removing any subsequent markers (single tag format)
                cleaned_description = re.sub(
                    r"<(thinking|execute|complete)\b.*",
                    "",
                    raw_description,
                    flags=re.IGNORECASE | re.DOTALL,
                ).strip()

                return {
                    "state": state,
                    "description": cleaned_description,
                    "title": f"{emoji} {title}",
                }

    # Assistant with tool calls but no explicit markers
    if message.get("role") == "assistant" and has_tool_calls:
        return {
            "state": "executing",
            "description": content,
            "title": "⚡ Executing Tools",
        }

    # Default fallback - use content as description if no markers found
    return {"state": "reasoning", "description": content, "title": ""}


class SmartMessageEnhancer:
    """Extract metadata from structured LLM responses with explicit state support"""

    def enhance_message(self, message: dict) -> dict:
        """Extract metadata from state markers and clean content for display"""

        state_info = _detect_conversation_state_static(message)

        # Add metadata to message
        message["step_title"] = state_info["title"]  # Already includes emoji
        message["conversation_state"] = state_info["state"]

        # Extract both title and detailed content separately
        message["step_briefi_title"] = self._extract_brief_title(state_info["title"])
        message["display_content"] = self._clean_content_for_display(
            state_info["description"]
        )

        # Keep original content for backward compatibility
        if not message.get("display_content"):
            message["display_content"] = message.get("content", "")

        return message

    def _extract_brief_title(self, full_title: str) -> str:
        """Extract just the title without emoji"""
        # Remove emoji and extra whitespace
        import re

        clean_title = re.sub(r"^[^\w\s]+\s*", "", full_title).strip()
        return clean_title if clean_title else "Processing"

    def _clean_content_for_display(self, description: str) -> str:
        """Return the description content (content after the marker)"""
        return description.strip() if description else ""
