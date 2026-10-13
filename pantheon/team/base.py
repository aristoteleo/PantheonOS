from abc import ABC

from pantheon.agent import Agent, AgentInput


class Team(ABC):
    def __init__(self, agents: list[Agent]):
        self.agents = {}
        for agent in agents:
            self.agents[agent.name] = agent

    async def async_setup(self):
        pass

    async def run(self, msg: AgentInput, **kwargs):
        pass

    async def chat(self, message: str | dict | None = None):
        """Chat with the team with a REPL interface."""
        from pantheon.repl.core import Repl

        repl = Repl(self)
        await repl.run(message)
