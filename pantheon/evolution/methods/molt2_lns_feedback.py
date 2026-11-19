"""Separate feedback ablation: expose measured parent metrics to LNS writers.

No scope/scheduling changes beyond the frozen VNS2 parent. A per-call dataclass
copy prevents concurrent writers from changing one another's objectives.
"""
import json
from dataclasses import replace
from .molt2_lns import LNSVariator
from .molt2_lns_vns2 import Molt2LNSVNS2


class LNSFeedbackVariator(LNSVariator):
    async def create(self, ctx, item):
        if 'lns_edit' not in item.meta:
            return await super().create(ctx, item)
        anchor = ctx.store.get(item.meta['lns_edit']['anchor'])
        feedback = {
            'anchor': {'id':anchor.id,'metrics':anchor.metrics()} if anchor else None,
            'parents': [{'id':p.id,'metrics':p.metrics()} for p in item.context.parents],
            'chain_history':item.context.history,
        }
        enriched = replace(ctx, objective=ctx.objective + '\nMEASURED FEEDBACK (training evaluator only):\n' + json.dumps(feedback, default=str))
        return await super().create(enriched, item)


class Molt2LNSFeedback(Molt2LNSVNS2):
    name = 'molt2_lns_feedback'
    variator_cls = LNSFeedbackVariator
