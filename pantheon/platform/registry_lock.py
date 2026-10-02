"""Short-lived registry file locks shared by cooperating local processes."""

from contextlib import contextmanager
import os
import sys
import time


@contextmanager
def registry_lock(path, timeout=5):
    """Lock a stable sidecar, never the JSON inode replaced by atomic writes.

    This coordinates processes on one host/filesystem, not independent replicas
    of a cloud volume. Such replicas still need a single registry owner.
    """
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
                    raise TimeoutError(f'Project registry is busy: {path}')
                time.sleep(.05)
        try:
            yield
        finally:
            if sys.platform == 'win32':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
