"""Optional post-cohort paired development analysis. No search/held-out access.

Sign tests use paired independent search seeds, not 150 correlated case scores.
Any incomplete pair group is withheld. Six one-sided comparisons are corrected
with Holm's procedure. Bootstrap intervals are descriptive with only five seeds.
"""
from pathlib import Path
import math,json,random,statistics,subprocess,sys,importlib
R=Path(__file__).resolve().parents[1]
def analyze(rows):
 out=[]
 for task in ['ahc024','ahc058']:
  for control in ['simpletes','molt','molt_modified']:
   deltas=[]
   for seed in range(400,405):
    a=next((x for x in rows if (x['task'],x['method'],x['seed'])==(task,'molt2_adaptive_branches',seed)),None)
    b=next((x for x in rows if (x['task'],x['method'],x['seed'])==(task,control,seed)),None)
    if a and b and a['status']==b['status']=='complete' and a['score'] is not None and b['score'] is not None:deltas.append(a['score']-b['score'])
   item={'task':task,'control':control,'n_pairs':len(deltas),'deltas':deltas,'status':'incomplete_withheld'}
   if len(deltas)==5:
    n=sum(d!=0 for d in deltas);wins=sum(d>0 for d in deltas)
    p=sum(math.comb(n,k) for k in range(wins,n+1))/2**n if n else 1.
    rng=random.Random(20261123);samples=sorted(statistics.mean(rng.choices(deltas,k=5)) for _ in range(20000))
    item.update(status='complete_development_only',mean_difference=statistics.mean(deltas),wins=wins,non_tied_pairs=n,one_sided_sign_p=p,bootstrap_mean_95pct=[samples[500],samples[19499]])
   out.append(item)
 # Keep all six planned comparisons in the family, including incomplete groups.
 ranked=sorted([x for x in out if 'one_sided_sign_p' in x],key=lambda x:x['one_sided_sign_p']);prev=0
 for rank,item in enumerate(ranked):
  prev=max(prev,min(1.,(6-rank)*item['one_sided_sign_p']));item['holm_adjusted_p']=prev
 return {'comparisons':out,'planned_comparisons':6,'caution':'Exploratory development tasks and n=5 seeds. No validation accessed. Descriptive bootstrap coverage can be poor at n=5. With six comparisons, even five wins alone cannot establish familywise significance at 0.05.'}
def optional(name,callback,errors):
 try:return callback()
 except Exception as exc:
  error={'diagnostic':name,'error_type':type(exc).__name__,'error':str(exc)[:1500]};errors.append(error);return {'status':'error','details':error}

def build_analysis():
 rows=json.loads((R/'comparison.json').read_text())['rows'];errors=[]
 events=optional('measurement_event_audit',lambda:importlib.import_module('measurement_event_audit').build(),errors)
 excluded=set(events.get('excluded_primary_run_ids',[]))
 # Unknown event-audit failure withholds new-method inferences, never hides it.
 if events.get('status')=='error':excluded.update(x['id'] for x in rows if x['method']=='molt2_adaptive_branches')
 primary=[dict(x,status='implementation_review_required') if x['id'] in excluded else x for x in rows]
 result=analyze(primary);result['measurement_event_audit']=events;result['excluded_primary_runs']=sorted(excluded)
 def save():
  result['diagnostic_errors']=errors
  p=R/'research/PAIRED_INFERENCE.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(p)
 save()  # retain core paired results even if a later optional diagnostic fails
 for field,module in [('writer_parent_audit','writer_parent_audit'),('resource_audit','run_resource_audit'),('baseline_parity','baseline_parity')]:
  result[field]=optional(field,lambda module=module:importlib.import_module(module).build(),errors);save()
 def rewards():
  subprocess.run([sys.executable,str(R/'research/reward_sensitivity.py')],check=True,capture_output=True,text=True,timeout=120)
  return json.loads((R/'research/REWARD_SENSITIVITY.json').read_text())
 result['reward_signal_audit']=optional('reward_signal_audit',rewards,errors);save()
 def curves():
  subprocess.run([sys.executable,str(R/'research/plot_completed_blocks.py')],check=True,capture_output=True,text=True,timeout=120)
  return {'pdf':str(R/'research/COMPLETED_MATCHED_BLOCKS.pdf'),'data':str(R/'research/COMPLETED_MATCHED_BLOCKS_DATA.json'),'scope':'Completed matched task/seed blocks only; individual curves, not pooled inference'}
 result['completed_block_curves']=optional('completed_block_curves',curves,errors);save()
 if (R/'CAMPAIGN_COMPLETE.json').exists() and (R/'FINALIST_FREEZE.json').exists():
  def rereads():
   subprocess.run([sys.executable,str(R/'research/reread_finalists.py')],check=True,capture_output=True,text=True,timeout=44100)
   return json.loads((R/'research/PUBLIC_REREAD_REPORT.json').read_text())
  result['public_reproducibility_rereads']=optional('public_reproducibility_rereads',rereads,errors);save()
 return result
if __name__=='__main__':print(json.dumps(build_analysis(),indent=2))
