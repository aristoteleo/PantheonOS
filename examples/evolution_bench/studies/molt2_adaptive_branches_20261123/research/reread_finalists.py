"""Two public training rereads per frozen source, across both original CPU slots.

Never changes selection, calls a model, or reads held-out cases. Runs only after
all optimization stops. Duplicated source hashes share the same rereads.
"""
from pathlib import Path
import json,hashlib,os,sys,time,subprocess,concurrent.futures,random,shutil,fcntl,tarfile,signal
R=Path(__file__).resolve().parents[1]

def main():
 if not (R/'CAMPAIGN_COMPLETE.json').exists():raise SystemExit('Refused: optimization has not completed; no rereads launched')
 frozen=json.loads((R/'FINALIST_FREEZE.json').read_text());assert frozen['optimizer_stopped']
 pid=json.loads((R/'CONTROLLER_HANDLE.json').read_text())['pid']
 for _ in range(60):
  try:alive=str(R/'runner/controller.py').encode() in Path(f'/proc/{pid}/cmdline').read_bytes()
  except OSError:alive=False
  if not alive:break
  time.sleep(1)
 else:raise SystemExit('Refused: optimizer process still live')
 cfg=json.loads((R/'campaign.json').read_text());jobs={j['id']:j for j in json.loads((R/'manifest.json').read_text())}
 for name,digest in json.loads((R/'research/RUNTIME_DEPENDENCIES.json').read_text()).items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
 output=R/'public-rereads';output.mkdir(exist_ok=True);lock=(output/'lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 unique={};origins=[]
 for run,sel in frozen['selections'].items():
  if not sel:continue
  source=R/'runs'/run/'selected/solution.cpp';digest=hashlib.sha256(source.read_bytes()).hexdigest();assert digest==sel['sha256']
  key=jobs[run]['task']+'-'+digest;unique.setdefault(key,{'task':jobs[run]['task'],'source':str(source),'sha256':digest,'key':key});origins.append({'run':run,'key':key,'search_score':sel['score']})
 deadline=time.monotonic()+12*3600
 worker_code='''from pathlib import Path
import os,json
task=Path(os.environ['REREAD_TASK']);config=json.loads((task/'task.json').read_text());os.environ.update(config['env']);os.environ['AHC_TASK_DIR']=str(task);os.environ['TASK_DIR']=str(task)
ns={};exec((task/'evaluator.py').read_text(),ns)
result=ns['evaluate'](os.environ['REREAD_OUTPUT'],fidelity='full')
Path(os.environ['REREAD_OUTPUT'],'RESULT.json').write_text(json.dumps(result,indent=2))
'''
 def slot_runner(slot_id,cores):
  order=list(unique.values());random.Random(20261124+slot_id).shuffle(order);rows=[]
  for item in order:
   if time.monotonic()>deadline:rows.append({'key':item['key'],'slot':slot_id,'status':'deadline_not_evaluated'});continue
   folder=output/item['key']/f'slot{slot_id}';folder.mkdir(parents=True,exist_ok=True);source=folder/'solution.cpp';shutil.copy2(item['source'],source);assert hashlib.sha256(source.read_bytes()).hexdigest()==item['sha256']
   env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8','REREAD_TASK':str(R/'tasks'/item['task']),'REREAD_OUTPUT':str(folder),'OMP_NUM_THREADS':'8','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
   record={'key':item['key'],'task':item['task'],'source_sha256':item['sha256'],'slot':slot_id,'cores':cores,'outside_search_budget':True,'used_for_selection':False}
   began=time.time()
   try:
    proc=subprocess.Popen(['taskset','-c',','.join(map(str,cores)),cfg['python'],'-c',worker_code],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:stdout,stderr=proc.communicate(timeout=650)
    except subprocess.TimeoutExpired:
     os.killpg(proc.pid,signal.SIGTERM)
     try:stdout,stderr=proc.communicate(timeout=5)
     except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);stdout,stderr=proc.communicate()
     (folder/'stdout.log').write_text(stdout);(folder/'stderr.log').write_text(stderr);raise RuntimeError('Reread timeout; owned process group terminated')
    (folder/'stdout.log').write_text(stdout);(folder/'stderr.log').write_text(stderr);assert proc.returncode==0,stderr[-1000:]
    raw=json.loads((folder/'RESULT.json').read_text());record.update(status='valid' if raw.get('validity')==1 and raw.get('num_cases')==raw.get('num_accepted')==150 else 'invalid',result=raw)
   except Exception as exc:record.update(status='failed',error=str(exc))
   record['wall_seconds']=time.time()-began;record['at_unix']=time.time();(folder/'AUDIT.json').write_text(json.dumps(record,indent=2)+'\n');rows.append(record)
  return rows
 with concurrent.futures.ThreadPoolExecutor(len(cfg['cpu_slots'])) as pool:
  futures=[pool.submit(slot_runner,i,cores) for i,cores in enumerate(cfg['cpu_slots'])];rows=[row for f in futures for row in f.result()]
 report={'status':'all_selected_sources_reread' if rows and all(x['status']=='valid' for x in rows) else 'issues_or_no_finalists','rows':rows,'origins':origins,'unique_sources':len(unique),'selected_runs':len(origins),'scheduled_runs':len(jobs),'omitted_unselected_runs':len(jobs)-len(origins),'public_cases_per_reread':150,'rereads_per_source':len(cfg['cpu_slots']),'no_model_calls':True,'validation_accessed':False,'used_as_optimizer_feedback':False,'selection_changed':False,'caution':'Post-search public robustness checks only. Report invalid/timeouts explicitly; do not replace primary first80 search scores with these rereads. Both original CPU slots are used for every unique source. Generated programs may use clock-seeded RNGs, timing-dependent cooling or iteration counts. Two rereads per source do not characterize their full stochastic score distribution.'}
 result=R/'research/PUBLIC_REREAD_REPORT.json';result.write_text(json.dumps(report,indent=2)+'\n')
 archive=R/'public-reread-artifacts.tar.gz'
 with tarfile.open(archive,'w:gz') as t:t.add(output,arcname='public-rereads');t.add(result,arcname='PUBLIC_REREAD_REPORT.json')
 digest=hashlib.sha256(archive.read_bytes()).hexdigest();receipt={'sha256':digest,'local':str(archive),'readback_verified':False}
 try:
  import modal
  volume=modal.Volume.from_name('molt-cardiac4d-runs',create_if_missing=False);remote=f'/archives/molt2-adaptive-branches-20261123/public-rereads-{digest}.tar.gz'
  with volume.batch_upload(force=True) as batch:batch.put_file(archive,remote)
  seen=hashlib.sha256()
  for block in volume.read_file(remote):seen.update(block)
  assert seen.hexdigest()==digest
  receipt.update(readback_verified=True,volume='molt-cardiac4d-runs',remote=remote)
 except Exception as exc:receipt['backup_error']=repr(exc)
 (R/'research/PUBLIC_REREAD_BACKUP.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps({'status':report['status'],'rereads':len(rows),'unique_sources':len(unique),'archive':receipt},indent=2))
if __name__=='__main__':main()
