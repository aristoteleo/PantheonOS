"""Portable App backend + data plane. Stdlib only; bound to a Runner loopback port."""
from __future__ import annotations
import argparse
import asyncio
import concurrent.futures
from collections import OrderedDict
import gzip
import hashlib
from datetime import datetime, timezone
import inspect
import json
import mimetypes
import os
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, quote
from urllib.request import Request, urlopen

from app_runtime import AppContext, _load_backend

MAX_RPC = 512 * 1024
MAX_STATIC_CACHE = 32 * 1024 * 1024


def accepts_gzip(header):
    encodings = {}
    for item in header.lower().split(','):
        name, *options = item.strip().split(';')
        quality = 1.0
        for option in options:
            if option.strip().startswith('q='):
                try:
                    quality = float(option.strip()[2:])
                except ValueError:
                    quality = 0
        encodings[name] = 0 < quality <= 1
    return encodings.get('gzip', encodings.get('*', False))


class Backend:
    def __init__(self, package, data, workspace=None):
        self.package, self.data = package.resolve(), data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.files = {}
        self.lock = threading.RLock()
        self.static_cache = OrderedDict()
        self.static_cache_size = 0
        self.accepting, self.active = True, 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        manifest = next(self.package / n for n in ('app.json', 'atrium.json') if (self.package / n).is_file())
        self.manifest = json.loads(manifest.read_text())
        workspace = workspace.resolve() if workspace else self.data / 'workspace'
        workspace.mkdir(parents=True, exist_ok=True)
        self.ctx = AppContext(self.manifest['id'], workspace, self.data, self)
        self.serial = None
        async def register():
            self.serial = asyncio.Lock()
            result = _load_backend(self.package).register(self.ctx)
            if inspect.isawaitable(result):
                await result
        asyncio.run_coroutine_threadsafe(register(), self.loop).result(100)

    def compressed_static(self, path, stat):
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self.lock:
            cached = self.static_cache.get(key)
            if cached is not None:
                self.static_cache.move_to_end(key)
                return cached
        body = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
        with self.lock:
            previous = self.static_cache.pop(key, None)
            if previous is not None:
                self.static_cache_size -= len(previous)
            while self.static_cache and self.static_cache_size + len(body) > MAX_STATIC_CACHE:
                _, removed = self.static_cache.popitem(last=False)
                self.static_cache_size -= len(removed)
            if len(body) <= MAX_STATIC_CACHE:
                self.static_cache[key] = body
                self.static_cache_size += len(body)
        return body

    def notify(self, method, params):
        import sys
        print(params.get('message', ''), file=sys.stderr, flush=True)

    async def request(self, method, params):
        if method != 'ctx.serve':
            raise ValueError('Unsupported App context request')
        path = Path(params['path']).resolve(strict=True)
        with self.lock:
            token = secrets.token_urlsafe(24)
            self.files[token] = path
        return {'url': '/_fleet/files/' + token + '/' + quote(path.name)}

    def invoke(self, name, args, timeout):
        with self.lock:
            if not self.accepting:
                raise RuntimeError('App is stopping; new calls are not accepted')
            if name not in self.ctx._methods:
                raise ValueError(f'No registered method: {name}')
            self.active += 1
        async def run():
            try:
                async def call():
                    # Preserve the original backend's serialized sync semantics while
                    # keeping the event loop available for readiness and drain checks.
                    fn = self.ctx._methods[name]
                    result = await fn(**args) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, **args)
                    if inspect.isawaitable(result):
                        result = await result
                    return result if result is not None else {}
                # Stateful backends opt in only when they provide their own
                # resource locks (e.g. one per Jupyter kernel). Interrupt/status
                # must remain reachable while a long cell is executing.
                if name in self.ctx.concurrent_methods:
                    return await call()
                async with self.serial:
                    return await call()
            finally:
                with self.lock:
                    self.active -= 1
        future = asyncio.run_coroutine_threadsafe(run(), self.loop)
        # A caller timeout does not pretend a mutation was cancelled; drain still
        # waits for its actual completion. Never replay a timed-out invocation.
        return future.result(timeout)

    def close(self):
        if self.ctx._cleanup:
            async def cleanup():
                result = self.ctx._cleanup()
                if inspect.isawaitable(result):
                    await result
            asyncio.run_coroutine_threadsafe(cleanup(), self.loop).result(25)

    def filesystem(self, payload):
        op = payload.get('op')
        caps = self.manifest.get('caps', {}).get('fs', [])
        required = 'write' if op == 'write' else 'read'
        if required not in caps and not (required == 'read' and 'write' in caps):
            raise ValueError('App does not declare this filesystem capability')
        root = self.ctx.workspace.resolve()
        path = (root / str(payload.get('path') or '.')).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Path escapes this App workspace')
        with self.lock:
            if not self.accepting:
                raise RuntimeError('App is stopping')
            self.active += 1
        try:
            if op == 'read':
                with path.open() as stream:
                    return {'content': stream.read(200000)}
            if op == 'write':
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(payload.get('content', '')))
                return {}
            if op == 'ls':
                return {'entries': [{'name': p.name, 'path': str(p), 'type': 'directory' if p.is_dir() else 'file',
                    'last_modified': datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
                    'size': p.stat().st_size} for p in sorted(path.iterdir())][:2000]}
            raise ValueError('Unknown filesystem operation')
        finally:
            with self.lock:
                self.active -= 1

    def drain(self):
        guard = getattr(self.ctx, 'before_stop', None)
        if guard:
            async def check():
                result = guard()
                if inspect.isawaitable(result):
                    await result
            try:
                asyncio.run_coroutine_threadsafe(check(), self.loop).result(20)
            except Exception as exc:
                return {'status': 'waiting', 'safe_to_stop': False, 'message': str(exc)}
        with self.lock:
            self.accepting = False
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            with self.lock:
                if not self.active:
                    return {'status': 'succeeded', 'safe_to_stop': True}
            time.sleep(.1)
        return {'status': 'waiting', 'safe_to_stop': False, 'message': 'Backend calls are still running'}

    def file(self, url):
        path = unquote(urlsplit(url).path)
        if path.startswith('/package/'):
            root, relative = self.package, path.removeprefix('/package/')
        elif path.startswith('/_fleet/files/'):
            pieces = path.split('/', 4)
            root = self.files.get(pieces[3])
            if root is None:
                raise FileNotFoundError()
            relative = pieces[4] if len(pieces) > 4 else ''
            if root.is_file():
                if relative != root.name:
                    raise FileNotFoundError()
                return root
            relative = relative.removeprefix(root.name + '/')
        else:
            root, relative = Path(__file__).parent / 'assets', path.lstrip('/')
        target = (root / relative).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise FileNotFoundError()
        return target


def handler(backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if self.path == '/_fleet/drain':
                    # This endpoint is for local lifecycle probes, never the gateway.
                    if self.headers.get('X-Forwarded-Host') or self.headers.get('Origin'):
                        return self.json({'error': 'Local lifecycle endpoint'}, 403)
                    return self.json(backend.drain())
                if self.path not in ('/rpc', '/_fleet/fs') or not self.headers.get('Content-Type', '').startswith('application/json'):
                    return self.json({'error': 'Unknown API'}, 404)
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > MAX_RPC:
                    return self.json({'error': 'RPC payload too large'}, 413)
                payload = json.loads(self.rfile.read(size))
                if self.path == '/_fleet/fs':
                    return self.json({'success': True, **backend.filesystem(payload)})
                args = payload.get('args') or {}
                if not isinstance(args, dict):
                    raise ValueError('args must be an object')
                result = backend.invoke(payload['method'], args, min(600, max(1, float(payload.get('timeout_s', 60)))))
                self.json({'success': True, 'result': result})
            except concurrent.futures.TimeoutError:
                self.json({'success': False, 'error': 'Call timed out; outcome unknown. Do not repeat a mutation automatically.'}, 504)
            except Exception as exc:
                self.json({'success': False, 'error': str(exc)}, 400)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if self.path == '/health':
                return self.json({'ready': backend.accepting, 'app_id': backend.manifest['id'],
                    'generation': os.environ.get('PANTHEON_INSTANCE_GENERATION'),
                    'methods': sorted(backend.ctx._methods)})
            try:
                path = backend.file(self.path)
                stat = path.stat()
                size = stat.st_size
                start, end, status = 0, size - 1, 200
                byte_range = self.headers.get('Range', '')
                # Revalidate authenticated static assets without downloading the
                # editor bundle again on every window open. User files never cache.
                is_static = not unquote(urlsplit(self.path).path).startswith('/_fleet/files/')
                stamp = f'{path}:{stat.st_mtime_ns}:{stat.st_size}'
                etag = 'W/"' + hashlib.sha256(stamp.encode()).hexdigest() + '"' if is_static else None
                cache_control = 'private, no-cache' if is_static else 'no-store'
                validators = [value.strip() for value in self.headers.get('If-None-Match', '').split(',')]
                if etag and (etag in validators or etag[2:] in validators or '*' in validators):
                    self.send_response(304)
                    self.send_header('ETag', etag)
                    self.send_header('Cache-Control', cache_control)
                    self.send_header('Vary', 'Accept-Encoding')
                    self.end_headers()
                    return
                # Large editor bundles must not cross the Fleet tunnel raw.
                # Ranges retain their original byte offsets; user data is not cached.
                compressible = (not self.path.startswith('/_fleet/files/') and
                    path.suffix.lower() in {'.js', '.mjs', '.css', '.html', '.svg', '.json', '.wasm'} and
                    1024 <= size <= MAX_STATIC_CACHE)
                compressed = (backend.compressed_static(path, stat) if compressible and not byte_range
                    and accepts_gzip(self.headers.get('Accept-Encoding', '')) else None)
                if byte_range:
                    import re
                    match = re.fullmatch(r'bytes=(\d*)-(\d*)', byte_range)
                    if not match or not any(match.groups()):
                        raise ValueError('Invalid range')
                    first, last = match.groups()
                    start = int(first) if first else max(0, size - int(last))
                    end = min(size - 1, int(last)) if first and last else size - 1
                    if start >= size or start > end:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{size}')
                        self.end_headers()
                        return
                    status = 206
                self.send_response(status)
                self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(compressed) if compressed is not None else max(0, end - start + 1)))
                if compressible:
                    self.send_header('Vary', 'Accept-Encoding')
                if compressed is not None:
                    self.send_header('Content-Encoding', 'gzip')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Cache-Control', cache_control)
                if etag:
                    self.send_header('ETag', etag)
                if status == 206:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                if self.command == 'HEAD':
                    return
                if compressed is not None:
                    self.wfile.write(compressed)
                    return
                with path.open('rb') as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (FileNotFoundError, ValueError):
                self.send_error(404)
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['start', 'ready', 'drain'])
    ap.add_argument('--package', type=Path, required=True)
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--workspace', type=Path)
    args = ap.parse_args()
    endpoint_file = args.data / 'backend-endpoint.json'
    if args.action != 'start':
        endpoint = json.loads(endpoint_file.read_text())
        request = Request(f"http://127.0.0.1:{endpoint['port']}/" + ('health' if args.action == 'ready' else '_fleet/drain'),
                          method='GET' if args.action == 'ready' else 'POST')
        with urlopen(request, timeout=55) as response:
            result = json.load(response)
        if args.action == 'ready':
            if not result.get('ready') or result.get('generation') != endpoint.get('generation'):
                raise RuntimeError('Backend is not ready at this generation')
        else:
            print(json.dumps(result))
        return
    backend = Backend(args.package, args.data, args.workspace)
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ['PANTHEON_PORT_HTTP'])), handler(backend))
    endpoint_file.write_text(json.dumps({'port': server.server_port,
        'generation': os.environ.get('PANTHEON_INSTANCE_GENERATION')}))
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            backend.close()
        finally:
            backend.loop.call_soon_threadsafe(backend.loop.stop)


if __name__ == '__main__':
    main()
