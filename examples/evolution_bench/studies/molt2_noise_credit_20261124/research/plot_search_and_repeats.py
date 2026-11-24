"""Visualize frozen search finalists and verified public repeats; no evaluation."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args();root=args.root.resolve()
audit=json.loads((root/'research/PUBLIC_REPEAT_COVERAGE_AUDIT.json').read_text())
if not audit['verified']:raise SystemExit('Repeat coverage is not yet verified; no figure generated')
cfg=json.loads((root/'campaign.json').read_text());report=json.loads((root/'research/PUBLIC_REREAD_REPORT.json').read_text());jobs=json.loads((root/'manifest.json').read_text());origins={x['run']:x for x in report['origins']};groups=defaultdict(list)
for row in report['rows']:groups[row['key']].append(row['result']['combined_score'])
colors={'simpletes':'#cb762c','molt':'#356ba5','molt_modified':'#42926a','molt2_adaptive_branches':'#8059a2','molt2_noise_credit':'#cb762c'}
labels={'simpletes':'SimpleTES','molt':'Molt','molt_modified':'Modified Molt','molt2_adaptive_branches':'Molt2','molt2_noise_credit':'Noise-credit variant'}
fig,axes=plt.subplots(1,len(cfg['tasks']),figsize=(12,4.6),squeeze=False);data=[];methods=cfg['methods'];seeds=cfg['seeds'];width=.62/len(methods)
for ax,task in zip(axes[0],cfg['tasks']):
    for index,seed in enumerate(seeds):
        for mi,method in enumerate(methods):
            selected=[j for j in jobs if j['task']==task and j['seed']==seed and j['method']==method]
            assert len(selected)==1
            run=selected[0]['id'];origin=origins[run];values=groups[origin['key']];assert len(values)==2
            center=statistics.mean(values);search=origin['search_score'];x=index+(mi-(len(methods)-1)/2)*width;color=colors[method]
            ax.plot([x,x],[search,center],color=color,alpha=.45,linewidth=1)
            ax.errorbar(x,center,yerr=[[center-min(values)],[max(values)-center]],fmt='o',color=color,capsize=3,markersize=5,zorder=3)
            ax.scatter(x,search,marker='x',s=28,color='black',linewidth=1,zorder=4)
            data.append(dict(task=task,seed=seed,method=method,run=run,search_score=search,repeat_scores=values,repeat_mean=center))
    ax.set_title(task.upper());ax.set_xticks(range(len(seeds)),seeds);ax.set_xlabel('Matched search seed');ax.set_ylabel('Training score (higher is better)');ax.grid(axis='y',alpha=.2);ax.margins(y=.15)
legend=[Line2D([],[],marker='o',linestyle='',color=colors[m],label=labels[m]) for m in methods]
legend.append(Line2D([],[],marker='x',linestyle='',color='black',label='Selected search score'))
fig.suptitle('Selected search scores and post-search TRAIN repeats',fontsize=14)
fig.legend(handles=legend,loc='upper center',bbox_to_anchor=(.5,.92),ncol=min(3,len(legend)),frameon=False,fontsize=10)
fig.text(.5,.025,'Whiskers: range of two TRAIN repeats, not confidence intervals · 150 cases · no reselection or held-out evaluation',ha='center',fontsize=8)
fig.tight_layout(rect=(.03,.07,.99,.76))
for suffix in ['pdf','png']:fig.savefig(root/'research'/f'SEARCH_AND_PUBLIC_REPEATS.{suffix}',dpi=170)
(root/'research/SEARCH_AND_PUBLIC_REPEATS_DATA.json').write_text(json.dumps(dict(rows=data,repeat_coverage_verified=True,validation_accessed=False),indent=2)+'\n')
print('Saved search/repeat figure:',len(data),'frozen finalists')
