"""Describe invalid-program evaluation cost; never score or alter the search."""
from pathlib import Path
import collections,json,time
R=Path(__file__).resolve().parents[1]
def build():
 rows=[]
 for job in json.loads((R/'manifest.json').read_text()):
  p=R/'runs'/job['id']
  if not (p/'workflow.json').exists() or json.loads((p/'workflow.json').read_text())['status']!='complete':continue
  summary=json.loads((p/'summary.json').read_text());store=json.loads((p/'store.json').read_text())
  usage=summary['eval_usage'];cutoff=min(80,usage['calls']-usage.get('calls_harness',0));events=[];low_before=0
  for event in summary.get('usage_timeline',[]):
   if event.get('kind')!='eval':continue
   low=event.get('eval_calls_low',0)
   if low<=low_before:events.append(event)
   low_before=low
  joined=collections.defaultdict(list)
  for ind in store['individuals']:
   if not isinstance(ind.get('genome',{}).get('files',{}).get('solution.cpp'),str):continue
   for m in ind.get('measurements',[]):
    at=m.get('at')
    if not isinstance(at,(float,int)) or not events:continue
    event=min(events,key=lambda e:abs(e['t']-at));idx=event['eval_calls']-event.get('eval_calls_harness',0)
    if abs(event['t']-at)>=.1 or not 0<idx<=cutoff:continue
    metrics=m.get('metrics',{});valid=metrics.get('validity')
    category='fully_valid' if m.get('ok',True) and valid==1 and metrics.get('num_accepted')==metrics.get('num_cases')==150 else 'invalid_or_partial' if valid is not None else 'unknown_metrics'
    joined[idx].append({'id':ind['id'],'category':category,'reason':m.get('artifacts',{}).get('invalid_reason')})
  counts=collections.Counter();reasons=collections.Counter()
  for idx in range(1,cutoff+1):
   matches=joined[idx]
   if not matches:counts['unmatched_evaluation']+=1
   elif len(matches)!=1:counts['ambiguous_join']+=1
   else:
    counts[matches[0]['category']]+=1
    if matches[0]['reason']:reasons[matches[0]['reason'].splitlines()[0][:250]]+=1
  assert sum(counts.values())==cutoff
  rows.append({'run':job['id'],'task':job['task'],'method':job['method'],'seed':job['seed'],'selection_cutoff':cutoff,'counts':dict(counts),'rejection_reasons':dict(reasons)})
 return {'at_unix':time.time(),'rows':rows,'scope':'Completed runs only; first80 evaluator-call ceiling, starting program included. No model calls or evaluations.','limits':'Strict nearest timestamp join (<0.1s). Unmatched and ambiguous events remain explicit; they are not assumed invalid. Counts include repeated-code evaluations and are not counts of distinct programs. No causal or five-seed inference from incomplete groups.'}
if __name__=='__main__':
 d=build();(R/'research/VALIDITY_BUDGET_AUDIT.json').write_text(json.dumps(d,indent=2)+'\n')
 for x in d['rows']:print(x['run'],x['counts'])
