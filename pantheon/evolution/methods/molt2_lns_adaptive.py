"""Combined development candidate with scale-calibrated recent-window UCB.

No task-specific thresholds. Normalize all recent nonnegative frontier rewards
by the same observed range before adding a dimensionless exploration bonus.
This removes sensitivity to the magnitude of typical improvements. It is an
engineering correction, not a claim of a stationary-bandit regret guarantee:
program trajectories evolve and the recent window is nonstationary.
"""
import math, copy
from .molt2_lns_global import Molt2LNSGlobal
from .molt2_lns import validate_regions
from .molt2_lns_feedback import LNSFeedbackVariator


class Molt2LNSAdaptive(Molt2LNSGlobal):
    name = 'molt2_lns_adaptive'
    variator_cls = LNSFeedbackVariator

    def _accept_families(self, files, families):
        accepted=[];names=set();ideas=set()
        for family in families[:self.portfolio_size]:
            try:
                fields=['name','decision','hypothesis','replacement','preserve']
                if not isinstance(family,dict) or any(not isinstance(family.get(k),str) or not family[k].strip() for k in fields):
                    raise ValueError('Missing family explanation')
                regions=validate_regions(files,family.get('regions'),self.scope)
                name=family['name'].strip()
                # A neighborhood is a reconstruction mechanism, not just a line mask.
                idea=tuple(' '.join(family[k].casefold().split()) for k in ['decision','replacement'])
                if name.casefold() in names or idea in ideas:
                    raise ValueError('Duplicate family name or exact decision/replacement proposal')
                names.add(name.casefold());ideas.add(idea)
                accepted.append({**family,'regions':regions})
            except ValueError as exc:
                self.generation_audit.append({'status':'invalid_family','reason':str(exc),'family':family})
        return accepted

    def _global_family(self, original, masks):
        # Retain a concrete model-proposed mechanism when granting broad edit scope.
        # The frozen Global ablation instead replaced this idea with a generic rewrite.
        family=copy.deepcopy(original);family['regions']=copy.deepcopy(masks)
        family['replacement'] += ' Implement this concrete mechanism with any coupled code changes needed across the approved whole-program regions; preserve useful existing mechanisms.'
        family['preserve']='Task input/output contract, evaluator/data rules, source file set and immutable markers.'
        family['whole_program_expansion']=True
        return family

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
