"""Entry-worker helper: exact confirm phase only uses queued admission."""
import importlib,pathlib,hashlib,json,datetime

def _source(module):
 path=pathlib.Path(module.__file__).resolve()
 return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def install_for_job(job,*,module_loader=importlib.import_module):
 phase=job.get('phase');name='model_io_confirmation' if phase=='confirm' else 'model_io'
 module=module_loader(name);core_name=getattr(module,'CORE_MODULE',name);core=module if core_name==name else module_loader(core_name)
 metadata={'phase':phase,'module':name,'module_source':_source(module),'core_module':core_name,'core_source':_source(core),'status':'selected','recorded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'policy':'Only exact confirm phase selects queued admission; all other phases retain legacy model_io.'}
 path=pathlib.Path(job['output'])/'instrumentation.json';path.parent.mkdir(parents=True,exist_ok=True)
 def save():
  temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(metadata,indent=2)+'\n');temporary.replace(path)
 save()
 module.install()
 metadata['status']='installed';save();return metadata
