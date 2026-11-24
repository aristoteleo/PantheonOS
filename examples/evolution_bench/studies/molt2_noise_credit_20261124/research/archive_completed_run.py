"""Preserve one terminal successful run, with checksum readback. No model calls."""
from pathlib import Path
import sys,json,hashlib,tarfile,time
import modal
R=Path(__file__).resolve().parents[1];name=sys.argv[1];run=R/'runs'/name
assert run.parent==R/'runs' and '/' not in name
assert json.loads((run/'workflow.json').read_text())['status']=='complete'
selection=json.loads((run/'selection.json').read_text());assert hashlib.sha256((run/'selected/solution.cpp').read_bytes()).hexdigest()==selection['sha256']
folder=R/'preservation/completed-runs';folder.mkdir(parents=True,exist_ok=True)
archive=folder/(name+'.tar.gz')
with tarfile.open(archive,'w:gz') as t:t.add(run,arcname='runs/'+name)
digest=hashlib.sha256(archive.read_bytes()).hexdigest();remote=f'/archives/molt2-noise-credit-20261124/runs/{name}-{digest}.tar.gz'
v=modal.Volume.from_name('molt-cardiac4d-runs',create_if_missing=False)
with v.batch_upload(force=True) as batch:batch.put_file(archive,remote)
observed=hashlib.sha256()
for chunk in v.read_file(remote):observed.update(chunk)
assert observed.hexdigest()==digest
receipt={'run':name,'sha256':digest,'remote':remote,'volume':'molt-cardiac4d-runs','readback_verified':True,'selected_source_sha256':selection['sha256'],'at_unix':time.time(),'validation_accessed':False}
(R/'research'/f'BACKUP-{name}.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
