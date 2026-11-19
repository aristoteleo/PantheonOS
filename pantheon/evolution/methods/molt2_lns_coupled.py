"""Semantic neighborhoods with permission to update coupled dependencies.

Separate development ablation: retain each inferred mechanism, but treat its
line mask as a focus rather than a hard boundary. All existing non-marker
regions may change so a new helper can actually be connected to its callers.
Scheduling, rewards, feedback, trial counts and ordinary search are inherited.
"""
import copy
from .molt2_lns_adaptive import Molt2LNSAdaptive
from .molt2_lns_global import whole_program_regions


class Molt2LNSCoupled(Molt2LNSAdaptive):
    name = 'molt2_lns_coupled'

    async def _make_portfolio(self, ctx):
        previous = len(self.portfolios)
        await super()._make_portfolio(ctx)
        if len(self.portfolios) == previous:
            return
        record = self.portfolios[-1]
        anchor = ctx.store.get(record['anchor'])
        masks = whole_program_regions(anchor.genome.files)
        arms = []
        for index, original in enumerate(record['families']):
            family = copy.deepcopy(original)
            family['primary_focus_regions'] = copy.deepcopy(original['regions'])
            family['regions'] = copy.deepcopy(masks)
            family['replacement'] += (
                ' Implement the specified mechanism as reachable behavior from the program entry point.'
                ' Update callers, interfaces, state initialization and dependent code wherever needed;'
                ' adding an unused helper does not implement this strategy.'
                ' Keep this mechanism as the focus and preserve unrelated useful behavior.'
            )
            family['preserve'] = 'Task input/output contract, evaluator/data rules, existing source file set and immutable markers.'
            family['coupled_dependencies_open'] = True
            arm = f"round{record['round']}/family{index}"
            self.operators[arm].update(strategy=copy.deepcopy(family),
                                       initial_regions=copy.deepcopy(masks), scope=3)
            record['families'][index] = family
            arms.append(arm)
        record['coupled_dependency_arms'] = arms
