"""Combined development candidate with scale-calibrated recent-window UCB.

No task-specific thresholds. Normalize all recent nonnegative frontier rewards
by the same observed range before adding a dimensionless exploration bonus.
This removes sensitivity to the magnitude of typical improvements. It is an
engineering correction, not a claim of a stationary-bandit regret guarantee:
program trajectories evolve and the recent window is nonstationary.
"""
import math
from .molt2_lns_global import Molt2LNSGlobal
from .molt2_lns_feedback import LNSFeedbackVariator


class Molt2LNSAdaptive(Molt2LNSGlobal):
    name = 'molt2_lns_adaptive'
    variator_cls = LNSFeedbackVariator

    def _choose_operator(self, available):
        if not available:
            return None
        if self.completed_batches % 2 == 0 and 'ordinary' in available:
            return 'ordinary'
        window = [(arm,r) for arm,r in self.operator_history[-self.operator_window:]
                  if isinstance(r,(int,float)) and math.isfinite(r) and r >= 0]
        total = 1 + len(window)
        scale = max((r for _,r in window),default=0) or 1.
        def utility(key):
            recent = [r / scale for arm,r in window if arm == key]
            if not recent:
                return float('inf')
            return sum(recent)/len(recent) + self.operator_exploration*math.sqrt(math.log(total)/(1+len(recent)))
        return max(available,key=utility)
