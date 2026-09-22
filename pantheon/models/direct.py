"""Scoped stdio-to-QUIC bridge. Never a localhost proxy or inference retry."""
import asyncio
from contextlib import contextmanager
import json
import re
import shutil
import time

import httpcore
import httpx


ORIGIN = 'http://fleet-app.invalid'


class DirectUnavailable(RuntimeError):
    """Direct setup failed before any application HTTP request was submitted."""


def binary():
    return shutil.which('fleet')


@contextmanager
def http_errors():
    try:
        yield
    except httpcore.TimeoutException as error:
        raise httpx.TimeoutException('Direct model transport timed out') from error
    except httpcore.NetworkError as error:
        raise httpx.NetworkError('Direct model connection failed') from error
    except httpcore.ProtocolError as error:
        raise httpx.ProtocolError('Direct model response was interrupted') from error


class ProcessStream(httpcore.AsyncNetworkStream):
    def __init__(self, process, closed):
        self.process, self.closed = process, closed
        self.close_task = None

    async def read(self, max_bytes, timeout=None):
        try:
            async with asyncio.timeout(timeout):
                return await self.process.stdout.read(max_bytes)
        except TimeoutError as error:
            raise httpcore.ReadTimeout('Direct stream timed out') from error
        except OSError as error:
            raise httpcore.ReadError('Direct stream closed') from error

    async def write(self, buffer, timeout=None):
        try:
            async with asyncio.timeout(timeout):
                self.process.stdin.write(buffer)
                await self.process.stdin.drain()
        except TimeoutError as error:
            raise httpcore.WriteTimeout('Direct stream timed out') from error
        except (OSError, RuntimeError) as error:
            raise httpcore.WriteError('Direct stream closed') from error

    async def start_tls(self, *args, **kwargs):
        raise httpcore.ConnectError('Direct HTTP must use its authenticated QUIC channel')

    def get_extra_info(self, info):
        if info == 'is_readable':
            return self.process.returncode is not None or self.process.stdout.at_eof()
        return None

    async def _close(self):
        try:
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
                # Drain bounded asyncio pipe buffers so process.wait can finish
                # even when the consumer stopped reading in the middle of SSE.
                while await self.process.stdout.read(65536):
                    pass
                await self.process.wait()
        finally:
            self.closed(self)

    async def aclose(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        with http_errors():
            async for block in self.stream:
                yield block

    async def aclose(self):
        await self.stream.aclose()


class Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, transport):
        self.transport = transport

    async def connect_tcp(self, host, port, timeout=None, **kwargs):
        if host != 'fleet-app.invalid' or port != 80:
            raise httpcore.ConnectError('Direct destination is fixed by the App grant')
        if self.transport.first is not None:
            first, self.transport.first = self.transport.first, None
            return first
        try:
            async with asyncio.timeout(timeout):
                return await self.transport.new_stream()
        except TimeoutError as error:
            raise httpcore.ConnectTimeout('Direct connection timed out') from error

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError('Direct transport does not accept socket paths')

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class DirectHTTPTransport(httpx.AsyncBaseTransport):
    """One invocation's pool, including a second connection for explicit cancel.

    prepare() completes peer authentication before the caller can submit HTTP.
    No fallback or inference retry is performed anywhere in this transport.
    """
    def __init__(self, executable, issue_grant, *, limit=None):
        self.executable, self.issue_grant = executable, issue_grant
        self.limit, self.owns_slot, self.close_task = limit, False, None
        self.streams, self.first = set(), None
        self.pool = httpcore.AsyncConnectionPool(network_backend=Backend(self), retries=0,
            max_connections=2, max_keepalive_connections=0, http1=True, http2=False)

    async def prepare(self):
        if self.close_task is not None:
            raise RuntimeError('Direct transport is closed')
        if self.limit is not None:
            await self.limit.acquire()
            self.owns_slot = True
        self.first = await self.new_stream()

    async def new_stream(self):
        if self.close_task is not None:
            raise RuntimeError('Direct transport is closed')
        try:
            process = await asyncio.create_subprocess_exec(self.executable, 'app-dial',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=65536)
        except OSError as error:
            raise DirectUnavailable('Fleet direct helper is unavailable') from error
        stream = ProcessStream(process, self.streams.discard)
        self.streams.add(stream)
        try:
            async with asyncio.timeout(18):
                line = await process.stdout.readline()
                try:
                    hello = json.loads(line)
                except ValueError as error:
                    raise DirectUnavailable('Fleet direct helper needs an update') from error
                peer = hello.get('peer_id', '') if isinstance(hello, dict) else ''
                if not peer or hello.get('protocol') != 1 or not isinstance(peer, str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,128}', peer):
                    raise DirectUnavailable('Fleet direct helper protocol is unavailable')
                grant = await self.issue_grant(peer)
                # Hub's binding is checked by the caller; only the public QUIC
                # grant fields enter this single-connection helper.
                grant = {key: grant[key] for key in ('peer_id', 'addresses', 'access_token', 'expires', 'transport')}
                if grant['transport'] != 'fleet_direct' or grant['expires'] <= time.time():
                    raise ValueError('Invalid direct workload grant')
                process.stdin.write(json.dumps(grant).encode() + b'\n')
                await process.stdin.drain()
                try:
                    connected = json.loads(await process.stdout.readline())
                except ValueError as error:
                    raise DirectUnavailable('Direct peer connection is unavailable') from error
                if connected == {'ready': False, 'error': 'grant_rejected'}:
                    raise ValueError('Direct App authority was rejected; refresh the selected instance')
                if connected != {'ready': True, 'transport': 'fleet_direct'}:
                    raise DirectUnavailable('Direct peer did not confirm authentication')
            return stream
        except BaseException as error:
            await stream.aclose()
            if isinstance(error, (TimeoutError, OSError)):
                raise DirectUnavailable('Direct connection setup timed out or failed') from error
            raise

    async def handle_async_request(self, request):
        if request.url.scheme != 'http' or request.url.host != 'fleet-app.invalid' or request.url.port not in (None, 80):
            raise httpx.ConnectError('Direct model destination is fixed', request=request)
        req = httpcore.Request(method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions)
        with http_errors():
            response = await self.pool.handle_async_request(req)
        return httpx.Response(response.status, headers=response.headers,
            stream=ResponseStream(response.stream), extensions=response.extensions)

    async def _close(self):
        try:
            await self.pool.aclose()
        finally:
            try:
                await asyncio.gather(*(stream.aclose() for stream in list(self.streams)))
                self.first = None
            finally:
                if self.owns_slot:
                    self.owns_slot = False
                    self.limit.release()

    async def aclose(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)
