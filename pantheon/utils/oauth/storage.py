"""Atomic OAuth records and serialized local credential operations.

The stable sidecar coordinates independent managers/processes on one filesystem.
It is not a distributed lock or a completed credential ownership transfer. A
migration must still exclude old/non-cooperating writers before moving tokens.
"""
from contextlib import contextmanager, nullcontext
from functools import wraps
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from weakref import WeakValueDictionary

MAX_BYTES = 2 * 1024 * 1024
_states = WeakValueDictionary()
_states_lock = threading.Lock()


class CredentialStorageError(RuntimeError):
    pass


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.depth = 0


def _regular(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or os.name != 'nt' and info.st_uid != os.geteuid()):
        raise CredentialStorageError('OAuth storage requires owned regular files')


def _open(path, flags):
    fd = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
                 | getattr(os, 'O_NONBLOCK', 0), 0o600)
    try:
        _regular(fd)
        if path.is_symlink():
            raise CredentialStorageError('OAuth storage cannot use symbolic links')
        return fd
    except BaseException:
        os.close(fd)
        raise


class OAuthStorage:
    def __init__(self, path, *, ownership_root=None):
        path = Path(path).absolute()
        self.path = path.parent.resolve()/path.name
        self.ownership_root = Path(ownership_root).resolve() if ownership_root is not None else None
        if self.ownership_root is not None and not self.path.is_relative_to(self.ownership_root):
            raise CredentialStorageError('OAuth credentials must belong to their declared data root')
        self._pid = os.getpid()
        key = (self._pid, str(self.path))
        with _states_lock:
            state = _states.get(key)
            if state is None:
                state = _states[key] = _State()
        self._state = state

    @contextmanager
    def transaction(self, timeout=90):
        """Serialize refresh/read/write as one operation, including nested calls."""
        # An inherited RLock/depth is not proof of ownership in the child. OAuth
        # workers must exec/spawn, rather than reuse a live manager after fork.
        if os.getpid() != self._pid:
            raise CredentialStorageError('OAuth managers cannot be reused after fork; spawn a fresh worker')
        state = self._state
        deadline = time.monotonic() + timeout
        if not state.lock.acquire(timeout=timeout):
            raise CredentialStorageError('OAuth credentials are busy; retry after the current operation')
        fd = None
        try:
            if state.depth == 0:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = _open(self.path.with_name(self.path.name + '.lock'), os.O_CREAT | os.O_RDWR)
                if not os.fstat(fd).st_size:
                    os.write(fd, b'\0')
                while True:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            os.lseek(fd, 0, os.SEEK_SET)
                            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise CredentialStorageError('OAuth credentials are busy; retry after the current operation')
                        time.sleep(.025)
            state.depth += 1
            try:
                from ..local_data_ownership import data_access
                # Keep the shared data lease for the whole network refresh, not
                # just its file write. Migration cannot snapshot an old token
                # while an in-flight request rotates it remotely.
                with data_access(self.ownership_root) if self.ownership_root is not None else nullcontext():
                    yield self
            finally:
                state.depth -= 1
        finally:
            if fd is not None:
                os.close(fd)
            state.lock.release()

    def load(self):
        if not self.path.parent.exists():
            return {}
        with self.transaction():
            try:
                fd = _open(self.path, os.O_RDONLY)
            except FileNotFoundError:
                if self.path.is_symlink():
                    raise CredentialStorageError('OAuth storage cannot use symbolic links') from None
                return {}
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            try:
                record = json.loads(raw) if len(raw) <= MAX_BYTES else None
                if not isinstance(record, dict):
                    raise ValueError
                return record
            except (ValueError, UnicodeError):
                raise CredentialStorageError('OAuth credential record is invalid; explicit recovery is required') from None

    def save(self, record):
        if not isinstance(record, dict):
            raise CredentialStorageError('OAuth credential record must be an object')
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2).encode()
        if len(raw) > MAX_BYTES:
            raise CredentialStorageError('OAuth credential record exceeds its storage limit')
        with self.transaction():
            if self.path.exists() or self.path.is_symlink():
                fd = _open(self.path, os.O_RDONLY)
                os.close(fd)
            fd, temporary = tempfile.mkstemp(prefix='.' + self.path.name + '.', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                if os.name != 'nt':
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return record


def serialized(method):
    """Provider operations share the same transaction as their atomic writes."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._auth_store.transaction():
            return method(self, *args, **kwargs)
    return call
