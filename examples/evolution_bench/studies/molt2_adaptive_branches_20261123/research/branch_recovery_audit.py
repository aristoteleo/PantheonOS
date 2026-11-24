"""Descriptive retained-chain recovery, not a replay of alternative pruning rules."""
import json
import math
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
        selection = json.loads((run / 'selection_audit.json').read_text())
        eligible = {x['individual_id']: x for x in selection['first_valid_code_readings'] if x['eligible']}
        sprouts = state.get('molt', {}).get('sprouts', {})
        by = {i['id']: i for i in json.loads((run / 'store.json').read_text())['individuals']}
        for chain, design in sprouts.items():
            records = []
            for id in state['chains'][int(chain)]:
                ind = by[id]
                # The chain retains one winner per generated batch. A repeated
                # genome can have multiple measurements; expose ambiguity.
                ms = ind.get('measurements', [])
                if len(ms) != 1:
                    records.append({'id': id, 'ambiguous_measurement_count': len(ms)})
                    continue
                m = ms[0]['metrics']
                score = m.get('combined_score')
                point = eligible.get(id)
                records.append({'id': id, 'score': score,
                                'validity': m.get('validity'),
                                'eligible_first80_valid_reading': point is not None,
                                'search_eval_index': point['search_eval_index'] if point else None})
            known = [x for x in records if isinstance(x.get('score'), (int, float)) and math.isfinite(x['score'])]
            first_valid = next((n for n, x in enumerate(records) if x.get('validity') == 1), None)
            reference = design.get('old')
            first_gain = next((n for n, x in enumerate(records) if x.get('eligible_first80_valid_reading') and x.get('validity') == 1 and isinstance(reference, (int, float)) and x.get('score', float('-inf')) > reference + 0.01 * abs(reference)), None)
            rows.append({'run': run.name, 'chain': int(chain), 'fate': design.get('fate'),
                         'initial_validity': records[0].get('validity') if records else None,
                         'first_valid_retained_index': first_valid,
                         'first_over_1pct_retained_index': first_gain,
                         'reference_score': reference,
                         'best_retained_score': max((x['score'] for x in known), default=None),
                         'retained_entries': records})
    out = {'at_unix': time.time(), 'rows': rows,
           'caution': 'Indices count retained chain entries, zero-based, not evaluations or all children. Full saved chains can include entries beyond80; recovery gains require an eligible first80 valid reading. The completed-run endpoint can censor late branches. Repeated-genome ambiguity is exposed. Observed recovery does not estimate counterfactual pruning performance or an optimal probation length.'}
    (R / 'research/BRANCH_RECOVERY_AUDIT.json').write_text(json.dumps(out, indent=2) + '\n')
    return out


if __name__ == '__main__':
    out = build()
    print('Audited branches:', len(out['rows']))
    print('Initially invalid then >1% reference:', sum(x['initial_validity'] == 0 and x['first_over_1pct_retained_index'] is not None for x in out['rows']))
