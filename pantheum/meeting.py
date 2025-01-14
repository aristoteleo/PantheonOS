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


class WantResponse(BaseModel):
    want_response: bool


def format_record(record: Record, name: str) -> str:
    return (
        f"# Meeting message\n"
        f"You are a meeting participant, your name is {name}, "
        f"this is the message you received:\n"
        f"Timestamp: {record.timestamp}\n"
        f"Source: {record.source}\n"
        f"Targets: {record.targets}\n"
        f"Content:\n{record.content}\n"
        f"Don't send message to 'all', when it's not necessary."
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
    def __init__(self, agent: Agent, public_queue: asyncio.Queue):
        self.agent = agent
        self.public_queue = public_queue
        self.queue = asyncio.Queue()

    async def run(self):
        while True:
            record = await self.queue.get()
            prompt = format_record(record, self.agent.name)

            resp = await self.agent.run(prompt, response_format=WantResponse)
            if resp.content.want_response:
                resp = await self.agent.run(prompt, response_format=Message)
                record = message_to_record(resp.content, self.agent.name)
                await self.public_queue.put(record)


class Meeting:
    def __init__(self, agents: List[Agent]):
        self.agents = {agent.name: agent for agent in agents}
        self.public_queue = asyncio.Queue()
        self.stream_queue = asyncio.Queue()
        self.agent_runners = {
            agent.name: AgentRunner(agent, self.public_queue)
            for agent in agents
        }

    async def process_public_queue(self):
        while True:
            record = await self.public_queue.get()
            await self.stream_queue.put(record)
            if record.targets == "all":
                for runner in self.agent_runners.values():
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
