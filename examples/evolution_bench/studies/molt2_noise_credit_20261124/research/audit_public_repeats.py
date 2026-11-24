"""Verify public repeat coverage and source identity; never evaluates or selects."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

parser=argparse.ArgumentParser()
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
args=parser.parse_args();root=args.root.resolve();destination=root/'research/PUBLIC_REPEAT_COVERAGE_AUDIT.json'
required=['FINALIST_FREEZE.json','research/PUBLIC_REREAD_REPORT.json','research/PUBLIC_REREAD_BACKUP.json']
missing=[name for name in required if not (root/name).is_file()]
if missing:
    report=dict(at_unix=time.time(),status='pending',verified=False,missing=missing)
else:
    errors=[]
    def check(ok,message):
        if not ok:errors.append(message)
    frozen=json.loads((root/'FINALIST_FREEZE.json').read_text())
    data=json.loads((root/'research/PUBLIC_REREAD_REPORT.json').read_text())
    cfg=json.loads((root/'campaign.json').read_text())
    jobs={job['id']:job for job in json.loads((root/'manifest.json').read_text())}
    check(frozen['optimizer_stopped'] is True,'Optimizer stop not frozen')
    check(set(frozen['selections'])==set(jobs),'Finalist freeze omits scheduled runs')
    expected={};unique={}
    for run in jobs:
        selection=frozen['selections'].get(run)
        check(bool(selection),'Missing selected finalist: '+run)
        if not selection:continue
        check(selection['eval_cap']==80 and selection['cases']==150,'Selection protocol: '+run)
        current=json.loads((root/'runs'/run/'selection.json').read_text())
        check(current==selection,'Selection changed after freeze: '+run)
        digest=hashlib.sha256((root/'runs'/run/'selected/solution.cpp').read_bytes()).hexdigest()
        check(digest==selection['sha256'],'Selected source changed: '+run)
        key=jobs[run]['task']+'-'+digest
        expected[run]=dict(key=key,search_score=selection['score'])
        unique[key]=digest
    origins={row['run']:dict(key=row['key'],search_score=row['search_score']) for row in data['origins']}
    check(len(origins)==len(data['origins']),'Duplicate origin records')
    check(origins==expected,'Origins do not exactly cover frozen finalists')
    expected_pairs={(key,slot) for key in unique for slot in range(len(cfg['cpu_slots']))}
    observed=[]
    for row in data['rows']:
        pair=(row['key'],row['slot']);observed.append(pair)
        check(pair in expected_pairs,'Unexpected repeat '+str(pair))
        if pair not in expected_pairs:continue
        key,slot=pair;digest=unique[key]
        check(row.get('status')=='valid','Invalid repeat '+str(pair))
        check(row.get('outside_search_budget') is True and row.get('used_for_selection') is False,'Feedback flag '+str(pair))
        check(row.get('source_sha256')==digest,'Reported source hash '+str(pair))
        check(row.get('cores')==cfg['cpu_slots'][slot],'CPU slot '+str(pair))
        folder=root/'public-rereads'/key/f'slot{slot}'
        check(hashlib.sha256((folder/'solution.cpp').read_bytes()).hexdigest()==digest,'Copied source '+str(pair))
        result=json.loads((folder/'RESULT.json').read_text())
        check(result==row.get('result'),'Raw result mismatch '+str(pair))
        check(result.get('validity')==1 and result.get('num_cases')==result.get('num_accepted')==150,'Case coverage '+str(pair))
        score=result.get('combined_score');check(isinstance(score,(int,float)) and math.isfinite(score),'Nonfinite score '+str(pair))
    check(len(observed)==len(expected_pairs) and set(observed)==expected_pairs,'Missing or duplicate repeats')
    for key,value in [('no_model_calls',True),('validation_accessed',False),('used_as_optimizer_feedback',False),('selection_changed',False)]:
        check(data.get(key) is value,'Report flag '+key)
    backup=json.loads((root/'research/PUBLIC_REREAD_BACKUP.json').read_text())
    check(backup.get('readback_verified') is True,'Remote archive readback unverified')
    archive=Path(backup['local'])
    check(archive.exists() and hashlib.sha256(archive.read_bytes()).hexdigest()==backup['sha256'],'Local archive hash mismatch')
    report=dict(at_unix=time.time(),status='verified' if not errors else 'issues',verified=not errors,
                scheduled_runs=len(jobs),selected_runs=len(expected),unique_sources=len(unique),
                expected_repeats=len(expected_pairs),observed_repeats=len(observed),errors=errors,
                limitation='Coverage and source identity audit; two repeats do not establish statistical superiority or fully characterize score noise.')
destination.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
