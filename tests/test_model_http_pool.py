"""Real TCP tests for bounded connection reuse and independent cancellation."""
import asyncio
from contextlib import asynccontextmanager
import json

import httpx
import pytest

from pantheon.models.client import ModelServices
from pantheon.models.http_pool import HTTPPool


@asynccontextmanager
async def server():
    state = {'accepted': 0, 'open': 0, 'requests': [], 'tasks': set(),
             'stream_started': asyncio.Event(), 'release': asyncio.Event()}

    async def handle(reader, writer):
        state['tasks'].add(asyncio.current_task())
        state['accepted'] += 1
        state['open'] += 1
        try:
            while True:
                try:
                    raw = await reader.readuntil(b'\r\n\r\n')
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    break
                lines = raw.decode().split('\r\n')
                method, path, _ = lines[0].split(' ')
                headers = {k.lower(): v.strip() for k, v in
                           (line.split(':', 1) for line in lines[1:] if ':' in line)}
                await reader.readexactly(int(headers.get('content-length', 0)))
                state['requests'].append((method, path, headers))
                if path == '/stream':
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nx')
                    await writer.drain()
                    state['stream_started'].set()
                    await state['release'].wait()
                    writer.write(b'y')
                elif path == '/broken':
                    # Ambiguous result after receiving POST: never replay it.
                    break
                else:
                    payload = json.dumps({'cancelled': True}).encode()
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                                 b'Set-Cookie: wrong_authority=never_reuse; Path=/\r\n'
                                 + f'Content-Length: {len(payload)}\r\n\r\n'.encode() + payload)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            state['open'] -= 1
            state['tasks'].discard(asyncio.current_task())

    listener = await asyncio.start_server(handle, '127.0.0.1', 0)
    try:
        yield f'http://127.0.0.1:{listener.sockets[0].getsockname()[1]}', state
    finally:
        listener.close()
        await listener.wait_closed()
        state['release'].set()
        for task in list(state['tasks']):
            task.cancel()
        await asyncio.gather(*state['tasks'], return_exceptions=True)


async def wait_until(predicate, seconds=2):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.005)


@pytest.mark.asyncio
async def test_control_reuses_tcp_without_retaining_cookies_or_authority():
    async with server() as (origin, state):
        client = ModelServices(origin, 'first', direct_executable='')
        try:
            for index in range(12):
                client.token = f'token-{index}'
                await client.hub_request('GET', '/catalog')
            assert state['accepted'] == 1
            assert [r[2]['authorization'] for r in state['requests']] == [f'Bearer token-{i}' for i in range(12)]
            assert all('cookie' not in r[2] for r in state['requests'])
            assert len(client.control_http.client.cookies) == 0
        finally:
            await client.aclose()
        await wait_until(lambda: state['open'] == 0)
        assert client.control_http.client is None


@pytest.mark.asyncio
async def test_idle_expiry_and_retirement_release_real_connections():
    async with server() as (origin, state):
        pool = HTTPPool(timeout=2, connections=2, keepalive=1, idle_seconds=.02)
        try:
            async with pool.lease() as client:
                await client.get(origin)
            await wait_until(lambda: pool.client is None and state['open'] == 0)
            async with pool.lease() as client:
                await client.get(origin)
                pool.retire()
                assert not client.is_closed  # Active owners may finish normally.
                await client.get(origin)
            await wait_until(lambda: pool.client is None and state['open'] == 0)
            assert state['accepted'] == 2
        finally:
            await pool.aclose()


@pytest.mark.asyncio
async def test_saturated_relay_does_not_block_frozen_cancel():
    async with server() as (origin, state):
        client = ModelServices(origin, 'test', direct_executable='')
        client.relay_http.connections = 1
        client.relay_http.keepalive = 1
        row = {'state': 'ready', 'binding': {'generation': 8}, 'config_revision': 'original'}
        grant = {'origin': origin, 'access_token': 'original-token'}
        try:
            async with client.connection(row, grant=grant) as (http, frozen, _):
                async with http.stream('GET', origin + '/stream') as response:
                    await state['stream_started'].wait()
                    assert await client.cancel_request(row, 'request-1', http, frozen)
                    cancel = next(r for r in state['requests'] if r[1] == '/cancel')
                    assert cancel[2]['x-model-config'] == 'original'
                    assert cancel[2]['authorization'] == 'Bearer original-token'
                    assert state['accepted'] == 2
                    state['release'].set()
                    assert await response.aread() == b'xy'
        finally:
            await client.aclose()
        await wait_until(lambda: state['open'] == 0)


@pytest.mark.asyncio
async def test_post_disconnect_is_never_replayed():
    async with server() as (origin, state):
        client = ModelServices(origin, 'test', direct_executable='')
        try:
            with pytest.raises(httpx.RemoteProtocolError):
                await client.hub_request('POST', '/broken', {'mutation': 'once'})
            assert len(state['requests']) == 1
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_credential_cache_eviction_retires_all_pools(monkeypatch):
    from pantheon.models import client as module
    clients = []
    monkeypatch.setattr(module, '_clients', module.OrderedDict())
    monkeypatch.setattr(module, 'direct_binary', lambda: '')
    monkeypatch.setenv('PANTHEON_HUB_URL', 'https://hub.test')
    try:
        for index in range(5):
            monkeypatch.setenv('FLEET_KEY', f'credential-{index}')
            clients.append(module.get_client())
        assert len(module._clients) == 4
        first = clients[0]
        assert first.direct_peers.retired
        assert all(pool.retired for pool in (first.control_http, first.relay_http, first.cancel_http))
    finally:
        for client in clients:
            await client.aclose()


@pytest.mark.asyncio
async def test_reuse_after_asyncio_run_releases_the_old_loop_sockets():
    async with server() as (origin, state):
        client = ModelServices(origin, 'test', direct_executable='')
        async def once():
            await client.hub_request('GET', '/catalog')
        try:
            for _ in range(2):
                await asyncio.to_thread(lambda: asyncio.run(once()))
                assert client.control_http.client is None
                await wait_until(lambda: state['open'] == 0)
            assert state['accepted'] == 2
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_slow_socket_close_cannot_be_cancelled_or_overlap_new_generation():
    async with server() as (origin, state):
        pool = HTTPPool(timeout=2, connections=1, keepalive=1, idle_seconds=.02)
        entered, release = asyncio.Event(), asyncio.Event()
        async with pool.lease() as client:
            await client.get(origin)
            close = client.aclose
            async def delayed_close():
                entered.set()
                await release.wait()
                await close()
            client.aclose = delayed_close
        await asyncio.wait_for(entered.wait(), 2)

        async def request():
            async with pool.lease() as client:
                await client.get(origin)

        abandoned = asyncio.create_task(request())
        next_request = None
        try:
            await asyncio.sleep(.01)
            abandoned.cancel()
            assert isinstance((await asyncio.gather(abandoned, return_exceptions=True))[0], asyncio.CancelledError)
            next_request = asyncio.create_task(request())
            await asyncio.sleep(.03)
            assert not next_request.done()
            assert state['accepted'] == state['open'] == 1
            release.set()
            await asyncio.wait_for(next_request, 2)
            assert state['accepted'] == 2
        finally:
            release.set()
            await asyncio.gather(abandoned, *([next_request] if next_request else []), return_exceptions=True)
            await pool.aclose()
        await wait_until(lambda: state['open'] == 0)


@pytest.mark.asyncio
async def test_cancelled_shutdown_caller_still_releases_sockets():
    async with server() as (origin, state):
        pool = HTTPPool(timeout=2, connections=1, keepalive=1)
        entered, release = asyncio.Event(), asyncio.Event()
        async with pool.lease() as client:
            await client.get(origin)
            close = client.aclose
            async def delayed_close():
                entered.set()
                await release.wait()
                await close()
            client.aclose = delayed_close
        shutdown = asyncio.create_task(pool.aclose())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            shutdown.cancel()
            assert isinstance((await asyncio.gather(shutdown, return_exceptions=True))[0], asyncio.CancelledError)
            with pytest.raises(RuntimeError, match='closed'):
                async with pool.lease():
                    pytest.fail('A closed pool must not open another connection')
        finally:
            release.set()
            await pool.aclose()
        await wait_until(lambda: state['open'] == 0)
