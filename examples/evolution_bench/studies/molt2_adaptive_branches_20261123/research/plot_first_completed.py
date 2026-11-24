"""One-seed diagnostic only; uses the audited80-call selection data."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path(__file__).resolve().parents[1]
fig,axes=plt.subplots(1,2,figsize=(10.5,4.3))
for method,label,color in [('molt','Molt','#2368ad'),('molt2_adaptive_branches','Adaptive Molt2','#d37b1c')]:
 run=R/'runs'/f'matched-ahc024-{method}-s400';assert json.loads((run/'workflow.json').read_text())['status']=='complete'
 audit=json.loads((run/'selection_audit.json').read_text());points=sorted((x for x in audit['first_valid_code_readings'] if x['eligible']),key=lambda x:x['search_eval_index']);best=float('-inf');xs=[];ys=[]
 for p in points:best=max(best,p['score']);xs.append(p['search_eval_index']);ys.append(best)
 if xs[-1]<80:xs.append(80);ys.append(best)
 axes[0].step(xs,ys,where='post',label=f'{label}: {best:.5f}',color=color,lw=2)
axes[0].set(xlabel='Evaluator calls (starting program included)',ylabel='Best valid training score',xlim=(1,80),title='AHC024, seed400: completed searches')
axes[0].legend(fontsize=9);axes[0].grid(alpha=.2)
d=json.loads((R/'research/REWARD_SENSITIVITY.json').read_text())['rows'][0];values=[x['positive_work_items'] for x in d['thresholds']]
axes[1].bar(range(4),values,color=['#9da4aa','#9da4aa','#d37b1c','#d37b1c'])
axes[1].set_xticks(range(4),['1%\n(current)','0.1%','0.01%','Any positive'])
axes[1].set(xlabel='Gain threshold in offline audit',ylabel='Observed work items receiving reward',title='Molt2 reward signal is sparse',ylim=(0,max(values)+1.5))
for n,value in enumerate(values):axes[1].text(n,value+.15,str(value),ha='center')
axes[1].grid(axis='y',alpha=.2);axes[1].set_axisbelow(True)
fig.suptitle('Development diagnostic — one seed, no superiority conclusion',fontsize=12)
fig.text(.5,.025,'Right: observed traces only; lowering a threshold would change future search. Tiny score gains can include timing noise.',ha='center',fontsize=8)
fig.tight_layout(rect=(0,.08,1,.94));fig.savefig(R/'research/FIRST_PAIR_DIAGNOSTIC.png',dpi=180);fig.savefig(R/'research/FIRST_PAIR_DIAGNOSTIC.pdf');plt.close(fig)
