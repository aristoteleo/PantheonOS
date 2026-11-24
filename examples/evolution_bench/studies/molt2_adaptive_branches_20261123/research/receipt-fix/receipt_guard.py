"""Financial receipt completion; late responses never reach search callers."""
import asyncio,time
from model_io_queue import BudgetQueueGuard,AdmissionDenied

class ReceiptGuard(BudgetQueueGuard):
 def __init__(self,**kwargs):
  super().__init__(**kwargs);self.receipts=set();self.draining=False;self.drain_seconds=300.
 def _try_admit(self,stamp):
  if self.draining:raise AdmissionDenied("Search ended; no new paid requests")
  return super()._try_admit(stamp)
 async def admit_async(self,kwargs):
  if self.draining:raise AdmissionDenied('Search ended; no new paid requests')
  return await super().admit_async(kwargs)
 def wrap_async(self,original):
  async def recorded(client,*args,**kwargs):
   stamp=await self.admit_async(kwargs)
   if getattr(client,'_client',None) is not None:client._client.max_retries=0
   async def transaction():
    try:
     self._request(stamp,kwargs);response=await original(client,*args,**kwargs)
    except BaseException as error:
     await self._finish_protected(stamp,error=error);raise
    await self._finish_protected(stamp,response=response);return response
   task=asyncio.create_task(transaction());self.receipts.add(task);task.add_done_callback(self.receipts.discard)
   cancelled=False;cancel_deadline=None
   while True:
    try:
     if cancelled:
      remaining=max(0.,cancel_deadline-time.monotonic())
      try:response=await asyncio.wait_for(asyncio.shield(task),remaining)
      except asyncio.TimeoutError:
       task.cancel();await asyncio.gather(task,return_exceptions=True);raise asyncio.CancelledError()
     else:response=await asyncio.shield(task)
     break
    except asyncio.CancelledError:
     cancelled=True
     if cancel_deadline is None:cancel_deadline=time.monotonic()+self.drain_seconds
     if task.done():response=task.result();break
   if cancelled or self.draining:raise asyncio.CancelledError()
   return response
  return recorded
 async def drain(self):
  self.draining=True
  pending={t for t in self.receipts if not t.done()}
  if not pending:return
  _,unfinished=await asyncio.wait(pending,timeout=self.drain_seconds)
  for task in unfinished:task.cancel()
  await asyncio.gather(*pending,return_exceptions=True)

async def run_and_drain(coro,guard):
 try:return await coro
 finally:await guard.drain()
