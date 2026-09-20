"""Native ScreenCaptureKit/WGC adapter for owned Browser and QuPath windows.

The stream port is loopback-only, behind Fleet's authenticated service gateway.
A fresh in-memory secret authenticates WebSocket handshakes; no token in a URL.
"""
from __future__ import annotations
import asyncio
import base64
import contextlib
import hashlib
import json
import io
import math
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from aiohttp import web, WSMsgType
from .helper import Helper, probe

METHODS = ('browser_popup_ack desktop_read desktop_act desktop_native_screenshot browser_ui_page '
    'browser_ui_nav browser_ui_close browser_ui_stage browser_ui_focus browser_ui_key browser_ui_unstage '
    'browser_clear_data browser_goto browser_read browser_click browser_type browser_act browser_scroll '
    'browser_screenshot browser_pages browser_close native_ui_launch native_ui_status native_ui_close '
    'native_ui_call native_read').split()


def url(value):
    value = str(value or 'about:blank').strip()
    if value.startswith(('http://', 'https://', 'about:', 'data:')):
        return value
    if '://' in value or value.startswith(('file:', 'javascript:')):
        raise ValueError('Unsupported Browser URL')
    return 'https://' + value


def qupath_executable():
    explicit = os.environ.get('PANTHEON_QUPATH_EXECUTABLE')
    paths = [explicit] if explicit else []
    paths += [shutil.which('qupath'), shutil.which('QuPath'), '/Applications/QuPath.app/Contents/MacOS/QuPath']
    for root in [os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')]:
        if root:
            paths.extend(str(p) for p in Path(root).glob('QuPath*/QuPath*.exe') if 'console' not in p.name.lower())
    for name in paths:
        if name and Path(name).is_file():
            return name
    raise RuntimeError('Install QuPath on this node, or configure PANTHEON_QUPATH_EXECUTABLE when starting Fleet')


@dataclass
class Session:
    id: str
    desktop_id: str
    process: object
    helper: Helper | None = None
    main: int | None = None
    windows: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)
    browser: object = None
    page: object = None
    browser_window: int | None = None
    bridge: object = None
    error: str = ''
    path: str = ''
    operation: str = 'initial'
    revision: int = 1


class Runtime:
    def __init__(self, ctx):
        self.ctx = ctx
        self.sessions = {}
        self.windows = {}  # virtual wid -> (session, owned native id, public metadata)
        self.next_window = 0
        self.clients = {}
        self.secret = secrets.token_urlsafe(32)
        self.identity = uuid.uuid4().hex
        self.creation = asyncio.Lock()
        self.playwright = None
        self.runner = None
        self.poll_task = None

    async def start(self):
        await probe()  # Never prompts; permissions are configured on the node.
        if self.ctx.app_id == 'qupath':
            qupath_executable()
        else:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(sys.prefix) / 'browsers')
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
        app = web.Application(client_max_size=65536)
        app.router.add_get('/native-stream', self.websocket)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, '127.0.0.1', int(os.environ['PANTHEON_PORT_STREAM'])).start()
        self.poll_task = asyncio.create_task(self.poll())

    def event(self, event, **value):
        for client in tuple(self.clients.values()):
            if client['events'].qsize() >= 128:
                client['overflow'] = True
            else:
                client['events'].put_nowait({'event': event, **value})
            client['wake'].set()

    def frame(self, session, native, jpeg):
        virtual = session.windows.get(native)
        if virtual is None:
            return
        session.frames[virtual] = jpeg
        for client in self.clients.values():
            client['frames'][virtual] = jpeg  # Replace stale frames, never queue video.
            client['wake'].set()

    def error(self, session, message):
        if session.error != message:
            session.error = message
            self.event('error', error=message)

    async def inventory(self, session):
        found = (await session.helper.command('list'))['windows']
        # Limit popups/resources. Never enumerate or capture another PID.
        found = sorted(found, key=lambda w: w['w'] * w['h'], reverse=True)[:16]
        if session.main is None and found:
            session.main = found[0]['id']
        present = {w['id'] for w in found}
        for native in list(session.windows):
            if native not in present:
                virtual = session.windows.pop(native)
                self.windows.pop(virtual, None)
                session.frames.pop(virtual, None)
                await session.helper.command('uncapture', window=native)
                self.event('close', wid=virtual)
        for window in found:
            native = window['id']
            new = native not in session.windows
            if new:
                self.next_window += 1
                session.windows[native] = self.next_window
            virtual = session.windows[native]
            main = session.windows.get(session.main)
            prefix = 'pantheon-page-' if self.ctx.app_id == 'browser' else 'pantheon-native-qupath-'
            metadata = {'wid': virtual, 'title': window['title'], 'windowClass': prefix + session.id if native == session.main else 'pantheon-dialog-' + str(virtual),
                'transientFor': None if native == session.main else main, 'overrideRedirect': False,
                'geometry': {key: window[key] for key in ('x', 'y', 'w', 'h')}}
            old = self.windows.get(virtual)
            self.windows[virtual] = (session, native, metadata)
            if new:
                await session.helper.command('capture', window=native)
                self.event('open', window=metadata)
            elif old and old[2] != metadata:
                self.event('metadata', window=metadata)

    async def poll(self):
        while True:
            for session in list(self.sessions.values()):
                if session.process.poll() is not None:
                    for virtual in list(session.windows.values()):
                        self.windows.pop(virtual, None)
                        self.event('close', wid=virtual)
                    session.windows.clear()
                    session.frames.clear()
                    continue
                if session.helper:
                    try:
                        await self.inventory(session)
                    except Exception as error:
                        self.error(session, str(error))
            await asyncio.sleep(.5)

    async def launch(self, desktop_id, target='about:blank', width=1280, height=800, path=''):
        key = uuid.uuid4().hex
        profile = self.ctx.state_dir / 'profiles' / hashlib.sha256(desktop_id.encode()).hexdigest()[:24]
        profile.mkdir(parents=True, exist_ok=True)
        log = profile / 'native.log'
        env = dict(os.environ)
        bridge = None
        if self.ctx.app_id == 'browser':
            binary = self.playwright.chromium.executable_path
            argv = [binary, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                '--window-size=' + str(int(width)) + ',' + str(int(height)), url(target)]
            (profile / 'DevToolsActivePort').unlink(missing_ok=True)
        else:
            from .qupath.bridge import QuPathBridge
            if path:
                resolved = (self.ctx.workspace / path).resolve()
                if not resolved.is_relative_to(self.ctx.workspace.resolve()) or not resolved.is_file():
                    raise ValueError('QuPath file must exist in this node’s App workspace')
                if resolved.suffix.lower() == '.qpdata':
                    raise ValueError('Open the QuPath project containing this data file')
                path = str(resolved)
            bridge = QuPathBridge(profile / ('bridge-' + key), key)
            env.update(bridge.launch_environment())
            prefs = str(profile).replace('\\', '\\\\').replace('"', '\\"')
            env['JAVA_TOOL_OPTIONS'] = f'-Djava.util.prefs.userRoot="{prefs}" -Dqupath.win.width={int(width)} -Dqupath.win.height={int(height)} ' + bridge.startup_option()
            argv = [qupath_executable(), '--quiet']
            if path:
                argv.append(('--project=' if path.lower().endswith('.qpproj') else '--image=') + path)
        with log.open('ab') as output:
            process = subprocess.Popen(argv, cwd=self.ctx.workspace, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT)
        session = Session(key, desktop_id, process, bridge=bridge, path=path)
        try:
            # jpackage and Chromium native launchers remain the GUI process.
            # A launcher that forks is not followed into an arbitrary PID.
            session.helper = await Helper.start(process.pid, lambda wid, frame: self.frame(session, wid, frame),
                lambda message: self.error(session, message))
            deadline = asyncio.get_running_loop().time() + 45
            while session.main is None:
                if process.poll() is not None:
                    raise RuntimeError(f'Native app exited during startup; see {log}')
                await self.inventory(session)
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeError(f'Native app did not create an owned window; see {log}')
                if session.main is None:
                    await asyncio.sleep(.15)
            if self.ctx.app_id == 'browser':
                endpoint = profile / 'DevToolsActivePort'
                while not endpoint.is_file():
                    if asyncio.get_running_loop().time() > deadline or process.poll() is not None:
                        raise RuntimeError('Chromium did not start its local control interface')
                    await asyncio.sleep(.1)
                port = int(endpoint.read_text().splitlines()[0])
                session.browser = await self.playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
                session.page = session.browser.contexts[0].pages[0]
                cdp = await session.page.context.new_cdp_session(session.page)
                try: session.browser_window = (await cdp.send('Browser.getWindowForTarget'))['windowId']
                finally: await cdp.detach()
            # A successful OS start does not mean a usable frame exists (for
            # example, the user may lock the node during startup).
            first_frame_deadline = asyncio.get_running_loop().time() + 15
            while session.windows.get(session.main) not in session.frames:
                if session.error:
                    raise RuntimeError(session.error)
                if asyncio.get_running_loop().time() > first_frame_deadline:
                    raise RuntimeError('No native window frame arrived. Unlock this node’s desktop and check its recording permission, then retry.')
                await asyncio.sleep(.05)
            self.sessions[key] = session
            return session
        except BaseException:
            # This launch has not been handed to the user yet.
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), 3)
                except asyncio.TimeoutError:
                    process.kill()
            if session.helper:
                await session.helper.close()
            for virtual in session.windows.values():
                self.windows.pop(virtual, None)
                self.event('close', wid=virtual)
            raise

    def session(self, args):
        token = args.get('page_id') or args.get('native_session_id') or str(args.get('window_id', '')).split('::native:', 1)[0]
        session = self.sessions.get(token) or next((s for s in self.sessions.values() if s.desktop_id == token), None)
        if not session or session.process.poll() is not None:
            raise ValueError('The native app window is no longer running; open a new window')
        return session

    async def active_page(self, session):
        if session.browser:
            for page in session.browser.contexts[0].pages:
                try:
                    if await page.evaluate('document.visibilityState') != 'visible': continue
                    cdp = await page.context.new_cdp_session(page)
                    try: window = (await cdp.send('Browser.getWindowForTarget'))['windowId']
                    finally: await cdp.detach()
                    if window == session.browser_window:
                        session.page = page
                        break
                except Exception:
                    continue
        return session.page

    def credentials(self):
        return {'mode': 'seamless', 'stream_protocol': 'native-v1', 'password': self.secret}

    async def info(self, session):
        result = {'success': True, 'page_id': session.id, 'active_page_id': session.id,
            'session_id': session.desktop_id, 'window_class': ('pantheon-page-' if self.ctx.app_id == 'browser' else 'pantheon-native-qupath-') + session.id,
            'running': session.process.poll() is None and bool(session.windows),
            'state': 'running' if session.process.poll() is None and session.windows else 'stopped',
            'mode': 'seamless', 'stream_protocol': 'native-v1',
            'width': 1280, 'height': 800, 'xpra': True, 'path': session.path, 'popups': []}
        await self.active_page(session)
        if session.page and not session.page.is_closed():
            result.update(url=session.page.url, title=await session.page.title())
        if session.bridge:
            ready = session.bridge.ready()
            result.update(bridge_ready=bool(ready), capabilities=(ready or {}).get('capabilities', []))
        return result

    async def close_session(self, session):
        if session.main in session.windows:
            await session.helper.command('close', window=session.main)
        return {**await self.info(session), 'close_requested': True}

    async def dispatch(self, method, args):
        a = dict(args)
        if method.startswith('browser_') and self.ctx.app_id != 'browser' or method.startswith('native_') and self.ctx.app_id != 'qupath':
            raise ValueError('Method belongs to a different app')
        if method == 'browser_popup_ack':
            return {'success': True}
        if method == 'browser_pages':
            return {'success': True, 'pages': [await self.info(s) for s in self.sessions.values() if s.process.poll() is None]}
        if method in ('browser_ui_page', 'native_ui_launch'):
            async with self.creation:
                if a.get('page_id'):
                    return await self.info(self.session(a))
                desktop = str(a.get('window_id') or a.get('native_session_id') or ('agent-' + uuid.uuid4().hex if method == 'browser_ui_page' else ''))
                if not desktop or len(desktop) > 200:
                    raise ValueError('A stable Desktop window ID is required')
                previous = next((s for s in self.sessions.values() if s.desktop_id == desktop and s.process.poll() is None), None)
                if previous:
                    if a.get('operation_id') and a['operation_id'] != previous.operation:
                        raise ValueError('This Browser window is already bound; open a new window')
                    if self.ctx.app_id == 'qupath' and a.get('path', '') != previous.path and a.get('path'):
                        raise ValueError('This QuPath window already has a file; open a new window')
                    if not previous.windows:
                        previous.process.terminate()
                        await asyncio.wait_for(asyncio.to_thread(previous.process.wait), 5)
                        previous = None
                    session = previous
                if previous is None:
                    session = await self.launch(desktop, a.get('url', ''), max(320, min(3840, int(a.get('width', 1280)))),
                        max(200, min(2160, int(a.get('height', 800)))), a.get('path', ''))
                    session.operation = a.get('operation_id') or 'initial'
                return {**await self.info(session), **(self.credentials() if method == 'native_ui_launch' else {}), 'binding': {'page_id': session.id, 'operation_id': session.operation, 'revision': session.revision}}
        if method == 'browser_clear_data':
            for s in self.sessions.values():
                if s.browser and s.process.poll() is None:
                    await s.browser.contexts[0].clear_cookies()
            return {'success': True}
        if method == 'native_ui_status':
            try:
                return await self.info(self.session(a))
            except ValueError:
                return {'success': True, 'running': False, 'state': 'stopped'}
        session = self.session(a)
        if method in ('native_ui_close', 'browser_ui_close', 'browser_close'):
            return await self.close_session(session)
        if method in ('browser_ui_stage', 'browser_ui_focus'):
            await session.helper.command('focus', window=session.main)
            if method == 'browser_ui_stage':
                await session.helper.command('resize', window=session.main, w=a.get('width', 1280), h=a.get('height', 800))
            return {**await self.info(session), **self.credentials()}
        if method == 'browser_ui_unstage':
            return {'success': True}
        if method in ('native_read', 'native_ui_call'):
            action = a.get('action', 'get_state')
            params = a.get('args', {})
            if action == 'close':
                return await self.close_session(session)
            if action == 'focus':
                await session.helper.command('focus', window=session.main)
                value = {'focused': True}
            elif not session.bridge or not session.bridge.ready():
                raise RuntimeError('QuPath script bridge is still starting')
            elif action in ('status', 'get_state'):
                value = await session.bridge.call('state', {'annotation_limit': params.get('annotation_limit', a.get('annotation_limit', 200))}, timeout=2)
            elif action == 'script_status':
                value = session.bridge.request_status(params.get('request_id'))
            elif action == 'run_script':
                wait = params.get('wait_s', 2)
                if not isinstance(wait, (int, float)) or not 0 <= wait <= 10:
                    raise ValueError('wait_s must be between 0 and 10')
                value = await session.bridge.call('script', {k: v for k, v in params.items() if k in ('script', 'thread', 'args', 'expected_image', 'update_hierarchy')}, request_id=params.get('request_id'), timeout=wait)
            else:
                raise ValueError('Unsupported QuPath action')
            return {'success': True, 'result': value} if method == 'native_ui_call' else {'success': True, **value}
        if method.startswith('desktop_'):
            native = session.main
            desktop = str(a.get('window_id', ''))
            if '::native:' in desktop:
                virtual = int(desktop.rsplit('::native:', 1)[1])
                target = self.windows.get(virtual)
                if not target or target[0] is not session:
                    raise ValueError('This child window does not belong to this session')
                native = target[1]
            targets = [{'window_id': session.desktop_id if n == session.main else session.desktop_id + '::native:' + str(v), **self.windows[v][2]} for n, v in session.windows.items()]
            if method == 'desktop_read':
                return {'success': True, 'result': {'native_windows': targets, 'window_id': desktop}}
            if method == 'desktop_native_screenshot':
                frame = session.frames.get(session.windows.get(native))
                if not frame:
                    raise RuntimeError('Waiting for the first native frame')
                from PIL import Image
                width, height = Image.open(io.BytesIO(frame)).size
                return {'success': True, 'data_url': 'data:image/jpeg;base64,' + base64.b64encode(frame).decode(),
                    'width': width, 'height': height, 'coordinate_space': 'native_window', 'capture_source': 'native_window', 'native_windows': targets}
            from .input import batch
            from PIL import Image
            frame = session.frames.get(session.windows.get(native))
            if not frame: raise RuntimeError('Take a native screenshot before sending pixel input')
            width, height = Image.open(io.BytesIO(frame)).size
            events = batch(a.get('actions'), width, height)
            held = {}
            try:
                for event in events:
                    delay = event.pop('_delay', 0)
                    await session.helper.command('input', window=native, **event)
                    if event['kind'] in ('key', 'pointer'):
                        key = event.get('code', str(event.get('button', 0)))
                        if event.get('down') or event.get('phase') == 'down': held[key] = event
                        elif event.get('phase') != 'move': held.pop(key, None)
                    if delay: await asyncio.sleep(delay)
            finally:
                for event in held.values():
                    with contextlib.suppress(Exception):
                        await session.helper.command('input', window=native, **{**event, 'down': False, 'phase': 'up', 'modifiers': []})
            return {'success': True}
        page = await self.active_page(session)
        if not page or page.is_closed():
            pages = session.browser.contexts[0].pages if session.browser else []
            if not pages:
                raise ValueError('Browser page has closed')
            session.page = page = pages[-1]
        if method in ('browser_ui_nav', 'browser_goto'):
            op = a.get('op', 'goto')
            if op == 'goto': await page.goto(url(a.get('url')), wait_until='domcontentloaded')
            elif op == 'back': await page.go_back(wait_until='domcontentloaded')
            elif op == 'forward': await page.go_forward(wait_until='domcontentloaded')
            elif op == 'reload': await page.reload(wait_until='domcontentloaded')
            elif op == 'stop': await page.evaluate('window.stop()')
            else: raise ValueError('Unknown Browser navigation')
        elif method == 'browser_read':
            from .browser_snapshot import BROWSER_SNAPSHOT_JS, ELEMENT_LIMIT
            snapshot = await page.evaluate(BROWSER_SNAPSHOT_JS, {'textLimit': 20000, 'elementLimit': ELEMENT_LIMIT})
            return {**await self.info(session), **snapshot}
        elif method == 'browser_click': await page.click(a['selector'], timeout=5000)
        elif method == 'browser_type':
            await page.fill(a['selector'], a['text'], timeout=5000)
            if a.get('submit'): await page.press(a['selector'], 'Enter')
        elif method == 'browser_screenshot':
            frame = await page.screenshot(type='jpeg', quality=80, scale='css')
            return {'success': True, 'image_base64': base64.b64encode(frame).decode(), 'mime_type': 'image/jpeg'}
        elif method == 'browser_scroll':
            if a.get('to') in ('top', 'bottom'):
                await page.evaluate('(bottom) => window.scrollTo(0, bottom ? document.body.scrollHeight : 0)', a['to'] == 'bottom')
            else: await page.mouse.wheel(a.get('dx', 0), a.get('dy', 0))
        elif method in ('browser_act', 'browser_ui_key'):
            from .browser import input_events
            events = input_events(a.get('actions', [])) if method == 'browser_act' else a.get('events', [])
            for event in events:
                kind = event['t']
                if kind == 'move': await page.mouse.move(event['x'], event['y'])
                elif kind in ('down', 'up'):
                    await page.mouse.move(event.get('x', 0), event.get('y', 0))
                    await getattr(page.mouse, kind)(button={0:'left', 1:'middle', 2:'right'}[event.get('button', 0)], click_count=event.get('clicks', 1))
                elif kind == 'wheel': await page.mouse.wheel(event.get('dx', 0), event.get('dy', 0))
                elif kind == 'scroll': await page.evaluate('(y) => window.scrollTo(0, y)', event.get('y', 0))
                elif kind in ('keydown', 'keyup'): await getattr(page.keyboard, 'down' if kind == 'keydown' else 'up')(event['key'])
                elif kind == 'text': await page.keyboard.insert_text(event['text'])
                else: raise ValueError('Unsupported browser event')
        else:
            raise ValueError('Unsupported native stream method: ' + method)
        return await self.info(session)

    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=65536, compress=False)
        await ws.prepare(request)
        client = {'events': asyncio.Queue(), 'frames': {}, 'wake': asyncio.Event(), 'overflow': False}
        sender = None
        pressed = {}  # Only releases events injected by this connection.
        try:
            message = await asyncio.wait_for(ws.receive(), 5)
            if message.type != WSMsgType.TEXT:
                raise ValueError('Authentication required')
            hello = json.loads(message.data)
            if not isinstance(hello, dict):
                raise ValueError('Invalid stream handshake')
            if not secrets.compare_digest(str(hello.get('password', '')), self.secret):
                raise ValueError('Invalid stream credentials')
            await ws.send_json({'event': 'ready', 'identity': self.identity, 'windows': [w[2] for w in self.windows.values()]})
            self.clients[ws] = client
            for session in self.sessions.values():
                client['frames'].update(session.frames)
            client['wake'].set()
            async def send():
                while not ws.closed:
                    await client['wake'].wait()
                    client['wake'].clear()
                    if client['overflow']:
                        await ws.close(code=1013, message=b'Client too slow')
                        return
                    while not client['events'].empty():
                        await asyncio.wait_for(ws.send_json(client['events'].get_nowait()), 5)
                    frames, client['frames'] = client['frames'], {}
                    for wid, jpeg in frames.items():
                        if wid in self.windows:
                            await asyncio.wait_for(ws.send_bytes(struct.pack('>I', wid) + jpeg), 5)
            sender = asyncio.create_task(send())
            sender.add_done_callback(lambda task: asyncio.create_task(ws.close()) if not task.cancelled() and task.exception() else None)
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    break
                try:
                    a = json.loads(message.data)
                    op = a.get('op')
                    target = self.windows.get(a.get('wid'))
                    if not target:
                        raise ValueError('Window no longer belongs to this stream')
                    session, native, _ = target
                    if op == 'input':
                        event = validate_input(a)
                        await session.helper.command('input', window=native, **event)
                        key = (a['wid'], event.get('code') if event['kind'] == 'key' else str(event.get('button')))
                        if event['kind'] == 'key' or event['kind'] == 'pointer' and event['phase'] != 'move':
                            if event.get('down') or event.get('phase') == 'down': pressed[key] = (session, native, event)
                            else: pressed.pop(key, None)
                    elif op in ('resize', 'focus', 'close'):
                        await session.helper.command(op, window=native, **({'w': int(a['w']), 'h': int(a['h'])} if op == 'resize' else {}))
                    else: raise ValueError('Unsupported stream operation')
                except Exception as error:
                    await ws.send_json({'event': 'error', 'error': str(error)})
        except (ValueError, asyncio.TimeoutError, json.JSONDecodeError):
            await ws.close(code=1008)
        finally:
            self.clients.pop(ws, None)
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception): await sender
            for session, native, event in pressed.values():
                with contextlib.suppress(Exception):
                    await session.helper.command('input', window=native, **{**event, 'down': False, 'phase': 'up', 'modifiers': []})
        return ws

    async def before_stop(self):
        for session in self.sessions.values():
            if session.process.poll() is None:
                owned = (await session.helper.command('list'))['windows']
                if owned:
                    raise RuntimeError('Close the native app and complete any Save/Cancel dialogs before stopping its backend')
                # macOS apps can remain resident after the last window closes.
                session.process.terminate()
                await asyncio.wait_for(asyncio.to_thread(session.process.wait), 5)

    async def cleanup(self):
        if self.poll_task:
            self.poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await self.poll_task
        for client in self.clients.values(): client['overflow'] = True; client['wake'].set()
        if self.runner: await self.runner.cleanup()
        for session in self.sessions.values():
            if session.helper: await session.helper.close()
        if self.playwright: await self.playwright.stop()


def validate_input(a):
    """Whitelist events. No arbitrary helper command, PID, path or native ID."""
    kind = a.get('kind')
    if kind == 'key':
        code = a.get('code')
        if not isinstance(code, str) or not 1 <= len(code) <= 32: raise ValueError('Invalid physical key')
        return {'kind': kind, 'code': code, 'down': bool(a.get('down')), 'modifiers': [m for m in a.get('modifiers', []) if m in ('shift', 'ctrl', 'alt', 'meta')]}
    if kind == 'text':
        text = a.get('text')
        if not isinstance(text, str) or len(text.encode()) > 4096: raise ValueError('Text exceeds 4096 bytes')
        return {'kind': kind, 'text': text}
    if kind == 'pointer':
        if any(not math.isfinite(float(a.get(axis, 0))) for axis in ('x', 'y')): raise ValueError('Coordinates must be finite')
        if int(a.get('button', 0)) not in (0, 1, 2): raise ValueError('Unsupported pointer button')
        if a.get('phase') not in ('move', 'down', 'up'): raise ValueError('Invalid pointer phase')
        return {'kind': kind, 'phase': a['phase'], 'x': max(0., min(1., float(a.get('x', 0)))), 'y': max(0., min(1., float(a.get('y', 0)))),
            'button': int(a.get('button', 0)), 'buttons': int(a.get('buttons', 0)), 'clicks': max(1, min(3, int(a.get('clicks', 1))))}
    if kind == 'wheel':
        return {'kind': kind, 'dx': max(-2000, min(2000, int(a.get('dx', 0)))), 'dy': max(-2000, min(2000, int(a.get('dy', 0))))}
    raise ValueError('Unsupported native input event')


async def register(ctx):
    runtime = Runtime(ctx)
    try:
        await runtime.start()
    except BaseException:
        await runtime.cleanup()
        raise
    for name in METHODS:
        def wrap(name):
            async def call(**args): return await runtime.dispatch(name, args)
            call.__name__ = name
            return call
        ctx.method(wrap(name))
    ctx.before_stop = runtime.before_stop
    ctx.on_cleanup(runtime.cleanup)
