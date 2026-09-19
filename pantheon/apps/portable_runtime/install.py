"""Reuse node-local Python environments across code-only App updates.

Environments live outside artifact installations so uninstalling an old App
version cannot break a newer one. Only completed, locked installations are
reused; dependency changes and Python upgrades get independent environments.
"""
import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import traceback
import venv
from pathlib import Path

SCHEMA = 1


def remote_filesystem(path):
    """Detect Linux network mounts, including cloud volumes exposed over 9p."""
    if sys.platform != 'linux':
        return False
    try:
        target = Path(path).resolve()
        mounts = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields, filesystem = line.split(' - ', 1)
            mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields.split()[4]))
            if target.is_relative_to(mount):
                mounts.append((len(mount.parts), filesystem.split()[0]))
        kind = max(mounts, default=(0, ''))[1]
        return kind in {'9p', 'nfs', 'nfs4', 'cifs', 'smb3', 'ceph'} or kind.startswith('fuse')
    except (OSError, ValueError, IndexError):
        return False


def dependency_cache(install):
    node = install.parent.parent.resolve()
    if not remote_filesystem(node):
        return node / 'python-environments'
    # App data stays durable. Rebuildable dependencies belong on node-local
    # disk: importing thousands of files over a cold cloud mount can time out.
    base = Path(tempfile.gettempdir()) / f'pantheon-fleet-python-{os.getuid()}'
    if remote_filesystem(base):
        raise RuntimeError('Python dependencies need a node-local temporary directory')
    base.mkdir(mode=0o700, exist_ok=True)
    if base.is_symlink() or base.stat().st_uid != os.getuid():
        raise RuntimeError('Unsafe node-local Python cache directory')
    return base / hashlib.sha256(str(node).encode()).hexdigest()


def local_interpreter():
    """Do not keep importing the standard library from a cloud conda prefix."""
    if not remote_filesystem(sys.base_prefix):
        return None
    for directory in os.get_exec_path():
        for name in ('python3', 'python'):
            candidate = Path(directory) / name
            if not candidate.is_file() or remote_filesystem(candidate):
                continue
            try:
                result = subprocess.run([str(candidate), '-I', '-c',
                    'import json,sys; print(json.dumps([list(sys.version_info[:2]),sys.base_prefix]))'],
                    capture_output=True, text=True, check=True, timeout=5)
                version, prefix = json.loads(result.stdout)
                if version >= [3, 10] and not remote_filesystem(prefix):
                    return str(candidate)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
    raise RuntimeError('A node-local Python 3.10 or newer is required for this App')


def environment_key(package, install, requirements):
    manifest = next(package / n for n in ('app.json', 'atrium.json') if (package / n).is_file())
    specification = requirements.read_text() if requirements else ''
    # Reuse only index requirements across revisions. Includes, editable
    # projects, URLs, wheel paths and pip options can depend on other App files;
    # keep those isolated per artifact instead of guessing their dependencies.
    simple = all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.\[\],<>=!~* ;"\'()-]*', line.strip())
                 and not re.search(r'\.(whl|zip|tar|gz|bz2|xz)(\s|$)', line)
                 for line in specification.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    interpreter = Path(sys.executable).resolve()
    identity = {
        'schema': SCHEMA,
        'app': json.loads(manifest.read_text())['id'],
        'requirements': specification,
        'python': sys.version,
        'abi': sysconfig.get_config_var('SOABI'),
        'platform': sys.platform,
        'machine': platform.machine(),
        'interpreter': str(interpreter),
        'interpreter_mtime': interpreter.stat().st_mtime_ns,
        'artifact': None if simple else str(install.resolve()),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


@contextlib.contextmanager
def environment_lock(path, timeout=540):
    """OS-owned lock: releases on crashes, works on Windows without symlinks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'\0')
            lock.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another installation is still preparing these dependencies')
                time.sleep(.1)
        try:
            yield
        finally:
            if sys.platform == 'win32':
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_UN)


def prepare(package, install, log):
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required on this node')
    requirements = next((package / n for n in ('backend/requirements.txt', 'requirements.txt')
                         if (package / n).is_file()), None)
    key = environment_key(package, install, requirements)
    # Fleet: <node>/installations/<artifact> and <node>/python-environments.
    cache = dependency_cache(install)
    root = cache / key
    python = root / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    marker = root / '.fleet-ready.json'
    with environment_lock(cache / (key + '.lock')):
        reused = marker.is_file() and python.is_file()
        if reused:
            if json.loads(marker.read_text()) != {'schema': SCHEMA, 'key': key}:
                raise RuntimeError(f'Invalid dependency cache marker: {marker}')
            print('Reusing installed Python dependencies', file=log, flush=True)
        else:
            # Interrupted installs never become ready. Build in the final path:
            # moving a venv afterwards breaks console-script interpreter paths.
            if root.exists():
                shutil.rmtree(root)
            print('Preparing new Python dependency environment', file=log, flush=True)
            venv.EnvBuilder(with_pip=True, symlinks=sys.platform != 'win32').create(root)
            if requirements:
                # App HOME is private. Share the node's pip download cache
                # explicitly rather than re-downloading wheels each revision.
                subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                                '--cache-dir', str(cache / 'downloads'), '-r', str(requirements)],
                               cwd=package, check=True, stdout=log, stderr=log)
            subprocess.run([str(python), '-I', '-m', 'pip', 'check'],
                           cwd=root, check=True, stdout=log, stderr=log)
        subprocess.run([str(python), '-I', '-c', 'import sys; assert sys.version_info >= (3, 10)'],
                       check=True, stdout=log, stderr=log, timeout=15)
        if not reused:
            pending_marker = marker.with_suffix('.tmp')
            pending_marker.write_text(json.dumps({'schema': SCHEMA, 'key': key}))
            pending_marker.replace(marker)
    # Atomic binding; no symlinks/admin privileges needed on Windows.
    binding = install / 'python-environment.json'
    # Preserve the venv executable path, NOT the system binary it symlinks to.
    # Different scopes can run before_start concurrently for the same artifact.
    with tempfile.NamedTemporaryFile(mode='w', dir=install, prefix='python-environment-', delete=False) as stream:
        pending = Path(stream.name)
        json.dump({'schema': SCHEMA, 'key': key, 'python': str(python.absolute())}, stream)
    try:
        pending.replace(binding)
    finally:
        pending.unlink(missing_ok=True)
    return reused


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', type=Path, required=True)
    ap.add_argument('--install', type=Path, required=True)
    args = ap.parse_args()
    args.install.mkdir(parents=True, exist_ok=True)
    log_path = args.install / 'dependencies.log'
    try:
        interpreter = local_interpreter()
        if interpreter:
            os.execv(interpreter, [interpreter, '-I', __file__, *sys.argv[1:]])
        with log_path.open('w') as log, contextlib.redirect_stderr(log):
            reused = prepare(args.package.resolve(), args.install.resolve(), log)
    except Exception:
        with log_path.open('a') as log:
            traceback.print_exc(file=log)
        print(json.dumps({'status': 'failed', 'message':
            f'Could not prepare Python dependencies on this node. Details: {log_path}'}))
        return
    print(json.dumps({'status': 'succeeded', 'message':
        'Reused installed Python dependencies on this node' if reused else
        'Python dependencies installed and cached on this node'}))


if __name__ == '__main__':
    main()
