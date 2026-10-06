"""Shared local data ownership locks used by runtimes and credential storage.

The on-disk names retain the existing migration protocol. This module imports no
Agent code. Locks coordinate cooperative processes on a single filesystem only.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat

LOCK_NAME = '.agent-migration.lock'
MARKER_NAME = '.agent-migration.json'
CONTROL_FILES = frozenset({LOCK_NAME, MARKER_NAME})


class DataFencedError(RuntimeError):
    pass


def _root(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('Agent data root must not be a symlink')
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _open(path, flags):
    fd = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0), 0o600)
    try:
        info = os.fstat(fd)
        actual = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino)
                or stat.S_ISLNK(actual.st_mode)):
            raise ValueError('Agent migration control file must be a regular unlinked file')
        return fd
    except BaseException:
        os.close(fd)
        raise


class _Lock:
    def __init__(self, root, *, exclusive):
        self.fd = _open(root / LOCK_NAME, os.O_CREAT | os.O_RDWR)
        try:
            if os.name == 'nt':
                import msvcrt
                # msvcrt locks a byte range; read locks allow legacy peers.
                if not os.fstat(self.fd).st_size:
                    os.write(self.fd, b'\0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BaseException as exc:
            self.close()
            if isinstance(exc, OSError):
                raise DataFencedError('Agent data is in use; stop and drain its writers before migration') from exc
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _read_marker(root):
    try:
        fd = _open(root / MARKER_NAME, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(64 * 1024 + 1)
    try:
        if len(raw) > 64 * 1024:
            raise ValueError('Oversized migration marker')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Invalid migration marker')
        return value
    except (ValueError, UnicodeError) as exc:
        raise DataFencedError('Agent data has an invalid migration marker; explicit recovery is required') from exc


def _sync_directory(root):
    if os.name != 'nt':
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)



@contextmanager
def data_access(root):
    """Hold shared access for one operation; durable migration markers deny it."""
    root = _root(root)
    lock = _Lock(root, exclusive=False)
    try:
        if _read_marker(root) is not None:
            raise DataFencedError('Data is reserved for migration; explicit recovery is required')
        yield
    finally:
        lock.close()
