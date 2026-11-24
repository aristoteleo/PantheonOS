"""Bounded post-processing only: no optimizer, model calls, or held-out access."""
from pathlib import Path
import os,time,json,subprocess,sys,hashlib
R=Path(__file__).resolve().parents[1]
handle=json.loads((R/'CONTROLLER_HANDLE.json').read_text());pid=handle['pid']
started=time.monotonic();deadline=started+73*3600
status=R/'research/ANALYSIS_WATCH_STATUS.json'
def save(value):
 t=status.with_suffix('.tmp');t.write_text(json.dumps(value,indent=2)+'\n');t.replace(status)
while time.monotonic()<deadline:
 if (R/'CAMPAIGN_COMPLETE.json').exists():break
 try:
  cmd=Path(f'/proc/{pid}/cmdline').read_bytes()
  alive=str(R/'runner/controller.py').encode() in cmd
 except OSError:alive=False
 if not alive:
  save({'status':'controller_missing_without_completion','pid':pid,'at_unix':time.time()});raise SystemExit(2)
 save({'status':'waiting_for_live_campaign','pid':pid,'at_unix':time.time()})
 time.sleep(30)
else:
 save({'status':'deadline_exceeded_no_search_restarted','at_unix':time.time()});raise SystemExit(3)
# comparison is final because completion is written after the last build.
subprocess.run([sys.executable,str(R/'research/paired_inference.py')],check=True)
p=R/'research/PAIRED_INFERENCE.json';data=p.read_bytes();digest=hashlib.sha256(data).hexdigest()
receipt={'status':'paired_development_analysis_saved','sha256':digest,'path':str(p),'at_unix':time.time(),'validation_accessed':False}
try:
 import modal
 remote=f'/archives/molt2-adaptive-branches-20261123/paired-inference-{digest}.json'
 v=modal.Volume.from_name('molt-cardiac4d-runs',create_if_missing=False)
 with v.batch_upload(force=True) as b:b.put_file(p,remote)
 observed=hashlib.sha256()
 for block in v.read_file(remote):observed.update(block)
 assert observed.hexdigest()==digest
 receipt.update(volume='molt-cardiac4d-runs',remote_path=remote,readback_verified=True)
except Exception as e:receipt.update(readback_verified=False,backup_error=repr(e))
save(receipt)
