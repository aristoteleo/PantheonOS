import asyncio
from abc import ABC

from ..agent import Agent, AgentInput, RemoteAgent


class Team(ABC):
    def __init__(self, agents: list[Agent | RemoteAgent]):
        self.agents = {}
        for agent in agents:
            self.agents[agent.name] = agent
        self.events_queue = asyncio.Queue()

    async def async_setup(self):
        pass

    async def gather_events(self):
        async def _gather_agent_events(agent: Agent | RemoteAgent):
            while True:
                event = await agent.events_queue.get()
                new_event = {
                    "agent_name": agent.name,
                    "event": event,
                }
                self.events_queue.put_nowait(new_event)

        tasks = []
        for agent in self.agents.values():
            tasks.append(_gather_agent_events(agent))
        await asyncio.gather(*tasks)

    async def run(self, msg: AgentInput, **kwargs):
        pass

    async def chat(self, message: str | dict | None = None):
        """Chat with the team with a REPL interface."""
        from ..repl.core import Repl

        repl = Repl(self)
        await repl.run(message)
