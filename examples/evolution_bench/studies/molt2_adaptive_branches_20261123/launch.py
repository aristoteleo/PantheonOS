"""One detached, bounded development campaign; all paid calls have prior authorization."""
from pathlib import Path
import json,os,subprocess,sys,time
R=Path(__file__).resolve().parent
assert (R/'research/QUALIFICATION_PASSED.json').exists()
assert (R/'research/PREFLIGHT.json').exists()
with (R/'LAUNCH.json').open('x') as f:
    json.dump({'at_unix':time.time(),'session':'molt2-branches-20261123','trials':40,'cumulative_math_cap_usd':74,'biological_budget_excluded':True},f,indent=2)
env=dict(os.environ,PYTHONUNBUFFERED='1')
with (R/'controller.log').open('a') as log:
    child=subprocess.Popen([sys.executable,'-u',str(R/'runner/controller.py')],cwd=R,env=env,stdout=log,stderr=subprocess.STDOUT)
    (R/'CONTROLLER_HANDLE.json').write_text(json.dumps({'pid':child.pid,'at_unix':time.time(),'script':str(R/'runner/controller.py')},indent=2)+'\n')
    rc=child.wait()
(R/'CONTROLLER_EXIT.json').write_text(json.dumps({'returncode':rc,'at_unix':time.time()},indent=2)+'\n')
if rc:raise SystemExit(rc)
with (R/'finalize.log').open('a') as log:
    result=subprocess.run([sys.executable,'-u',str(R/'finalize.py')],cwd=R,env=env,stdout=log,stderr=subprocess.STDOUT)
raise SystemExit(result.returncode)
