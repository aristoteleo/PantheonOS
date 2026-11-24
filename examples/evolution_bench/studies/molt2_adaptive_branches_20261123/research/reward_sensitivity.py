"""Offline signal-density audit. Does not replay or predict alternative searches."""
from pathlib import Path
import json,collections,time,math
R=Path(__file__).resolve().parents[1]
def audit(run):
 workflow=json.loads((run/'workflow.json').read_text());assert workflow['status']=='complete'
 state=json.loads((run/'method.json').read_text())['state']['adaptive_branches'];store=json.loads((run/'store.json').read_text());by_batch=collections.defaultdict(list)
 for ind in store['individuals']:
  for m in ind.get('measurements',[]):
   score=m.get('metrics',{}).get('combined_score')
   if isinstance(score,(int,float)) and math.isfinite(score):by_batch[m['batch_id']].append(float(score))
 rows=[];native_by_batch={x['batch_id']:x for x in state['history']};fallbacks=[];mismatches=[]
 for decision in state['decisions']:
  scores=by_batch.get(decision['batch_id'],[]);native=native_by_batch.get(decision['batch_id'])
  if scores:
   row={'batch_id':decision['batch_id'],'arm':decision['arm'],'reason':decision['reason'],'dispatch_frontier':decision['frontier'],'evaluations':len(scores),'best_child':max(scores),'source':'stored_measurements'}
  elif native:
   row={'batch_id':decision['batch_id'],'arm':decision['arm'],'reason':decision['reason'],'dispatch_frontier':decision['frontier'],'evaluations':native['evaluations'],'best_child':native['best_child'],'source':'native_window_unmeasured_result'}
   fallbacks.append(decision['batch_id'])
  else:continue
  if native and (row['evaluations']!=native['evaluations'] or row['best_child']!=native['best_child']):mismatches.append(decision['batch_id'])
  rows.append(row)
 thresholds=[]
 for relative in [.01,.001,.0001,0.]:
  eligible=[r for r in rows if r['dispatch_frontier'] is not None]
  successes=[r for r in eligible if r['best_child'] is not None and r['best_child']>r['dispatch_frontier']+relative*abs(r['dispatch_frontier'])]
  thresholds.append({'relative_threshold':relative,'positive_work_items':len(successes),'by_arm':dict(collections.Counter(r['arm'] for r in successes))})
 return {'run':run.name,'observed_work_items':len(rows),'joined_candidate_evaluations':sum(r['evaluations'] for r in rows if r['source']=='stored_measurements'),'window_only_accounted_evaluations':sum(r['evaluations'] for r in rows if r['source']!='stored_measurements'),'total_accounted_evaluations':sum(r['evaluations'] for r in rows),'thresholds':thresholds,'reconstruction_matches_native_window':not mismatches and not fallbacks,'native_window_fallback_batches':fallbacks,'mismatched_batches':mismatches}
if __name__=='__main__':
 rows=[]
 for run in sorted((R/'runs').iterdir()):
  if not (run/'workflow.json').exists() or not (run/'method.json').exists():continue
  if json.loads((run/'workflow.json').read_text()).get('status')!='complete':continue
  if 'adaptive_branches' not in json.loads((run/'method.json').read_text())['state']:continue
  rows.append(audit(run))
 out={'at_unix':time.time(),'rows':rows,'caution':'Post-hoc training-only diagnostic of observed signal density, not a policy comparison. Different rewards change future sampling and candidates; no claim that a lower threshold improves performance. Concurrent work uses its recorded dispatch frontier. Tiny differences can reflect scorer timing noise.'}
 (R/'research/REWARD_SENSITIVITY.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
