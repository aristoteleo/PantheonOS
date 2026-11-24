"""Summarize the fixed development cohort and preserve its artifacts. No model calls."""
from pathlib import Path
import hashlib,itertools,json,statistics,tarfile,time,sys
R=Path(__file__).resolve().parent
sys.path.insert(0,str(R/'runner'))
from report import build
rows,groups=build();pairs=[]
# Preserve raw outcomes and apply only the documented implementation-integrity gate.
(R/'research/RAW_CAMPAIGN_COMPARISON_AT_END.json').write_text(json.dumps({'rows':rows,'groups':groups},indent=2)+'\n')
sys.path.insert(0,str(R/'research'))
try:
    from measurement_event_audit import build as event_audit
    event_integrity=event_audit();excluded=set(event_integrity['excluded_primary_run_ids'])
except Exception as exc:
    event_integrity={'status':'audit_error','error':repr(exc)}
    excluded={x['id'] for x in rows if x['method']=='molt2_adaptive_branches'}
rows=[dict(x,status='implementation_review_required') if x['id'] in excluded else x for x in rows]
if excluded:
    revised=[]
    for g in groups:
        values=[x['score'] for x in rows if (x['phase'],x['task'],x['method'])==(g['phase'],g['task'],g['method']) and x['status']=='complete' and x['score'] is not None]
        revised.append({**g,'complete':len(values),'mean':statistics.mean(values) if values else None,'sd':statistics.stdev(values) if len(values)>1 else None})
    groups=revised

for task in ['ahc024','ahc058']:
    for control in ['simpletes','molt','molt_modified']:
        values=[]
        for seed in range(400,405):
            a=next(x for x in rows if x['task']==task and x['method']=='molt2_adaptive_branches' and x['seed']==seed)
            b=next(x for x in rows if x['task']==task and x['method']==control and x['seed']==seed)
            if a['status']=='complete' and b['status']=='complete':values.append({'seed':seed,'delta':a['score']-b['score']})
        deltas=[x['delta'] for x in values]
        pairs.append({'task':task,'control':control,'paired_seeds':len(values),'values':values,
                      'mean_difference':statistics.mean(deltas) if deltas else None,
                      'sd_difference':statistics.stdev(deltas) if len(deltas)>1 else None,
                      'wins':sum(x>0 for x in deltas),'ties':sum(x==0 for x in deltas)})
result={'status':'complete' if all(x['status']=='complete' for x in rows) else 'incomplete',
        'groups':groups,'paired_development_differences':pairs,'implementation_event_audit':event_integrity,'excluded_primary_run_ids':sorted(excluded),
        'interpretation':'Exploratory development evidence on two selected tasks. Five seeds cannot establish broad superiority. Scores are from first80 eligible native search calls; no historical-provider controls are pooled into fresh means.',
        'validation_accessed':False,'wall_time_scoring_caveat':'C++ heuristics are time limited; CPU scheduling/frequency affects numeric scores. Fresh method controls share hardware/affinity policy and randomized block order. Exact historical score parity is not claimed.',
        'at_unix':time.time()}
(R/'research/FINAL_ANALYSIS.json').write_text(json.dumps(result,indent=2)+'\n')
lines=['Adaptive Molt2 development comparison',result['interpretation'],result['wall_time_scoring_caveat'],'']
if excluded:lines.append('Primary inference withholds runs requiring event-identity implementation review: '+', '.join(sorted(excluded)))
for g in groups:lines.append(f"{g['task']} / {g['method']}: n={g['complete']}/{g['scheduled']}, mean={g['mean']}, SD={g['sd']}")
lines.append('\nValidation remained untouched. Budget and all raw traces remain independently auditable.')
(R/'research/FINAL_REPORT.txt').write_text('\n'.join(lines)+'\n')
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4.5))
    methods=['simpletes','molt','molt_modified','molt2_adaptive_branches']
    labels=['SimpleTES','Molt','Modified\nMolt','Adaptive\nMolt2']
    for ax,task in zip(axes,['ahc024','ahc058']):
        data=[[x['score'] for x in rows if x['task']==task and x['method']==m and x['status']=='complete'] for m in methods]
        for j,values in enumerate(data):
            if values:
                ax.bar(j,statistics.mean(values),alpha=.45)
                ax.scatter([j]*len(values),values,color='black',s=18,zorder=3)
                ax.text(j,statistics.mean(values),f' n={len(values)}',ha='center',va='bottom',fontsize=8)
        ax.set_xticks(range(4),labels,fontsize=9);ax.set_title(task);ax.set_ylabel('Best training score within80 calls');ax.grid(axis='y',alpha=.2)
    fig.suptitle('Development only: each dot is one independent search seed')
    fig.tight_layout();fig.savefig(R/'research/FINAL_REPORT.pdf');plt.close(fig)
except Exception as exc:(R/'research/PLOT_ERROR.json').write_text(json.dumps({'error':repr(exc)})+'\n')
# Keep financial receipts for only this cohort, never credentials or unrelated studies.
cfg=json.loads((R/'campaign.json').read_text());ledger=json.loads((Path(cfg['shared_ledger_root'])/'spend-ledger.json').read_text())
ids={x['id'] for x in rows};calls={k:v for k,v in ledger['calls'].items() if v.get('run_id') in ids}
(R/'research/STUDY_MODEL_COST.json').write_text(json.dumps({'calls':calls,'conservative_total_usd':sum(v['conservative_charge_usd'] for v in calls.values()),'cumulative_math_cap_usd':74},indent=2)+'\n')
archive=R/'development-artifacts.tar.gz'
with tarfile.open(archive,'w:gz') as t:
    for name in ['runner','tasks','runs','research','source','README.txt','campaign.json','manifest.json','SOURCE_FREEZE.json','AUTHORIZATION.json','LAUNCH.json','controller.log','launch.py','preflight.py','qualify.py','finalize.py']:
        p=R/name
        if p.exists():t.add(p,arcname=name,filter=lambda info:None if '__pycache__' in Path(info.name).parts else info)
from archive_validation import validate_archive
archive_validation=validate_archive(archive,R)
digest=hashlib.sha256(archive.read_bytes()).hexdigest();receipt={'sha256':digest,'bytes':archive.stat().st_size,'local_archive':str(archive),'artifact_validation':archive_validation}
try:
    import modal
    volume=modal.Volume.from_name('molt-cardiac4d-runs',create_if_missing=False)
    remote=f'/archives/molt2-adaptive-branches-20261123/{digest}.tar.gz'
    with volume.batch_upload(force=True) as batch:batch.put_file(archive,remote)
    observed=hashlib.sha256()
    for chunk in volume.read_file(remote):observed.update(chunk)
    assert observed.hexdigest()==digest
    receipt.update(volume='molt-cardiac4d-runs',remote_path=remote,readback_verified=True)
except Exception as exc:receipt.update(readback_verified=False,remote_error=repr(exc))
(R/'research/FINAL_ARCHIVE_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'status':result['status'],'archive':receipt},indent=2))
