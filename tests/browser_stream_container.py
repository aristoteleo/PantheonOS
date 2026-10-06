"""Exercise an immutable Browser package with real Chromium/Xpra, without Agent.

Runs only inside the opt-in disposable Linux test container. Browser binaries
and OS libraries are pre-provisioned by its image; this does not test installation
or replace the Desktop/Fleet end-to-end acceptance gate.
"""
import base64
from contextlib import suppress
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import unquote, urlsplit

import psutil
from PIL import Image


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    package, results = Path('/package'), Path('/results')
    # The image supplies the exact Playwright browser version. The adapter owns
    # this conventional environment path; never use a host browser/profile.
    cache = Path(sys.prefix) / 'browsers'
    if not cache.exists():
        cache.symlink_to('/usr/local/share/ms-playwright', target_is_directory=True)
    state = results / 'state'
    state.mkdir()
    stream = free_port()
    env = {**os.environ, 'HOME': str(state), 'PANTHEON_PORT_HTTP': '0',
           'PANTHEON_PORT_STREAM': str(stream), 'PANTHEON_INSTANCE_GENERATION': '1',
           'BROWSER_XPRA_MODE': 'seamless', 'PANTHEON_TEST_IMPORT_AUDIT': str(results/'imports.log')}
    boot = """import runpy,sys
sys.path.insert(0, '/checks')
from platform_no_agent import install
install()
sys.path.insert(0, '/package/.fleet-runtime')
sys.argv=['/package/.fleet-runtime/host.py','start','--package','/package','--data','/results/state']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path.startswith('/xpra/'):
                file = (Path('/checks') / path.lstrip('/')).resolve()
                if not file.is_relative_to('/checks/xpra') or not file.is_file():
                    self.send_error(404)
                    return
                body = file.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
                return
            label = {'/nav': 'VIEWER_NAVIGATION', '/tab': 'VIEWER_NEW_TAB'}.get(self.path, 'NO_AGENT_BROWSER')
            html = ('<title>Independent Browser</title><body style="background:rgb(19,97,163)"><h1>'
                    + label + '</h1><button id="step" style="position:absolute;left:20px;top:100px;width:80px;height:30px" '
                    'onclick="this.innerText=\'CLICKED\'">STEP</button><input id="text" '
                    'style="position:absolute;left:120px;top:100px;width:200px;height:30px"></body>')
            body = html.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    fixture = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    descendants = []
    viewer_browser = playwright = None
    with (results/'backend.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, '-I', '-c', boot], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 90
            endpoint = state/'backend-endpoint.json'
            while not endpoint.exists():
                assert proc.poll() is None, (results/'backend.log').read_text()[-6000:]
                assert time.monotonic() < deadline, 'Browser host did not become ready'
                time.sleep(.1)
            base = 'http://127.0.0.1:' + str(json.loads(endpoint.read_text())['port'])
            def request(path, value):
                try:
                    with urlopen(Request(base+path, data=json.dumps(value).encode(),
                                         headers={'Content-Type': 'application/json'}), timeout=150) as response:
                        return json.load(response)
                except HTTPError as error:
                    raise AssertionError(error.read().decode()) from error
            def rpc(method, **args):
                response = request('/rpc', {'method': method, 'args': args, 'timeout_s': 140})
                assert response['success'], response
                value = response['result']
                assert value.get('success', True), value
                return value
            page = f'http://127.0.0.1:{fixture.server_port}/'
            first = rpc('browser_ui_page', window_id='test-browser-one', operation_id='first', url=page)
            second = rpc('browser_ui_page', window_id='test-browser-two', operation_id='second', url=page)
            assert first['page_id'] != second['page_id']
            stage = rpc('browser_ui_stage', page_id=first['page_id'], width=900, height=650)
            assert stage['mode'] == 'seamless', stage
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            viewer_browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            viewer = viewer_browser.new_page(viewport={'width': 1600, 'height': 1100})
            viewer_crashes = []
            viewer.on('crash', lambda: viewer_crashes.append('viewer renderer crashed'))
            viewer.goto(page + f'xpra/index.html?server=127.0.0.1&port={stream}&ssl=false&username=' + stage['username'])
            viewer.locator('#password').fill(stage['password'], timeout=20000)
            viewer.locator('#login_connect').click()
            viewer.locator('div.window canvas').first.wait_for(timeout=30000)
            rpc('browser_click', page_id=first['page_id'], selector='#step')
            rpc('browser_type', page_id=first['page_id'], selector='#text', text='independent input')
            read = rpc('browser_read', page_id=first['page_id'])
            assert 'CLICKED' in str(read) and 'independent input' in str(read), read
            shot = rpc('desktop_native_screenshot', window_id='test-browser-one', page_id=first['page_id'])
            pixels = base64.b64decode(shot['data_url'].split(',', 1)[1])
            (results/'browser-native.png').write_bytes(pixels)
            image = Image.open(io.BytesIO(pixels)).convert('RGB')
            assert image.width >= 600 and image.height >= 400
            assert sum(1 for r,g,b in image.getdata() if b > 120 and g > 50 and r < 60) > 50000
            viewer.screenshot(path=str(results/'browser-viewer.png'))
            with urlopen(f'http://127.0.0.1:{stream}/', timeout=10) as response:
                assert b'xpra' in response.read().lower()
            rpc('browser_ui_close', page_id=first['page_id'])
            pages = rpc('browser_pages')['pages']
            assert first['page_id'] not in {p['page_id'] for p in pages}
            assert second['page_id'] in {p['page_id'] for p in pages}
            second_stage = rpc('browser_ui_stage', page_id=second['page_id'], width=1000, height=700)
            expression = "(cls) => Object.values(client.id_to_window).find(w => (w.metadata['class-instance'] || [])[0] === cls)?.canvas"
            viewer.wait_for_function(expression, arg=second_stage['window_class'])
            canvas = viewer.evaluate_handle(expression, second_stage['window_class']).as_element()
            assert canvas is not None
            canvas.click(position={'x': 400, 'y': 400})
            viewer.evaluate("""() => {
                window.inputTrace = [];
                for (const type of ['keydown', 'keyup']) document.addEventListener(type,
                    e => window.inputTrace.push({type, key: e.key, code: e.code,
                        ctrl: e.ctrlKey, shift: e.shiftKey}), true);
                const send = client.send.bind(client);
                client.send = packet => {
                    if (String(packet[0]).includes('key')) window.inputTrace.push({packet});
                    return send(packet);
                };
            }""")
            def wait_text(text):
                deadline = time.monotonic() + 15
                observed = None
                while True:
                    try:
                        observed = rpc('browser_read', page_id=second['page_id'])
                        if text in str(observed):
                            return
                    except AssertionError as error:
                        # Read-only observation may race the real navigation;
                        # never replay input or ignore unrelated backend errors.
                        if not any(message in str(error) for message in (
                            'Execution context was destroyed',
                            'The requested browser window has no uniquely selected tab',
                            'The native browser tab is not yet available to the controller',
                        )):
                            raise
                        observed = str(error)
                    assert time.monotonic() < deadline, 'Viewer keyboard did not reach Browser: ' + text + ': ' + str(observed)
                    viewer.wait_for_timeout(100)
            def type_url(value):
                # Playwright.type(':') emits a colon key without physical Shift.
                # Xpra forwards hardware keycodes, so model the real chord.
                for char in value:
                    viewer.keyboard.press('Shift+Semicolon' if char == ':' else char, delay=25)
                    viewer.wait_for_timeout(50)
            # Locate the page origin from actual captured pixels, then click
            # the known fixture controls through the streamed canvas. No CDP
            # input substitutes for the viewer's pointer/keyboard path.
            shot = rpc('desktop_native_screenshot', window_id='test-browser-two', page_id=second['page_id'])
            native = Image.open(io.BytesIO(base64.b64decode(shot['data_url'].split(',', 1)[1]))).convert('RGB')
            top = next(y for y in range(native.height) if native.getpixel((10, y)) == (19, 97, 163))
            box = canvas.bounding_box()
            def click_content(x, y):
                canvas.click(position={'x': x * box['width'] / native.width,
                                       'y': (top + y) * box['height'] / native.height})
            click_content(40, 115)
            wait_text('CLICKED')
            click_content(150, 115)
            viewer.keyboard.down('x')
            # No browser-generated repeat events are sent during this hold.
            # The X server must not invent characters while key-up is delayed.
            viewer.wait_for_timeout(1200)
            viewer.keyboard.up('x')
            wait_text("'value': 'x'")
            field = next(e for e in rpc('browser_read', page_id=second['page_id'])['elements'] if e.get('tag') == 'input')
            assert field['value'] == 'x', field
            # Native toolbar and tabs are real Xpra input, not DOM RPC substitutes.
            viewer.keyboard.press('Control+l')
            viewer.wait_for_timeout(250)
            type_url(page + 'nav')
            viewer.wait_for_timeout(250)
            viewer.screenshot(path=str(results/'browser-input.png'))
            viewer.keyboard.press('Enter')
            wait_text('VIEWER_NAVIGATION')
            viewer.keyboard.press('Control+t')
            viewer.wait_for_timeout(250)
            type_url(page + 'tab')
            viewer.keyboard.press('Enter')
            wait_text('VIEWER_NEW_TAB')
            viewer.keyboard.press('Control+1')
            wait_text('VIEWER_NAVIGATION')
            viewer.keyboard.press('Control+2')
            wait_text('VIEWER_NEW_TAB')
            viewer.keyboard.press('Control+w')
            wait_text('VIEWER_NAVIGATION')
            viewer.screenshot(path=str(results/'browser-viewer.png'))
            viewer.keyboard.press('Control+w')
            deadline = time.monotonic() + 15
            while second['page_id'] in {p['page_id'] for p in rpc('browser_pages')['pages']}:
                assert time.monotonic() < deadline, 'Native window close did not retire the page'
                viewer.wait_for_timeout(100)
            third = rpc('browser_ui_page', window_id='test-browser-reopened', operation_id='third', url=page)
            rpc('browser_click', page_id=third['page_id'], selector='#step')
            assert 'CLICKED' in str(rpc('browser_read', page_id=third['page_id']))
            descendants = psutil.Process(proc.pid).children(recursive=True)
            assert any('chrome' in p.name().lower() for p in descendants)
            assert request('/_fleet/drain', {})['safe_to_stop']
            proc.terminate()
            assert proc.wait(timeout=45) == 0
            alive = [p.pid for p in descendants if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
            assert not alive, f'Browser cleanup left processes: {alive}'
            assert not viewer_crashes, viewer_crashes
            memory_events = dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())
            assert int(memory_events['oom_kill']) == 0, memory_events
            assert 'imported Agent:' not in (results/'imports.log').read_text()
            (results/'result.json').write_text(json.dumps({'success': True, 'capture': 'real X11 window',
                'checks': ['two windows', 'click/type/read', 'native capture', 'sibling survives close',
                           'viewer pointer and delayed key-up without phantom repeats',
                           'viewer native address bar and tab switching', 'viewer last window close',
                           'reopen after last window', 'drain and process cleanup', 'no Agent imports']}))
            print('PASS independent packaged Browser with real Xpra and Chromium', flush=True)
        finally:
            for file in ('memory.events', 'memory.peak'):
                with suppress(OSError):
                    print(file + ': ' + (Path('/sys/fs/cgroup')/file).read_text(), flush=True)
            if viewer_browser:
                try:
                    (results/'input-trace.json').write_text(json.dumps(viewer.evaluate('window.inputTrace || []')))
                    viewer.screenshot(path=str(results/'browser-viewer-final.png'))
                except Exception as error:
                    # Diagnostic capture must not replace the original failure.
                    print('Viewer diagnostic capture: ' + str(error), flush=True)
                with suppress(Exception):
                    viewer_browser.close()
            fixture.shutdown(); fixture.server_close(); fixture_thread.join(timeout=3)
            if playwright: playwright.stop()
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=45)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)


if __name__ == '__main__':
    main()
