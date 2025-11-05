import asyncio
import copy
import inspect
import json
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, Union
from uuid import uuid4

from funcdesc import parse_func
from pydantic import BaseModel, create_model

from .constant import SystemPromptMode, build_system_prompt
from .memory import Memory
from .remote import (
    RemoteBackendFactory,
    RemoteConfig,
    RemoteWorker,
)
from .toolset import ToolSet
from .utils.llm import (
    TimingTracker,
    process_messages_for_hook_func,
    process_messages_for_model,
    remove_hidden_fields,
)
from .utils.llm_providers import (
    call_llm_provider,
    create_enhanced_process_chunk,
    detect_provider,
    get_base_url,
)
from .utils.log import logger
from .utils.misc import desc_to_openai_dict, run_func
from .utils.vision import VisionInput, vision_to_openai

DEFAULT_MODEL = "gpt-5-mini"


# ===== Execution Context =====


@dataclass
class ExecutionContext:
    """Prepared execution context for agent run."""

    conversation_history: list[dict]
    runtime_context: dict
    should_use_memory: bool
    memory_instance: "Memory | None"
    execution_context_id: str | None = None
    input_messages: list[dict] | None = None


# ===== Tool Provider Base Class =====


class ToolInfo(BaseModel):
    """Information about a tool"""

    name: str
    description: str
    inputSchema: Optional[dict] = None  # JSON Schema format (OpenAI function schema)


class ToolProvider(ABC):
    """Abstract base class for tool providers (MCP, ToolSet, etc.)

    Tool providers abstract away the source of tools, allowing agents to
    work uniformly with tools from different sources.
    """

    @abstractmethod
    async def list_tools(self) -> list[ToolInfo]:
        """List all available tools from this provider

        Returns:
            List of ToolInfo objects describing available tools
        """
        pass

    @abstractmethod
    async def call_tool(self, name: str, args: dict) -> Any:
        """Call a tool by name with the given arguments

        Args:
            name: Name of the tool to call
            args: Arguments to pass to the tool

        Returns:
            Result from the tool call
        """
        pass

    async def initialize(self):
        """Optional: Initialize the provider (e.g., connect to remote server)

        Called once when the provider is added to an agent.
        """
        pass

    async def shutdown(self):
        """Optional: Clean up provider resources

        Called when the agent is being shut down.
        """
        pass


_CTX_VARS_NAME = "context_variables"
_SKIP_PARAMS = [_CTX_VARS_NAME]
_CLIENT_ID_NAME = "client_id"


class AgentService:
    def __init__(self, agent: "Agent", **kwargs):
        self.agent = agent

        self.backend = RemoteBackendFactory.create_backend()
        self.worker: RemoteWorker = self.backend.create_worker(**kwargs)
        self.setup_worker()

    async def response(self, msg, **kwargs):
        resp = await self.agent.run(msg, **kwargs)
        return resp

    async def get_info(self):
        return {
            "name": self.agent.name,
            "instructions": self.agent.instructions,
            "models": self.agent.models,
            "tool_names": list(
                self.agent.functions.keys()
            ),  # Simplified: return tool names
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
        self.tool_names = info.get("tool_names", [])  # New format
        # Backward compatibility
        if "functions_names" in info:
            self.tool_names = info["functions_names"]
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
    """

    messages: list[dict]
    context_variables: dict
    execution_context_id: str | None = None  # NEW: Context ID for message filtering


class AgentResponse(BaseModel):
    """
    The AgentResponse class is used to store the agent response.
    """

    agent_name: str
    content: Any
    details: ResponseDetails | None
    interrupt: bool = False


class AgentTransfer(BaseModel):
    """
    The AgentTransfer class is used to transfer the agent response to another agent.
    """

    from_agent: str
    to_agent: str
    history: list[dict]
    context_variables: dict
    init_message_length: int
    instruction: str = ""  # NEW: Task description for call_agent delegation
    execution_context_id: str | None = None  # NEW: Context ID for message filtering
    tool_call_id: str | None = None


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
        description: The description of the agent. (default: None)
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
        system_prompt_mode: SystemPromptMode | None = None,
        description: str | None = None,
    ):
        self.id = uuid4()
        self.name = name
        self.instructions = instructions
        self.description = description
        if isinstance(model, str):
            self.models = [model]
            if model != DEFAULT_MODEL:
                self.models.append(DEFAULT_MODEL)
        else:
            self.models = model
        # Tool storage (simplified - unified handling)
        self._base_functions: dict[
            str, Callable
        ] = {}  # All tools (local + remote wrappers)

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

        # Provider management (MCP, ToolSet, etc.)
        self.providers: dict[str, ToolProvider] = {}  # name -> ToolProvider instance
        self.not_loaded_toolsets: list[str] = []  # Track which toolsets failed to load

        # Plan Mode support
        self.plan_mode = False

        # Can delegate to other agents - set by Team if this agent can delegate/coordinate
        # Used in unified architecture: True when has_transfer_agents or has_sub_agents
        self.can_delegate = False

        # System prompt mode
        self.system_prompt_mode = system_prompt_mode

    @property
    def functions(self) -> dict[str, Callable]:
        """Get current tools (returns a copy for safety)."""
        return self._base_functions

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

        # Add to _base_functions
        self._base_functions[key] = func

        return self

    def remove_tool(self, key: str) -> bool:
        """
        Remove a tool from the agent.

        Args:
            key: The name of the tool to remove.

        Returns:
            bool: True if the tool was found and removed, False otherwise.
        """
        if key in self._base_functions:
            del self._base_functions[key]
            return True

        return False

    def enable_plan_mode(self) -> dict:
        """Enable Plan Mode - a read-only environment for safe planning and analysis."""
        if self.plan_mode:
            return {"already_in_plan_mode": True, "message": "Already in Plan Mode"}

        # Set flag - system prompt will enforce restrictions
        self.plan_mode = True
        logger.info(f"🔒 [Agent:{self.name}] Entered Plan Mode (Read-Only). ")

        return {
            "success": True,
            "plan_mode": True,
            "message": "Plan Mode activated. Tool restrictions enforced via system prompt.",
        }

    def _disable_plan_mode_internal(self):
        """Internal method to disable Plan Mode (called by exit_plan_mode tool)."""
        if not self.plan_mode:
            return

        # Clear flag - system prompt will return to normal
        self.plan_mode = False

        logger.info(
            f"🔓 [Agent:{self.name}] Exited Plan Mode. Full tool access restored."
        )

    async def toolset(self, toolset: Union["ToolSet", "ToolProvider"]) -> "Agent":
        """Add a toolset to the agent (supports both ToolSet and ToolSetProvider)

        Behavior depends on type:
        - ToolSet: Local tools loaded directly into _base_functions
        - ToolSetProvider: Dynamic routing - tools retrieved on-demand, no pre-wrapping

        Args:
            toolset: Either a ToolSet instance (local tools loaded directly)
                    or a ToolSetProvider instance (provider-based tool access)

        Returns:
            The agent instance
        """
        # Import here to avoid circular imports
        from .providers import ToolSetProvider

        if isinstance(toolset, ToolSet):
            # Local ToolSet - immediately load all tools via agent.tool()
            for name, (func, _) in toolset.tool_functions.items():
                self.tool(func, key=name)
            logger.debug(
                f"Agent '{self.name}': Added local toolset with {len(toolset.functions)} tools"
            )
        elif isinstance(toolset, ToolSetProvider):
            self.providers[toolset.toolset_name] = toolset
            logger.debug(
                f"Agent '{self.name}': Added ToolSetProvider (dynamic routing)"
            )
        else:
            raise TypeError(
                f"Invalid toolset type: {type(toolset)}. "
                f"Expected ToolSet or ToolSetProvider instance."
            )

        return self

    # ===== New Provider-Based Tool Management =====

    async def mcp(self, name: str, provider: "ToolProvider") -> "Agent":
        """Add a MCP provider to the agent (one at a time)

        Dynamic routing approach: Provider tools are retrieved on-demand via get_tools_for_llm()
        and called via call_tool() with prefix routing. No pre-wrapping.

        Args:
            name: Name/identifier for this provider (e.g., 'biomcp')
            provider: ToolProvider instance (MCPProvider recommended, already initialized)

        Returns:
            The agent instance
        """
        # Store the provider for dynamic routing
        self.providers[name] = provider

        logger.debug(
            f"Agent '{self.name}': Added MCP provider '{name}' (dynamic routing)"
        )

        return self

    async def get_tools_for_llm(self) -> list[dict]:
        """Get all tools for LLM - includes _base_functions and provider tools

        Dynamically retrieves tools from providers (utilizing their caching mechanisms).
        Provider tools are prefixed with {provider_name}_ to distinguish them from agent's own tools.

        Returns:
            List of tool definitions in OpenAI format
        """
        # 1. Get tools from _base_functions (Agent's own tools - no prefix)
        base_tools = self._convert_functions(
            litellm_mode=self.force_litellm, allow_transfer=True
        )

        # 2. Get tools from providers (dynamic retrieval - uses provider caching)
        # Providers return ToolInfo with pre-generated inputSchema (the "function" part)
        logger.info(f"get tools for llm: {self.providers} ")
        provider_tools = []
        for provider_name, provider in self.providers.items():
            try:
                # Get tools from provider (uses cached list if available)
                tools = await provider.list_tools()

                # All providers must provide inputSchema as the complete "function" part
                for tool_info in tools:
                    # inputSchema must be present (design contract with providers)
                    assert tool_info.inputSchema is not None, (
                        f"Provider '{provider_name}': Tool '{tool_info.name}' missing inputSchema"
                    )

                    # Make a copy to avoid modifying cached data
                    function_schema = tool_info.inputSchema.copy()

                    # Add provider prefix to tool name (using __ as separator to support provider names with _)
                    function_schema["name"] = f"{provider_name}__{tool_info.name}"

                    # Build complete OpenAI tool dict
                    provider_tools.append(
                        {
                            "type": "function",
                            "function": function_schema,
                        }
                    )

                logger.info(
                    f"Agent '{self.name}': Added {len(tools)} tools from provider '{provider_name}'"
                )

            except Exception as e:
                logger.warning(
                    f"Agent '{self.name}': Failed to get tools from provider '{provider_name}': {e}"
                )

        return base_tools + provider_tools

    def _should_inject_context_variables(self, prefixed_name: str) -> bool:
        """Determine if context_variables should be injected for a tool.

        Returns True for:
        1. ToolSet base functions (has _is_tool attribute)
        2. ToolSetProvider calls
        3. Functions that explicitly declare context_variables parameter

        Args:
            prefixed_name: Tool name (possibly with prefix)

        Returns:
            bool: Whether to inject context_variables
        """
        # Check 1: Is it a ToolSet base function with _is_tool attribute?
        if prefixed_name in self._base_functions:
            func = self._base_functions[prefixed_name]
            if hasattr(func, "_is_tool"):
                return True
            else:
                # Check 3: Does the function explicitly declare context_variables parameter?
                try:
                    sig = inspect.signature(func)
                    if "context_variables" in sig.parameters:
                        return True
                except (ValueError, TypeError):
                    pass
                return False

        # Check 2: Is it a ToolSetProvider call (prefix routing)?
        if "__" in prefixed_name:
            provider_name = prefixed_name.split("__", 1)[0]
            if provider_name in self.providers:
                provider = self.providers[provider_name]
                # Check if it's a ToolSetProvider
                from .providers import ToolSetProvider

                if isinstance(provider, ToolSetProvider):
                    return True

        return False

    async def call_tool(
        self,
        prefixed_name: str,
        args: dict,
        context_variables: dict | None = None,
    ) -> Any:
        """Call a tool by prefixed name, with conditional context_variables injection.

        Only injects context_variables for:
        1. ToolSet base functions (has _is_tool attribute)
        2. ToolSetProvider calls
        3. Functions that explicitly declare context_variables parameter

        Tool routing order:
        1. Agent's own _base_functions (unprefixed)
        2. Provider tools with prefix format: {provider_name}__{tool_name}
           Uses __ (double underscore) as separator to support provider names with underscores

        Args:
            prefixed_name: Tool name (possibly with prefix)
            args: Arguments to pass to the tool
            context_variables: Context variables to conditionally inject

        Returns:
            Result from the tool call

        Raises:
            ValueError: If tool not found in any source
        """
        # Determine if we should inject context_variables
        should_inject_context = self._should_inject_context_variables(prefixed_name)

        # Only inject context_variables if needed
        if should_inject_context and context_variables is not None:
            # Build complete context_variables with execution metadata
            full_context_variables = context_variables.copy()
            full_context_variables["agent_name"] = self.name
            # remove call_* variables injected for debug
            for k in list(full_context_variables.keys()):
                if k.startswith("call_"):
                    del full_context_variables[k]

            # Merge with any existing context_variables in args
            if _CTX_VARS_NAME in args:
                existing = args[_CTX_VARS_NAME]
                if isinstance(existing, dict):
                    merged = dict(existing)
                    merged.update(full_context_variables)
                    args[_CTX_VARS_NAME] = merged
                else:
                    args[_CTX_VARS_NAME] = full_context_variables
            else:
                args[_CTX_VARS_NAME] = full_context_variables

        logger.info(f"Calling tool {prefixed_name}({args})")
        # 1. Try Agent's own _base_functions first (no prefix required)
        if prefixed_name in self._base_functions:
            func = self._base_functions[prefixed_name]
            try:
                # Unpack args dict as keyword arguments to the function
                result = await run_func(func, **args)
            except TypeError as e:
                logger.error(
                    f"Failed to call tool '{prefixed_name}': {e}\n"
                    f"  Function: {func}\n"
                    f"  Arguments: {args}\n"
                )
                raise
        else:
            # 2. Try prefix-based routing: {provider_name}__{tool_name}
            # Using __ (double underscore) as separator allows provider names to contain single underscores
            if "__" not in prefixed_name:
                raise ValueError(
                    f"Tool '{prefixed_name}' not found in _base_functions or providers (no provider prefix)"
                )

            # Split on first __ only (tool_name may contain underscores)
            source, tool_name = prefixed_name.split("__", 1)

            if source not in self.providers:
                raise ValueError(
                    f"Provider '{source}' not found (tool: '{prefixed_name}')"
                )

            provider = self.providers[source]
            try:
                result = await provider.call_tool(tool_name, args)
            except Exception as e:
                logger.error(
                    f"Provider '{source}' failed to call tool '{tool_name}': {e}"
                )
                raise

        if isinstance(result, dict) and "inner_call" in result:
            name = result["inner_call"]["name"]
            if name == "__agent_run__":
                resp = await self.run(
                    result["inner_call"]["args"], use_memory=False, update_memory=False
                )
                result = resp.content

        return result

    # ===== Legacy MCP method (deprecated, kept for backward compatibility) =====

    def _convert_functions(
        self, litellm_mode: bool, allow_transfer: bool
    ) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            desc = parse_func(func)

            assert isinstance(desc.name, str), "Function name must be a string"

            # Filter transfer functions if not allowed
            if not allow_transfer:
                if desc.name.startswith("transfer_to_") or desc.name.startswith(
                    "call_agent_"
                ):
                    # NOTE: transfer function should start with `transfer_to_`
                    continue

            skip_params = list(_SKIP_PARAMS)

            func_dict = desc_to_openai_dict(
                desc,
                skip_params=skip_params,
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
        tool_messages = []

        # Unified tool calling with support for both base functions and provider tools
        for call in tool_calls:
            try:
                func_name = call["function"]["name"]
                params = json.loads(call["function"]["arguments"]) or {}

                # Use unified call_tool() method for all tool calls
                # This method handles:
                # 1. Conditional context variable injection (ToolSet, ToolSetProvider, explicit params)
                # 2. Prefix-based routing for provider tools (e.g., mcp__search_docs)
                # 3. Proper error handling
                start_time = time.time()
                task = asyncio.create_task(
                    self.call_tool(func_name, params, context_variables)
                )
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

            tool_message = {
                "role": "tool",
                "tool_name": func_name,
                "id": tool_message_id,
                "tool_call_id": call["id"],
                "timestamp": end_timestamp,  # 保持兼容性
                "start_timestamp": start_time,  # 新增：开始时间
                "end_timestamp": end_timestamp,  # 新增：结束时间
                "execution_duration": execution_duration,  # 新增：执行时长（秒）
            }

            if isinstance(result, (Agent, RemoteAgent)):
                tool_message.update(
                    {
                        "transfer": True,
                        "content": result.name,
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
                tool_message.update(
                    {
                        "raw_content": result,
                        "content": content,
                    }
                )
            tool_messages.append(tool_message)

        return tool_messages

    async def _acompletion(
        self,
        messages: list[dict],
        model: str,
        tool_use: bool = True,
        response_format: Any | None = None,
        process_chunk: Callable | None = None,
        allow_transfer: bool = True,
    ) -> dict:
        """Get LLM completion for messages (simplified and optimized).

        This method orchestrates the LLM completion workflow:
        1. Process messages for the model
        2. Detect provider and load configuration
        3. Convert functions to tools
        4. Create enhanced chunk processor
        5. Call LLM provider
        6. Add metadata to response

        Args:
            messages: Chat message history
            model: Model identifier (e.g., "gpt-4", "zhipu/glm-4")
            tool_use: Whether to include tools
            response_format: Response format specification
            process_chunk: Optional callback for streaming chunks
            allow_transfer: Whether to allow agent transfers

        Returns:
            Message dictionary with id, timestamps, and generation_duration
        """
        # Initialize timing tracker
        tracker = TimingTracker()
        tracker.start("total")
        request_start_time = time.time()

        logger.info(f"🚀 [Agent:{self.name}] Starting LLM request for model: {model}")

        # Step 1: Process messages for the model
        async with tracker.measure("message_processing"):
            messages = process_messages_for_model(messages, model)

        # Step 2: Detect provider and get configuration
        provider_config = detect_provider(model, self.force_litellm)

        # Step 3: Get base URL from environment if available
        base_url = get_base_url(provider_config.provider_type)
        if base_url:
            provider_config.base_url = base_url

        # Step 4: Get unified tools (base functions + provider tools)
        tools = None
        if tool_use:
            async with tracker.measure("tools_conversion"):
                # Use get_tools_for_llm() for unified tool access
                # This includes both base_functions and provider tools
                tools = await self.get_tools_for_llm() or None

                # For non-OpenAI providers (litellm mode), adjust tool format
                if provider_config.provider_type.value != "openai" and tools:
                    for tool in tools:
                        if "function" in tool:
                            # litellm requires strict=False
                            tool["function"]["strict"] = False
                            # Remove unsupported additionalProperties
                            if "parameters" in tool["function"]:
                                tool["function"]["parameters"].pop(
                                    "additionalProperties", None
                                )

            tool_count = len(tools) if tools else 0
            logger.info(
                f"⚙️  [Agent:{self.name}] Tool conversion: "
                f"{tracker.timings['tools_conversion']:.3f}s for {tool_count} tools "
                f"(base + providers)"
            )

        # Step 5: Create message ID and enhanced chunk processor
        message_id = str(uuid4())
        enhanced_process_chunk = create_enhanced_process_chunk(
            process_chunk, message_id
        )

        # Step 6: Send begin chunk
        if enhanced_process_chunk:
            async with tracker.measure("begin_chunk"):
                await enhanced_process_chunk({"begin": True})
            logger.info(
                f"📤 [Agent:{self.name}] Begin chunk sent: "
                f"{tracker.timings['begin_chunk']:.3f}s"
            )

        # Step 7: Call LLM provider (unified interface)
        logger.info(
            f"🤖 [Agent:{self.name}] Calling {provider_config.provider_type.value} API "
            f"(model: {provider_config.model_name})"
        )

        async with tracker.measure("llm_api"):
            message = await call_llm_provider(
                config=provider_config,
                messages=messages,
                tools=tools,
                response_format=response_format,
                process_chunk=enhanced_process_chunk,
            )

        # Step 8: Add metadata to message
        end_timestamp = time.time()
        total_time = tracker.end("total")

        message["id"] = message_id
        message["timestamp"] = end_timestamp  # 保持兼容性
        message["start_timestamp"] = request_start_time
        message["end_timestamp"] = end_timestamp
        message["generation_duration"] = total_time

        # Step 9: Log timing breakdown
        timings = tracker.get_all()
        logger.info(
            f"⏱️  [Agent:{self.name}] LLM response received: {timings['llm_api']:.3f}s"
        )
        logger.info(
            f"📊 [Agent:{self.name}] Request breakdown - "
            f"Total: {total_time:.3f}s | "
            f"Message: {timings.get('message_processing', 0):.3f}s | "
            f"Tools: {timings.get('tools_conversion', 0):.3f}s | "
            f"Begin: {timings.get('begin_chunk', 0):.3f}s | "
            f"LLM: {timings['llm_api']:.3f}s"
        )

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
        execution_context_id: str
        | None = None,  # NEW: Context ID for message filtering
    ) -> ResponseDetails | AgentTransfer:
        response_format = response_format or self.response_format
        history = copy.deepcopy(messages)
        tool_timeout = tool_timeout or self.tool_timeout

        if history and any("execution_context_id" in msg for msg in history):
            # History contains messages with execution_context_id, need to filter
            filtered_messages = []
            for msg in history:
                if execution_context_id is not None:
                    # Sub-agent: only include messages tagged with our execution_context_id
                    if msg.get("execution_context_id") == execution_context_id:
                        filtered_messages.append(msg)
                else:
                    # Inline agent: only include messages without execution_context_id
                    if msg.get("execution_context_id") is None:
                        filtered_messages.append(msg)
            history = filtered_messages

        system_prompt = build_system_prompt(
            self.instructions,
            plan_mode=self.plan_mode,
            can_delegate=self.can_delegate,
            system_prompt_mode=self.system_prompt_mode,
        )
        current_timestamp = time.time()

        if (len(history) > 0) and (history[0]["role"] == "system"):
            history[0]["content"] = system_prompt
            history[0]["timestamp"] = current_timestamp  # 添加时间戳
            if "id" not in history[0]:
                history[0]["id"] = str(uuid4())  # 添加唯一ID（如果没有的话）
            # Mark system message with execution_context_id (not done in _process_step_message)
            if execution_context_id is not None:
                history[0]["execution_context_id"] = execution_context_id
        else:
            system_msg = {
                "role": "system",
                "content": system_prompt,
                "timestamp": current_timestamp,  # 添加时间戳
                "id": str(uuid4()),  # 添加唯一ID
            }
            # Mark system message with execution_context_id (not done in _process_step_message)
            if execution_context_id is not None:
                system_msg["execution_context_id"] = execution_context_id
            history.insert(0, system_msg)
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

            # If no tool calls, stop conversation
            if not message.get("tool_calls"):
                break

            tool_messages = await self._handle_tool_calls(
                message["tool_calls"],
                context_variables=context_variables,
                timeout=tool_timeout,
                check_stop=check_stop,
            )

            # Filter out all transfer messages - they will be handled in _prepare_execution_context()
            # Both call_agent and transfer_to_* transfers should skip history addition here
            non_transfer_messages = []
            transfer_message = None

            for msg in tool_messages:
                if msg.get("transfer"):
                    # Skip all transfer messages
                    transfer_message = msg
                else:
                    # Keep regular tool messages
                    non_transfer_messages.append(msg)

            history.extend(non_transfer_messages)
            for msg in non_transfer_messages:
                self.events_queue.put_nowait(msg)

            if process_step_message:
                for msg in process_messages_for_hook_func(non_transfer_messages):
                    await run_func(process_step_message, msg)

            if transfer_message:
                transfer = AgentTransfer(
                    from_agent=self.name,
                    to_agent=transfer_message["content"],
                    history=history,
                    context_variables=context_variables,
                    init_message_length=init_len,
                    tool_call_id=transfer_message.get("tool_call_id"),
                )
                return transfer

            # Check for exit_plan_mode tool call - stop execution for user approval
            for msg in tool_messages:
                if msg.get("tool_name") == "exit_plan_mode":
                    raw_content = msg.get("raw_content", {})
                    if isinstance(raw_content, dict) and raw_content.get(
                        "exit_plan_mode"
                    ):
                        logger.info(
                            f"🔒 [Agent:{self.name}] exit_plan_mode called - stopping execution for user approval"
                        )
                        # Stop conversation loop - user needs to approve the plan
                        break

        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
            execution_context_id=execution_context_id,  # NEW: Pass context ID through response
        )

    async def _create_delegation_task_message(
        self,
        transfer: AgentTransfer,
        execution_context_id: str | None = None,
    ) -> dict | None:
        """Create task context message for call_agent delegation with summary."""
        if not transfer.instruction:
            return None

        # Generate summary if execution_context_id is provided (call_agent delegation)
        summary_text = None
        if execution_context_id and transfer.history:
            try:
                from .chatroom.special_agents import get_summary_generator

                summary_gen = get_summary_generator()
                summary_text = await summary_gen.generate_summary(
                    transfer.history, max_tokens=1000
                )
            except Exception as e:
                logger.warning(f"Failed to generate summary for delegation: {e}")

        # Merge summary and instruction for context isolation
        content_parts = []
        if summary_text:
            content_parts.append(f"Context Summary:\n{summary_text}")
        content_parts.append(f"Task: {transfer.instruction}")
        combined_content = "\n\n".join(content_parts)

        return {
            "role": "user",
            "content": combined_content,
            "timestamp": time.time(),
            "id": str(uuid4()),
            "execution_context_id": transfer.execution_context_id,
        }

    async def _input_to_openai_messages(
        self,
        msg: AgentInput,
        execution_context_id: str
        | None = None,  # NEW: Context ID for generating summary
    ) -> list[dict]:
        """Convert input to OpenAI message format.

        Handles conversion of user input types to standardized message dicts:
        - VisionInput: Converts vision input to messages
        - BaseModel: Converts to JSON user message
        - str: Wraps in user message dict
        - list: Processes each item and accumulates messages

        Note: AgentTransfer is NOT handled here - it's processed directly in
        _prepare_execution_context() before calling this method.

        Args:
            msg: The input message (user input types, not AgentTransfer)
            execution_context_id: Context ID for generating summary in delegation

        Returns:
            List of message dicts in OpenAI format
        """
        assert isinstance(
            msg, (list, str, BaseModel, AgentResponse, VisionInput, dict)
        ), (
            "Message must be a list, string, BaseModel, AgentResponse, VisionInput, or dict"
        )
        if isinstance(msg, AgentResponse):
            # For accepting the result of previous run or other agent
            msg = msg.content

        # Convert message to OpenAI message format based on type
        if isinstance(msg, dict):
            # Handle dict messages (e.g., tool_message from team.run() loop)
            # These are already in OpenAI message format
            if msg.get("role") == "tool":
                # Tool message from sub-agent completion
                converted_messages = [msg]
            else:
                # Regular dict message, wrap as user message
                converted_messages = [_create_user_message(msg)]
        elif isinstance(msg, VisionInput):
            # Vision input: use specialized converter
            converted_messages = vision_to_openai(msg)

        elif isinstance(msg, BaseModel):
            # BaseModel input: convert to JSON user message
            converted_messages = [_create_user_message(msg.model_dump_json())]

        elif isinstance(msg, str):
            # String input: wrap in user message
            converted_messages = [_create_user_message(msg)]

        elif isinstance(msg, list):
            # List input: process each item and accumulate
            converted_messages = []
            for item in msg:
                if isinstance(item, str):
                    # String item: create user message
                    converted_messages.append(_create_user_message(item))
                elif isinstance(item, VisionInput):
                    # Vision input: extend with converted vision messages
                    converted_messages.extend(vision_to_openai(item))
                elif isinstance(item, BaseModel):
                    # BaseModel item: convert to JSON user message
                    converted_messages.append(
                        _create_user_message(item.model_dump_json())
                    )
                else:
                    # Dict item: use as-is
                    assert isinstance(item, dict), (
                        "Message must be a string, BaseModel or dict"
                    )
                    converted_messages.append(item)

        return converted_messages

    def _merge_with_memory(
        self,
        input_messages: list[dict],
        memory: Memory | None,
        use_memory: bool,
    ) -> list[dict]:
        """Merge input messages with memory if enabled.

        Note: No cleanup is performed. In flat memory model, all messages
        are preserved as they represent the complete history. Agents filter
        messages by execution_context_id to get their relevant context.
        """
        if not use_memory or not memory:
            return input_messages

        # Preserve complete history - cleanup() violated flat memory principles
        # by deleting messages that are part of the true history
        historical_messages = memory.get_messages()
        return historical_messages + input_messages

    async def _prepare_execution_context(
        self,
        msg: AgentInput,
        execution_context_id: str | None = None,
        context_variables: dict | None = None,
        memory: Memory | None = None,
        use_memory: bool | None = None,
    ) -> ExecutionContext:
        """Prepare execution context based on input type.

        Handles two paths:
        1. AgentTransfer (delegated from another agent): Uses transfer's history and context
        2. Normal user input: Converts input to messages and optionally merges with memory
        """
        # Determine whether to use memory
        should_use_memory = use_memory if use_memory is not None else self.use_memory
        memory_instance = memory or self.memory

        input_messages = None  # Only set for normal user input, not AgentTransfer

        if isinstance(msg, AgentTransfer):
            # Delegation path: use transfer's history and context
            conversation_history = msg.history
            runtime_context = msg.context_variables

            # If instruction provided (from call_agent delegation), create task context message
            if msg.instruction:
                task_msg = await self._create_delegation_task_message(
                    msg, execution_context_id=execution_context_id
                )
                if task_msg:
                    # Only use task_msg, discard conversation_history from parent
                    # because _run_stream filtering will exclude messages without matching execution_context_id
                    conversation_history = [task_msg]
                    # Set input_messages for call_agent case for proper message processing
                    input_messages = [task_msg]
            # For transfer_to_* (no instruction), tool_message will be created in team.py
        else:
            # User input path: convert input to messages
            input_messages = await self._input_to_openai_messages(
                msg, execution_context_id=execution_context_id
            )
            conversation_history = self._merge_with_memory(
                input_messages, memory_instance, should_use_memory
            )
            runtime_context = context_variables or {}

        return ExecutionContext(
            conversation_history=conversation_history,
            runtime_context=runtime_context,
            execution_context_id=execution_context_id,
            should_use_memory=should_use_memory,
            memory_instance=memory_instance,
            input_messages=input_messages,  # NEW: Pass converted messages for memory update
        )

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
        execution_context_id: str | None = None,
    ) -> AgentResponse | AgentTransfer:
        """Run the agent.

        Args:
            msg: The input message to the agent.
            response_format: The response format to use.
            tool_use: Whether to use tools.
            context_variables: Runtime variables available to tools during execution.
                These are stored in ExecutionContext.runtime_context and injected
                into tools that request them via the 'context_variables' parameter.
                Example: {'user_id': '123', 'session_id': 'abc'}
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
            execution_context_id: Unique identifier for sub-agent delegation contexts.
                Used for message filtering in _run_stream to isolate messages by
                delegation context. None for inline agents.

        Returns:
            The agent response. Either an AgentResponse or an AgentTransfer.
            If the agent is interrupted, the AgentResponse will have the interrupt flag set to True.
            If the agent is transferring to another agent, the AgentTransfer will be returned.
        """
        # Prepare execution context based on input type
        # This handles both AgentTransfer delegation and normal user input paths
        exec_context = await self._prepare_execution_context(
            msg,
            execution_context_id=execution_context_id,
            context_variables=context_variables,
            memory=memory,
            use_memory=use_memory,
        )

        # Prepare response format
        response_format_to_use = response_format or self.response_format

        # Add client ID to runtime context
        exec_context.runtime_context[_CLIENT_ID_NAME] = (
            exec_context.memory_instance.id if exec_context.memory_instance else ""
        )

        # ============ Unified message processing: Always detect attachments ============
        async def _process_step_message(step_message: dict):
            # Get execution_context_id from ExecutionContext
            if exec_context.execution_context_id is not None:
                step_message["execution_context_id"] = exec_context.execution_context_id

            await _detect_attachments(step_message)

            if update_memory and exec_context.memory_instance:
                exec_context.memory_instance.add_messages([step_message])

            if process_step_message is not None:
                await run_func(process_step_message, step_message)

        # now user input also goes through the pipeline
        # all messages: (user input, sub agent mock user input, sub agent transfer tool response)
        for msg in exec_context.input_messages or []:
            await _process_step_message(msg)

        try:
            run_result = await self._run_stream(
                messages=exec_context.conversation_history,
                response_format=response_format_to_use,
                tool_use=tool_use,
                context_variables=exec_context.runtime_context,
                process_chunk=process_chunk,
                process_step_message=_process_step_message,
                check_stop=check_stop,
                tool_timeout=tool_timeout,
                model=model,
                allow_transfer=allow_transfer,
                execution_context_id=exec_context.execution_context_id,
            )
        except StopRunning:
            logger.info("StopRunning")
            if update_memory and exec_context.memory_instance:
                exec_context.memory_instance.cleanup()
            return AgentResponse(
                agent_name=self.name,
                content="",
                details=None,
                interrupt=True,
            )

        # Handle response - could be AgentTransfer or ResponseDetails
        if isinstance(run_result, AgentTransfer):
            return run_result
        else:
            # Extract content from the last message
            last_message = run_result.messages[-1]
            if response_format_to_use:
                content = last_message.get("parsed")
            else:
                content = last_message.get("content")
            return AgentResponse(
                agent_name=self.name,
                content=content,
                details=run_result,
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


# ===== Utility Functions =====


def _create_user_message(content: str) -> dict:
    """Create a standard user message dict."""
    return {
        "role": "user",
        "content": content,
        "timestamp": time.time(),
        "id": str(uuid4()),
    }


async def _detect_attachments(step_message: dict) -> None:
    """Helper: Detect attachments in a message (independent of memory saving)."""
    try:
        from .message.attachment_pipeline import get_message_processor

        processor = get_message_processor()
        processed = await processor.process_message_with_attachments(step_message)

        step_message["detected_attachments"] = processed.get("detected_attachments", [])
    except Exception as e:
        logger.warning(f"Error in attachment detection: {e}")


def filter_inline_messages(messages: list[dict]) -> list[dict]:
    """Filter out sub-agent messages, keeping only inline agent messages."""
    inline_messages = []

    for msg in messages:
        # Always keep system messages - they apply to all agents
        if msg.get("role") == "system":
            inline_messages.append(msg)
            continue

        # Keep messages without execution_context_id (inline agent messages)
        # Skip messages with execution_context_id (sub-agent messages)
        if msg.get("execution_context_id") is None:
            inline_messages.append(msg)

    return inline_messages
