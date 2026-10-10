"""Short-lived registry file locks shared by cooperating local processes."""

from contextlib import contextmanager
import os
import sys
import time


@contextmanager
def registry_lock(path, timeout=5, *, retain_in_children=False):
    """Lock a stable sidecar, never the JSON inode replaced by atomic writes.

    This coordinates processes on one host/filesystem, not independent replicas
    of a cloud volume. Such replicas still need a single registry owner.

    POSIX owners may explicitly pass the yielded file descriptor to owned child
    processes. With retain_in_children, close our descriptor without LOCK_UN:
    the shared open-file-description lock then survives until the last child
    closes it, including when the original owner dies. Descriptors are not made
    inheritable here; the caller must select children through pass_fds.
    """
    if retain_in_children and sys.platform == 'win32':
        raise ValueError('Inherited registry locks require POSIX')
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
            yield lock
        finally:
            if sys.platform == 'win32':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            elif not retain_in_children:
                fcntl.flock(lock, fcntl.LOCK_UN)
