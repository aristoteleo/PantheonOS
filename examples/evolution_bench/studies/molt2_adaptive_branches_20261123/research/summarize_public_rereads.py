"""Describe frozen-finalist repeat scores without changing selection or ranking."""
import hashlib
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    frozen = json.loads((ROOT / 'FINALIST_FREEZE.json').read_text())['selections']
    observed = {}
    for path in (ROOT / 'public-rereads').glob('*/slot*/AUDIT.json'):
        row = json.loads(path.read_text())
        assert hashlib.sha256((path.parent / 'solution.cpp').read_bytes()).hexdigest() == row['source_sha256']
        key = (row['task'], row['source_sha256'], row['slot'])
        assert key not in observed, key
        observed[key] = row
    rows = []
    for name, selected in sorted(frozen.items()):
        task = name.split('-')[1]
        assert hashlib.sha256((ROOT / 'runs' / name / 'selected/solution.cpp').read_bytes()).hexdigest() == selected['sha256']
        repeats = [observed.get((task, selected['sha256'], slot)) for slot in range(2)]
        valid = [r['result']['combined_score'] for r in repeats if r and r['status'] == 'valid']
        rows.append(dict(run=name, search_score=selected['score'], repeat_scores=valid,
                         repeat_statuses=[r['status'] if r else 'pending' for r in repeats],
                         repeat_mean=statistics.mean(valid) if len(valid) == 2 else None,
                         repeat_range=max(valid)-min(valid) if len(valid) == 2 else None,
                         differences_from_search=[v-selected['score'] for v in valid]))
    report = dict(at_unix=time.time(), complete=all(all(s == 'valid' for s in r['repeat_statuses']) for r in rows),
                  observed_checks=len(observed), rows=rows, selection_changed=False,
                  validation_accessed=False,
                  caution='Two repeats describe observed variability only; they do not calibrate a noise distribution. '
                          'Public training scores are post-search diagnostics, never optimizer feedback or reselection. '
                          'Missing and invalid repeats remain explicit; no selective retries.')
    path = ROOT / 'research/PUBLIC_REREAD_VARIABILITY.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(report, indent=2) + '\n')
    temp.replace(path)
    print(json.dumps(dict(observed_checks=len(observed), complete=report['complete'])))


if __name__ == '__main__':
    main()
