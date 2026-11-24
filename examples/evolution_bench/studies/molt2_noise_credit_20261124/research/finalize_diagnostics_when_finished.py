"""Render descriptive diagnostics after the frozen controller finishes; no search."""
from pathlib import Path
import json
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
pid=json.loads((ROOT/'CONTROLLER_HANDLE.json').read_text())['pid']
deadline=time.monotonic()+73*3600
status=ROOT/'research/FINAL_DIAGNOSTICS_STATUS.json'
try:
    while True:
        try:
            alive=b'runner/controller.py' in Path(f'/proc/{pid}/cmdline').read_bytes() and Path(f'/proc/{pid}/cwd').resolve()==ROOT
        except OSError:
            alive=False
        if (ROOT/'CAMPAIGN_COMPLETE.json').exists() and not alive:
            break
        if not alive or time.monotonic()>deadline:
            raise RuntimeError('Controller disappeared or bounded wait expired; no experiments restarted')
        time.sleep(30)
    for script,extra in [('plot_paired_pilot.py',['--allow-partial']),('summarize_branch_credit.py',[])]:
        subprocess.run([sys.executable,str(ROOT/'research'/script),*extra],cwd=ROOT,check=True,timeout=180)
    result={'at_unix':time.time(),'status':'saved','validation_accessed':False,'search_restarted':False,'note':'Incomplete matched blocks remain visibly incomplete; no outcome-based exclusions or new evaluations.'}
except Exception as exc:
    result={'at_unix':time.time(),'status':'error','error':repr(exc),'search_restarted':False}
status.write_text(json.dumps(result,indent=2)+'\n')
if result['status']=='error':
    raise SystemExit(1)
