from pathlib import Path
import json,csv,statistics
R=Path(__file__).resolve().parents[1]
def read(p,default=None):
 try:return json.loads(p.read_text())
 except (OSError,ValueError):return default if default is not None else {}
def save(p,x):
 t=p.with_suffix('.json.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(p)
def build():
 rows=[]
 for p in sorted((R/'runs').glob('*/job.json')):
  j=read(p);d=p.parent;w=read(d/'workflow.json');sel=read(d/'selection.json');summary=read(d/'summary.json',read(d/'partial_summary.json'));state=read(d/'method.json').get('state',{}).get('lns',{})
  ledger=read(Path('/home/erwinpi/molt2-lab/spend-ledger.json'));bills=[v for v in ledger.get('calls',{}).values() if v.get('run_id')==j['id']]
  rows.append({'id':j['id'],'phase':j['phase'],'task':j['task'],'method':j['method'],'seed':j['seed'],'status':w.get('status','queued'),'score':sel.get('score'),'model_usd':sum(v.get('conservative_charge_usd',0) for v in bills),'model_calls':len(bills),'eval_calls':summary.get('eval_usage',{}).get('calls'),'wall_seconds':w.get('wall_seconds'),'valid_families':sum(len(p['families']) for p in state.get('portfolios',[])),'lns_edit_batches':sum(q.get('arm')!='ordinary' for q in state.get('decisions',[])), 'planning_rounds':len(read(d/'method.json').get('state',{}).get('molt',{}).get('interventions',[])), 'adaptive_dispatches':read(d/'method.json').get('state',{}).get('adaptive_branches',{}).get('dispatches',0)})
 groups=[]
 for phase in sorted({r['phase'] for r in rows}):
  for task in ['ahc058','ahc024']:
   for method in sorted({r['method'] for r in rows}):
    rr=[r for r in rows if (r['phase'],r['task'],r['method'])==(phase,task,method)];values=[r['score'] for r in rr if r['score'] is not None and r['status']=='complete'];groups.append({'phase':phase,'task':task,'method':method,'complete':len(values),'scheduled':len(rr),'mean':statistics.mean(values) if values else None,'sd':statistics.stdev(values) if len(values)>1 else None})
 save(R/'comparison.json',{'rows':rows,'groups':groups,'note':'Fresh DeepInfra matched development comparison. 150 public cases, first80 eligible calls, all methods share pinned provider/model settings. Incomplete groups are not full-cohort evidence. No validation scoring in this campaign.'})
 with (R/'comparison.csv').open('w') as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['id']);writer.writeheader();writer.writerows(rows)
 return rows,groups
if __name__=='__main__':build()
