"""Run the requested HTTP App as the batch job's real workload (stdlib only)."""
import base64
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time


def main():
    q = json.loads(base64.b64decode(sys.argv[1]))
    job = os.environ.get('SLURM_JOB_ID', '')
    if not job.isdecimal():
        raise RuntimeError('HTTP App must run inside a Slurm job')
    allocation, spec = q['allocation'], q['service']
    if len(allocation) != 32 or any(c not in '0123456789abcdef' for c in allocation):
        raise ValueError('invalid allocation')
    root = pathlib.Path.home() / '.pantheon-fleet' / 'workspaces' / allocation
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = root.resolve()
    cwd = (root / spec['cwd']).resolve()
    if cwd != root and root not in cwd.parents:
        raise ValueError('working directory escapes allocation')
    status = root / '.primary-http.json'
    def record(state, **extra):
        data = dict(state=state, job_id=job, allocation=allocation, revision=q['revision'], **extra)
        tmp = root / '.primary-http.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, status)
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    argv = [a.replace('${PORT}', str(port)).replace('${HOST}', '127.0.0.1').replace('${WORKSPACE}', str(root)) for a in spec['argv']]
    env = dict(os.environ, HOST='127.0.0.1', PORT=str(port), PYTHONUNBUFFERED='1')
    record('starting')
    child = None
    try:
        # Normal App logs go to Slurm stdout; the attachment transport never
        # shares this stream. The child belongs to the same Slurm job cgroup.
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL)
        def terminate(*_):
            if child.poll() is None:
                child.terminate()
        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        deadline = time.monotonic() + spec['startup_seconds']
        while child.poll() is None:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    pass
                record('running', port=port, pid=child.pid, hostname=socket.gethostname())
                print('Pantheon HTTP App ready on ' + socket.gethostname(), flush=True)
                code = child.wait()
                record('stopped', exit_code=code)
                return code
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('App did not bind its assigned loopback port before startup timeout')
                time.sleep(0.1)
        raise RuntimeError('App exited before readiness: ' + str(child.returncode))
    except BaseException as exc:
        record('failed', error=str(exc)[:2000])
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise

if __name__ == '__main__':
    sys.exit(main())
