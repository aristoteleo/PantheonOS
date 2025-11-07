from ..agent import Agent, AgentInput
from .base import Team


class SequentialTeam(Team):
    """Team that run agents in sequential order."""

    def __init__(
        self,
        agents: list[Agent],
        connect_prompt: str | list[str] = "Next:",
    ):
        super().__init__(agents)
        self.order = list(self.agents.keys())
        self.connect_prompt = connect_prompt

    async def run(
        self,
        msg: AgentInput,
        connect_prompt: str | list[str] | None = None,
        agent_kwargs: dict = {},
        **final_kwargs,
    ):
        first = self.agents[self.order[0]]
        history = first.input_to_openai_messages(msg, False)
        for i, name in enumerate(self.order):
            kwargs = agent_kwargs.get(name, {})
            if i == len(self.order) - 1:
                kwargs.update(final_kwargs)
            resp = await self.agents[name].run(history, **kwargs)
            history.extend(resp.details.messages)
            # Inject the connect prompt between agents
            if i < len(self.order) - 1:
                c_prompt = connect_prompt or self.connect_prompt
                if isinstance(c_prompt, list):
                    c_prompt = c_prompt[i]
                history.append({"role": "user", "content": c_prompt})
        return resp
