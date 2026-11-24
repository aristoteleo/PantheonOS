from pathlib import Path
import json,subprocess,os,time,fcntl,signal,concurrent.futures,queue,sys,hashlib
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'runner'));from report import build,read,save
from budget_selection import eligible_store
cfg=read(R/'campaign.json');lock=(R/'campaign.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=cfg['engine_root'],text=True).strip()==cfg['engine_commit']
for name,digest in read(R/'SOURCE_FREEZE.json')['files'].items():assert hashlib.sha256((R/name).read_bytes()).hexdigest()==digest
ledger=read(Path(cfg['shared_ledger_root'])/'spend-ledger.json')
assert cfg['global_usd_cap']-ledger['spent_usd']-sum(ledger.get('reserved',{}).values())>=3
for name,digest in read(R/'research/RUNTIME_DEPENDENCIES.json').items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest, 'Runtime dependency changed:'+name
slots=queue.Queue()
for cores in cfg['cpu_slots']:slots.put(cores)
start=time.time();deadline=start+72*3600

def select(j):
 d=Path(j['output']);summary=read(d/'summary.json',read(d/'partial_summary.json'));store=read(d/'store.json');eligible,audit=eligible_store(store,summary,j['eval_cap'],required_cases=150);save(d/'selection_audit.json',audit)
 candidates=[i for i in eligible['individuals'] if i['measurements'][0]['metrics'].get('validity')==1]
 if not candidates:return False
 best=max(candidates,key=lambda i:i['measurements'][0]['metrics']['combined_score']);score=best['measurements'][0]['metrics']['combined_score'];files=best['genome']['files'];target=d/'selected';target.mkdir(exist_ok=True)
 import hashlib
 for name,code in files.items():
  p=target/name
  if not p.resolve().is_relative_to(target.resolve()):raise ValueError('Unsafe filename')
  p.parent.mkdir(parents=True,exist_ok=True);p.write_text(code)
 save(d/'selection.json',{'id':best['id'],'score':score,'sha256':hashlib.sha256(files['solution.cpp'].encode()).hexdigest(),'eval_cap':j['eval_cap'],'cases':150,'policy':'First eligible full measurement per distinct source inside the evaluator-call ceiling.'});return True

def run(j):
 out=Path(j['output']);core=slots.get();beg=time.time();p=None;state={'status':'running','started_epoch':beg,'cores':core}
 try:
  if time.time()>deadline or (R/'GLOBAL_STOP.json').exists() or (Path(cfg['shared_ledger_root'])/'GLOBAL_STOP.json').exists():
   state.update(status='not_admitted_stop',returncode=None);return state
  env={k:v for k,v in os.environ.items() if not k.startswith('AHC_') and k!='EVAL_SERVER_URL'};env.update(OMP_NUM_THREADS='8',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
  with (out/'stdout.log').open('a') as log:
   p=subprocess.Popen(['taskset','-c',','.join(map(str,core)),cfg['python'],'-u',str(R/'runner/entry_worker.py'),str(out/'job.json')],stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True);state['pid']=p.pid;save(out/'workflow.json',state)
   while p.poll() is None:
    if time.time()>deadline or time.time()-beg>28800 or (R/'GLOBAL_STOP.json').exists():
     state['status']='timeout_or_stop';os.killpg(p.pid,signal.SIGTERM)
     try:p.wait(timeout=15)
     except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL)
     break
    time.sleep(2)
   rc=p.wait();state.update(returncode=rc,wall_seconds=time.time()-beg)
   if state['status']=='running':state['status']='complete' if rc==0 and select(j) else 'failed'
 except Exception as exc:state.update(status='failed',error=str(exc),wall_seconds=time.time()-beg)
 finally:
  save(out/'workflow.json',state);slots.put(core)
 return state

jobs=read(R/'manifest.json')
save(R/'RUNNING.json',{'controller_pid':os.getpid(),'started_epoch':start,'jobs':len(jobs)})
with concurrent.futures.ThreadPoolExecutor(max_workers=len(cfg['cpu_slots'])) as executor:
 futures=[executor.submit(run,j) for j in jobs]
 while not all(f.done() for f in futures):build();time.sleep(15)
 for future in futures:future.result()
rows,groups=build();save(R/'FINALIST_FREEZE.json',{'selections':{j['id']:read(Path(j['output'])/'selection.json') for j in jobs},'optimizer_stopped':True})
save(R/'CAMPAIGN_COMPLETE.json',{'status':'search_complete' if all(r['status']=='complete' for r in rows) else 'incomplete','reserved_accessed':False,'elapsed_seconds':time.time()-start})
