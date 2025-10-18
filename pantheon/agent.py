import asyncio
import copy
import json
import os
import sys
import time
from typing import Any, Callable, Optional, Protocol
from uuid import uuid4

from fastmcp import Client
from funcdesc import Description, Value, parse_func
from pydantic import BaseModel, create_model

from .constant import build_system_prompt
from .memory import Memory
from .remote import (
    RemoteBackendFactory,
    RemoteConfig,
    RemoteWorker,
)
from .toolset import ToolSet
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


_CTX_VARS_NAME = "context_variables"
_AGENT_RUN_NAME = "agent_run"
_SKIP_PARAMS = [_CTX_VARS_NAME, _AGENT_RUN_NAME]
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

        # Plan Mode support
        self.plan_mode = False

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

        # Unified approach: Ensure all functions have function_descriptions
        # Skip if already has function_descriptions (e.g., proxy wrappers)
        # Skip if has __schema__ (e.g., MCP tools - different format)
        if not hasattr(func, "function_descriptions") and not hasattr(
            func, "__schema__"
        ):
            # Parse function to create Description object
            if hasattr(func, "parameters") and isinstance(func.parameters, list):
                # Reverse callable - create Description manually
                func.function_descriptions = Description(
                    inputs=[Value(type_=str, name=p) for p in func.parameters],
                    name=getattr(func, "name", key),
                )

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

    async def toolset(self, toolset: "ToolSet") -> "Agent":
        """Add a local toolset to the agent."""
        if isinstance(toolset, ToolSet):
            # Local ToolSet - immediately load all tools
            for name, (func, _) in toolset.tool_functions.items():
                self.tool(func, key=name)
            logger.info(
                f"Agent '{self.name}': Added local toolset with {len(toolset.functions)} tools"
            )
        else:
            raise TypeError(
                f"Invalid toolset type: {type(toolset)}. "
                f"ToolsetProxy should be managed at ChatRoom/Factory level."
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

    def _convert_functions(
        self, litellm_mode: bool, allow_transfer: bool
    ) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            # Special case: MCP tools with __schema__
            if hasattr(func, "__schema__"):
                schema = copy.deepcopy(func.__schema__)
                if litellm_mode:
                    schema["function"]["strict"] = False
                    del schema["function"]["parameters"]["additionalProperties"]
                functions.append(func.__schema__)
                continue

            if hasattr(func, "function_descriptions"):
                # This is a remote function wrapper with stored descriptions
                desc = func.function_descriptions
            else:
                # All other functions (local) can be parsed normally
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
                params = json.loads(call["function"]["arguments"]) or {}

                # All functions are now in self.functions (unified approach)
                assert func_name in self.functions, (
                    f"Function `{func_name}` is not found in self.functions"
                )

                func = self.functions[func_name]

                # Handle parameter injection - unified approach
                # All functions now have function_descriptions (except MCP with __schema__)
                if hasattr(func, "__schema__"):
                    var_names = func.__code__.co_varnames
                elif hasattr(func, "function_descriptions"):
                    var_names = [v.name for v in func.function_descriptions.inputs]
                else:
                    var_names = func.__code__.co_varnames

                if _CTX_VARS_NAME in var_names:
                    params[_CTX_VARS_NAME] = context_variables
                if _AGENT_RUN_NAME in var_names:
                    params[_AGENT_RUN_NAME] = agent_run

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
                tool_messages.append(
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
                tool_messages.append(
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

        system_prompt = build_system_prompt(self.instructions, plan_mode=self.plan_mode)
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

            # If no tool calls, stop conversation
            if not message.get("tool_calls"):
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
        context_variables[_CLIENT_ID_NAME] = memory.id

        async def _detect_attachments(step_message: dict) -> None:
            """Helper: Detect attachments in a message (independent of memory saving)."""
            try:
                from .message.attachment_pipeline import get_message_processor

                processor = get_message_processor()
                processed = await processor.process_message_with_attachments(
                    step_message
                )

                step_message["detected_attachments"] = processed.get(
                    "detected_attachments", []
                )
            except Exception as e:
                logger.warning(f"Error in attachment detection: {e}")

        if update_memory:
            memory.add_messages(new_input_messages)

        # ============ Unified message processing: Always detect attachments ============
        async def _process_step_message(step_message: dict):
            await _detect_attachments(step_message)

            if update_memory:
                memory.add_messages([step_message])

            if process_step_message is not None:
                await run_func(process_step_message, step_message)

        try:
            details = await self._run_stream(
                messages=messages,
                response_format=response_format,
                tool_use=tool_use,
                context_variables=context_variables,
                process_chunk=process_chunk,
                process_step_message=_process_step_message,
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
