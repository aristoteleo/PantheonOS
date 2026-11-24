"""Experimental reward-only ablation; all search mechanics are inherited.

Fractional Beta pseudocounts are a heuristic, not calibrated probabilities.
The caller must freeze noise_tolerance from development repeats before search.
"""
import math
from .molt2_adaptive_branches import Molt2AdaptiveBranches
from .progress_credit import normalized_credit


class Molt2NoiseCredit(Molt2AdaptiveBranches):
    name = 'molt2_noise_credit'

    def __init__(self, *, noise_tolerance, **kwargs):
        if not math.isfinite(noise_tolerance) or noise_tolerance < 0:
            raise ValueError('noise_tolerance must be finite and nonnegative')
        super().__init__(**kwargs)
        self.noise_tolerance = float(noise_tolerance)

    def _resolve(self, batch_id, score=None, evaluated=True):
        # Capture only prior retained observations before the parent appends
        # and trims the current batch. No future information enters credit.
        prior = [r['frontier_gain'] for r in self.allocation_history
                 if math.isfinite(r.get('frontier_gain', 0)) and r.get('frontier_gain', 0) > 0]
        tracked = batch_id in self._allocation_open
        super()._resolve(batch_id, score=score, evaluated=evaluated)
        if not tracked or batch_id in self._allocation_open or not self.allocation_history:
            return
        row = self.allocation_history[-1]
        if row['batch_id'] != batch_id:
            return  # Generation-only failure: no evaluated child, no observation.
        before, best = row['frontier'], row['best_child']
        gain = best-before if best is not None and before is not None else 0.0
        row['frontier_gain'] = gain if math.isfinite(gain) else 0.0
        row['binary_credit_reference'] = row['success']
        row['success'] = normalized_credit(gain, prior, noise_tolerance=self.noise_tolerance)

    def state_dict(self):
        state = super().state_dict()
        state['noise_credit'] = {'noise_tolerance': self.noise_tolerance, 'version': 1}
        return state

    def load_state_dict(self, state):
        saved = state['noise_credit']
        if saved['version'] != 1:
            raise ValueError('Unsupported noise-credit checkpoint')
        tolerance = saved['noise_tolerance']
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError('Invalid checkpoint noise tolerance')
        super().load_state_dict(state)
        self.noise_tolerance = float(tolerance)
