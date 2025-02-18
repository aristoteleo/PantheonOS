import copy
import json
import asyncio
from typing import Callable, Any
from uuid import uuid4

from pydantic import BaseModel, create_model
from funcdesc import parse_func
from magique.client import ServiceProxy

from .utils.misc import desc_to_openai_dict, run_func
from .utils.llm import (
    acompletion_openai,
    process_messages,
    process_messages_for_save,
    acompletion_openai,
    acompletion_litellm,
)
from .utils.vision import vision_to_openai
from .types import AgentResponse, ResponseDetails, AgentInput, AgentTransfer, VisionInput
from .remote.utils import connect_remote
from .remote.constant import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT


__CTX_VARS_NAME__ = "context_variables"


class Agent:
    def __init__(
        self,
        name: str,
        instructions: str,
        model: str = "gpt-4o-mini",
        tools: list[Callable] | None = None,
        response_format: Any | None = None,
        use_memory: bool = True,
        memory: list[dict] | None = None,
        tool_timeout: int | None = 10 * 60,
        force_litellm: bool = False,
    ):
        self.id = uuid4()
        self.name = name
        self.instructions = instructions
        self.model = model
        self.functions: dict[str, Callable] = {}
        self.toolset_proxies: dict[str, ServiceProxy] = {}
        self._func_to_proxy: dict[str, str] = {}
        if tools:
            for func in tools:
                self.tool(func)
        self.response_format = response_format
        self.use_memory = use_memory
        self.memory = memory or []
        self.tool_timeout = tool_timeout
        self.events_queue: asyncio.Queue = asyncio.Queue()
        self.force_litellm = force_litellm
        # Restrict message targets in meeting
        self.message_to: None | list[str] = None

    def tool(self, func: Callable):
        """
        Add a tool to the agent.
        """
        self.functions[func.__name__] = func
        return self

    async def remote_toolset(
            self,
            service_id_or_name: str,
            server_host: str = DEFAULT_SERVER_HOST,
            server_port: int = DEFAULT_SERVER_PORT,
            **kwargs,
            ):
        """Add a remote toolset to the agent."""
        s = await connect_remote(
            service_id_or_name,
            server_host,
            server_port,
            **kwargs,
        )
        self.toolset_proxies[s.service_info.service_id] = s
        return self

    def _convert_functions(self, litellm_mode: bool) -> list[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            func_dict = desc_to_openai_dict(
                parse_func(func),
                skip_params=[__CTX_VARS_NAME__, "__client_id__"],
                litellm_mode=litellm_mode,
            )
            functions.append(func_dict)

        for proxy in self.toolset_proxies.values():
            for name, desc in proxy.service_info.functions_description.items():
                self._func_to_proxy[name] = proxy.service_info.service_id
                func_dict = desc_to_openai_dict(
                    desc,
                    skip_params=[__CTX_VARS_NAME__, "__client_id__"],
                    litellm_mode=litellm_mode,
                )
                functions.append(func_dict)
        return functions

    async def handle_tool_calls(
            self,
            tool_calls: list,
            context_variables: dict,
            timeout: int,
            ) -> list[dict]:
        messages = []
        for call in tool_calls:
            try:
                func_name = call["function"]["name"]
                params = json.loads(call["function"]["arguments"])
                if func_name in self.functions:
                    # call local functions
                    func = self.functions[func_name]
                    if __CTX_VARS_NAME__ in func.__code__.co_varnames:
                        params[__CTX_VARS_NAME__] = context_variables
                    result = await asyncio.wait_for(
                        run_func(func, **params),
                        timeout=timeout,
                    )
                else:
                    # remote toolset
                    assert func_name in self._func_to_proxy, \
                        f"Function `{func_name}` is not found in the toolset or local functions"
                    proxy = self.toolset_proxies[self._func_to_proxy[func_name]]
                    result = await asyncio.wait_for(
                        proxy.invoke(func_name, parameters=params),
                        timeout=timeout,
                    )
            except Exception as e:
                result = repr(e)

            context_variables[call["id"]] = result
            if isinstance(result, Agent):
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "tool_name": func_name,
                    "content": result.name,
                    "transfer": True,
                })
            else:
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "tool_name": func_name,
                    "content": repr(result),
                })
        return messages

    async def acompletion(
            self,
            messages: list[dict],
            model: str,
            tool_use: bool = True,
            response_format: Any | None = None,
            process_chunk: Callable | None = None,
            ) -> dict:
        force_litellm = self.force_litellm
        messages = process_messages(messages, model)
        provider = "openai"
        if "/" in model:
            provider = model.split("/")[0]
        litellm_mode = (provider != "openai") or force_litellm

        tools = None
        if tool_use:
            tools = self._convert_functions(litellm_mode) or None

        if not litellm_mode:
            complete_resp = await acompletion_openai(
                messages=messages,
                model=model,
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

    async def run_stream(
        self,
        messages: list[dict],
        process_chunk: Callable | None = None,
        process_step_message: Callable | None = None,
        max_turns: int | float = float("inf"),
        context_variables: dict | None = None,
        response_format: Any | None = None,
        tool_use: bool = True,
        tool_timeout: int | None = None,
        model: str | None = None,
    ) -> ResponseDetails | AgentTransfer:
        model = model or self.model
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

        while len(history) - init_len < max_turns:
            message = {}

            message = await self.acompletion(
                history,
                model=model,
                tool_use=tool_use,
                response_format=Response,
                process_chunk=process_chunk,
            )

            if Response is not None:
                content = message.get("content")
                if content:
                    parsed = Response.model_validate_json(content)
                    message["parsed"] = parsed.result

            history.append(message)
            self.events_queue.put_nowait(message)
            if process_step_message:
                await run_func(process_step_message, message)

            if not message["tool_calls"]:
                break

            tool_messages = await self.handle_tool_calls(
                message["tool_calls"],
                context_variables=context_variables,
                timeout=tool_timeout,
            )
            history.extend(tool_messages)
            for msg in tool_messages:
                self.events_queue.put_nowait(msg)

            if process_step_message:
                for msg in tool_messages:
                    await run_func(process_step_message, msg)

            for msg in tool_messages:
                if msg.get("transfer"):
                    return AgentTransfer(
                        from_agent=self.name,
                        to_agent=msg["content"],
                        history=history,
                        context_variables=context_variables,
                    )

        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
        )

    def input_to_openai_messages(
            self,
            msg: AgentInput,
            use_memory: bool,
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

        if use_memory:
            messages = self.memory + messages
        return messages


    async def run(
            self, msg: AgentInput,
            response_format: Any | None = None,
            tool_use: bool = True,
            context_variables: dict | None = None,
            process_chunk: Callable | None = None,
            process_step_message: Callable | None = None,
            use_memory: bool | None = None,
            update_memory: bool = True,
            tool_timeout: int | None = None,
            model: str | None = None,
            ) -> AgentResponse | AgentTransfer:
        """Run the agent.

        Args:
            msg: The input message to the agent.
            response_format: The response format to use.
            tool_use: Whether to use tools.
            context_variables: The context variables to use.
            process_chunk: The function to process the chunk.
            process_step_message: The function to process the step message.
            use_memory: Whether to use short term memory.
            update_memory: Whether to update the short term memory.
            tool_timeout: The timeout for the tool.
            model: The model to use.
        """
        _use_m = self.use_memory
        if use_memory is not None:
            _use_m = use_memory
        messages = self.input_to_openai_messages(msg, _use_m)
        response_format = response_format or self.response_format
        context_variables = context_variables or {}
        if isinstance(msg, AgentTransfer):
            context_variables = msg.context_variables

        details = await self.run_stream(
            messages=messages,
            response_format=response_format,
            tool_use=tool_use,
            context_variables=context_variables,
            process_chunk=process_chunk,
            process_step_message=process_step_message,
            tool_timeout=tool_timeout,
            model=model,
        )

        if isinstance(details, AgentTransfer):
            return details
        else:
            final_msg = details.messages[-1]
            if response_format:
                content = final_msg.get("parsed")
            else:
                content = final_msg.get("content")
            if self.use_memory and update_memory:
                self.memory = messages + details.messages
            return AgentResponse(
                agent_name=self.name,
                content=content,
                details=details,
            )

    async def chat(self, message: str | dict | None = None):
        """Chat with the agent with a REPL interface."""
        from .repl.single import Repl
        repl = Repl(self)
        await repl.run(message)

    def save_memory(self, file_path: str):
        """Save the memory to a file."""
        with open(file_path, "w") as f:
            processed_memory = process_messages_for_save(self.memory)
            json.dump(processed_memory, f)

    def load_memory(self, file_path: str):
        """Load the memory from a file."""
        with open(file_path, "r") as f:
            self.memory = json.load(f)

    async def serve(self, **kwargs):
        """Serve the agent to a remote server."""
        from .remote.agent import AgentService
        service = AgentService(self, **kwargs)
        return await service.run()
