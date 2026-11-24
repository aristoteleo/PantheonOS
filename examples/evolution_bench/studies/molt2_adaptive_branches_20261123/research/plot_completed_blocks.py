"""Public development traces only; plot complete matched blocks without pooling seeds."""
from pathlib import Path
import json
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path(__file__).resolve().parents[1]
METHODS=[('simpletes','SimpleTES','#d97918'),('molt','Molt','#2267ae'),('molt_modified','Modified Molt','#7e57a3'),('molt2_adaptive_branches','Molt2','#288651')]
rows=json.loads((R/'comparison.json').read_text())['rows']
complete={(x['task'],x['seed'],x['method']):x for x in rows if x['status']=='complete'}
blocks=[(task,seed) for task,seed in sorted({(x['task'],x['seed']) for x in rows}) if all((task,seed,m) in complete for m,_,_ in METHODS)]
if not blocks:raise SystemExit('No complete matched blocks yet')
cols=2 if len(blocks)==4 else min(3,len(blocks));nrows=math.ceil(len(blocks)/cols)
fig,axes=plt.subplots(nrows,cols,figsize=(5*cols,4.4*nrows),squeeze=False)
for ax in axes.flat[len(blocks):]:ax.set_visible(False)
export=[]
for ax,(task,seed) in zip(axes.flat,blocks):
 for method,label,color in METHODS:
  row=complete[task,seed,method];run=R/'runs'/row['id']
  audit=json.loads((run/'selection_audit.json').read_text())
  points=sorted((p for p in audit['first_valid_code_readings'] if p['eligible']),key=lambda p:p['search_eval_index'])
  xs=[];ys=[];best=float('-inf')
  for p in points:
   best=max(best,p['score']);xs.append(p['search_eval_index']);ys.append(best)
  assert abs(best-row['score'])<1e-10,(row['id'],best,row['score'])
  # End at the observed budget, never invent missing evaluations after early stops.
  end=min(80,row['eval_calls'])
  if xs[-1]<end:xs.append(end);ys.append(best)
  ax.step(xs,ys,where='post',color=color,label=label,lw=1.8)
  ax.scatter([xs[-1]],[ys[-1]],color=color,s=20,zorder=3)
  export.append({'run':row['id'],'x':xs,'best_score':ys,'eligible_ceiling':80,'actual_calls':row['eval_calls']})
 ax.set(title=f'{task.upper()} · seed {seed}',xlabel='Evaluator calls (baseline included)',ylabel='Best eligible training score',xlim=(1,80))
 ax.grid(alpha=.2);ax.legend(fontsize=8)
fig.suptitle('Completed matched development blocks — individual seeds, not five-seed means',fontsize=12)
fig.text(.5,.015,'Higher is better. Each panel has its own score axis. Marks show observed endpoints; no validation data or significance claim.',ha='center',fontsize=9)
fig.tight_layout(rect=(0,.055,1,.92))
for ext in ['pdf','png']:fig.savefig(R/'research'/f'COMPLETED_MATCHED_BLOCKS.{ext}',dpi=180)
(R/'research/COMPLETED_MATCHED_BLOCKS_DATA.json').write_text(json.dumps({'scope':'Completed matched blocks only; unpooled individual seeds','curves':export},indent=2)+'\n')
print(f'Plotted {len(blocks)} completed matched blocks')
