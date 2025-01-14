import copy
import json
import inspect
from typing import Optional, List, Callable, Union
from uuid import uuid4

from pydantic import BaseModel
from litellm import acompletion, stream_chunk_builder

from .utils.misc import func_to_openai_dict
from .types import AgentResponse, ResponseDetails, AgentInput


__CTX_VARS_NAME__ = "context_variables"


class Agent:
    def __init__(
        self,
        name: str,
        instructions: str,
        model: str = "gpt-4o-mini",
        tools: Optional[List[Callable]] = None,
        response_format: Optional[BaseModel] = None,
        use_short_term_memory: bool = True,
        short_term_memory: Optional[List[dict]] = None,
    ):
        self.id = uuid4()
        self.name = name
        self.instructions = instructions
        self.model = model
        self.functions = {}
        if tools:
            for func in tools:
                self.functions[func.__name__] = func
        self.response_format = response_format
        self.use_short_term_memory = use_short_term_memory
        self.short_term_memory = short_term_memory or []

    def tool(self, func: Callable):
        """
        Add a tool to the agent.
        """
        self.functions[func.__name__] = func
        return self

    def _convert_functions(self) -> List[dict]:
        """Convert function to the format that the model can understand."""
        functions = []

        for func in self.functions.values():
            func_dict = func_to_openai_dict(
                func,
                skip_params=[__CTX_VARS_NAME__],
            )
            functions.append(func_dict)
        return functions

    async def handle_tool_calls(
            self, tool_calls: List, context_variables: dict):
        messages = []
        for call in tool_calls:
            func_name = call["function"]["name"]
            params = json.loads(call["function"]["arguments"])
            func = self.functions[func_name]
            if __CTX_VARS_NAME__ in func.__code__.co_varnames:
                params[__CTX_VARS_NAME__] = context_variables
            if inspect.iscoroutinefunction(func):
                result = await func(**params)
            else:
                result = func(**params)
            context_variables[call["id"]] = result
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "tool_name": func_name,
                "content": repr(result),
            })
        return messages

    async def run_stream(
        self,
        messages: List[dict],
        process_chunk: Optional[Callable] = None,
        process_step_message: Optional[Callable] = None,
        max_turns: Union[int, float] = float("inf"),
        context_variables: Optional[dict] = None,
        response_format: Optional[BaseModel] = None,
        tool_use: bool = True,
    ):
        response_format = response_format or self.response_format
        history = copy.deepcopy(messages)
        history.insert(0, {"role": "system", "content": self.instructions})
        init_len = len(history)
        if context_variables is None:
            context_variables = {}

        while len(history) - init_len < max_turns:

            message = {}
            history_clear_parsed = copy.deepcopy(history)
            for msg in history_clear_parsed:
                if "parsed" in msg:
                    del msg["parsed"]
            if tool_use:
                tools = self._convert_functions() or None
            else:
                tools = None
            response = await acompletion(
                model=self.model,
                messages=history_clear_parsed,
                tools=tools,
                response_format=response_format,
                stream=True,
            )
            async for chunk in response:
                if process_chunk:
                    choice = chunk.choices[0]
                    if choice.finish_reason == "stop":
                        break
                    process_chunk(choice.delta.model_dump())
            complete_resp = stream_chunk_builder(response.chunks)
            message = complete_resp.choices[0].message.model_dump()

            if response_format is not None:
                content = message.get("content")
                if content:
                    parsed = response_format.model_validate_json(content)
                    message["parsed"] = parsed

            history.append(message)
            if process_step_message:
                process_step_message(message)

            if not message["tool_calls"]:
                break

            tool_messages = await self.handle_tool_calls(
                message["tool_calls"],
                context_variables=context_variables,
            )
            history.extend(tool_messages)
            if process_step_message:
                for msg in tool_messages:
                    process_step_message(msg)

        return ResponseDetails(
            messages=history[init_len:],
            context_variables=context_variables,
        )

    def input_to_openai_messages(
            self,
            msg: AgentInput,
            use_short_term_memory: bool,
            ) -> List[dict]:
        assert isinstance(msg, (list, str, BaseModel, AgentResponse)), \
            "Message must be a list, string, BaseModel or AgentResponse"
        if isinstance(msg, AgentResponse):
            # For acceping the result of previous run or other agent
            msg = msg.content

        # Convert message to the openai message format
        if isinstance(msg, BaseModel):
            messages = [{"role": "user", "content": msg.model_dump_json()}]
        elif isinstance(msg, str):
            messages = [{"role": "user", "content": msg}]
        elif isinstance(msg, list):
            new_messages = []
            for m in msg:
                if isinstance(m, BaseModel):
                    new_messages.append(
                        {"role": "user", "content": m.model_dump_json()})
                elif isinstance(m, str):
                    new_messages.append({"role": "user", "content": m})
                else:
                    assert isinstance(m, dict), \
                        "Message must be a string, BaseModel or dict"
                    new_messages.append(m)
            messages = new_messages
        if use_short_term_memory:
            messages = self.short_term_memory + messages
        return messages

    async def run(
            self, msg: AgentInput,
            response_format: Optional[BaseModel] = None,
            tool_use: bool = True,
            context_variables: Optional[dict] = None,
            process_chunk: Optional[Callable] = None,
            process_step_message: Optional[Callable] = None,
            use_short_term_memory: bool | None = None,
            ) -> AgentResponse:
        """Run the agent.

        Args:
            msg: The input message to the agent.
            response_format: The response format to use.
            tool_use: Whether to use tools.
            context_variables: The context variables to use.
            process_chunk: The function to process the chunk.
            process_step_message: The function to process the step message.
            use_short_term_memory: Whether to use short term memory.
        """
        _use_sm = self.use_short_term_memory
        if use_short_term_memory is not None:
            _use_sm = use_short_term_memory
        messages = self.input_to_openai_messages(msg, _use_sm)
        response_format = response_format or self.response_format
        details = await self.run_stream(
            messages=messages,
            response_format=response_format,
            tool_use=tool_use,
            context_variables=context_variables,
            process_chunk=process_chunk,
            process_step_message=process_step_message,
        )
        final_msg = details.messages[-1]
        if response_format:
            content = final_msg.get("parsed")
        else:
            content = final_msg.get("content")
        if self.use_short_term_memory:
            self.short_term_memory.extend(details.messages)
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
