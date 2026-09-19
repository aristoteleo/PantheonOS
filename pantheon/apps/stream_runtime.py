"""Fleet adapter for the canonical Browser/Xpra and trusted native drivers.

Copied into an immutable execution package together with the canonical engine.
No Workspace service or Agent environment is required on the selected node.
"""
from __future__ import annotations
import asyncio
import base64
import fcntl
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .browser import BrowserEngine, input_events, normalize_url, READ_LIMIT
from .browser_snapshot import BROWSER_SNAPSHOT_JS, ELEMENT_LIMIT


def reserve_display():
    # Serialize allocation across app versions, scopes and concurrent node starts.
    # Keep the lock until the owning display process exits; never reuse :97.
    root = Path(tempfile.gettempdir()) / f'atrium-displays-{os.getuid()}'
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise RuntimeError('Unsafe display reservation directory')
    for number in range(200, 2000):
        lock = (root / str(number)).open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if Path(f'/tmp/.X11-unix/X{number}').exists() or Path(f'/tmp/.X{number}-lock').exists():
                lock.close()
                continue
            return f':{number}', lock
        except BlockingIOError:
            lock.close()
    raise RuntimeError('No free X display on this node')


async def register(ctx):
    missing = [name for name in ('xpra', 'Xvfb', 'xdpyinfo') if not shutil.which(name)]
    if missing:
        raise RuntimeError('Install streaming dependencies on this Fleet node: ' + ', '.join(missing))
    if ctx.app_id != 'qupath':
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(sys.prefix) / 'browsers')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            binary = Path(playwright.chromium.executable_path)
        if not binary.is_file():
            raise RuntimeError('Chromium is missing on this node. Reinstall Browser to prepare its dependencies.')
    display, lock = reserve_display()
    engine = BrowserEngine(profile=ctx.state_dir / 'browser-profile', display=display,
                           stream_port=int(os.environ['PANTHEON_PORT_STREAM']), managed=True)
    if ctx.app_id == 'qupath':
        from .qupath.native import NativeAppManager
        engine._native_apps = NativeAppManager(engine, workspace=ctx.workspace)
    try:
        if ctx.app_id == 'qupath':
            engine._native_apps._executable()
        await engine.call(engine.ensure_native_stage())
    except BaseException:
        await engine.call(engine.shutdown())
        lock.close()
        raise
    pending_popups = {}
    async def popup(session):
        pending_popups[session.id] = session
    engine.on_popup_page = popup
    windows = {}  # stable Desktop window -> idempotent page binding
    creation = asyncio.Lock()

    async def info(session, token=None):
        return {'success': True, 'page_id': token or session.id, 'active_page_id': session.id,
                'url': session.url, 'title': await session.title(),
                'width': session.width, 'height': session.height, 'xpra': True,
                'popups': [{'page_id': p.id, 'url': p.url} for p in pending_popups.values() if p.id in engine.pages]}

    async def dispatch(method, args):
        a = dict(args or {})
        token = str(a.get('page_id') or '')
        if method == 'browser_popup_ack':
            for page in a.get('page_ids', []): pending_popups.pop(page, None)
            return {'success': True}
        if method in ('desktop_read', 'desktop_act', 'desktop_native_screenshot'):
            from .native_targets import window_targets
            from .native_control import NativeWindowController
            wid = a['window_id']
            parent = wid.split('::native:', 1)[0]
            if ctx.app_id == 'qupath':
                status = await engine.native_apps().status(parent)
                if not status.get('running'):
                    raise RuntimeError('The native app is no longer running')
                window_class = status['window_class']
                state = await engine.native_apps().read(parent) if method == 'desktop_read' else {}
            else:
                from .browser import PAGE_CLASS_PREFIX
                page = token or (windows.get(parent) or {}).get('page_id')
                if not page:
                    raise ValueError('The window has no Browser page binding')
                owned = engine.window_binding(page)
                window_class = owned.window_class
                session = await engine.window_page(page)
                state = await info(session, page)
            targets = await asyncio.to_thread(window_targets, engine, parent, window_class)
            target = next((t for t in targets if t['window_id'] == wid), None)
            if not target:
                raise ValueError('Native child is no longer owned by this window')
            public = [{k: v for k, v in t.items() if k != 'xid' and not k.startswith('_')} for t in targets]
            if method == 'desktop_read':
                return {'success': True, 'result': {**state, 'window_id': wid, 'native_windows': public}}
            controller = NativeWindowController(engine)
            if method == 'desktop_act':
                return await controller.act(target['xid'], a['actions'],
                    allowed_xids={t['xid'] for t in targets}, focus_parent_xids=target.get('_focus_parent_xids', ()))
            shot = await controller.screenshot(target['xid'])
            return {'success': True, **shot, 'native_windows': public}
        if method == 'browser_ui_page':
            async with creation:
                if token:
                    session = await engine.window_page(token, require_visible=False)
                    return await info(session, token)
                wid = str(a.get('window_id') or '')
                operation = str(a.get('operation_id') or '')
                previous = windows.get(wid) if wid else None
                if previous and (not operation or operation == previous['operation_id']):
                    session = await engine.window_page(previous['page_id'], require_visible=False)
                    return {**await info(session, previous['page_id']), 'binding': previous}
                if previous and a.get('expected_page_id', '') != previous['page_id']:
                    raise ValueError('The Browser page binding changed; read the window before retrying')
                session = await engine.open_page(normalize_url(a.get('url', '')), wait_for_load=False)
                binding = {'page_id': session.id, 'operation_id': operation or 'initial',
                           'revision': (previous or {}).get('revision', 0) + 1}
                if wid:
                    windows[wid] = binding
                return {**await info(session), 'binding': binding}
        if method == 'browser_pages':
            return {'success': True, 'pages': [await info(s) for s in engine.pages.values()]}
        if method == 'browser_ui_key':
            return {'success': True, 'sent': await engine.send_keys(a.get('events', []))}
        if method == 'browser_clear_data':
            await engine.clear_data()
            return {'success': True}
        if method.startswith('native_'):
            if ctx.app_id != 'qupath':
                raise ValueError('This instance does not own native application sessions')
            driver = engine.native_apps()
            session_id = a.get('native_session_id', '')
            if method == 'native_ui_launch':
                if a.get('app_id', ctx.app_id) != ctx.app_id:
                    raise ValueError('Native driver belongs to a different App')
                value = await driver.launch(ctx.app_id, session_id, a.get('path', ''),
                                           a.get('width', 1200), a.get('height', 800))
            elif method == 'native_ui_status': value = await driver.status(session_id)
            elif method == 'native_ui_close': value = await driver.close(session_id)
            elif method == 'native_read': value = await driver.read(session_id, annotation_limit=a.get('annotation_limit', 200))
            elif method == 'native_ui_call':
                value = await driver.call(session_id, a['action'], a.get('args', {}))
                return {'success': value.get('success', True) if isinstance(value, dict) else True, 'result': value}
            else: raise ValueError('Unknown native method')
            return {'success': True, **value}
        if ctx.app_id != 'browser':
            raise ValueError('This instance does not own Browser pages')
        if method == 'browser_ui_close':
            await engine.close_window(token)
            return {'success': True}
        if method == 'browser_ui_stage':
            return {'success': True, **await engine.stage_page(token, a.get('width') or 1280,
                a.get('height') or 800, a.get('fb_width') or 0, a.get('fb_height') or 0)}
        if method == 'browser_ui_unstage':
            await engine.unstage_page(token)
            return {'success': True}
        if method == 'browser_ui_focus':
            await engine.focus_stage(token)
            return {'success': True}
        session = await engine.window_page(token) if token else engine.latest()
        if method in ('browser_goto', 'browser_ui_nav'):
            await engine.navigate(session.id, a.get('op', 'goto'), a.get('url', ''))
        elif method == 'browser_read':
            snapshot = await session.page.evaluate(BROWSER_SNAPSHOT_JS, {'textLimit': READ_LIMIT, 'elementLimit': ELEMENT_LIMIT})
            return {**await info(session, token), **snapshot}
        elif method == 'browser_click': await session.page.click(a['selector'], timeout=5000)
        elif method == 'browser_type':
            await session.page.fill(a['selector'], a['text'], timeout=5000)
            if a.get('submit'): await session.page.press(a['selector'], 'Enter')
        elif method == 'browser_act': await engine.dispatch(session.id, input_events(a['actions']))
        elif method == 'browser_scroll':
            where = a.get('to', '')
            if where not in ('', 'top', 'bottom'): raise ValueError('to must be top or bottom')
            events = [{'t': 'scroll', 'y': 0 if where == 'top' else 10**7}] if where else [
                {'t': 'wheel', 'dx': a.get('dx', 0), 'dy': a.get('dy', 0), 'x': 0, 'y': 0}]
            await engine.dispatch(session.id, events)
        elif method == 'browser_screenshot':
            data = await session.page.screenshot(type='jpeg', quality=80, scale='css')
            return {**await info(session, token), 'image_base64': base64.b64encode(data).decode()}
        elif method == 'browser_close': await engine.close_page(session.id)
        else: raise ValueError('Unknown Browser method')
        return await info(session, token)

    methods = ['browser_popup_ack', 'desktop_read', 'desktop_act', 'desktop_native_screenshot', 'browser_ui_page', 'browser_ui_nav', 'browser_ui_close', 'browser_ui_stage',
        'browser_ui_focus', 'browser_ui_key', 'browser_ui_unstage', 'browser_clear_data',
        'browser_goto', 'browser_read', 'browser_click', 'browser_type', 'browser_act',
        'browser_scroll', 'browser_screenshot', 'browser_pages', 'browser_close',
        'native_ui_launch', 'native_ui_status', 'native_ui_close', 'native_ui_call', 'native_read']
    for name in methods:
        def wrap(name):
            async def call(**args):
                return await engine.call(dispatch(name, args))
            call.__name__ = name
            return call
        ctx.method(wrap(name))
    # Safe normal close must happen inside the native app before Fleet can stop
    # its display. A save/cancel dialog keeps its lease and never gets killed.
    async def before_stop():
        if ctx.app_id == 'qupath':
            for session in engine.native_apps().sessions.values():
                if session.process.poll() is None:
                    raise RuntimeError('Close QuPath and finish any Save/Cancel dialog before stopping its backend')
    ctx.before_stop = before_stop

    @ctx.on_cleanup
    async def cleanup():
        try:
            await engine.call(engine.shutdown())
        finally:
            lock.close()
