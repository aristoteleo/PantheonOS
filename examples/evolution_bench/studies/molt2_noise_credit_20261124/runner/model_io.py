"""Full SDK request/response archive, fixed-provider verification, shared spend ledger."""
import os,json,time,uuid,pathlib,fcntl,threading

def install():
 from openai.resources.chat.completions import AsyncCompletions,Completions
 root=pathlib.Path(os.environ['MOLT_LLM_IO_DIR']);root.mkdir(parents=True,exist_ok=True)
 lab=pathlib.Path(os.environ.get('MOLT2_LEDGER_ROOT',os.environ['MOLT2_LAB']));ledger=lab/'spend-ledger.json';lockfile=lab/'spend-ledger.lock'
 secrets=[x for x in [os.environ.get('OPENROUTER_API_KEY'),os.environ.get('OPENAI_API_KEY')] if x]
 provider=os.environ.get('MOLT2_PROVIDER','Wafer');limit=float(os.environ.get('MOLT2_GLOBAL_USD','40'))
 def clean(x):
  if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k.lower() not in ['api_key','authorization','headers','extra_headers','access_token','secret']}
  if isinstance(x,list):return [clean(v) for v in x]
  if isinstance(x,str):
   for key in secrets:x=x.replace(key,'[REDACTED]')
  return x
 def update(fn):
  with lockfile.open('a') as f:
   fcntl.flock(f,fcntl.LOCK_EX)
   state=json.loads(ledger.read_text()) if ledger.exists() else {'spent_usd':0.,'reported_usd':0.,'calls':{},'reserved':{}}
   result=fn(state);tmp=ledger.with_suffix('.json.partial');tmp.write_text(json.dumps(state,indent=2));tmp.replace(ledger);return result
 def stop(reason):
  p=lab/'GLOBAL_STOP.json'
  if not p.exists():p.write_text(json.dumps({'reason':reason,'when':time.time()},indent=2))
 def begin(kwargs):
  declared=(kwargs.get('extra_body') or {}).get('provider') or {}
  if declared.get('order')!=[provider] or declared.get('allow_fallbacks') is not False:
   stop('Provider settings were not identical/pinned; no API call made');raise RuntimeError('All methods must pin the same provider with fallback disabled')
  stamp=f'{time.time_ns()}-{uuid.uuid4().hex[:8]}'
  def reserve(s):
   if s['spent_usd']+sum(s['reserved'].values())+1.>limit:
    stop('Global reported/conservative spend plus in-flight reservation reached cap');raise RuntimeError('Global model budget unavailable')
   s['reserved'][stamp]=1.
  update(reserve);(root/(stamp+'.request.json')).write_text(json.dumps(clean(kwargs),indent=2,default=str));return stamp
 def finish(stamp,response=None,error=None):
  data=response.model_dump() if hasattr(response,'model_dump') else (response if response is not None else None)
  usage=(data or {}).get('usage') or {} if isinstance(data,dict) else {}
  cost=usage.get('cost');actual_provider=(data or {}).get('provider') if isinstance(data,dict) else None
  def book(s):
   s['reserved'].pop(stamp,None)
   charge=float(cost) if cost is not None else 1.
   s['spent_usd']+=charge
   if cost is not None:s['reported_usd']+=charge
   s['calls'][stamp]={'run_id':os.environ.get('MOLT2_RUN_ID'),'cost_usd':cost,'conservative_charge_usd':charge,'provider':actual_provider,'error':clean(str(error))[:2000] if error else None}
  update(book)
  if response is not None:(root/(stamp+'.response.json')).write_text(json.dumps(clean(data),indent=2,default=str))
  else:(root/(stamp+'.error.json')).write_text(json.dumps({'type':type(error).__name__,'error':clean(str(error))[:3000]},indent=2))
  if actual_provider and str(actual_provider).lower()!=provider.lower():stop('Actual provider differed from pre-registered provider')
  if error:
   status=getattr(error,'status_code',None);text=str(error).lower()
   if status in [401,402,403,404] or any(x in text for x in ['no endpoints found','provider not','provider unavailable']):stop('Model/provider/auth availability error; pause all matched arms, never provider-switch by arm')
 async_orig=AsyncCompletions.create;sync_orig=Completions.create
 async def async_recorded(self,*args,**kwargs):
  stamp=begin(kwargs)
  try:resp=await async_orig(self,*args,**kwargs)
  except BaseException as e:finish(stamp,error=e);raise
  finish(stamp,response=resp);return resp
 def sync_recorded(self,*args,**kwargs):
  stamp=begin(kwargs)
  try:resp=sync_orig(self,*args,**kwargs)
  except BaseException as e:finish(stamp,error=e);raise
  finish(stamp,response=resp);return resp
 AsyncCompletions.create=async_recorded;Completions.create=sync_recorded
