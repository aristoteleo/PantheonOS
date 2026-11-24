"""Optional post-cohort paired development analysis. No search/held-out access.

Sign tests use paired independent search seeds, not 150 correlated case scores.
Any incomplete pair group is withheld. Six one-sided comparisons are corrected
with Holm's procedure. Bootstrap intervals are descriptive with only five seeds.
"""
from pathlib import Path
import math,json,random,statistics,subprocess,sys
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
if __name__=='__main__':
 result=analyze(json.loads((R/'comparison.json').read_text())['rows']);
 from writer_parent_audit import build
 result['writer_parent_audit']=build();
 from run_resource_audit import build as resource_audit
 result['resource_audit']=resource_audit();
 from baseline_parity import build as baseline_audit
 result['baseline_parity']=baseline_audit();
 subprocess.run([sys.executable,str(R/'research/reward_sensitivity.py')],check=True,capture_output=True,text=True)
 result['reward_signal_audit']=json.loads((R/'research/REWARD_SENSITIVITY.json').read_text());
 if (R/'CAMPAIGN_COMPLETE.json').exists() and (R/'FINALIST_FREEZE.json').exists():
  subprocess.run([sys.executable,str(R/'research/reread_finalists.py')],check=True,capture_output=True,text=True)
  result['public_reproducibility_rereads']=json.loads((R/'research/PUBLIC_REREAD_REPORT.json').read_text());(R/'research/PAIRED_INFERENCE.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
