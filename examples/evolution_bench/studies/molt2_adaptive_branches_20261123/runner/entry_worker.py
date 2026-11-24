"""Start one native optimizer with audit hooks; no method changes or secret files copied."""
from pathlib import Path
import sys,json,os,runpy
ROOT=Path(__file__).resolve().parent.parent
job=json.loads(Path(sys.argv[1]).read_text());config=json.loads((ROOT/'campaign.json').read_text())
engine=Path(config['engine_root']);sys.path.insert(0,str(engine))
import subprocess,hashlib
actual_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=engine,text=True).strip()
assert actual_commit==config['engine_commit'],'Unexpected engine checkout'
(Path(job['output'])/'ENGINE_PROVENANCE.json').write_text(json.dumps({'engine_root':str(engine),'engine_commit':actual_commit,'algorithm_sources':{name:hashlib.sha256((engine/name).read_bytes()).hexdigest() for name in ['pantheon/evolution/methods/molt.py','pantheon/evolution/methods/simpletes.py','pantheon/evolution/methods/molt_modified.py','pantheon/evolution/methods/molt2_adaptive_branches.py']}},indent=2)+'\n')
os.environ.update(PYTHONPATH=str(engine),MOLT2_LAB=str(ROOT),MOLT2_PROVIDER=config['provider'],MOLT2_GLOBAL_USD=str(config['global_usd_cap']),MOLT2_RUN_ID=job['job_id'],MOLT_LLM_IO_DIR=str(Path(job['output'])/'llm_io'),MOLT2_EVAL_FINGERPRINT=job['eval_fingerprint'])
os.environ['MOLT2_LEDGER_ROOT']=config['shared_ledger_root']
os.environ['MOLT2_MODEL_SEED']=str(job['seed'])
key=Path(config['api_key_file']).read_text().strip();os.environ['OPENROUTER_API_KEY']=key
class Redactor:
 def __init__(self,stream):self.stream=stream
 def write(self,text):return self.stream.write(str(text).replace(key,'[REDACTED]'))
 def flush(self):return self.stream.flush()
 def __getattr__(self,name):return getattr(self.stream,name)
sys.stdout=Redactor(sys.stdout);sys.stderr=Redactor(sys.stderr)
from instrumentation_route import install_for_job
install_for_job(job)
import pantheon
if not Path(pantheon.__file__).resolve().is_relative_to(engine.resolve()):raise RuntimeError('Incorrect engine import root')
sys.argv=[str(engine/config['entrypoint'])]+job['argv']
# Receipt-only amendment: finish already-admitted financial transactions before
# asyncio.run cancels all remaining tasks. Late responses never reach search.
import asyncio,hashlib
from priced_math_guard import ACTIVE_GUARD
from receipt_guard import run_and_drain
assert ACTIVE_GUARD is not None
receipt_source=ROOT/'runner/receipt_guard.py'
(Path(job['output'])/'RECEIPT_PROTOCOL.json').write_text(json.dumps({
    'amendment':'20261124-receipt-drain-v1',
    'receipt_guard_sha256':hashlib.sha256(receipt_source.read_bytes()).hexdigest(),
    'max_receipt_drain_seconds':ACTIVE_GUARD.drain_seconds,
    'late_responses_returned_to_optimizer':False,
},indent=2)+'\n')
_original_run=asyncio.run
def _receipt_run(coro,*args,**kwargs):
    return _original_run(run_and_drain(coro,ACTIVE_GUARD),*args,**kwargs)
asyncio.run=_receipt_run
try:
    runpy.run_path(sys.argv[0],run_name='__main__')
finally:
    asyncio.run=_original_run
