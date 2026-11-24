"""Compare frozen noise and native progress gates without evaluating programs."""
import json
from pathlib import Path
import statistics
import time
ROOT=Path(__file__).resolve().parents[1]
rows=[]
for path in sorted((ROOT/'runs').glob('*noise_credit*/method.json')):
    state=json.loads(path.read_text())['state']
    noise=state['noise_credit']['noise_tolerance']
    history=state['adaptive_branches']['history']
    comparisons=[]
    for item in history:
        frontier=item['frontier']
        gain=item.get('frontier_gain',0.)
        if frontier is not None:
            threshold=.002*abs(frontier)
            comparisons.append(dict(native_absolute_threshold=threshold,
                                    threshold_to_noise_ratio=threshold/noise,
                                    noise_gate_accepts=gain>noise,native_gate_accepts=gain>threshold))
    rows.append(dict(run=path.parent.name,noise_gate=noise,retained_work_items=len(comparisons),
                     median_native_threshold=statistics.median(x['native_absolute_threshold'] for x in comparisons) if comparisons else None,
                     median_native_threshold_to_noise_ratio=statistics.median(x['threshold_to_noise_ratio'] for x in comparisons) if comparisons else None,
                     noise_only_gain_items=sum(x['noise_gate_accepts'] and not x['native_gate_accepts'] for x in comparisons)))
report=dict(at_unix=time.time(),progress_rel=.002,
            source='pantheon/evolution/methods/molt2_adaptive_branches.py:127-128',rows=rows,
            interpretation='The reward-only variant changes both the progress gate and the magnitude of credit. This pilot cannot isolate those components.',
            scope='Retained rolling history; descriptive, not whole-run allocation or causal replay.',validation_accessed=False,search_changed=False)
(ROOT/'research/REWARD_GATE_COMPARISON.json').write_text(json.dumps(report,indent=2)+'\n')
print('Saved reward-gate comparison:',len(rows),'run snapshots')
