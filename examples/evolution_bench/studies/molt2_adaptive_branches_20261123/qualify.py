"""Recheck the unchanged full public evaluator interfaces, with no model calls."""
from pathlib import Path
import json,subprocess,concurrent.futures,os,sys,time
R=Path(__file__).resolve().parent;cfg=json.loads((R/'campaign.json').read_text())

def grade(task,cores):
    folder=R/'research'/('seed-'+task);folder.mkdir(exist_ok=True)
    code='''from pathlib import Path
import json,os,time
task=Path(os.environ['QUALIFY_TASK'])
cfg=json.loads((task/'task.json').read_text())
os.environ.update(cfg['env']);os.environ['AHC_TASK_DIR']=str(task);os.environ['TASK_DIR']=str(task)
ns={};exec((task/'evaluator.py').read_text(),ns)
result=ns['evaluate'](str(Path(os.environ['QUALIFY_OUTPUT'])),fidelity='full')
Path(os.environ['QUALIFY_OUTPUT'],'RESULT.json').write_text(json.dumps(result,indent=2)+'\\n')
assert result['validity']==1 and result['num_cases']==150 and result['num_accepted']==150,result
print(json.dumps(result))
'''
    (folder/'solution.cpp').write_bytes((R/'tasks'/task/'solution.cpp').read_bytes())
    env={k:v for k,v in os.environ.items() if not k.startswith('AHC_')}
    env.update(QUALIFY_TASK=str(R/'tasks'/task),QUALIFY_OUTPUT=str(folder),OMP_NUM_THREADS='8',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    proc=subprocess.run(['taskset','-c',','.join(map(str,cores)),cfg['python'],'-c',code],env=env,capture_output=True,text=True,timeout=650)
    (folder/'stdout.log').write_text(proc.stdout);(folder/'stderr.log').write_text(proc.stderr)
    if proc.returncode:raise RuntimeError(task+': '+proc.stderr[-2000:])
    return task,json.loads((folder/'RESULT.json').read_text())

with concurrent.futures.ThreadPoolExecutor(2) as pool:
    futures=[pool.submit(grade,task,slot) for task,slot in zip(cfg['tasks'],cfg['cpu_slots'])]
    results=dict(f.result() for f in futures)
(R/'research/QUALIFICATION_PASSED.json').write_text(json.dumps({'status':'passed','public_seed_results':results,'no_model_calls':True,'validation_accessed':False,'used_as_optimizer_feedback':False,'at_unix':time.time()},indent=2)+'\n')
print('Full150-case evaluator checks passed for both tasks.')
