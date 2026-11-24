"""Finite post-processing watcher: uploads only terminal completed trials."""
from pathlib import Path
import os,sys,json,time,subprocess
R=Path(__file__).resolve().parents[1];pid=json.loads((R/'CONTROLLER_HANDLE.json').read_text())['pid'];deadline=time.monotonic()+73*3600
attempts={};errors={}
while time.monotonic()<deadline:
 for run in sorted((R/'runs').iterdir()):
  workflow=run/'workflow.json';receipt=R/'research'/f'BACKUP-{run.name}.json'
  if receipt.exists() or not workflow.exists():continue
  state=json.loads(workflow.read_text())
  if state.get('status')!='complete' or attempts.get(run.name,0)>=3:continue
  attempts[run.name]=attempts.get(run.name,0)+1
  result=subprocess.run([sys.executable,str(R/'research/archive_completed_run.py'),run.name],capture_output=True,text=True,timeout=900)
  if result.returncode:errors[run.name]=result.stderr[-2000:]
  else:errors.pop(run.name,None)
 terminal=(R/'CAMPAIGN_COMPLETE.json').exists()
 try:alive=str(R/'runner/controller.py').encode() in Path(f'/proc/{pid}/cmdline').read_bytes()
 except OSError:alive=False
 status={'at_unix':time.time(),'status':'finished' if terminal else 'waiting_for_live_campaign' if alive else 'controller_missing','attempts':attempts,'backup_errors':errors,'search_or_model_calls':False}
 p=R/'research/INCREMENTAL_BACKUP_STATUS.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(status,indent=2)+'\n');tmp.replace(p)
 if terminal or not alive:break
 time.sleep(30)
