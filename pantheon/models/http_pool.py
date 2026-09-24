"""Bounded HTTP reuse for model control, inference and cancellation.

Only connections are shared. Credentials stay on individual requests, cookies
are rejected, and inference POSTs are never replayed by this layer.
"""
import asyncio
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx


class NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


class BorrowedTransport(httpx.AsyncBaseTransport):
    """An injected test/application transport remains owned by its caller."""
    def __init__(self, transport):
        self.transport = transport

    async def handle_async_request(self, request):
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        pass


class HTTPPool:
    def __init__(self, *, timeout, connections, keepalive, transport=None, idle_seconds=30):
        self.timeout, self.connections, self.keepalive = timeout, connections, keepalive
        self.transport, self.idle_seconds = transport, idle_seconds
        self.client = None
        self.active = 0
        self.retired = False
        self.idle_task = None
        self.close_task = None
        self.closed = False

    def _schedule_close(self, delay):
        if self.idle_task is None:
            self.idle_task = asyncio.create_task(self._expire(delay))

    async def _expire(self, delay):
        try:
            await asyncio.sleep(delay)
            if self.active == 0 and self.client is not None:
                client, self.client = self.client, None
                await client.aclose()
        except asyncio.CancelledError:
            # asyncio.run() also cancels maintenance tasks at loop shutdown.
            # A lease/retirement cancels only after detaching this task, so only
            # loop shutdown owns this cleanup. Do not retain sockets bound to a
            # loop which can no longer drive them.
            if self.idle_task is asyncio.current_task() and self.active == 0 and self.client is not None:
                client, self.client = self.client, None
                await client.aclose()
            raise
        finally:
            if self.idle_task is asyncio.current_task():
                self.idle_task = None

    @asynccontextmanager
    async def lease(self):
        if self.closed:
            raise RuntimeError('Model HTTP pool is closed')
        task = self.idle_task
        if task is not None:
            if self.client is None:
                # Physical close has started: finish it before creating another
                # generation, even if a network backend is slow to release.
                await asyncio.shield(task)
            else:
                self.idle_task = None
                task.cancel()
        if self.closed:
            raise RuntimeError('Model HTTP pool is closed')
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=False,
                cookies=CookieJar(policy=NoCookies()),
                transport=BorrowedTransport(self.transport) if self.transport is not None else None,
                limits=httpx.Limits(max_connections=self.connections,
                                   max_keepalive_connections=self.keepalive,
                                   keepalive_expiry=min(15, self.idle_seconds)),
            )
        self.active += 1
        try:
            yield self.client
        finally:
            self.active -= 1
            if self.active == 0 and self.client is not None:
                self._schedule_close(0 if self.retired else self.idle_seconds)

    def retire(self):
        """Credential-cache eviction drains existing calls without interrupting them."""
        self.retired = True
        if self.active == 0 and self.client is not None:
            if self.idle_task is not None and self.client is not None:
                self.idle_task.cancel()
                self.idle_task = None
            self._schedule_close(0)

    async def _close(self):
        task = self.idle_task
        if self.client is not None:
            if task is not None:
                self.idle_task = None
                task.cancel()
            client, self.client = self.client, None
            await client.aclose()
        elif task is not None:
            await asyncio.shield(task)

    async def aclose(self):
        """Explicit shutdown closes sockets; eviction drains via retire instead."""
        if self.close_task is None:
            self.closed = self.retired = True
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)
