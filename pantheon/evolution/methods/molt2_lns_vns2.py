"""Separate development variant: neighborhood breadth follows redesign evidence.

The frozen LNS method and vanilla optimizers are unchanged. Scheduling, four-family
portfolios, parent selection, trial depth, model prompts and reward definitions
are inherited. Only scope adaptation changes, isolating the proposed mechanism.
"""
from .molt2_lns import Molt2LNS


class Molt2LNSVNS2(Molt2LNS):
    name = 'molt2_lns_vns2'

    def next_portfolio_scope(self):
        if not self.portfolios:
            return 1
        previous = self.portfolios[-1]
        prefix = f"round{previous['round']}/"
        # Rewards are frontier improvement per candidate, not local-parent gains.
        successful = any(
            reward > self.progress_rel
            for arm, operator in self.operators.items() if arm.startswith(prefix)
            for reward in operator.get('rewards', [])
        )
        return 1 if successful else min(3, previous['scope'] + 1)

    async def _make_portfolio(self, ctx):
        # Set scope BEFORE the planner sees the prompt and regions are validated.
        self.scope = self.next_portfolio_scope()
        await super()._make_portfolio(ctx)

    async def on_measured(self, ctx, individual, measurement):
        scope = self.scope
        await super().on_measured(ctx, individual, measurement)
        # Ordinary-chain gains still reset the global stall detector; they do not
        # constitute evidence that a failed edit neighborhood should shrink.
        self.scope = scope
