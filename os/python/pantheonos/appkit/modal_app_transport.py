"""Ordinary App stdio over an already owned Modal sandbox.

The sandbox owner separately proves container termination. This transport never
creates/reconnects containers or replays requests: losing a response leaves the
effect unknown. AppContext callbacks need an explicit handler supplied by the
deployment owner; there is no implicit file, model or credential authority.
"""
import asyncio
import json

from .modal_sandbox import _join


class AppTransportLost(ConnectionError):
    """The caller must reconcile accepted operations instead of retrying them."""


class AppMethodError(RuntimeError):
    """A correlated error response from the ordinary App host."""


class ModalAppTransport:
    MAX_FRAME = 16 * 1024 * 1024
    MAX_PENDING = 64
    LOG_TAIL = 64 * 1024

    def __init__(self, sandbox, *, callback=None):
        self.backend_id = sandbox.object_id
        self.sandbox, self.callback = sandbox, callback
        self.methods = set()
        self.stderr_tail = b''
        self._ready = asyncio.get_running_loop().create_future()
        self._ready.add_done_callback(self._consume)
        self._pending = {}
        self._calls, self._callbacks = set(), set()
        self._write_lock = asyncio.Lock()
        self._sequence = 0
        self._failure = None
        self._closing = None
        self._admitting = True
        self._stdout = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._logs())

    @staticmethod
    def _consume(task):
        if not task.cancelled():
            task.exception()

    def _fail(self, message):
        if self._failure is None:
            self._failure = AppTransportLost(message)
        self._admitting = False
        for future in (self._ready, *self._pending.values()):
            if not future.done():
                future.set_exception(self._failure)

    @classmethod
    def _frame(cls, message):
        raw = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        if len(raw) > cls.MAX_FRAME:
            raise ValueError('App message exceeds the stdio frame limit')
        return raw

    async def _write(self, raw):
        async with self._write_lock:
            if self._failure:
                raise self._failure
            try:
                # Modal's stdin buffer is 2 MiB. A lock spans the whole frame,
                # so concurrent requests/callbacks cannot interleave chunks.
                for offset in range(0, len(raw), 64 * 1024):
                    self.sandbox.stdin.write(raw[offset:offset + 64 * 1024])
                    await self.sandbox.stdin.drain.aio()
            except Exception:
                self._fail('App stdin delivery failed; accepted effects may be unknown')
                raise self._failure from None

    async def _respond(self, message):
        mid = message['id']
        # The ordinary host uses n-prefixed IDs for fire-and-forget logs.
        # Replying to those would produce an unsolicited response in the child.
        if mid.startswith('n'):
            if message['method'] == 'ctx.log':
                raw = str((message.get('params') or {}).get('message', '')).encode()
                self.stderr_tail = (self.stderr_tail + raw[-self.LOG_TAIL:])[-self.LOG_TAIL:]
            elif self.callback is not None:
                await self.callback(message['method'], message.get('params') or {})
            return
        try:
            if self.callback is None:
                raise PermissionError('No AppContext callback binding was provided')
            result = await self.callback(message['method'], message.get('params') or {})
            raw = self._frame({'jsonrpc': '2.0', 'id': mid, 'result': result})
        except Exception:
            # Do not export credentials or controller exceptions to the App.
            raw = self._frame({'jsonrpc': '2.0', 'id': mid, 'error': {
                'code': -32000, 'message': 'AppContext callback unavailable or failed'}})
        await self._write(raw)

    def _message(self, msg):
        if self._failure:
            raise self._failure
        if not isinstance(msg, dict):
            raise ValueError('Invalid App protocol message')
        if 'ready' in msg:
            if self._ready.done():
                raise ValueError('Duplicate App readiness')
            names = msg.get('methods')
            if (msg.get('ready') is not True or msg.get('api') != 1 or not isinstance(names, list)
                    or not all(isinstance(n, str) and n for n in names)):
                raise ValueError('App startup failed or protocol is unsupported')
            self.methods = set(names)
            self._ready.set_result(None)
        elif 'method' in msg:
            if (not isinstance(msg.get('id'), str) or not isinstance(msg['method'], str)
                    or not isinstance(msg.get('params', {}), dict)):
                raise ValueError('Invalid AppContext callback')
            if len(self._callbacks) >= self.MAX_PENDING:
                raise ValueError('Too many pending AppContext callbacks')
            task = asyncio.create_task(self._respond(msg))
            self._callbacks.add(task)
            task.add_done_callback(self._callbacks.discard)
            task.add_done_callback(self._consume)
        else:
            mid = msg.get('id')
            if not isinstance(mid, str):
                raise ValueError('Invalid App response identity')
            future = self._pending.get(mid)
            if future is None or future.done() or ('result' in msg) == ('error' in msg):
                raise ValueError('Unexpected App response')
            if 'error' in msg:
                future.set_exception(AppMethodError(str(msg['error'])))
            else:
                future.set_result(msg['result'])

    async def _read(self):
        buffer = bytearray()
        try:
            async for chunk in self.sandbox.stdout:
                data = chunk.encode() if isinstance(chunk, str) else chunk
                # Slice before buffering; an SDK log batch can contain many
                # valid frames without granting an unbounded partial frame.
                for offset in range(0, len(data), 64 * 1024):
                    buffer.extend(data[offset:offset + 64 * 1024])
                    while b'\n' in buffer:
                        line, _, rest = buffer.partition(b'\n')
                        if len(line) + 1 > self.MAX_FRAME:
                            raise ValueError('App frame exceeds limit')
                        buffer = bytearray(rest)
                        self._message(json.loads(line))
                    if len(buffer) >= self.MAX_FRAME:
                        raise ValueError('App frame exceeds limit')
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail('App output transport failed; accepted effects may be unknown')
        finally:
            self._fail('App output ended; accepted effects may be unknown')

    async def _logs(self):
        try:
            async for chunk in self.sandbox.stderr:
                raw = chunk.encode() if isinstance(chunk, str) else chunk
                self.stderr_tail = (self.stderr_tail + raw[-self.LOG_TAIL:])[-self.LOG_TAIL:]
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail('App diagnostic stream failed')

    async def ready(self, timeout=60):
        # Timeout stops observation only. The caller still owns the container
        # and must close it; this class cannot prove it stopped.
        await asyncio.wait_for(asyncio.shield(self._ready), timeout)

    async def _invoke(self, mid, raw):
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(self._consume)
        self._pending[mid] = future
        try:
            await self._write(raw)
            return await future
        finally:
            self._pending.pop(mid, None)

    async def invoke(self, method, args):
        if not self._admitting or self._failure:
            raise self._failure or RuntimeError('App transport is closing')
        if not self._ready.done():
            raise RuntimeError('Await App readiness before invoking methods')
        self._ready.result()
        if method not in self.methods or not isinstance(args, dict):
            raise ValueError('Supply a registered App method and object arguments')
        if len(self._calls) >= self.MAX_PENDING:
            raise RuntimeError('Too many pending App invocations')
        self._sequence += 1
        mid = 'p' + str(self._sequence)
        raw = self._frame({'jsonrpc': '2.0', 'id': mid, 'method': 'invoke',
                          'params': {'method': method, 'args': args}})
        task = asyncio.create_task(self._invoke(mid, raw))
        self._calls.add(task)
        task.add_done_callback(self._calls.discard)
        task.add_done_callback(self._consume)
        return await asyncio.shield(task)

    async def shutdown(self):
        """Request ordinary App cleanup; this is NOT a stop receipt."""
        self._admitting = False
        await _join(asyncio.create_task(self._write(self._frame({'method': 'shutdown'}))))

    async def disconnect(self):
        """Join local IO after the deployment owner confirms remote termination.

        Pending callback handlers are joined, not abandoned; the binding owner
        must interrupt any separately owned callback resources first if needed.
        """
        async def close():
            self._fail('App transport disconnected; accepted effects may be unknown')
            self._stdout.cancel()
            self._stderr.cancel()
            await asyncio.gather(self._stdout, self._stderr, return_exceptions=True)
            await asyncio.gather(*self._calls, *self._callbacks, return_exceptions=True)
        self._admitting = False
        if self._closing is None:
            self._closing = asyncio.create_task(close())
        await _join(self._closing)
