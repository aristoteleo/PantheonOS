"""Confirmation-only guard: bounded async/sync reservation queue, explicit caller paths.

No imports or patches of the real SDK; install() requires explicitly supplied classes.
No pricing/model/evaluator changes. Every admitted request reserves exactly1USD.
Unknown/failed admitted requests conservatively charge1USD, as the frozen guard.
"""
import asyncio,fcntl,json,pathlib,time,uuid

class AdmissionDenied(RuntimeError):pass
class ReservationWaitTimeout(RuntimeError):pass

class BudgetQueueGuard:
 def __init__(self,*,caller_lab,ledger_root,io_dir,provider='Wafer',limit=40.,secrets=(),run_id=None,wait_timeout=300.,poll_interval=.25,clock=time.monotonic,sleep=time.sleep,async_sleep=asyncio.sleep):
  self.caller=pathlib.Path(caller_lab);self.shared=pathlib.Path(ledger_root);self.root=pathlib.Path(io_dir)
  self.ledger=self.shared/'spend-ledger.json';self.lockfile=self.shared/'spend-ledger.lock'
  self.provider=provider;self.limit=float(limit);self.secrets=tuple(x for x in secrets if x);self.run_id=run_id
  self.wait_timeout=max(0.,float(wait_timeout));self.poll=max(.001,float(poll_interval));self.clock=clock;self.sleep=sleep;self.async_sleep=async_sleep
  for p in [self.caller,self.shared,self.root]:p.mkdir(parents=True,exist_ok=True)
 def clean(self,x):
  if isinstance(x,dict):return {k:self.clean(v) for k,v in x.items() if k.lower() not in ['api_key','authorization','headers','extra_headers','access_token','secret']}
  if isinstance(x,list):return [self.clean(v) for v in x]
  if isinstance(x,str):
   for secret in self.secrets:x=x.replace(secret,'[REDACTED]')
  return x
 def stop(self,reason):
  # Both controller-local and shared flags are authoritative. First reason wins.
  for directory in dict.fromkeys([self.caller,self.shared]):
   p=directory/'GLOBAL_STOP.json'
   try:
    with p.open('x') as f:json.dump({'reason':reason,'when':time.time()},f,indent=2)
   except FileExistsError:pass
 def stopped(self):return any((p/'GLOBAL_STOP.json').exists() for p in [self.caller,self.shared])
 def validate(self,kwargs):
  declared=(kwargs.get('extra_body') or {}).get('provider') or {}
  if declared.get('order')!=[self.provider] or declared.get('allow_fallbacks') is not False:
   self.stop('Provider settings were not identical/pinned; no API call made')
   raise AdmissionDenied('All methods must pin the same provider with fallback disabled')
 def _transaction(self,fn):
  # Never sleep/block on flock in the event loop. All backoff is caller-owned,
  # after this fd/lock has been released. Every admission/book mutation is atomic.
  with self.lockfile.open('a') as f:
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return False,None
   state=json.loads(self.ledger.read_text()) if self.ledger.exists() else {'spent_usd':0.,'reported_usd':0.,'calls':{},'reserved':{}}
   value,changed=fn(state)
   if changed:
    tmp=self.ledger.with_suffix('.json.partial');tmp.write_text(json.dumps(state,indent=2));tmp.replace(self.ledger)
   return True,value
 def _try_admit(self,stamp):
  if self.stopped():return 'persistent_stop'
  def reserve(state):
   if self.stopped():return 'persistent_stop',False
   if state['spent_usd']+1.>self.limit:return 'insufficient_spend_headroom',False
   if state['spent_usd']+sum(state['reserved'].values())+1.>self.limit:return 'temporary_reservations',False
   state['reserved'][stamp]=1.;return 'admitted',True
  locked,status=self._transaction(reserve)
  return status if locked else 'temporary_ledger_lock'
 def _deny_if_permanent(self,status):
  if status=='persistent_stop':
   self.stop('Persistent stop flag already present; no API call made')
   raise AdmissionDenied('Campaign stop flag present')
  if status=='insufficient_spend_headroom':
   self.stop('Global reported/conservative spend leaves less than1USD headroom')
   raise AdmissionDenied('Global model budget unavailable')
 async def admit_async(self,kwargs):
  self.validate(kwargs);stamp=f'{time.time_ns()}-{uuid.uuid4().hex[:8]}';end=self.clock()+self.wait_timeout
  while True:
   status=self._try_admit(stamp)
   if status=='admitted':return stamp
   self._deny_if_permanent(status)
   remaining=end-self.clock()
   if remaining<=0:raise ReservationWaitTimeout('Temporary reservation/ledger wait timed out; no API call/reservation/charge')
   await self.async_sleep(min(self.poll,remaining))
 def admit_sync(self,kwargs):
  self.validate(kwargs);stamp=f'{time.time_ns()}-{uuid.uuid4().hex[:8]}';end=self.clock()+self.wait_timeout
  while True:
   status=self._try_admit(stamp)
   if status=='admitted':return stamp
   self._deny_if_permanent(status)
   remaining=end-self.clock()
   if remaining<=0:raise ReservationWaitTimeout('Temporary reservation/ledger wait timed out; no API call/reservation/charge')
   self.sleep(min(self.poll,remaining))
 def _request(self,stamp,kwargs):
  (self.root/(stamp+'.request.json')).write_text(json.dumps(self.clean(kwargs),indent=2,default=str))
 def _result_data(self,response):return response.model_dump() if hasattr(response,'model_dump') else response
 def _try_book(self,stamp,response=None,error=None):
  data=self._result_data(response);usage=((data or {}).get('usage') or {}) if isinstance(data,dict) else {}
  cost=usage.get('cost');actual=(data or {}).get('provider') if isinstance(data,dict) else None
  def book(state):
   # Idempotent finalization avoids duplicate charges if a completion is delivered
   # twice; it does not erase a deliberately unknown/failed admitted request.
   if stamp in state['calls']:return False,False
   if stamp not in state['reserved']:raise RuntimeError('No matching admitted reservation to finalize')
   state['reserved'].pop(stamp);charge=float(cost) if cost is not None else 1.
   state['spent_usd']+=charge
   if cost is not None:state['reported_usd']+=charge
   state['calls'][stamp]={'run_id':self.run_id,'cost_usd':cost,'conservative_charge_usd':charge,'provider':actual,'error':self.clean(str(error))[:2000] if error else None}
   return True,True
  locked,booked=self._transaction(book)
  return locked,booked,data,actual
 def _archive_result(self,stamp,data,actual,error):
  if data is not None:(self.root/(stamp+'.response.json')).write_text(json.dumps(self.clean(data),indent=2,default=str))
  else:(self.root/(stamp+'.error.json')).write_text(json.dumps({'type':type(error).__name__,'error':self.clean(str(error))[:3000]},indent=2))
  if actual and str(actual).lower()!=self.provider.lower():self.stop('Actual provider differed from pre-registered provider')
  if error:
   status=getattr(error,'status_code',None);text=str(error).lower()
   if status in [401,402,403,404] or any(x in text for x in ['no endpoints found','provider not','provider unavailable']):self.stop('Model/provider/auth availability error; pause all matched arms, never provider-switch by arm')
 async def finish_async(self,stamp,response=None,error=None):
  # Financial finalization must not be abandoned after wire admission. Retain the
  # conservative reservation if storage is unavailable rather than invent funds.
  while True:
   locked,booked,data,actual=self._try_book(stamp,response,error)
   if locked:break
   await self.async_sleep(self.poll)
  if booked:self._archive_result(stamp,data,actual,error)
 def finish_sync(self,stamp,response=None,error=None):
  while True:
   locked,booked,data,actual=self._try_book(stamp,response,error)
   if locked:break
   self.sleep(self.poll)
  if booked:self._archive_result(stamp,data,actual,error)
 async def _finish_protected(self,stamp,response=None,error=None):
  # Keep a strong task reference and delay cancellation propagation until the
  # admitted financial transaction completes; shield alone returns early on cancel.
  task=asyncio.create_task(self.finish_async(stamp,response=response,error=error));cancelled=False
  while True:
   try:
    await asyncio.shield(task);break
   except asyncio.CancelledError:
    cancelled=True
    if task.done():task.result();break
  if cancelled:raise asyncio.CancelledError()
 def wrap_async(self,original):
  async def recorded(client,*args,**kwargs):
   # Cancellation while queued has no reservation and no wire/charge. Once
   # admitted, the existing conservative finish semantics apply even on cancel.
   stamp=await self.admit_async(kwargs)
   if getattr(client,'_client',None) is not None:client._client.max_retries=0
   try:
    self._request(stamp,kwargs);response=await original(client,*args,**kwargs)
   except BaseException as error:
    await self._finish_protected(stamp,error=error);raise
   await self._finish_protected(stamp,response=response);return response
  return recorded
 def wrap_sync(self,original):
  def recorded(client,*args,**kwargs):
   stamp=self.admit_sync(kwargs)
   if getattr(client,'_client',None) is not None:client._client.max_retries=0
   try:self._request(stamp,kwargs);response=original(client,*args,**kwargs)
   except BaseException as error:self.finish_sync(stamp,error=error);raise
   self.finish_sync(stamp,response=response);return response
  return recorded
 def install(self,async_cls,sync_cls):
  # Explicit fake/SDK class arguments only; NEVER called at module import.
  async_orig=async_cls.create;sync_orig=sync_cls.create
  async_cls.create=self.wrap_async(async_orig);sync_cls.create=self.wrap_sync(sync_orig)
  def restore():async_cls.create=async_orig;sync_cls.create=sync_orig
  return restore
