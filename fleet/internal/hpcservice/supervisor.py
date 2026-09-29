"""Ephemeral service supervisor inside one Slurm step; stdlib only, no listener except the App."""
import base64
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import threading
import time

send_lock = threading.Lock()
sockets_lock = threading.Lock()
sockets = {}
credits = {}
last_ping = time.monotonic()
stop = threading.Event()
child = None


def send(kind, **fields):
    with send_lock:
        data = (json.dumps(dict(type=kind, **fields), separators=(',', ':')) + '\n').encode()
        while data:
            data = data[os.write(1, data):]


def close_stream(key):
    with sockets_lock:
        conn = sockets.pop(key, None)
        credits.pop(key, None)
    if conn:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()


def pump(key, conn, window):
    try:
        while not stop.is_set():
            if not window.acquire(timeout=1):
                with sockets_lock:
                    if key not in sockets:
                        break
                continue
            try:
                data = conn.recv(16384)
            except socket.timeout:
                window.release()
                continue
            if not data:
                break
            send('data', id=key, data=base64.b64encode(data).decode())
    except (OSError, BrokenPipeError):
        pass
    finally:
        send('close', id=key)
        close_stream(key)


def receive(port):
    global last_ping
    pending = b''
    try:
        while not stop.is_set():
            while b'\n' not in pending:
                data = os.read(0, 16384)
                if not data:
                    return
                pending += data
                if len(pending) > 100000:
                    return
            line, pending = pending.split(b'\n', 1)
            q = json.loads(line)
            kind, key = q.get('type'), q.get('id', '')
            if kind == 'ping':
                last_ping = time.monotonic()
            elif kind == 'stop':
                break
            elif kind == 'open':
                with sockets_lock:
                    available = len(sockets) < 16 and key not in sockets
                if not available or not isinstance(key, str) or len(key) > 64:
                    send('close', id=key)
                    continue
                try:
                    conn = socket.create_connection(('127.0.0.1', port), timeout=5)
                    with sockets_lock:
                        sockets[key] = conn
                        window = threading.BoundedSemaphore(8)
                        credits[key] = window
                    send('opened', id=key)
                    threading.Thread(target=pump, args=(key, conn, window), daemon=True).start()
                except OSError:
                    send('close', id=key)
            elif kind == 'data':
                data = base64.b64decode(q['data'], validate=True)
                if len(data) > 16384:
                    raise ValueError('oversized frame')
                with sockets_lock:
                    conn = sockets.get(key)
                if conn:
                    try:
                        conn.sendall(data)
                    except OSError:
                        close_stream(key)
                        send('close', id=key)
            elif kind == 'ack':
                with sockets_lock:
                    window = credits.get(key)
                if window:
                    window.release()
            elif kind == 'close':
                close_stream(key)
            else:
                raise ValueError('invalid frame')
    except Exception:
        pass
    finally:
        stop.set()


def logs(pipe):
    remaining = 32768
    try:
        while True:
            data = os.read(pipe.fileno(), 8192)
            if not data:
                break
            if remaining:
                send('log', data=base64.b64encode(data[:remaining]).decode())
                remaining = max(0, remaining - len(data))
    finally:
        pipe.close()


def main():
    global child
    q = json.loads(base64.b64decode(sys.argv[1]))
    allocation = q['allocation']
    if len(allocation) != 32 or any(c not in '0123456789abcdef' for c in allocation):
        raise ValueError('invalid allocation')
    if os.environ.get('SLURM_JOB_ID') != q['job_id']:
        raise ValueError('not inside requested Slurm allocation')
    root = (pathlib.Path.home() / '.pantheon-fleet' / 'workspaces' / allocation).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cwd = (root / q.get('cwd', '.')).resolve()
    if cwd != root and root not in cwd.parents:
        raise ValueError('cwd escapes allocation workspace')
    if q.get('attach'):
        # Attach to the App already running as the actual batch workload.
        # This transport cannot launch/restart/kill that App or choose a port.
        status = root / '.primary-http.json'
        deadline = time.monotonic() + q['startup_seconds']
        def primary_state():
            with status.open('rb') as f:
                data = f.read(65537)
            if len(data) > 65536:
                raise ValueError('oversized primary App state')
            data = json.loads(data)
            if any(data.get(k) != q[k] for k in ('job_id', 'allocation', 'revision')):
                raise ValueError('primary App identity mismatch')
            return data
        while True:
            try:
                state = primary_state()
                if state['state'] == 'running':
                    port = state['port']
                    if type(port) is not int or not 1 <= port <= 65535:
                        raise ValueError('invalid assigned App port')
                    break
                if state['state'] in ('failed', 'stopped'):
                    raise RuntimeError(state.get('error') or 'primary App has ended')
            except FileNotFoundError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('primary App readiness deadline exceeded')
            time.sleep(0.1)
        threading.Thread(target=receive, args=(port,), daemon=True).start()
        send('ready', hostname=socket.gethostname())
        while not stop.wait(1):
            if time.monotonic() - last_ping > 30:
                raise RuntimeError('connector heartbeat expired')
            if primary_state()['state'] != 'running':
                raise RuntimeError('primary App has ended')
        return
    # Legacy allocation service path, retained for existing supported clusters.
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    argv = [s.replace('${PORT}', str(port)).replace('${HOST}', '127.0.0.1').replace('${WORKSPACE}', str(root)) for s in q['argv']]
    env = dict(os.environ, PORT=str(port), HOST='127.0.0.1', PYTHONUNBUFFERED='1')
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    threading.Thread(target=logs, args=(child.stdout,), daemon=True).start()
    threading.Thread(target=receive, args=(port,), daemon=True).start()
    deadline = time.monotonic() + q['startup_seconds']
    ready = False
    while not stop.wait(0.1):
        if child.poll() is not None:
            raise RuntimeError('service exited with code ' + str(child.returncode))
        if time.monotonic() - last_ping > 30:
            raise RuntimeError('connector heartbeat expired')
        if not ready:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    pass
                send('ready', hostname=socket.gethostname())
                ready = True
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError('service did not bind its assigned loopback port before startup timeout')


def cleanup():
    stop.set()
    if child:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        # Also kill descendants after the parent exits.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    with sockets_lock:
        keys = list(sockets)
    for key in keys:
        close_stream(key)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGHUP, lambda *_: stop.set())
    try:
        main()
    except Exception as exc:
        try:
            send('error', error=str(exc))
        except Exception:
            pass
    finally:
        cleanup()
