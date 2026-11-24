import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'runner'))
from receipt_guard import ReceiptGuard,run_and_drain
KW={'model':'x','extra_body':{'provider':{'order':['DeepInfra'],'allow_fallbacks':False}}}
def guard(tmp):
 return ReceiptGuard(caller_lab=tmp/'lab',ledger_root=tmp/'ledger',io_dir=tmp/'io',provider='DeepInfra',limit=20,run_id='test')
def response():return {'provider':'DeepInfra','usage':{'cost':.004},'choices':[{'message':{'content':'never use late output'}}]}
def charges(g):return json.loads(g.ledger.read_text())
def test_normal_response(tmp_path):
 async def main():
  g=guard(tmp_path)
  async def wire(*a,**k):return response()
  out=await g.wrap_async(wire)(None,**KW);assert out==response();assert charges(g)['spent_usd']==.004
 asyncio.run(main())
def test_cancelled_caller_gets_receipt_not_result(tmp_path):
 async def main():
  g=guard(tmp_path);entered=asyncio.Event();release=asyncio.Event();used=[]
  async def wire(*a,**k):entered.set();await release.wait();return response()
  async def caller():used.append(await g.wrap_async(wire)(None,**KW))
  task=asyncio.create_task(caller());await entered.wait();task.cancel();await asyncio.sleep(0);task.cancel();release.set()
  result=await asyncio.gather(task,return_exceptions=True);assert isinstance(result[0],asyncio.CancelledError);assert not used;assert charges(g)['spent_usd']==.004
 asyncio.run(main())
def test_shutdown_drains_before_loop_cancellation(tmp_path):
 g=guard(tmp_path);used=[]
 async def main():
  entered=asyncio.Event()
  async def wire(*a,**k):entered.set();await asyncio.sleep(.01);return response()
  async def caller():used.append(await g.wrap_async(wire)(None,**KW))
  asyncio.create_task(caller());await entered.wait();return 42
 assert asyncio.run(run_and_drain(main(),g))==42
 assert not used and charges(g)['spent_usd']==.004 and not charges(g)['reserved']
def test_timeout_stays_conservative(tmp_path):
 async def main():
  g=guard(tmp_path);g.drain_seconds=.01;entered=asyncio.Event()
  async def wire(*a,**k):entered.set();await asyncio.Event().wait()
  task=asyncio.create_task(g.wrap_async(wire)(None,**KW));await entered.wait();await g.drain();await asyncio.gather(task,return_exceptions=True)
  assert charges(g)['spent_usd']==1 and not charges(g)['reserved']
 asyncio.run(main())
def test_wire_error_keeps_unknown_charge(tmp_path):
 async def main():
  g=guard(tmp_path)
  async def wire(*a,**k):raise RuntimeError('network failed')
  try:await g.wrap_async(wire)(None,**KW)
  except RuntimeError:pass
  else:raise AssertionError('Lost error')
  assert charges(g)['spent_usd']==1
 asyncio.run(main())
def test_no_new_admissions_after_drain(tmp_path):
 async def main():
  g=guard(tmp_path);await g.drain();wire=[]
  async def call(*a,**k):wire.append(1)
  try:await g.wrap_async(call)(None,**KW)
  except Exception as e:assert 'Search ended' in str(e)
  else:raise AssertionError('New call admitted')
  assert not wire and not g.ledger.exists()
 asyncio.run(main())
def test_cancelled_caller_has_bounded_receipt_wait(tmp_path):
 async def main():
  g=guard(tmp_path);g.drain_seconds=.01;entered=asyncio.Event()
  async def wire(*a,**k):entered.set();await asyncio.Event().wait()
  task=asyncio.create_task(g.wrap_async(wire)(None,**KW));await entered.wait();task.cancel();result=await asyncio.gather(task,return_exceptions=True)
  assert isinstance(result[0],asyncio.CancelledError) and charges(g)['spent_usd']==1
 asyncio.run(main())
def test_main_error_still_drains(tmp_path):
 g=guard(tmp_path)
 async def main():
  entered=asyncio.Event()
  async def wire(*a,**k):entered.set();await asyncio.sleep(.01);return response()
  asyncio.create_task(g.wrap_async(wire)(None,**KW));await entered.wait();raise ValueError('main stopped')
 try:asyncio.run(run_and_drain(main(),g))
 except ValueError:pass
 else:raise AssertionError('Lost main error')
 assert charges(g)['spent_usd']==.004
