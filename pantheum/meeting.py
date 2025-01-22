import copy
import time
from typing import List, Literal
import asyncio
import datetime

from pydantic import BaseModel

from .agent import Agent


class Record(BaseModel):
    timestamp: str
    source: str
    targets: List[str] | Literal["all", "user"]
    content: str


class Message(BaseModel):
    content: str
    targets: List[str] | Literal["all", "user"]


class ToolEvent(BaseModel):
    agent_name: str
    tool_name: str
    tool_args_info: str


class ToolResponseEvent(BaseModel):
    agent_name: str
    tool_name: str
    tool_response: str


class ThinkingEvent(BaseModel):
    agent_name: str


class StopSignal(BaseModel):
    pass


def message_to_record(message: Message, source: str) -> Record:
    now = datetime.datetime.now()
    return Record(
        timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
        source=source,
        targets=message.targets,
        content=message.content,
    )


class AgentRunner:
    def __init__(
            self,
            agent: Agent,
            meeting: "Meeting",
            message_time_threshold: float = 2.0,
            receive_message_time_threshold: float = 0.5,
            ):
        self.status = "running"
        if 'send_message' not in agent.functions:
            agent.tool(self.send_message)
        self.agent = agent
        self.meeting = meeting
        self.queue = asyncio.Queue()
        self.message_time_threshold = message_time_threshold
        self.receive_message_time_threshold = receive_message_time_threshold
        self.run_start_time = None

    async def process_step_message(self, message: dict):
        if tool_calls := message.get("tool_calls"):
            for tool_call in tool_calls:
                event = ToolEvent(
                    agent_name=self.agent.name,
                    tool_name=tool_call["function"]["name"],
                    tool_args_info=tool_call["function"]["arguments"],
                )
                self.meeting._stream.put_nowait(event)
        if message.get("role") == "tool":
            event = ToolResponseEvent(
                agent_name=self.agent.name,
                tool_name=message.get("tool_name"),
                tool_response=message.get("content"),
            )
            self.meeting._stream.put_nowait(event)
            if event.tool_name == "send_message":
                self.run_start_time = None

    async def process_chunk(self, _):
        if self.run_start_time is not None:
            run_time = time.time() - self.run_start_time
            if run_time > self.message_time_threshold:
                self.meeting._stream.put_nowait(
                    ThinkingEvent(agent_name=self.agent.name)
                )
                self.run_start_time = None

    async def get_events(self, timeout: float | None = None):
        events = []
        timeout = timeout or self.receive_message_time_threshold
        while True:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                events.append(event)
            except asyncio.TimeoutError:
                break
        return events
    
    async def send_message(self, content: str, targets: List[str]):
        message = Message(content=content, targets=targets)
        if self.agent.message_to is not None:
            message.targets = self.agent.message_to
        record = message_to_record(message, self.agent.name)
        if content:
            self.meeting.public_queue.put_nowait(record)

    async def run(self):
        while True:
            if self.status == "sleep":
                await asyncio.sleep(0.5)
            elif self.status == "stop":
                break

            events = await self.get_events()
            if len(events) == 0:
                continue

            prompt = self.meeting.records_to_prompt(events, self.agent)
            self.run_start_time = time.time()
            await self.agent.run(
                prompt,
                process_step_message=self.process_step_message,
                process_chunk=self.process_chunk,
            )
            self.run_start_time = None


class Meeting():
    def __init__(
            self,
            agents: List[Agent],
            shared_memory: bool = False,
            copy_agents: bool = False,
            ):
        self.shared_memory = shared_memory
        self.copy_agents = copy_agents
        self.setup_agents(agents)
        self.public_queue = asyncio.Queue()
        self._stream = asyncio.Queue()
        self.stream_queue = asyncio.Queue()
        self.agent_runners = {
            agent.name: AgentRunner(agent, self)
            for agent in self.agents.values()
        }
        self._records: list[Record] = []
        self.max_rounds = None
        self.round = 0
        self.print_stream = False

    def setup_agents(
            self,
            agents: List[Agent],
            ):
        self.agents = {}
        if self.copy_agents:
            self.agents = {agent.name: copy.deepcopy(agent) for agent in agents}
        else:
            self.agents = {agent.name: agent for agent in agents}
        if self.shared_memory:
            for agent in self.agents.values():
                agent.use_shared_memory = False

    async def process_public_queue(self):
        while True:
            if (self.max_rounds is not None) and (self.round >= self.max_rounds):
                # Stop all agents and break the loops
                await self.stop()
                break
            record = await self.public_queue.get()
            self._stream.put_nowait(record)
            if record.targets == "all":
                for runner in self.agent_runners.values():
                    if runner.agent.name != record.source:
                        runner.queue.put_nowait(record)
            elif isinstance(record.targets, list):
                for target in record.targets:
                    if target in self.agent_runners:
                        self.agent_runners[target].queue.put_nowait(record)
            self.round += 1

    async def stop(self):
        for runner in self.agent_runners.values():
            runner.status = "stop"
        self._stream.put_nowait(StopSignal())

    async def process_stream(self):
        while True:
            event = await self._stream.get()
            self.stream_queue.put_nowait(event)
            if self.print_stream:
                await self.print_stream_event(event)
            if isinstance(event, Record):
                self._records.append(event)
            if isinstance(event, StopSignal):
                break

    async def print_stream_event(self, event):
        if isinstance(event, Record):
            print(f"Round: {self.round}")
            print(self.format_record(event))
        elif isinstance(event, ToolEvent):
            if event.tool_name == "send_message":
                return
            print(f"Tool call: {event.tool_name} with args: {event.tool_args_info}\n")
        elif isinstance(event, ToolResponseEvent):
            if event.tool_name == "send_message":
                return
            print(f"Tool response: {event.tool_response}\n")

    def format_record(self, record: Record) -> str:
        return (
            f"Timestamp: {record.timestamp}\n"
            f"From: {record.source}\n"
            f"To: {record.targets}\n"
            f"Content:\n{record.content}\n"
        )

    def records_to_prompt(
            self,
            records: list[Record],
            agent: Agent,
            inject_instructions: str = "",
            ) -> str:
        if agent.message_to is None:
            participants_str = (
                f"## Current participants\n" +
                "\n".join([f"- {p}" for p in self.agents.keys()])
            )
        else:
            participants_str = ""

        if self.max_rounds is not None:
            left_rounds = self.max_rounds - self.round
            rounds_str = f"The meeting will end after {left_rounds} rounds.\n"
        else:
            rounds_str = ""

        messages_str = "\n".join(
            self.format_record(record)
            for record in records
        )

        if self.shared_memory and len(records) > 0:
            history_str = f"## Meeting history\n" 
            filtered_records = [
                r for r in self._records
                if not (r in records)
            ]
            history_str += self.format_meeting_records(filtered_records)
        else:
            history_str = ""

        return (
            f"# Meeting message\n"
            f"You are a meeting participant, your name is {agent.name}\n"
            f"Don't repeat the input message in your response.\n"
            f"Don't be too modest and polite, be creative and think deeply.\n"
            f"You can ask questions to other participants.\n"
            f"You should use your functions to get more information, before you reply.\n"
            f"You should think step by step, and your answer should be structured and in detail.\n"
            f"You can send message to other participants by calling the function 'send_message'.\n"
            f"{inject_instructions}\n"
            f"{rounds_str}"
            f"{participants_str}\n"
            f"{history_str}\n"
            f"## Messages\n"
            f"{messages_str}"
        )

    def format_meeting_records(self, records: list[Record]) -> str:
        return "\n---\n".join(
            self.format_record(record)
            for record in records
        )

    async def run(
            self,
            initial_message: Record | str | None = None,
            rounds: int | None = None,
            print_stream: bool = False,
            ) -> str:
        """Run the meeting and return the meeting record.
        """
        self._records.clear()
        self.max_rounds = rounds
        self.round = 0
        self.print_stream = print_stream

        if isinstance(initial_message, str):
            msg = Message(
                content=initial_message,
                targets="all",
            )
            initial_message = message_to_record(msg, "user")

        if isinstance(initial_message, Record):
            self.public_queue.put_nowait(initial_message)

        await asyncio.gather(
            self.process_public_queue(),
            self.process_stream(),
            *[runner.run() for runner in self.agent_runners.values()],
        )
        return self.format_meeting_records(self._records)


class BrainStorm(Meeting):
    def __init__(self, agents: List[Agent]):
        super().__init__(agents)
        for agent in self.agents.values():
            agent.message_to = "all"


class UserCentricMeeting(Meeting):
    def __init__(self, agents: List[Agent], shared_memory: bool = True):
        super().__init__(agents, shared_memory=shared_memory)
        for agent in self.agents.values():
            agent.message_to = "user"

    def records_to_prompt(
            self,
            records: list[Record],
            agent: Agent,
            ) -> str:
        inject_instructions = (
            "You only need to reply to the user's message. "
            "And only need call send_message once when you have finished your thinking."
            "And you should reply every message you received with the function 'send_message'.\n"
        )
        return super().records_to_prompt(records, agent, inject_instructions)
