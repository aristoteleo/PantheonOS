"""Read-only completion audit; no evaluation, model call, or experiment restart."""
from pathlib import Path
import hashlib
import json
import time
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'campaign.json').read_text())
freeze=json.loads((ROOT/'SOURCE_FREEZE.json').read_text())['files']
changed=[]
for rel,expected in freeze.items():
    path=ROOT/rel
    actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if actual!=expected:changed.append(rel)
rows=[]
for task in cfg['tasks']:
    for seed in cfg['seeds']:
        for method in cfg['methods']:
            name=f'noise-{task}-{method}-s{seed}'
            folder=ROOT/'runs'/name
            workflow=folder/'workflow.json'
            status=json.loads(workflow.read_text())['status'] if workflow.exists() else 'not_started'
            row=dict(run=name,status=status,strict_selection_verified=False,remote_archive_verified=False)
            if status=='complete':
                selection=json.loads((folder/'selection.json').read_text())
                source=folder/'selected/solution.cpp'
                row['strict_selection_verified']=(selection['eval_cap']==80 and selection['cases']==150
                    and hashlib.sha256(source.read_bytes()).hexdigest()==selection['sha256'])
                receipt=ROOT/'research'/f'BACKUP-{name}.json'
                if receipt.exists():
                    backup=json.loads(receipt.read_text())
                    row['remote_archive_verified']=(backup.get('readback_verified') is True
                        and backup.get('selected_source_sha256')==selection['sha256'])
            rows.append(row)
all_runs_ready=all(r['status']=='complete' and r['strict_selection_verified'] and r['remote_archive_verified'] for r in rows)
report=dict(at_unix=time.time(),expected_run_count=len(rows),completed_run_count=sum(r['status']=='complete' for r in rows),
            rows=rows,frozen_file_count=len(freeze),changed_frozen_files=changed,
            all_search_runs_and_archives_verified=all_runs_ready and not changed,
            paired_analysis_present=(ROOT/'research/PAIRED_PILOT_REPORT.json').exists(),
            public_repeat_report_present=(ROOT/'research/PUBLIC_REREAD_REPORT.json').exists(),
            completion_proven=False,
            limitation='This audit does not prove statistical superiority. Public repeat results and their archive must still be inspected against the full frozen finalist set. Presence alone does not verify repeat coverage or validity.')
(ROOT/'research/PILOT_COMPLETION_AUDIT.json').write_text(json.dumps(report,indent=2)+'\n')
print('Completed',report['completed_run_count'],'of',len(rows),'runs; frozen source changes:',len(changed))
