"""Descriptive bounded-history diagnostic, separate from strict-cap ranking."""
from pathlib import Path
import json
import time
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
rows=[]
for folder in sorted((ROOT/'runs').iterdir()):
    wp=folder/'workflow.json'
    if not wp.exists() or json.loads(wp.read_text()).get('status')!='complete':
        continue
    state=json.loads((folder/'method.json').read_text())['state']
    history=state['adaptive_branches']['history']
    by_role=defaultdict(lambda:dict(work_items=0,evaluations=0,positive_credit_items=0,credit_sum=0.,positive_frontier_gains=0))
    sprouts=state['molt']['sprouts']
    for item in history:
        arm=item['arm']
        if arm.startswith('chain:'):
            chain=arm.split(':',1)[1]
            role='redesign_origin' if chain in sprouts else 'original_chain'
        else:
            role=arm.split(':',1)[0]
        bucket=by_role[role]
        bucket['work_items']+=1
        bucket['evaluations']+=item['evaluations']
        bucket['credit_sum']+=item['success']
        bucket['positive_credit_items']+=item['success']>0
        gain=item.get('frontier_gain')
        if gain is None and item.get('best_child') is not None:
            gain=item['best_child']-item['frontier']
        bucket['positive_frontier_gains']+=gain is not None and gain>0
    selection=json.loads((folder/'selection.json').read_text())
    membership=[i for i,c in enumerate(state['chains']) if selection['id'] in c]
    rows.append(dict(run=folder.name,selected_score=selection['score'],
                     selected_chain_memberships=membership,
                     selected_origin=['redesign_origin' if str(i) in sprouts else 'original_chain' for i in membership],
                     history_evaluations_retained=state['adaptive_branches']['history_evals'],
                     rolling_history_by_role=dict(by_role),
                     redesign_directions=[dict(chain=k,task=v['task'],hypothesis=v.get('hypothesis'),fate=v.get('fate')) for k,v in sprouts.items()]))
report=dict(at_unix=time.time(),rows=rows,validation_accessed=False,
            limitations='Native adaptive history is a rolling window, not a whole-run or strict first-80 allocation. A work item may evaluate multiple children; planner and implementation costs differ. Credit is a policy heuristic, not a probability. Selected score uses the separate strict-cap selection file. This observational decomposition does not establish causality.')
(ROOT/'research/BRANCH_CREDIT_DIAGNOSTIC.json').write_text(json.dumps(report,indent=2)+'\n')
print('Completed runs summarized:',len(rows))
for r in rows:
    print(r['run'],r['selected_origin'],r['rolling_history_by_role'])
