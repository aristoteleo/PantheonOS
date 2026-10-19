# Executed as a Slurm job step over the attended SSH session; no daemon installed.
import base64
import json
import os
import pathlib
import platform
import signal
import subprocess
import sys
import tempfile
import threading

LIMIT = 65536


def main(req):
    allocation = req['allocation']
    if len(allocation) != 32 or any(c not in '0123456789abcdef' for c in allocation):
        raise ValueError('invalid allocation')
    if os.environ.get('SLURM_JOB_ID') != req['job_id']:
        raise ValueError('not inside the requested Slurm allocation')
    root = pathlib.Path.home() / '.pantheon-fleet' / 'workspaces' / allocation
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = root.resolve()

    def target(value):
        path = pathlib.Path(value or '.')
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if path != root and root not in path.parents:
            raise ValueError('file path is outside this allocation workspace')
        return path

    op = req['operation']
    if op == 'probe':
        return dict(os='linux', arch=platform.machine(), python=platform.python_version(),
                    root=str(root), hostname=platform.node())
    if op == 'task':
        t = req['task']
        kind = t.get('kind', 'shell')
        if kind not in ('shell', 'python'):
            raise ValueError('unsupported task kind')
        argv = [sys.executable, '-c', t['code']] if kind == 'python' else ['bash', '-c', t['code']]
        env = dict(os.environ)
        for key, value in t.get('env', {}).items():
            if key.startswith('SLURM_') or '\x00' in key + value or '=' in key:
                raise ValueError('invalid task environment')
            env[key] = value
        timeout = min(3600, max(1, t.get('timeout_s') or 60))
        p = subprocess.Popen(argv, cwd=target(t.get('cwd')), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        chunks = [bytearray(), bytearray()]
        truncated = [False, False]

        def drain(pipe, i):
            while True:
                data = pipe.read(8192)
                if not data:
                    break
                remaining = 32768 - len(chunks[i])
                chunks[i].extend(data[:remaining])
                truncated[i] |= len(data) > remaining
            pipe.close()

        def kill(*_):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        old = signal.signal(signal.SIGTERM, kill)
        old_hup = signal.signal(signal.SIGHUP, kill)
        readers = [threading.Thread(target=drain, args=(p.stdout, 0), daemon=True),
                   threading.Thread(target=drain, args=(p.stderr, 1), daemon=True)]
        for reader in readers:
            reader.start()
        error = ''
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            error = 'timeout'
        finally:
            # Descendants must not hold pipes/resources after the task returns.
            kill()
            p.wait()
            for reader in readers:
                reader.join(timeout=2)
            signal.signal(signal.SIGTERM, old)
            signal.signal(signal.SIGHUP, old_hup)
        return dict(task_id=t['task_id'], exit_code=-1 if error else p.returncode,
                    stdout=chunks[0].decode('utf-8', errors='replace'),
                    stderr=chunks[1].decode('utf-8', errors='replace'),
                    error=error, truncated=any(truncated))

    path = target(req.get('path'))
    if op == 'list':
        entries = []
        with os.scandir(path) as scan:
            for ent in scan:
                if len(entries) == 200:
                    return dict(entries=entries, truncated=True)
                stat = ent.stat(follow_symlinks=False)
                entries.append(dict(name=ent.name, size=stat.st_size,
                                    directory=ent.is_dir(follow_symlinks=False), symlink=ent.is_symlink()))
        return dict(entries=entries, truncated=False)
    if op == 'read':
        offset = req.get('offset', 0)
        size = req.get('size', LIMIT)
        if not isinstance(offset, int) or offset < 0 or not isinstance(size, int) or not 1 <= size <= LIMIT:
            raise ValueError('invalid read range')
        with path.open('rb') as f:
            f.seek(offset)
            data = f.read(size)
            eof = not f.read(1)
        return dict(data=base64.b64encode(data).decode(), offset=offset, next_offset=offset+len(data), eof=eof)
    if op == 'write':
        data = base64.b64decode(req.get('data', ''), validate=True)
        if len(data) > LIMIT:
            raise ValueError('write exceeds 64 KiB')
        # Exclusive creation unless the caller explicitly requests replacement.
        if req.get('overwrite', False):
            fd, temp = tempfile.mkstemp(prefix='.fleet-', dir=str(path.parent))
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(data)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        else:
            with path.open('xb') as f:
                f.write(data)
        return dict(size=len(data))
    if op == 'mkdir':
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        return dict(ok=True)
    raise ValueError('unsupported operation')


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        response = main(request)
    except Exception as exc:
        response = dict(error=str(exc))
    print(json.dumps(response, ensure_ascii=True))
