from pathlib import Path
from datetime import datetime

from .base import Team
from ..agent import Agent, AgentInput
from ..utils.display import print_agent_message

class AgentAsToolTeam(Team):
    """Team that uses sub-agents as tools."""

    def __init__(self, leader_agent: Agent, sub_agents: list[Agent]):
        super().__init__([leader_agent] + sub_agents)
        self.leader_agent = leader_agent
        self.leader_agent.tool(self.list_sub_agents)
        self.leader_agent.tool(self.call_sub_agent)
        self.sub_agents = {agent.name: agent for agent in sub_agents}

        self.report_dir = Path("team_instructions")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def list_sub_agents(self) -> list[dict]:
        """Return description for all sub-agents."""
        sub_agents_info = []
        for sub_agent in self.sub_agents.values():
            import ipdb; ipdb.set_trace()
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.report_dir / f"{timestamp}_instruction_{name}.md"
        with open(report_path, "w") as f:
            f.write(instruction)
        try:
            resp = await self.sub_agents[name].run(
                instruction,
                process_step_message=lambda msg: print_agent_message(name, msg)
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = self.report_dir / f"{timestamp}_result_{name}.md"
            with open(report_path, "w") as f:
                f.write(str(resp.content))
        except Exception as e:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = self.report_dir / f"{timestamp}_error_{name}.md"
            with open(report_path, "w") as f:
                f.write(str(e))
            raise e
        return resp.content

    async def run(self, msg: AgentInput, **kwargs):
        resp = await self.leader_agent.run(
            msg,
            process_step_message=lambda msg: print_agent_message(self.leader_agent.name, msg, **kwargs),
            **kwargs
        )
        return resp
        
