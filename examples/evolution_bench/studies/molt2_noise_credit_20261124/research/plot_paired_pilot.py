"""Plot completed matched seed blocks only; never selects or evaluates programs."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    cfg = json.loads((ROOT/'campaign.json').read_text())
    rows=[]
    for task in cfg['tasks']:
        for seed in cfg['seeds']:
            scores={}
            statuses={}
            for method in cfg['methods']:
                folder=ROOT/'runs'/f'noise-{task}-{method}-s{seed}'
                wp=folder/'workflow.json'
                status=json.loads(wp.read_text())['status'] if wp.exists() else 'not_started'
                statuses[method]=status
                if status!='complete':
                    continue
                selection=json.loads((folder/'selection.json').read_text())
                assert selection['eval_cap']==80 and selection['cases']==150
                assert hashlib.sha256((folder/'selected/solution.cpp').read_bytes()).hexdigest()==selection['sha256']
                scores[method]=selection['score']
            rows.append(dict(task=task,seed=seed,scores=scores,statuses=statuses,paired=len(scores)==2))
    finished=all(row['paired'] for row in rows)
    if not finished and not args.allow_partial:
        raise SystemExit('Cohort unfinished; use --allow-partial for an explicitly interim figure')
    colors={'molt2_adaptive_branches':'#3569a8','molt2_noise_credit':'#cc7529'}
    labels={'molt2_adaptive_branches':'Original Molt2','molt2_noise_credit':'Noise-credit variant'}
    fig,axes=plt.subplots(1,len(cfg['tasks']),figsize=(10,3.7),squeeze=False)
    for ax,task in zip(axes[0],cfg['tasks']):
        for row in (r for r in rows if r['task']==task):
            x=cfg['seeds'].index(row['seed'])
            if not row['paired']:
                label='Incomplete' if any(s not in ['complete','running','queued','not_started'] for s in row['statuses'].values()) else 'Pending'
                ax.text(x,.5,label,ha='center',va='center',transform=ax.get_xaxis_transform(),color='gray')
                continue
            ys=[row['scores'][m] for m in cfg['methods']]
            ax.plot([x-.12,x+.12],ys,color='#999999',linewidth=1)
            for offset,method in zip([-.12,.12],cfg['methods']):
                y=row['scores'][method]
                ax.scatter(x+offset,y,color=colors[method],s=55,zorder=3)
                ax.annotate(f'{y:.4f}',(x+offset,y),xytext=(0,9 if offset<0 else -16),textcoords='offset points',ha='center',fontsize=8)
        ax.set(title=task.upper(),ylabel='Selected training score (higher is better)',xlabel='Matched seed',xlim=(-.5,len(cfg['seeds'])-.5))
        ax.set_xticks(range(len(cfg['seeds'])),cfg['seeds'])
        ax.margins(y=.25)
        ax.grid(axis='y',alpha=.2)
    for method in cfg['methods']:
        axes[0,0].scatter([],[],color=colors[method],label=labels[method])
    fig.legend(loc='upper center',bbox_to_anchor=(.5,.91),ncol=2,frameon=False)
    status='Complete development pilot' if finished else 'INTERIM — completed seed pairs only'
    fig.suptitle(status,fontsize=13)
    fig.text(.5,.015,'150 public training cases · first 80 evaluator calls · no held-out evaluation · no pooled mean for unfinished cohorts',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.06,1,.86))
    stem='PAIRED_PILOT_FINALISTS' if finished else 'PAIRED_PILOT_FINALISTS_INTERIM'
    for suffix in ['pdf','png']:
        fig.savefig(ROOT/'research'/f'{stem}.{suffix}',dpi=170)
    (ROOT/'research'/f'{stem}_DATA.json').write_text(json.dumps(dict(at_unix=time.time(),complete=finished,rows=rows,validation_accessed=False),indent=2)+'\n')
    print(stem)

if __name__=='__main__':
    main()
