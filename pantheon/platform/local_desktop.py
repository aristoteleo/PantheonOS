"""Native Desktop view of one installed Agent App, without an embedded Agent.

The private parent pipe owns stop/retry. The loopback view gets only an opaque,
process-local capability for one exact App generation, never Fleet credentials.
Closing a view does not stop its backend; closing the owner drains the profile.
"""
import asyncio
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sys
import tarfile
import tempfile
import threading
from urllib.parse import unquote, urlsplit

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.owner_journal import OwnerJournal

PREFIX = 'PANTHEON_APP_DESKTOP:'
MAX_REQUEST = 2 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024


def emit(kind, value):
    # This pipe carries a short-lived view capability. Native hosts must parse
    # it without copying ready messages into logs or telemetry.
    print(PREFIX + json.dumps({'protocol': 1, 'kind': kind, 'value': value}), flush=True)


async def control_input(commands):
    """Only the owning native process has this pipe. EOF requests a clean stop."""
    reader = asyncio.StreamReader(limit=1024)
    pipe = os.fdopen(os.dup(sys.stdin.fileno()), 'rb', buffering=0)
    transport = None
    try:
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), pipe)
        while line := await reader.readline():
            value = json.loads(line)
            if (not isinstance(value, dict) or set(value) != {'protocol', 'command'}
                    or type(value['protocol']) is not int or value['protocol'] != 1
                    or value['command'] not in ('stop', 'retry', 'status')):
                raise ValueError('Invalid Desktop control command')
            await commands.put(value['command'])
    except asyncio.CancelledError:
        raise
    except Exception:
        # Invalid input is not permission to kill accepted work.
        emit('control_error', {'error': 'Desktop control disconnected; draining the local profile'})
    finally:
        if transport: transport.close()
        else: pipe.close()
        commands.put_nowait('stop')


def snapshot_frontend(package, destination):
    """Copy GUI bytes from the exact canonical artifact, not a mutable dev dir."""
    payload, revision = build_artifact(Path(package['path']), package.get('platform'))
    if revision != package['revision']:
        raise AssemblyError('Desktop package changed; restore the paired release before opening it')
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:*') as archive:
        metadata = json.load(archive.extractfile('app.json'))
        if metadata.get('id') != 'agent' or metadata.get('entry', {}).get('frontend') != 'frontend/index.js':
            raise AssemblyError('Desktop requires the paired Agent GUI release')
        for member in archive:
            path = PurePosixPath(member.name)
            if not path.parts or path.parts[0] != 'frontend': continue
            if not member.isfile() or path.is_absolute() or '..' in path.parts:
                raise AssemblyError('Invalid GUI artifact member')
            target = destination.joinpath(*path.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('wb') as output:
                while block := source.read(192 * 1024): output.write(block)
    if not (destination/'index.js').is_file():
        raise AssemblyError('Agent GUI entry is absent')


def view_state(value):
    if (not isinstance(value, dict) or value.keys() - {'chatId'}
            or value.get('chatId') is not None and (not isinstance(value['chatId'], str)
                or not 1 <= len(value['chatId']) <= 200)):
        raise ValueError('Invalid Desktop conversation selection')
    return dict(value)


class ViewServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, view):
        self.view = view
        self.capacity = threading.BoundedSemaphore(32)
        super().__init__(('127.0.0.1', 0), ViewHandler)

    def process_request(self, request, address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request, address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, address):
        try: super().process_request_thread(request, address)
        finally: self.capacity.release()


class ViewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Do not log capability-bearing URLs.

    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def reply(self, code, value, mime='application/json'):
        data = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
        if len(data) > MAX_RESPONSE:
            code, data, mime = 502, b'{"error":"App reply exceeds the view limit"}', 'application/json'
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self' blob: data:; script-src 'self' 'nonce-" +
            self.server.view.token + "' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass

    def route(self):
        view = self.server.view
        if view.closed or self.headers.get('Host') != view.authority:
            return None
        path = urlsplit(self.path).path
        prefix = '/view/' + view.token + '/'
        return path[len(prefix):] if path.startswith(prefix) else None

    def do_GET(self):
        route = self.route()
        if route is None: return self.reply(404, {'error': 'View unavailable'})
        view = self.server.view
        if route == '':
            source = Path(__file__).with_name('local_desktop.html').read_text()
            return self.reply(200, source.replace('__NONCE__', view.token).encode(), 'text/html; charset=utf-8')
        if not route.startswith('package/'): return self.reply(404, {'error': 'Not found'})
        path = PurePosixPath(unquote(route[len('package/'):]))
        if not path.parts or path.is_absolute() or any(p in ('.', '..') for p in path.parts):
            return self.reply(404, {'error': 'Not found'})
        target = view.assets.joinpath(*path.parts)
        if not target.is_file() or target.stat().st_size > MAX_RESPONSE:
            return self.reply(404, {'error': 'Not found'})
        mime = {'.js': 'text/javascript', '.css': 'text/css'}.get(target.suffix)
        return self.reply(200, target.read_bytes(), mime or mimetypes.guess_type(str(target))[0] or 'application/octet-stream')

    def do_POST(self):
        route = self.route()
        view = self.server.view
        if (route not in ('rpc', 'state') or self.headers.get('Origin') != view.origin
                or self.headers.get('X-Pantheon-View') != '1'
                or self.headers.get('Content-Type') != 'application/json'
                or self.headers.get('Transfer-Encoding')):
            return self.reply(403, {'error': 'View request denied'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_REQUEST: raise ValueError()
            raw = self.rfile.read(size)
            if len(raw) != size: raise ValueError()
            value = json.loads(raw)
            if route == 'state':
                if value != {'read': True}: view_state(value)
            elif (not isinstance(value, dict) or set(value) != {'method', 'args', 'timeout_s'}
                    or not isinstance(value['method'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,100}', value['method'])
                    or not isinstance(value['args'], dict)
                    or type(value['timeout_s']) not in (int, float) or not 0 < value['timeout_s'] <= 600):
                raise ValueError()
        except (ValueError, TypeError):
            return self.reply(400, {'error': 'Invalid bounded view request'})
        try:
            future = asyncio.run_coroutine_threadsafe(view.request(route, value), view.loop)
            result = future.result(timeout=610)
            return self.reply(200, {'success': True, 'result': result})
        except (Exception, concurrent.futures.CancelledError):
            # Unknown RPC outcomes never trigger an automatic resubmission.
            return self.reply(502, {'error': 'Agent request failed or was interrupted. Check its state before retrying.'})


class DesktopView:
    def __init__(self, assets, invoke, state_path):
        self.assets, self.invoke, self.state_path = Path(assets), invoke, Path(state_path)
        self.journal = OwnerJournal(self.state_path.parent)
        self.token = secrets.token_urlsafe(32)
        self.closed = False
        self.tasks = set()
        self.state_lock = asyncio.Lock()

    async def request(self, route, value):
        if self.closed: raise AssemblyError('This view is closed')
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            if route == 'rpc':
                return await self.invoke(value['method'], value['args'], value['timeout_s'])
            async with self.state_lock:
                if value == {'read': True}:
                    if not self.state_path.exists(): return {}
                    self.journal._private(self.state_path)
                    if self.state_path.stat().st_size > 4096: raise ValueError('Invalid view state')
                    return view_state(json.loads(self.state_path.read_text()))
                await self.journal._checkpoint(self.state_path, view_state(value))
                return value
        finally: self.tasks.discard(task)

    async def __aenter__(self):
        self.loop = asyncio.get_running_loop()
        self.server = ViewServer(self)
        self.authority = '127.0.0.1:' + str(self.server.server_port)
        self.origin = 'http://' + self.authority
        self.url = self.origin + '/view/' + self.token + '/'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()
        return self

    async def __aexit__(self, *args):
        self.closed = True
        await asyncio.to_thread(self.server.shutdown)
        tasks = list(self.tasks)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.server.server_close()
        await asyncio.to_thread(self.thread.join)


async def desktop_view(session, alias):
    binding = session.app_binding(alias)
    package = session.spec['packages'][session.spec['apps'][alias]['package']]
    invoke = await session.bind_rpc(alias, 'agent')
    # Keep the transport descriptor small and require the native Agent protocol
    # before exposing any URL. The GUI negotiates its own full feature contract.
    info = await invoke('get_agent_app_info', {}, 30)
    if not isinstance(info, dict) or info.get('protocol') != 1:
        raise AssemblyError('Agent does not support the native Desktop client protocol')
    with tempfile.TemporaryDirectory(prefix='desktop-view-', dir=session.runtime.root) as temporary:
        assets = Path(temporary)
        snapshot = asyncio.create_task(asyncio.to_thread(snapshot_frontend, package, assets))
        cancelled = False
        while not snapshot.done():
            try:
                await asyncio.shield(snapshot)
            except asyncio.CancelledError:
                # A worker still writing files owns the directory until it
                # settles, including when the owner receives repeated stop.
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not snapshot.cancelled(): snapshot.exception()
            raise asyncio.CancelledError
        snapshot.result()
        async with DesktopView(assets, invoke, session.root/'desktop-view.json') as view:
            emit('ready', {'app_id': 'agent', 'revision': package['revision'],
                'instance_id': binding['instance_id'], 'node_id': binding['node_id'],
                'generation': binding['generation'], 'url': view.url})
            await asyncio.Future()
