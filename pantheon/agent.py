import copy
import json
import time
import asyncio
from typing import Callable, Any
from uuid import uuid4

from pydantic import BaseModel, create_model
from funcdesc import parse_func, Description, Value
from magique.client import ServiceProxy
from magique.worker import ReverseCallable
from pantheon.toolsets.utils.toolset import ToolSet
from pantheon.toolsets.utils.remote import connect_remote

from .utils.misc import desc_to_openai_dict, run_func
from .utils.llm import (
    acompletion_openai,
    process_messages_for_model,
    process_messages_for_hook_func,
    remove_hidden_fields,
    acompletion_litellm,
)
from .utils.vision import vision_to_openai, VisionInput
from .memory import Memory
from .utils.log import logger


DEFAULT_MODEL = "gpt-4.1-mini"

__CTX_VARS_NAME__ = "context_variables"
__AGENT_RUN_NAME__ = "agent_run"
__SKIP_PARAMS__ = [__CTX_VARS_NAME__, __AGENT_RUN_NAME__]
__CLIENT_ID_NAME__ = "client_id"


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


AgentInput = str | BaseModel | AgentResponse | list[str | BaseModel | dict] | AgentTransfer | VisionInput


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
        icon: str = '🤖',
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
        self.functions: dict[str, Callable] = {}
        self.toolset_proxies: dict[str, ServiceProxy] = {}
        self._func_to_proxy: dict[str, str] = {}
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
        return self

    async def remote_toolset(
            self,
            service_id_or_name: str,
            server_url: str | list[str] | None = None,
            **kwargs,
            ):
        """Add a remote toolset to the agent.
        
        Args:
            service_id_or_name: The service ID or name of the toolset.
            server_url: The URL of the magique server.
            **kwargs: Additional keyword arguments to pass to the connect_remote function.

        Returns:
            The agent instance.
        """
        s = await connect_remote(
            service_id_or_name,
            server_url,
            **kwargs,
        )
        self.toolset_proxies[s.service_info.service_id] = s
        return self

    def toolset(self, toolset: ToolSet):
        """Add a toolset to the agent.
        
        Args:
            toolset: The toolset to add to the agent.

        Returns:
            The agent instance.
        """
        for name, (func, _) in toolset.worker.functions.items():
            self.tool(func, key=name)
        return self

    def _convert_functions(self, litellm_mode: bool, allow_transfer: bool) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            if isinstance(func, ReverseCallable):
                desc = Description(
                    inputs=[Value(type_=str, name=p) for p in func.parameters],
                    name=func.name
                )
            else:
                desc = parse_func(func)
            assert isinstance(desc.name, str), "Function name must be a string"
            if not allow_transfer:
                if desc.name.startswith("transfer_to_") or desc.name.startswith("call_agent_"):
                    # NOTE: transfer function should start with `transfer_to_`
                    continue
            func_dict = desc_to_openai_dict(
                desc,
                skip_params=__SKIP_PARAMS__,
                litellm_mode=litellm_mode,
            )
            functions.append(func_dict)

        for proxy in self.toolset_proxies.values():
            for name, desc in proxy.service_info.functions_description.items():
                self._func_to_proxy[name] = proxy.service_info.service_id
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
        from .remote.agent import RemoteAgent
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

        for call in tool_calls:
            try:
                func_name = call["function"]["name"]
                params = json.loads(call["function"]["arguments"])
                if func_name in self.functions:
                    # call local functions
                    func = self.functions[func_name]
                    if isinstance(func, ReverseCallable):
                        var_names = func.parameters
                    else:
                        var_names = func.__code__.co_varnames
                    if __CTX_VARS_NAME__ in var_names:
                        params[__CTX_VARS_NAME__] = context_variables
                    if __AGENT_RUN_NAME__ in var_names:
                        params[__AGENT_RUN_NAME__] = agent_run
                    _func = func
                else:
                    # remote toolset
                    assert func_name in self._func_to_proxy, \
                        f"Function `{func_name}` is not found in the toolset or local functions"
                    proxy = self.toolset_proxies[self._func_to_proxy[func_name]]
                    service_info = await proxy.fetch_service_info()
                    func_desc = service_info.functions_description[func_name]
                    function_args = [v.name for v in func_desc.inputs]
                    if __AGENT_RUN_NAME__ in function_args:
                        params[__AGENT_RUN_NAME__] = agent_run
                    if __CTX_VARS_NAME__ in function_args:
                        params[__CTX_VARS_NAME__] = context_variables
                    async def _func(**params):
                        resp = await proxy.invoke(func_name, parameters=params)
                        if isinstance(resp, dict) and 'inner_call' in resp:
                            inner_call = resp.pop('inner_call')
                            name = inner_call['name']
                            args = inner_call['args']
                            result_field = inner_call['result_field']
                            if name == "__agent_run__":
                                result = await agent_run(args)
                            else:
                                result = await run_func(self.functions[name], **args)
                            resp[result_field] = result
                        return resp
                start_time = time.time()
                task = asyncio.create_task(run_func(_func, **params))
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
            if isinstance(result, (Agent, RemoteAgent)):
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "tool_name": func_name,
                    "content": result.name,
                    "transfer": True,
                })
            else:
                if isinstance(result, dict):
                    processed_result = remove_hidden_fields(result)
                else:
                    processed_result = result
                content = repr(processed_result)
                if self.max_tool_content_length is not None:
                    content = content[:self.max_tool_content_length]
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "tool_name": func_name,
                    "raw_content": result,
                    "content": content,
                })
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
        force_litellm = self.force_litellm
        messages = process_messages_for_model(messages, model)
        provider = "openai"
        if "/" in model:
            provider = model.split("/")[0]
            model_name = model.split("/")[1]
        else:
            model_name = model
        litellm_mode = (provider != "openai") or force_litellm

        tools = None
        if tool_use:
            tools = self._convert_functions(litellm_mode, allow_transfer) or None

        if process_chunk:
            await run_func(process_chunk, {"begin": True})

        if not litellm_mode:
            complete_resp = await acompletion_openai(
                messages=messages,
                model=model_name,
                tools=tools,
                response_format=response_format,
                process_chunk=process_chunk,
            )
            message = complete_resp.choices[0].message.model_dump()
            if "parsed" in message:
                message.pop("parsed")
            if "tool_calls" in message:
                if message["tool_calls"] == []:
                    message['tool_calls'] = None
        else:
            complete_resp = await acompletion_litellm(
                messages=messages,
                model=model,
                tools=tools,
                response_format=response_format,
                process_chunk=process_chunk,
            )
            message = complete_resp.choices[0].message.model_dump()
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
                logger.warning(f"Try to use {model}, because of the error of the previous model.")
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
        if (len(history) > 0) and (history[0]["role"] == "system"):
            history[0]["content"] = self.instructions
        else:
            history.insert(0, {"role": "system", "content": self.instructions})
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

        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
        )

    def _input_to_openai_messages(
            self,
            msg: AgentInput,
            ) -> list[dict]:
        assert isinstance(msg, (list, str, BaseModel, AgentResponse, AgentTransfer, VisionInput)), \
            "Message must be a list, string, BaseModel or AgentResponse, AgentTransfer, VisionInput"
        if isinstance(msg, AgentResponse):
            # For acceping the result of previous run or other agent
            msg = msg.content

        # Convert message to the openai message format
        if isinstance(msg, AgentTransfer):
            messages = msg.history
        elif isinstance(msg, VisionInput):
            messages = vision_to_openai(msg)
        elif isinstance(msg, BaseModel):
            messages = [{"role": "user", "content": msg.model_dump_json()}]
        elif isinstance(msg, str):
            messages = [{"role": "user", "content": msg}]
        elif isinstance(msg, list):
            new_messages = []
            for m in msg:
                if isinstance(m, str):
                    new_messages.append({"role": "user", "content": m})
                elif isinstance(m, VisionInput):
                    new_messages.extend(vision_to_openai(m))
                elif isinstance(m, BaseModel):
                    new_messages.append(
                        {"role": "user", "content": m.model_dump_json()})
                else:
                    assert isinstance(m, dict), \
                        "Message must be a string, BaseModel or dict"
                    new_messages.append(m)
            messages = new_messages
        return messages

    async def run(
            self, msg: AgentInput,
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

    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        from .repl.core import Repl
        repl = Repl(self)
        await repl.run(message)

    async def serve(self, **kwargs):
        """Serve the agent to a remote server."""
        from .remote.agent import AgentService
        service = AgentService(self, **kwargs)
        return await service.run()
