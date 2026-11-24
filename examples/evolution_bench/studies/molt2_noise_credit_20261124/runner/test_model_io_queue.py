"""Fake SDK/temp ledgers only. Never imports or patches the active SDK."""
import asyncio,fcntl,json,concurrent.futures
from pathlib import Path
import pytest
from model_io_queue import BudgetQueueGuard,AdmissionDenied,ReservationWaitTimeout

KW={'model':'fake-model','messages':[{'role':'user','content':'not a scientific task'}],'extra_body':{'provider':{'order':['Wafer'],'allow_fallbacks':False}}}
def guard(tmp_path,spent=0.,reserved=None,**kwargs):
 g=BudgetQueueGuard(caller_lab=tmp_path/'caller',ledger_root=tmp_path/'shared',io_dir=tmp_path/'io',poll_interval=.001,**kwargs)
 g.ledger.write_text(json.dumps({'spent_usd':spent,'reported_usd':spent,'calls':{},'reserved':reserved or {}}));return g
def ledger(g):return json.loads(g.ledger.read_text())
def response(cost=.1,provider='Wafer'):return {'provider':provider,'usage':{'cost':cost},'choices':[{'message':{'content':'fake'}}]}

def test_two_concurrent_calls_wait_then_release_without_premature_stop(tmp_path):
 async def scenario():
  waiting=asyncio.Event();release=asyncio.Event();entered=asyncio.Event();wire=[]
  async def pause(n):waiting.set();await asyncio.sleep(0)
  g=guard(tmp_path,spent=38.75,async_sleep=pause)
  async def sdk(client,**kwargs):
   wire.append(len(wire))
   if len(wire)==1:entered.set();await release.wait()
   return response(.1)
  wrapped=g.wrap_async(sdk);first=asyncio.create_task(wrapped(None,**KW));await entered.wait();second=asyncio.create_task(wrapped(None,**KW))
  await waiting.wait();assert len(wire)==1 and not second.done() and not g.stopped()
  release.set();await asyncio.wait_for(asyncio.gather(first,second),1)
  assert len(wire)==2 and not g.stopped() and not ledger(g)['reserved']
  assert ledger(g)['spent_usd']==pytest.approx(38.95)
 asyncio.run(scenario())

def test_sync_wait_is_outside_flock_and_releases_healthy_reservation(tmp_path):
 tick=[0.];slept=[];g=None
 def pause(n):
  slept.append(n);tick[0]+=n
  with g.lockfile.open('a') as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  g.finish_sync('healthy',response(.1))
 g=guard(tmp_path,spent=38.75,reserved={'healthy':1.},sleep=pause,clock=lambda:tick[0]);wire=[]
 def sdk(client,**kwargs):wire.append(1);return response(.1)
 assert g.wrap_sync(sdk)(None,**KW)['provider']=='Wafer'
 assert slept and wire==[1] and not ledger(g)['reserved'] and not g.stopped()

def test_actual_cap_stops_both_roots_without_api_or_charge(tmp_path):
 g=guard(tmp_path,spent=39.5);wire=[]
 def sdk(client,**kwargs):wire.append(1)
 with pytest.raises(AdmissionDenied):g.wrap_sync(sdk)(None,**KW)
 assert not wire and ledger(g)['spent_usd']==39.5 and not ledger(g)['reserved']
 assert (g.caller/'GLOBAL_STOP.json').exists() and (g.shared/'GLOBAL_STOP.json').exists()

@pytest.mark.parametrize('which',['caller','shared'])
def test_existing_stop_mirrored_and_prevents_wire(tmp_path,which):
 g=guard(tmp_path);(getattr(g,which)/'GLOBAL_STOP.json').write_text('{}')
 with pytest.raises(AdmissionDenied):g.wrap_sync(lambda *a,**kw:pytest.fail('wire called'))(None,**KW)
 assert (g.caller/'GLOBAL_STOP.json').exists() and (g.shared/'GLOBAL_STOP.json').exists()

def test_async_queued_cancel_has_no_wire_request_charge_or_reservation(tmp_path):
 async def scenario():
  waiting=asyncio.Event()
  async def pause(n):waiting.set();await asyncio.sleep(10)
  g=guard(tmp_path,spent=38.75,reserved={'existing-wire':1.},async_sleep=pause);before=ledger(g)
  async def sdk(*a,**kw):pytest.fail('wire called')
  task=asyncio.create_task(g.wrap_async(sdk)(None,**KW));await waiting.wait();task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  assert ledger(g)==before and not list(g.root.iterdir()) and not g.stopped()
 asyncio.run(scenario())

def test_async_after_admission_cancel_finishes_conservative_charge(tmp_path):
 async def scenario():
  entered=asyncio.Event();g=guard(tmp_path)
  async def sdk(*a,**kw):entered.set();await asyncio.Event().wait()
  task=asyncio.create_task(g.wrap_async(sdk)(None,**KW));await entered.wait();task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  state=ledger(g);assert state['spent_usd']==1. and not state['reserved'] and len(state['calls'])==1
 asyncio.run(scenario())

def test_async_external_flock_contention_yields_instead_of_blocking_loop(tmp_path):
 async def scenario():
  g=guard(tmp_path);held=g.lockfile.open('a');fcntl.flock(held,fcntl.LOCK_EX);observed=[]
  async def pause(n):observed.append('yield');fcntl.flock(held,fcntl.LOCK_UN);await asyncio.sleep(0)
  g.async_sleep=pause
  async def sdk(*a,**kw):return response()
  await asyncio.wait_for(g.wrap_async(sdk)(None,**KW),1);held.close()
  assert observed and not ledger(g)['reserved']
 asyncio.run(scenario())

def test_wait_timeout_has_no_stop_wire_reservation_or_charge(tmp_path):
 clock=[0.]
 def pause(n):clock[0]+=n
 g=guard(tmp_path,spent=38.75,reserved={'healthy':1.},clock=lambda:clock[0],sleep=pause,wait_timeout=.01);before=ledger(g)
 with pytest.raises(ReservationWaitTimeout):g.wrap_sync(lambda *a,**kw:pytest.fail('wire'))(None,**KW)
 assert ledger(g)==before and not g.stopped() and not list(g.root.iterdir())

def test_provider_pin_and_redaction_unchanged(tmp_path):
 secret='FAKE_TEST_SECRET';g=guard(tmp_path,secrets=[secret]);bad={**KW,'extra_body':{'provider':{'order':['Other'],'allow_fallbacks':True}}}
 with pytest.raises(AdmissionDenied):g.wrap_sync(lambda *a,**kw:pytest.fail('wire'))(None,**bad)
 assert ledger(g)['spent_usd']==0
 # Independent temp campaign: request/response/error and ledger cannot leak secret.
 h=guard(tmp_path/'redact',secrets=[secret]);kwargs={**KW,'messages':[{'content':secret}],'headers':{'Authorization':secret}}
 def sdk(*a,**kw):return {**response(), 'echo':secret,'api_key':secret}
 h.wrap_sync(sdk)(None,**kwargs)
 assert all(secret not in p.read_text() for p in [h.ledger,*h.root.iterdir()])
 assert 'Authorization' not in next(h.root.glob('*.request.json')).read_text()

@pytest.mark.parametrize('cost',[None,1.])
def test_unknown_cost_conservative_charge_and_idempotent_finish(tmp_path,cost):
 g=guard(tmp_path);stamp=g.admit_sync(KW);g.finish_sync(stamp,response(cost));g.finish_sync(stamp,response(cost))
 assert ledger(g)['spent_usd']==1. and len(ledger(g)['calls'])==1 and not ledger(g)['reserved']

def test_provider_response_or_auth_error_stops_both_roots(tmp_path):
 g=guard(tmp_path/'provider');g.wrap_sync(lambda *a,**kw:response(provider='Other'))(None,**KW)
 assert (g.caller/'GLOBAL_STOP.json').exists() and (g.shared/'GLOBAL_STOP.json').exists()
 h=guard(tmp_path/'auth')
 class Unauthorized(Exception):status_code=401
 def sdk(*a,**kw):raise Unauthorized('test auth unavailable')
 with pytest.raises(Unauthorized):h.wrap_sync(sdk)(None,**KW)
 assert ledger(h)['spent_usd']==1. and not ledger(h)['reserved'] and h.stopped()

def test_multithread_race_cannot_overrun_hard40(tmp_path):
 g=guard(tmp_path);wire=[]
 def sdk(*a,**kw):wire.append(1);return response(1.)
 wrapped=g.wrap_sync(sdk)
 def one(_):
  try:wrapped(None,**KW);return True
  except AdmissionDenied:return False
 with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:outcomes=list(pool.map(one,range(80)))
 state=ledger(g);assert sum(outcomes)==len(wire)==40 and state['spent_usd']==40.
 assert not state['reserved'] and len(state['calls'])==40 and g.stopped()

def test_install_only_explicit_fake_classes_and_restore(tmp_path):
 g=guard(tmp_path)
 class AsyncFake:
  async def create(self,**kwargs):return response()
 class SyncFake:
  def create(self,**kwargs):return response()
 olda,olds=AsyncFake.create,SyncFake.create;restore=g.install(AsyncFake,SyncFake)
 asyncio.run(AsyncFake().create(**KW));SyncFake().create(**KW);restore()
 assert AsyncFake.create is olda and SyncFake.create is olds and len(ledger(g)['calls'])==2

def test_cancel_during_finalization_waits_for_book_before_propagating(tmp_path):
 async def scenario():
  waiting=asyncio.Event();holder=[]
  async def pause(n):waiting.set();await asyncio.sleep(0)
  g=guard(tmp_path,async_sleep=pause)
  async def sdk(client,**kwargs):
   lock=g.lockfile.open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);holder.append(lock);return response(.1)
  task=asyncio.create_task(g.wrap_async(sdk)(None,**KW));await waiting.wait();task.cancel();await asyncio.sleep(0)
  assert not task.done() and len(ledger(g)['reserved'])==1
  fcntl.flock(holder[0],fcntl.LOCK_UN);holder[0].close()
  with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,1)
  assert ledger(g)['spent_usd']==pytest.approx(.1) and not ledger(g)['reserved']
 asyncio.run(scenario())

def test_independent_calling_labs_share_atomic40_reservations(tmp_path):
 shared=tmp_path/'shared';first=guard(tmp_path/'seed');shared.mkdir();(shared/'spend-ledger.json').write_text(json.dumps({'spent_usd':0.,'reported_usd':0.,'reserved':{},'calls':{}}))
 guards=[BudgetQueueGuard(caller_lab=tmp_path/f'caller{i}',ledger_root=shared,io_dir=tmp_path/f'io{i}',poll_interval=.001,wait_timeout=3) for i in range(4)]
 def call(i):
  try:return guards[i%4].wrap_sync(lambda *a,**kw:response(1.))(None,**KW)
  except AdmissionDenied:return None
 with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:out=list(pool.map(call,range(100)))
 d=json.loads((shared/'spend-ledger.json').read_text())
 assert sum(x is not None for x in out)==40 and d['spent_usd']==40 and len(d['calls'])==40 and not d['reserved']
 assert all((g.caller/'GLOBAL_STOP.json').exists() for g in guards)
