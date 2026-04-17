from pantheon.agent import Agent, AgentInput, AgentResponse, AgentTransfer, RemoteAgent
from pantheon.internal.memory import Memory
from pantheon.utils.log import logger
from pantheon.utils.misc import run_func
from .base import Team


class SwarmTeam(Team):
    """Team that run agents in handoff & routines patterns like
    OpenAI's [Swarm framework](https://github.com/openai/swarm).
    """

    def __init__(self, agents: list[Agent | RemoteAgent]):
        super().__init__(agents)

    def get_active_agent(self, memory: Memory) -> Agent | RemoteAgent:
        active_agent_name = memory.extra_data.get("active_agent")
        if (active_agent_name is None) or (active_agent_name not in self.agents):
            active_agent_name = list(self.agents.keys())[0]
            logger.warning(
                f"Active agent not found in memory, setting to {active_agent_name}"
            )
            memory.set_metadata("active_agent", active_agent_name)
        active_agent = self.agents[active_agent_name]
        return active_agent

    def set_active_agent(self, memory: Memory, agent_name: str):
        memory.set_metadata("active_agent", agent_name)

    async def run(self, msg: AgentInput, memory: Memory | None = None, **kwargs):
        if memory is None:
            memory = Memory(name="swarm-team")
        while True:
            active_agent = self.get_active_agent(memory)
            resp = await active_agent.run(msg, memory=memory, **kwargs)
            if isinstance(resp, AgentTransfer):
                self.set_active_agent(memory, resp.to_agent)
                msg = resp
            else:
                return resp


class SwarmCenterTeam(SwarmTeam):
    """Swarm team that has a central triage agent that decides which agent to handoff to."""

    def __init__(self, triage: Agent, agents: list[Agent | RemoteAgent]):
        super().__init__([triage])
        self.triage = triage
        self._agents_to_add = agents

    async def add_agent(self, agent: Agent | RemoteAgent):
        if isinstance(agent, RemoteAgent):
            await agent.fetch_info()
        assert isinstance(agent.name, str), "Agent name must be a string"
        agent_func_name = agent.name.replace(" ", "_").lower()
        func_name = f"transfer_to_{agent_func_name}"

        # Create transfer function using closure (no exec needed)
        # Note: No return type annotation to avoid Pydantic serialization issues
        def transfer_func(target_name: str = agent.name):
            """Transfer to the target agent."""
            return self.agents[target_name]

        transfer_func.__name__ = func_name
        transfer_func.__doc__ = f"Transfer to {agent.name}."
        await run_func(self.triage.tool, transfer_func)

        # Transfer back to triage
        # Note: No return type annotation to avoid Pydantic serialization issues
        def transfer_back_to_triage():
            """Transfer back to the triage agent."""
            return self.triage

        await run_func(agent.tool, transfer_back_to_triage)
        self.agents[agent.name] = agent

    async def remove_agent(self, agent: Agent | RemoteAgent):
        assert isinstance(agent.name, str), "Agent name must be a string"
        del self.agents[agent.name]
        self.triage.functions.pop(f"transfer_to_{agent.name.replace(' ', '_').lower()}")

    async def async_setup(self):
        while self._agents_to_add:
            agent = self._agents_to_add.pop(0)
            await self.add_agent(agent)

    async def run(self, msg: AgentInput, **kwargs):
        await self.async_setup()
        return await super().run(msg, **kwargs)
