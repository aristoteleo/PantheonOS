"""Bounded descriptive analysis of the frozen three-seed reward-only pilot."""
from pathlib import Path
import hashlib
import json
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    pid = json.loads((ROOT/'CONTROLLER_HANDLE.json').read_text())['pid']
    deadline = time.monotonic()+73*3600
    while not (ROOT/'CAMPAIGN_COMPLETE.json').exists():
        try:
            alive = b'runner/controller.py' in Path(f'/proc/{pid}/cmdline').read_bytes() and Path(f'/proc/{pid}/cwd').resolve() == ROOT
        except OSError:
            alive = False
        if not alive or time.monotonic() > deadline:
            raise RuntimeError('Controller missing or analysis deadline exceeded; no search restarted')
        time.sleep(30)
    cfg = json.loads((ROOT/'campaign.json').read_text())
    rows = []
    for task in cfg['tasks']:
        pairs = []
        for seed in cfg['seeds']:
            scores = {}
            statuses = {}
            for method in cfg['methods']:
                folder = ROOT/'runs'/f'noise-{task}-{method}-s{seed}'
                workflow = json.loads((folder/'workflow.json').read_text())
                statuses[method] = workflow['status']
                if workflow['status'] != 'complete':
                    continue
                selection = json.loads((folder/'selection.json').read_text())
                assert selection['eval_cap'] == 80 and selection['cases'] == 150
                assert hashlib.sha256((folder/'selected/solution.cpp').read_bytes()).hexdigest() == selection['sha256']
                scores[method] = selection['score']
            pair = dict(seed=seed, scores=scores, statuses=statuses)
            if len(scores) == 2:
                pair['difference'] = scores['molt2_noise_credit']-scores['molt2_adaptive_branches']
            pairs.append(pair)
        complete = all('difference' in p for p in pairs)
        row = dict(task=task, pairs=pairs, complete=complete)
        if complete:
            deltas = [p['difference'] for p in pairs]
            row.update(mean_difference=statistics.mean(deltas), median_difference=statistics.median(deltas),
                       wins=sum(d > 0 for d in deltas), mean_scores={m: statistics.mean(p['scores'][m] for p in pairs) for m in cfg['methods']})
        rows.append(row)
    report = dict(at_unix=time.time(), tasks=rows, development_only=True, validation_accessed=False,
                  limitations='Three fresh seeds per task; descriptive pilot, no superiority claim or outcome-based exclusions. '
                              'Do not pool with the prior cohort or substitute historical control scores. '
                              'Selected search scores can be noisy; no held-out evaluation or automatic reselection.')
    (ROOT/'research/PAIRED_PILOT_REPORT.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
