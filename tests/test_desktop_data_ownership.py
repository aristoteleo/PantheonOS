"""Prepared Desktop roots and actual HTTP lifetime, including interrupted setup."""
import asyncio
import builtins
import json
from pathlib import Path
import socket
import threading
from urllib.parse import urlparse

from aiohttp import web
import httpx
import pytest

from pantheon.apps.builtin.desktop.data_server import DataServerConfig, LiveViewDataServer
from pantheon.apps.builtin.desktop.files_binding import DesktopFilesBinding
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


def assert_listener_closed(url):
    parsed = urlparse(url)
    with socket.socket() as probe:
        probe.settimeout(.5)
        assert probe.connect_ex(('127.0.0.1', parsed.port)) != 0


@pytest.mark.asyncio
async def test_close_drains_endpoint_and_joins_owned_thread(tmp_path):
    server = LiveViewDataServer(config=DataServerConfig())
    base = await server.ensure_started([tmp_path])
    thread = server._thread
    entered, release = threading.Event(), threading.Event()
    async def held(request):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return web.Response(text='saved output')
    url = await server.register_endpoint('held', held)
    request = close = None
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            request = asyncio.create_task(client.get(url))
            assert await asyncio.to_thread(entered.wait, 5)
            close = asyncio.create_task(server.close())
            await asyncio.sleep(.05)
            assert not close.done()
            close.cancel()
            close.cancel()
            await asyncio.sleep(.02)
            assert not close.done(), 'Cancellation must not abandon the server thread'
            release.set()
            assert (await request).text == 'saved output'
            with pytest.raises(asyncio.CancelledError):
                await close
        assert not thread.is_alive()
        assert_listener_closed(base)
        assert server._roots == {} and server.list_endpoints() == []
        await server.close()
        with pytest.raises(RuntimeError, match='closed'):
            await server.ensure_started([tmp_path])
    finally:
        release.set()
        if request:
            await asyncio.gather(request, return_exceptions=True)
        if close:
            await asyncio.gather(close, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_cancelled_start_is_joined_before_cleanup(tmp_path, monkeypatch):
    server = LiveViewDataServer(config=DataServerConfig())
    entered, release = threading.Event(), threading.Event()
    start = server._start_blocking
    def delayed(roots):
        entered.set()
        assert release.wait(5)
        start(roots)
    monkeypatch.setattr(server, '_start_blocking', delayed)
    task = asyncio.create_task(server.ensure_started([tmp_path]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        url, thread = server._base_url, server._thread
        assert url and thread.is_alive()
        await server.close()
        assert_listener_closed(url)
        assert not thread.is_alive()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_bound_catalog_and_data_ignore_ambient_settings(tmp_path, monkeypatch):
    workspace, user, builtin, private = [tmp_path / name for name in ('workspace', 'user', 'builtin', 'private')]
    for root in (workspace, user, builtin, private):
        root.mkdir()
    app = user / 'sample'
    (app / 'frontend').mkdir(parents=True)
    (app / 'app.json').write_text(json.dumps({'id': 'sample', 'name': 'Bound App', 'version': '1.0.0',
        'entry': {'frontend': 'frontend/main.js'}}))
    source = app / 'frontend/main.js'
    source.write_text('export const bound = true;')
    (private / 'secret').write_text('not served')
    (workspace / 'escape').symlink_to(private / 'secret')
    # Neither a different process-global data port nor its tunnel may be used.
    monkeypatch.setenv('LIVE_VIEW_DATA_TOKEN', 'wrong-ambient-token')
    monkeypatch.setenv('LIVE_VIEW_DATA_PORT', '1')
    server = LiveViewDataServer(config=DataServerConfig())
    binding = DesktopFilesBinding(workspace=workspace, app_roots=[(user, 'user'), (builtin, 'builtin')],
        data_roots=[workspace, user, builtin], server=server)
    desktop = DesktopToolSet(files_binding=binding)
    original_import = builtins.__import__
    forbidden = []
    def guarded(name, *args, **kwargs):
        if name in ('pantheon.settings', 'pantheon.agent'):
            forbidden.append(name)
            raise AssertionError('Bound Desktop looked up global settings/Agent')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    try:
        catalog = await desktop.desktop_app_registry()
        assert catalog['success'] and [entry['manifest']['id'] for entry in catalog['apps']] == ['sample']
        result = await desktop.serve_local_data('.pantheon/apps/sample/frontend/main.js')
        assert result['success'], result
        assert result['resolved_path'] == str(source)
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(result['url'])).text == source.read_text()
            assert not (await desktop.serve_local_data(str(private / 'secret')))['success']
            assert not (await desktop.serve_local_data('escape'))['success']
            endpoint = workspace / 'handler.py'
            endpoint.write_text('from aiohttp import web\nasync def handle(request):\n return web.json_response({"bound": True})\n')
            served = await desktop.serve_endpoint('bound', 'handler.py')
            assert served['success'], served
            assert (await client.get(served['url'])).json() == {'bound': True}
            url = await desktop._serve_bespoke_module('export default {};')
            assert (await client.get(url)).text == 'export default {};'
        assert desktop._apps().workspace == workspace
        synced = await desktop.desktop_sync_apps({'sample/info.txt': 'owned'})
        assert synced['success'], synced
        assert (workspace / '.pantheon/apps/sample/info.txt').read_text() == 'owned'
        assert not forbidden
        thread, base = server._thread, server._base_url
        await desktop.cleanup()
        assert not thread.is_alive()
        assert_listener_closed(base)
    finally:
        await desktop.cleanup()


def test_explicit_tunnel_cache_belongs_to_the_app(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_VIEW_DATA_TOKEN', 'ambient')
    config = DataServerConfig(token='prepared', cache_directory=tmp_path)
    first = LiveViewDataServer(config=config)
    first.set_tunnel_base('https://prepared.example')
    assert first._tunnel_cache.parent == tmp_path
    assert LiveViewDataServer(config=config).base_url == 'https://prepared.example'
    assert LiveViewDataServer(config=DataServerConfig(token='other', cache_directory=tmp_path)).base_url is None
    assert LiveViewDataServer(config=DataServerConfig())._token is None


@pytest.mark.asyncio
async def test_failed_close_retains_owner_for_retry(tmp_path, monkeypatch):
    server = LiveViewDataServer(config=DataServerConfig())
    base = await server.ensure_started([tmp_path])
    thread, runner = server._thread, server._runner
    original, calls = web.AppRunner.cleanup, []
    async def cleanup(instance):
        if instance is runner:
            calls.append(instance)
            if len(calls) == 1:
                raise RuntimeError('injected drain failure')
        await original(instance)
    monkeypatch.setattr(web.AppRunner, 'cleanup', cleanup)
    try:
        with pytest.raises(RuntimeError, match='drain failure'):
            await server.close()
        assert server._thread is thread and thread.is_alive()
        with pytest.raises(RuntimeError, match='closed'):
            await server.ensure_started([tmp_path])
        await asyncio.gather(server.close(), server.close())
        assert len(calls) == 2
        assert not thread.is_alive()
        assert_listener_closed(base)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_bind_failure_cleans_thread_without_claiming_the_existing_listener(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(('0.0.0.0', 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        server = LiveViewDataServer(config=DataServerConfig(token='prepared', port=port))
        try:
            with pytest.raises(OSError):
                await server.ensure_started([tmp_path])
            assert server._thread is not None and not server._thread.is_alive()
            assert server._loop.is_closed()
            await server.close()
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                pass
        finally:
            await server.close()
