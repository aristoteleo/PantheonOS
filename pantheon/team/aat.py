from pathlib import Path
from datetime import datetime

from .base import Team
from ..agent import Agent, AgentInput


class AgentAsToolTeam(Team):
    """Team that uses sub-agents as tools."""

    def __init__(
        self,
        leader_agent: Agent,
        sub_agents: list[Agent],
        record: bool = True,
        record_dir: str = ".team_instructions",
        fully_connected: bool = False,
    ):
        super().__init__([leader_agent] + sub_agents)
        self.leader_agent = leader_agent
        self.leader_agent.tool(self.list_sub_agents)
        async def call_sub_agent(name: str, instruction: str):
            return await self.call_sub_agent(name, instruction, caller=leader_agent.name)
        self.leader_agent.tool(call_sub_agent)
        self.sub_agents = {agent.name: agent for agent in sub_agents}
        if fully_connected:
            for agent in sub_agents:
                async def call_sub_agent(name: str, instruction: str):
                    return await self.call_sub_agent(name, instruction, caller=agent.name)

                agent.tool(call_sub_agent)
                agent.tool(self.list_sub_agents)

        self.record = record
        self.record_dir = Path(record_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.run_kwargs = {}

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

    async def call_sub_agent(self, name: str, instruction: str, caller: str) -> str:
        if name not in self.sub_agents:
            raise ValueError(f"Sub-agent {name} not found")

        # Report the instruction to the sub-agent
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        record_path = self.record_dir / f"{timestamp}_instruction_{caller}__call__{name}.md"
        with open(record_path, "w") as f:
            f.write(instruction)

        try:
            resp = await self.sub_agents[name].run(
                instruction, **self.run_kwargs
            )
            if self.record:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                record_path = self.record_dir / f"{timestamp}_result_{caller}__call__{name}.md"
                with open(record_path, "w") as f:
                    f.write(str(resp.content))
        except Exception as e:
            if self.record:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                record_path = self.record_dir / f"{timestamp}_error_{caller}__call__{name}.md"
                with open(record_path, "w") as f:
                    f.write(str(e))
            raise e
        return resp.content

    async def run(self, msg: AgentInput, **kwargs):
        run_kwargs = kwargs.copy()
        self.run_kwargs = run_kwargs
        resp = await self.leader_agent.run(
            msg,
            **run_kwargs
        )
        return resp
        
