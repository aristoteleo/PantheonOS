from .base import Team
from ..agent import Agent, AgentInput

class AgentAsToolTeam(Team):
    """Team that uses sub-agents as tools."""

    def __init__(self, leader_agent: Agent, sub_agents: list[Agent]):
        super().__init__([leader_agent] + sub_agents)
        self.leader_agent = leader_agent
        self.sub_agents = {agent.name: agent for agent in sub_agents}

    def list_sub_agents(self) -> list[dict]:
        """Return description for all sub-agents."""
        sub_agents_info = []
        for sub_agent in self.sub_agents.values():
            sub_agents_info.append({
                "name": sub_agent.name,
                "description": sub_agent.description,
                "toolsets": [
                    name for name in sub_agent.providers.keys()
                ],
            })
        return sub_agents_info

    async def call_sub_agent(self, name: str, instruction: str) -> str:
        if name not in self.sub_agents:
            raise ValueError(f"Sub-agent {name} not found")
        resp = await self.sub_agents[name].run(instruction)
        return resp.content

    async def run(self, msg: AgentInput):
        resp = await self.leader_agent.run(msg)
        return resp
        
