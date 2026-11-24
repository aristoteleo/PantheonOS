"""Two-page development report; requires verified repeat coverage in both studies."""
from collections import defaultdict
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=Path('/home/erwinpi/molt2-adaptive-branches-lab')
LABELS={'simpletes':'SimpleTES','molt':'Molt','molt_modified':'Modified Molt',
        'molt2_adaptive_branches':'Original Molt2','molt2_noise_credit':'Noise-credit variant'}

def summarize(root):
    audit=json.loads((root/'research/PUBLIC_REPEAT_COVERAGE_AUDIT.json').read_text())
    if not audit['verified']:raise SystemExit('Repeat coverage is not verified: '+str(root))
    cfg=json.loads((root/'campaign.json').read_text())
    jobs={j['id']:j for j in json.loads((root/'manifest.json').read_text())}
    report=json.loads((root/'research/PUBLIC_REREAD_REPORT.json').read_text())
    repeated=defaultdict(list)
    for row in report['rows']:repeated[row['key']].append(row['result']['combined_score'])
    grouped=defaultdict(list)
    for origin in report['origins']:
        job=jobs[origin['run']];values=repeated[origin['key']];assert len(values)==2
        grouped[(job['task'],job['method'])].append(dict(seed=job['seed'],search=origin['search_score'],repeat_mean=statistics.mean(values)))
    means=[]
    for method in cfg['methods']:
        row={'method':method}
        for task in cfg['tasks']:
            values=grouped[(task,method)];assert len(values)==len(cfg['seeds'])
            row[task]={'search_mean':statistics.mean(x['search'] for x in values),
                       'repeat_mean':statistics.mean(x['repeat_mean'] for x in values),'seeds':len(values)}
        means.append(row)
    return dict(root=str(root),seeds=cfg['seeds'],methods=cfg['methods'],means=means,
                repeat_coverage_verified=True,validation_accessed=False)


def page(pdf,study,title,subtitle,conclusion,index):
    fig=plt.figure(figsize=(11.7,8.3))
    fig.text(.5,.96,title,ha='center',fontsize=18,weight='bold')
    fig.text(.5,.915,subtitle,ha='center',fontsize=10)
    ax=fig.add_axes([.02,.34,.96,.53]);ax.imshow(plt.imread(Path(study['root'])/'research/SEARCH_AND_PUBLIC_REPEATS.png'));ax.axis('off')
    values=[]
    for row in study['means']:
        label=LABELS[row['method']]
        values.append([label,*[f"{row[task][kind]:.4f}" for task in ['ahc024','ahc058'] for kind in ['search_mean','repeat_mean']]])
    table_ax=fig.add_axes([.07,.13,.86,.16]);table_ax.axis('off')
    table=table_ax.table(cellText=values,colLabels=['Method','AHC024 search','AHC024 repeats','AHC058 search','AHC058 repeats'],cellLoc='center',loc='center',colWidths=[.26,.185,.185,.185,.185]);table.auto_set_font_size(False);table.set_fontsize(10);table.scale(1,1.55)
    for (r,c),cell in table.get_celld().items():
        cell.set_edgecolor('#dddddd')
        if r==0:cell.set_facecolor('#eeeeee');cell.set_text_props(weight='bold')
    fig.text(.5,.085,conclusion,ha='center',fontsize=10)
    fig.text(.5,.035,'Means across search seeds; two public TRAIN repeats per finalist. No reselection or held-out evaluation.',ha='center',fontsize=9)
    pdf.savefig(fig)
    fig.savefig(ROOT/'research'/f'DEVELOPMENT_REPORT_PAGE{index}.png',dpi=140)
    plt.close(fig)

original=summarize(ORIGINAL);pilot=summarize(ROOT)
path=ROOT/'research/MOLT2_DEVELOPMENT_REPORT.pdf'
with PdfPages(path) as pdf:
    page(pdf,original,'Molt2 development results: five-seed comparison',
         'Separate method: molt2_adaptive_branches. Preserves vanilla Molt; protects promising branches and allocates exploration by progress.',
         'AHC024 gains are concentrated in rare breakthroughs. General superiority over the controls is not established.',1)
    page(pdf,pilot,'Reward-function ablation: three fresh seeds',
         'Original Molt2 versus noise-gated fractional credit. Same model, training cases and first 80 evaluator calls.',
         'The variant changes both the progress gate and credit magnitude. These development results do not isolate their individual effects.',2)
report={'original_five_seed_study':original,'fresh_three_seed_reward_ablation':pilot,
        'studies_pooled':False,'validation_accessed':False,
        'limitations':['Development tasks only.','Small seed counts and timing-dependent randomized programs.','Two repeats describe observed variation, not confidence intervals or the full score distribution.','No claim of general superiority.']}
(ROOT/'research/DEVELOPMENT_REPORT_DATA.json').write_text(json.dumps(report,indent=2)+'\n')
print(path)
