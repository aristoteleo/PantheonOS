"""Budget allocation across whole lineages, above unchanged RPUCG selection.

Before the first intervention, preserve native SimpleTES chain ordering and RNG
use. Afterwards, choose ordinary chains or the next pending planner direction
with sliding-window Thompson sampling. One meaningful frontier improvement is
one success; all evaluated children are charged, including failed candidates.
Generation failures create no evaluation observations.

One in four dispatched work items refines the best available chain. A newly
opened redesign receives one ordinary refinement turn before adaptive ranking.
Native Molt's five-turn probation and retirement rules remain unchanged.

All constants are development hypotheses, not established optimal settings.
"""
from copy import deepcopy
import math
from .molt_modified import ModifiedMolt
from .molt import Molt, SPROUT
from .simpletes import SimpleTES, score_of


class Molt2AdaptiveBranches(ModifiedMolt):
    name = 'molt2_adaptive_branches'

    def __init__(self, *, incumbent_every=4, min_probe_turns=2, history_evals=64, **kwargs):
        super().__init__(**kwargs)
        self.incumbent_every = max(1, int(incumbent_every))
        self.min_probe_turns = max(1, int(min_probe_turns))
        self.history_evals = max(1, int(history_evals))
        self.allocation_history = []
        self.allocation_decisions = []
        self.allocation_dispatches = 0
        self._allocation_open = {}
        self._allocation_seen = set()

    def _available(self, ctx):
        busy = {b.chain_idx for b in self.open.values()}
        return [c for c in range(self.num_chains)
                if c not in busy and not self._retired(c) and self._chain_nodes(ctx, c)]

    def _posterior(self, arm):
        observations = [r for r in self.allocation_history if r['arm'] == arm]
        successes = sum(r['success'] for r in observations)
        evaluations = sum(r['evaluations'] for r in observations)
        return 1 + successes, 1 + evaluations - successes

    def _choose(self, ctx, available, has_pending):
        champion = max(available, key=lambda c: score_of(self._chain_best(ctx, c), self.score_key)) if available else None
        if champion is not None and self.allocation_dispatches % self.incumbent_every == 0:
            return f'chain:{champion}', 'incumbent_floor', {}
        young = [c for c in available if self.role.get(c) == SPROUT and self._turns(c) < self.min_probe_turns]
        if young:
            return f'chain:{min(young, key=lambda c: (self._turns(c), c))}', 'minimum_probe', {}
        arms = [f'chain:{c}' for c in available] + (['proposal'] if has_pending else [])
        if not arms:
            return None, 'no_available_work', {}
        samples = {arm: self.rng.betavariate(*self._posterior(arm)) for arm in arms}
        return max(arms, key=samples.get), 'thompson_progress_per_evaluation', samples

    def _remember(self, ctx, item, arm, reason, samples):
        frontier = self._global_best(ctx)
        record = {'arm': arm, 'reason': reason, 'samples': samples, 'frontier': frontier,
                  'batch_id': item.batch_id, 'evaluations': 0, 'best_child': None,
                  'dispatch': self.allocation_dispatches}
        self._allocation_open[item.batch_id] = record
        self.allocation_decisions.append(deepcopy(record))
        if reason != 'native_prefix':
            self.allocation_dispatches += 1
        item.meta['molt2_allocation'] = {'arm': arm, 'reason': reason}

    async def ask(self, ctx, n):
        # Execute only the native planning/review phase; n=0 dispatches nothing.
        await Molt.ask(self, ctx, 0)
        if not self.interventions and not self.pending and not self.sprouts:
            items = await SimpleTES.ask(self, ctx, n)
            for item in items:
                self._remember(ctx, item, f"chain:{item.meta['chain']}", 'native_prefix', {})
            return items
        items = []
        while len(items) < n:
            available = self._available(ctx)
            arm, reason, samples = self._choose(ctx, available, bool(self.pending))
            if arm is None:
                break
            if arm == 'proposal':
                item = None
                while self.pending and item is None:
                    direction = self.pending.pop(0)
                    item = self._direction_item(ctx, direction)
                    if item is not None:
                        self.open_dirs[item.batch_id] = direction
                    else:
                        self.interventions[direction['intervention']]['children'].append({
                            'direction': direction['id'], 'type': direction.get('type'),
                            'child': None, 'score': None, 'skipped': 'target gone',
                        })
                if item is None:
                    continue
            else:
                c = int(arm.split(':')[1])
                self._next_chain = c
                ordinary = await SimpleTES.ask(self, ctx, 1)
                if not ordinary:
                    break
                item = ordinary[0]
                assert item.meta['chain'] == c
                if c in self.sprouts:
                    item.meta['molt_design'] = self._design_brief(ctx, c)
            self._remember(ctx, item, arm, reason, samples)
            items.append(item)
        return items

    def _resolve(self, batch_id, score=None, evaluated=True):
        record = self._allocation_open.get(batch_id)
        if record is None:
            return
        record['evaluations'] += int(evaluated)
        if score is not None and math.isfinite(score):
            record['best_child'] = max(score, record['best_child']) if record['best_child'] is not None else score
        if batch_id in self.open or batch_id in self.open_dirs:
            return
        self._allocation_open.pop(batch_id)
        count = record['evaluations']
        if not count:
            return
        before, best = record['frontier'], record['best_child']
        success = int(before is not None and best is not None
                      and best > before + self.progress_rel * abs(before))
        row = {**record, 'success': success}
        self.allocation_history.append(row)
        while len(self.allocation_history) > 1 and sum(x['evaluations'] for x in self.allocation_history) > self.history_evals:
            self.allocation_history.pop(0)

    async def on_measured(self, ctx, ind, measurement):
        token = f'{measurement.batch_id}:{ind.id}'
        if token in self._allocation_seen:
            return
        self._allocation_seen.add(token)
        await super().on_measured(ctx, ind, measurement)
        self._resolve(measurement.batch_id, score_of(ind, self.score_key))

    async def on_failed(self, ctx, failure):
        await super().on_failed(ctx, failure)
        self._resolve(failure.batch_id, evaluated=failure.stage != 'generate')

    def state_dict(self):
        state = super().state_dict()
        state['adaptive_branches'] = {
            'incumbent_every': self.incumbent_every, 'min_probe_turns': self.min_probe_turns,
            'history_evals': self.history_evals, 'history': deepcopy(self.allocation_history),
            'decisions': deepcopy(self.allocation_decisions), 'dispatches': self.allocation_dispatches,
            'seen': sorted(self._allocation_seen),
        }
        return state

    def load_state_dict(self, state):
        super().load_state_dict(state)
        saved = state.get('adaptive_branches', {})
        for key in ['incumbent_every', 'min_probe_turns', 'history_evals']:
            setattr(self, key, int(saved.get(key, getattr(self, key))))
        self.allocation_history = deepcopy(saved.get('history', []))
        self.allocation_decisions = deepcopy(saved.get('decisions', []))
        self.allocation_dispatches = int(saved.get('dispatches', 0))
        self._allocation_seen = set(saved.get('seen', []))
        self._allocation_open = {}
