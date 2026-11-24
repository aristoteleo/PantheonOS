"""Start one native optimizer with audit hooks; no method changes or secret files copied."""
from pathlib import Path
import sys,json,os,runpy
ROOT=Path(__file__).resolve().parent.parent
job=json.loads(Path(sys.argv[1]).read_text());config=json.loads((ROOT/'campaign.json').read_text())
engine=Path(config['engine_root']);sys.path.insert(0,str(engine))
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
runpy.run_path(sys.argv[0],run_name='__main__')
