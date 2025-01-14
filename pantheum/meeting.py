import copy
from typing import List, Literal
import asyncio
import datetime

from pydantic import BaseModel

from .agent import Agent
from .types import Task


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


class AgentBeginEvent(BaseModel):
    agent_name: str


def format_record(record: Record) -> str:
    return (
        f"# Meeting message\n"
        f"Timestamp: {record.timestamp}\n"
        f"Source: {record.source}\n"
        f"Targets: {record.targets}\n"
        f"Content:\n{record.content}\n"
    )


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
            public_queue: asyncio.Queue,
            stream_queue: asyncio.Queue):
        self.agent = agent
        self.public_queue = public_queue
        self.queue = asyncio.Queue()
        self.stream_queue = stream_queue

    async def process_step_message(self, message: dict):
        if message.get("tool_calls"):
            tool_calls = message["tool_calls"]
            for tool_call in tool_calls:
                event = ToolEvent(
                    agent_name=self.agent.name,
                    tool_name=tool_call["function"]["name"],
                    tool_args_info=tool_call["function"]["arguments"],
                )
                await self.stream_queue.put(event)
        if message.get("role") == "tool":
            event = ToolResponseEvent(
                agent_name=self.agent.name,
                tool_name=message.get("tool_name"),
                tool_response=message.get("content"),
            )
            await self.stream_queue.put(event)

    async def run(self):
        while True:
            record = await self.queue.get()
            prompt = format_record(record)

            await self.stream_queue.put(
                AgentBeginEvent(agent_name=self.agent.name)
            )
            resp = await self.agent.run(
                prompt,
                response_format=Message,
                process_step_message=self.process_step_message,
            )
            record = message_to_record(resp.content, self.agent.name)
            await self.public_queue.put(record)


class Meeting:
    def __init__(self, agents: List[Agent]):
        self.agents = {agent.name: copy.deepcopy(agent) for agent in agents}
        self.inject_instructions()
        self.public_queue = asyncio.Queue()
        self.stream_queue = asyncio.Queue()
        self.agent_runners = {
            agent.name: AgentRunner(agent, self.public_queue, self.stream_queue)
            for agent in agents
        }

    def inject_instructions(self):
        for agent in self.agents.values():
            agent.instructions += (
                f"You are a meeting participant, your name is {agent.name}, "
                f"Don't send message to 'all', when it's not necessary. "
                f"Don't repeat the input message in your response."
            )

    async def process_public_queue(self):
        while True:
            record = await self.public_queue.get()
            await self.stream_queue.put(record)
            if record.targets == "all":
                for runner in self.agent_runners.values():
                    if runner.agent.name != record.source:
                        await runner.queue.put(record)
            elif isinstance(record.targets, list):
                for target in record.targets:
                    await self.agent_runners[target].queue.put(record)

    async def run(self, initial_message: Record | None = None):
        if initial_message:
            await self.public_queue.put(initial_message)

        await asyncio.gather(
            self.process_public_queue(),
            *[runner.run() for runner in self.agent_runners.values()],
        )


class Team:
    def __init__(
            self,
            leader: Agent,
            members: List[Agent]):
        self.leader = leader
        self.members = members

    async def solve(self, task: Task):
        pass
