import asyncio
from abc import ABC

from .agent import Agent, AgentTransfer, AgentInput, AgentResponse
from .remote.agent import RemoteAgent
from .utils.misc import run_func


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
        from .repl.team import Repl
        repl = Repl(self)
        await repl.run(message)


class SwarmTeam(Team):
    """Team that run agents in handoff & routines patterns like
    OpenAI's [Swarm framework](https://github.com/openai/swarm).
    """
    def __init__(self, agents: list[Agent | RemoteAgent]):
        super().__init__(agents)
        self.active_agent = agents[0]

    async def run(self, msg: AgentInput, **kwargs):
        while True:
            resp = await self.active_agent.run(msg, **kwargs)
            if isinstance(resp, AgentTransfer):
                self.active_agent = self.agents[resp.to_agent]
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
        agent_func_name = agent.name.replace(" ", "_").lower()
        func_name = f"transfer_to_{agent_func_name}"
        exec(f"def {func_name}(): return self.agents['{agent.name}']", locals())
        transfer_func = eval(func_name)
        await run_func(self.triage.tool, transfer_func)

        def transfer_back_to_triage():
            return self.triage

        await run_func(agent.tool, transfer_back_to_triage)
        self.agents[agent.name] = agent

    async def remove_agent(self, agent: Agent | RemoteAgent):
        del self.agents[agent.name]
        self.triage.functions.pop(f"transfer_to_{agent.name.replace(' ', '_').lower()}")

    async def async_setup(self):
        while self._agents_to_add:
            agent = self._agents_to_add.pop(0)
            await self.add_agent(agent)

    async def run(self, msg: AgentInput, **kwargs):
        await self.async_setup()
        return await super().run(msg, **kwargs)


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


class MoATeam(Team):
    """Team that run agents in a MoA (Mixture-of-Agents) pattern.
    
    Reference:
        - [MoA: Mixure-of-Agents](https://arxiv.org/abs/2406.04692)
        - [Self-MoA](https://arxiv.org/abs/2502.00674)
    """

    AGGREGATION_TEMPLATE = """Below are responses from different AI models to the same query.  
Please carefully analyze these responses and generate a final answer that is:  
- Most accurate and comprehensive  
- Best aligned with the user's instructions  
- Free from errors or inconsistencies  

### Query:  
{user_query}  

### Responses:  
{responses}

### Final Answer:"""

    def __init__(
            self,
            proposers: list[Agent],
            aggregator: Agent,
            layers: int = 1,
            parallel: bool = True,
            ):
        super().__init__(proposers + [aggregator])
        self.proposers = proposers
        self.aggregator = aggregator
        self.layers = layers
        self.parallel = parallel

    def get_aggregate_prompt(
            self,
            user_query: list[dict],
            responses: dict[str, AgentResponse],
            ) -> str:
        resps_str = ""
        for i, resp in enumerate(responses.values()):
            resps_str += f"{i+1}. {resp.agent_name}:\n{resp.content}\n\n"
        user_query_str = user_query[-1]["content"]
        return self.AGGREGATION_TEMPLATE.format(
            user_query=user_query_str,
            responses=resps_str,
        )

    async def run_proposers(self, input_, **proposer_kwargs) -> dict[str, AgentResponse]:
        if self.parallel:
            tasks = [proposer.run(input_, **proposer_kwargs) for proposer in self.proposers]
            gathered = await asyncio.gather(*tasks)
            return {proposer.name: resp for proposer, resp in zip(self.proposers, gathered)}
        else:
            responses = {}
            for proposer in self.proposers:
                resp = await proposer.run(input_, **proposer_kwargs)
                responses[proposer.name] = resp
            return responses

    async def run(
            self,
            msg: AgentInput,
            proposer_kwargs: dict = {},
            **aggregator_kwargs,
            ) -> AgentResponse:
        history = self.aggregator.input_to_openai_messages(msg)
        for i in range(self.layers):
            if i == 0:
                responses = await self.run_proposers(history, **proposer_kwargs)
            else:
                agg_prompt = self.get_aggregate_prompt(history, responses)
                responses = await self.run_proposers(agg_prompt, **proposer_kwargs)

        agg_prompt = self.get_aggregate_prompt(history, responses)
        resp = await self.aggregator.run(agg_prompt, **aggregator_kwargs)
        return resp
