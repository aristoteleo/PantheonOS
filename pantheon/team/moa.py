import asyncio

from ..agent import Agent, AgentInput, AgentResponse
from .base import Team


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
            resps_str += f"{i + 1}. {resp.agent_name}:\n{resp.content}\n\n"
        user_query_str = user_query[-1]["content"]
        return self.AGGREGATION_TEMPLATE.format(
            user_query=user_query_str,
            responses=resps_str,
        )

    async def run_proposers(
        self, input_, **proposer_kwargs
    ) -> dict[str, AgentResponse]:
        if self.parallel:
            tasks = [
                proposer.run(input_, **proposer_kwargs) for proposer in self.proposers
            ]
            gathered = await asyncio.gather(*tasks)
            return {
                proposer.name: resp for proposer, resp in zip(self.proposers, gathered)
            }
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
