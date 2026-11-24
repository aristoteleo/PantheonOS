"""Describe completed Molt2 dispatches; never infer counterfactual policy scores."""
import collections
import json
import time
from pathlib import Path

R = Path(__file__).resolve().parents[1]


def build():
    rows = []
    for run in sorted((R / 'runs').iterdir()):
        if not (run / 'workflow.json').exists():
            continue
        if json.loads((run / 'workflow.json').read_text()).get('status') != 'complete':
            continue
        state = json.loads((run / 'method.json').read_text())['state']
        if 'adaptive_branches' not in state:
            continue
        decisions = state['adaptive_branches']['decisions']
        assert len({d['batch_id'] for d in decisions}) == len(decisions)
        counts = collections.Counter(d['reason'] for d in decisions)
        arms = collections.Counter(d['arm'] for d in decisions)
        sampled = [d for d in decisions if d['reason'] == 'thompson_progress_per_evaluation']
        assert all(d['arm'] in d['samples'] for d in sampled)
        rows.append({
            'run': run.name,
            'work_items_by_reason': dict(counts),
            'work_items_by_arm': dict(arms),
            'thompson_choices': len(sampled),
            'total_work_items': len(decisions),
            'retained_window_successes': sum(x['success'] for x in state['adaptive_branches']['history']),
            'retained_window_evaluations': sum(x['evaluations'] for x in state['adaptive_branches']['history']),
        })
    out = {'at_unix': time.time(), 'rows': rows,
           'caution': 'Work-item counts are not candidate-evaluation counts. Native history is a rolling window, not whole-run reward. When successes are absent, posterior sampling cannot learn which arm produces progress; failure counts still change posterior uncertainty. This descriptive audit does not establish that another allocation would improve scores.'}
    (R / 'research/ALLOCATION_DIAGNOSTIC.json').write_text(json.dumps(out, indent=2) + '\n')
    return out


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))
