"""Bounded reusable Fleet peers over private framed pipes, without a local proxy.

Only the QUIC peer is reused. Every lease sends a fresh instance-bound grant and
opens a separate App stream. No request is retried here, including after EOF.
"""
import asyncio
import json
import re
import struct
import time

import httpcore

from .direct import DirectUnavailable


FRAME = 65536
WINDOW = 4 * FRAME


class SessionStream(httpcore.AsyncNetworkStream):
    def __init__(self, session, closed):
        self.session, self.closed = session, closed
        self.buffer = bytearray()
        self.credit = WINDOW
        self.max_buffer = 0
        self.ready, self.changed, self.done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.error = None
        self.eof = self.closing = False
        self.close_task = None

    async def read(self, max_bytes, timeout=None):
        try:
            async with asyncio.timeout(timeout):
                while not self.buffer:
                    if self.session.failed:
                        raise httpcore.ReadError('Direct session closed')
                    if self.eof or self.closing:
                        return b''
                    self.changed.clear()
                    await self.changed.wait()
                size = min(max_bytes, len(self.buffer))
                data = bytes(self.buffer[:size])
                del self.buffer[:size]
                await self.session.send(b'W', struct.pack('!I', size))
                return data
        except TimeoutError as error:
            raise httpcore.ReadTimeout('Direct stream timed out') from error
        except (OSError, RuntimeError) as error:
            raise httpcore.ReadError('Direct stream closed') from error

    async def write(self, buffer, timeout=None):
        try:
            async with asyncio.timeout(timeout):
                offset = 0
                while offset < len(buffer):
                    if self.closing or self.eof or self.session.failed:
                        raise httpcore.WriteError('Direct stream closed')
                    if self.credit == 0:
                        self.changed.clear()
                        await self.changed.wait()
                        continue
                    size = min(FRAME, self.credit, len(buffer) - offset)
                    self.credit -= size
                    await self.session.send(b'D', buffer[offset:offset + size])
                    offset += size
        except TimeoutError as error:
            raise httpcore.WriteTimeout('Direct stream timed out') from error
        except (OSError, RuntimeError) as error:
            raise httpcore.WriteError('Direct stream closed') from error

    async def start_tls(self, *args, **kwargs):
        raise httpcore.ConnectError('Direct HTTP must use its authenticated QUIC channel')

    def get_extra_info(self, info):
        if info == 'is_readable':
            return self.eof or self.session.failed or bool(self.buffer)
        return None

    async def _close(self):
        self.closing = True
        self.buffer.clear()
        self.changed.set()
        try:
            if self.session.current is self and not self.session.failed:
                try:
                    async with asyncio.timeout(2):
                        await self.session.send(b'X')
                        await self.done.wait()
                except (TimeoutError, OSError, RuntimeError):
                    await self.session.aclose()
        finally:
            await self.session.pool.release(self.session)
            self.closed(self)

    async def aclose(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)


class PeerSession:
    def __init__(self, pool, node):
        self.pool, self.node = pool, node
        self.process = self.reader_task = self.close_task = None
        self.terminate_task = None
        self.peer = None
        self.current = None
        self.leased = True
        self.failed = False
        self.idle_since = None
        self.write_lock = asyncio.Lock()

    async def start(self):
        try:
            self.process = await asyncio.create_subprocess_exec(self.pool.executable, 'app-session',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=FRAME)
            async with asyncio.timeout(5):
                hello = json.loads(await self.process.stdout.readline())
            peer = hello.get('peer_id', '') if isinstance(hello, dict) else ''
            if (not isinstance(hello, dict) or hello.get('protocol') != 2 or not isinstance(peer, str)
                    or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,128}', peer)):
                raise ValueError('Invalid private protocol')
            self.peer = peer
            self.reader_task = asyncio.create_task(self._read())
        except BaseException as error:
            await self.aclose()
            if isinstance(error, (OSError, ValueError, TimeoutError)):
                raise DirectUnavailable('Fleet reusable direct helper is unavailable; update the runtime') from error
            raise

    async def send(self, kind, body=b''):
        if self.failed or not self.process or self.process.returncode is not None:
            raise OSError('Direct peer closed')
        if len(body) > FRAME:
            raise ValueError('Direct frame exceeds its limit')
        async with self.write_lock:
            self.process.stdin.write(kind + struct.pack('!I', len(body)) + body)
            await self.process.stdin.drain()

    async def _read(self):
        try:
            while True:
                header = await self.process.stdout.readexactly(5)
                kind, size = header[:1], struct.unpack('!I', header[1:])[0]
                if size > FRAME:
                    raise ValueError('Direct frame exceeds its limit')
                body = await self.process.stdout.readexactly(size)
                stream = self.current
                if stream is None:
                    raise ValueError('Direct stream boundary missing')
                if kind == b'R' and not body and not stream.ready.is_set():
                    stream.ready.set()
                elif kind == b'F' and body in (b'grant_rejected', b'unavailable') and not stream.ready.is_set():
                    stream.error = (ValueError('Direct App authority was rejected; refresh the selected instance')
                        if body == b'grant_rejected' else DirectUnavailable('Direct peer connection is unavailable'))
                    stream.ready.set()
                elif kind == b'D' and body and stream.ready.is_set() and stream.error is None:
                    if not stream.closing:
                        if len(stream.buffer) + len(body) > WINDOW:
                            raise ValueError('Direct response credit exceeded')
                        stream.buffer.extend(body)
                        stream.max_buffer = max(stream.max_buffer, len(stream.buffer))
                    stream.changed.set()
                elif kind == b'W' and len(body) == 4:
                    credit = struct.unpack('!I', body)[0]
                    if not 0 < credit <= WINDOW - stream.credit:
                        raise ValueError('Direct request credit exceeded')
                    stream.credit += credit
                    stream.changed.set()
                elif kind == b'E' and not body:
                    stream.eof = True
                    stream.changed.set()
                elif kind == b'C' and not body and stream.closing:
                    self.current = None
                    stream.done.set()
                else:
                    raise ValueError('Invalid direct session frame')
        except (EOFError, OSError, ValueError, RuntimeError):
            pass
        finally:
            self.failed = True
            if self.current:
                self.current.error = DirectUnavailable('Direct peer closed during setup')
                self.current.ready.set()
                self.current.changed.set()
                self.current.done.set()
            await self.terminate()

    async def open(self, issue, closed):
        stream = SessionStream(self, closed)
        self.current = stream
        try:
            async with asyncio.timeout(18):
                grant = await issue(self.peer)
                grant = {key: grant[key] for key in ('peer_id', 'addresses', 'access_token', 'expires', 'transport')}
                if grant['transport'] != 'fleet_direct' or grant['expires'] <= time.time():
                    raise ValueError('Invalid direct workload grant')
                await self.send(b'G', json.dumps(grant).encode())
                await stream.ready.wait()
                if stream.error:
                    raise stream.error
            return stream
        except BaseException as error:
            # If G has not been sent, X would be outside a stream boundary.
            # Retire this peer instead of risking reuse after a partial write.
            await self.aclose()
            await stream.aclose()
            if isinstance(error, (TimeoutError, OSError)):
                raise DirectUnavailable('Direct connection setup timed out or failed') from error
            raise

    async def _terminate(self):
        if not self.process:
            return
        self.process.stdin.close()
        if self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
        try:
            async with asyncio.timeout(1):
                await self.process.wait()
        except TimeoutError:
            if self.process.returncode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            # _read has stopped. Drain the bounded pipe before reaping an
            # unread response, otherwise asyncio's process.wait can hang.
            while await self.process.stdout.read(FRAME):
                pass
            await self.process.wait()

    async def _close(self):
        self.failed = True
        if self.reader_task:
            if not self.reader_task.cancelling():
                self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
        await self.terminate()

    async def terminate(self):
        if self.terminate_task is None:
            self.terminate_task = asyncio.create_task(self._terminate())
        await asyncio.shield(self.terminate_task)

    async def aclose(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)


class PeerPool:
    """At most sixteen peers per client, node-bound and reclaimed after idle TTL.

    retire() closes idle peers but lets existing calls finish. This is also the
    cache-eviction contract: replacing credentials must not interrupt old calls.
    Eight admitted invocations each retain room for a direct cancel connection
    if the normal scoped control gateway is unavailable.
    """
    def __init__(self, executable, *, capacity=16, idle_ttl=20):
        self.executable, self.capacity, self.idle_ttl = executable, capacity, idle_ttl
        self.sessions = set()
        self.condition = asyncio.Condition()
        self.retired = False
        self.reaper = None

    async def acquire(self, node):
        async with self.condition:
            while True:
                if self.retired:
                    raise DirectUnavailable('Direct client has been retired')
                idle = [s for s in self.sessions if not s.leased]
                session = next((s for s in idle if s.node == node and not s.failed), None)
                if session is not None:
                    session.leased = True
                    return session
                # Reclaim a dead peer or an idle different-node connection
                # before admitting another peer. Active calls are never evicted.
                victim = next((s for s in idle if s.failed), None)
                if victim is None and len(self.sessions) >= self.capacity and idle:
                    victim = min(idle, key=lambda s: s.idle_since)
                if victim:
                    await victim.aclose()
                    self.sessions.discard(victim)
                if len(self.sessions) < self.capacity:
                    session = PeerSession(self, node)
                    self.sessions.add(session)
                    if self.reaper is None or self.reaper.done():
                        self.reaper = asyncio.create_task(self._reap())
                    break
                await self.condition.wait()
        try:
            await session.start()
            return session
        except BaseException:
            await self.release(session)
            raise

    async def stream(self, node, issue, closed):
        session = await self.acquire(node)
        return await session.open(issue, closed)

    async def release(self, session):
        async with self.condition:
            session.leased = False
            session.idle_since = time.monotonic()
            if session.failed or self.retired:
                await session.aclose()
                self.sessions.discard(session)
            self.condition.notify_all()

    async def _reap(self):
        try:
            while self.sessions:
                await asyncio.sleep(min(self.idle_ttl, 1))
                async with self.condition:
                    for session in list(self.sessions):
                        if not session.leased and (session.failed or time.monotonic() - session.idle_since >= self.idle_ttl):
                            await session.aclose()
                            self.sessions.discard(session)
                    self.condition.notify_all()
        except asyncio.CancelledError:
            # Event-loop shutdown must not orphan any helper process.
            self.retired = True
            await asyncio.gather(*(s.aclose() for s in list(self.sessions)))
            self.sessions.clear()
            raise

    def retire(self):
        self.retired = True
        # Active streams call release when finished. The reaper also handles
        # idle sessions if eviction occurred outside a running event loop.
        for session in self.sessions:
            if not session.leased:
                session.idle_since = float('-inf')

    async def aclose(self):
        self.retire()
        async with self.condition:
            for session in list(self.sessions):
                if not session.leased:
                    await session.aclose()
                    self.sessions.discard(session)
            self.condition.notify_all()
        # A live call owns its helper until release. Don't cancel its reaper,
        # whose shutdown handler intentionally terminates all remaining peers.
        if not self.sessions and self.reaper:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
