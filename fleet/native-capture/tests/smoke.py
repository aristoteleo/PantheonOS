"""Capture only a synthetic child window; never inspect/capture the user's desktop."""
import io
import json
import queue
import struct
import subprocess
import sys
import threading
import time
from PIL import Image

helper, fixture = sys.argv[1:]
status = json.loads(subprocess.check_output([helper, '--probe']))
print('Probe:', status, flush=True)
if not status.get('available') or not status.get('screen_recording'):
    print('SKIP capture: interactive session/recording permission unavailable', flush=True)
    sys.exit(0)
apps = [subprocess.Popen([fixture]), subprocess.Popen([fixture])]
processes = []
try:
    time.sleep(2)
    def connect(pid):
        process = subprocess.Popen([helper, '--pid', str(pid)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        processes.append(process)
        messages = queue.Queue()
        def read():
            try:
                while True:
                    raw = process.stdout.read(4)
                    if len(raw) != 4: return
                    size, = struct.unpack('>I', raw)
                    if not 1 <= size <= 16777232: raise ValueError('Invalid packet')
                    data = process.stdout.read(size)
                    messages.put(json.loads(data[1:]) if data[0] == 1 else data)
            except Exception as error: messages.put(error)
        threading.Thread(target=read, daemon=True).start()
        def command(op, **args):
            key = str(time.monotonic_ns())
            process.stdin.write((json.dumps({'id': key, 'op': op, **args})+'\n').encode()); process.stdin.flush()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                value = messages.get(timeout=20)
                if isinstance(value, dict) and value.get('id') == key: return value
                if isinstance(value, dict) and value.get('event') == 'fatal': raise RuntimeError(value)
            raise TimeoutError(op)
        return command, messages
    command, messages = connect(apps[0].pid)
    foreign, _ = connect(apps[1].pid)
    owned = command('list')
    assert owned['ok'], owned
    window = next(w for w in owned['windows'] if w['title'] == 'Fleet capture fixture')
    assert all(w['pid'] == apps[0].pid for w in owned['windows'])
    other = next(w for w in foreign('list')['windows'] if w['title'] == 'Fleet capture fixture')
    denied = command('capture', window=other['id'])
    assert not denied['ok'], denied
    captured = command('capture', window=window['id'])
    assert captured['ok'], captured
    deadline = time.monotonic() + 20
    image = None
    while time.monotonic() < deadline:
        data = messages.get(timeout=20)
        if isinstance(data, dict):
            if data.get('event') == 'capture_error': raise RuntimeError(data)
            continue
        assert data[0] == 2
        assert struct.unpack('>Q', data[1:9])[0] == window['id']
        image = Image.open(io.BytesIO(data[9:])).convert('RGB')
        r,g,b = image.getpixel((image.width//2,image.height//2))
        assert g > r + 50 and g > b + 50, (r,g,b)
        break
    assert image is not None
    print('PASS owned-window JPEG:', image.size, 'foreign PID refused', flush=True)
    if sys.platform == 'win32' and status.get('input'):
        result = command('input', window=window['id'], kind='text', text='a')
        assert result['ok'], result
        time.sleep(.2)
        renamed = command('list')
        assert any(w['title'] == 'Fleet capture fixture typed:a' for w in renamed['windows']), renamed
        assert command('resize', window=window['id'], w=640, h=480)['ok']
        time.sleep(.2)
        assert next(w for w in command('list')['windows'] if w['id'] == window['id'])['w'] > 600
        print('PASS native Unicode input and resize', flush=True)
    assert command('uncapture', window=window['id'])['ok']
    if sys.platform == 'win32':
        assert command('close', window=window['id'])['ok']
        apps[0].wait(timeout=5)
        assert apps[1].poll() is None
        print('PASS normal close preserves unrelated process', flush=True)
finally:
    for process in processes:
        process.stdin.close()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    for app in apps:
        if app.poll() is None: app.terminate(); app.wait(timeout=5)
