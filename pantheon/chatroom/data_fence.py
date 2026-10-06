"""Cooperative local fencing for legacy Agent data migration.

Updated legacy runtimes hold shared leases until successful drain. A migrator
holds exclusive leases and leaves durable ownership markers, including on failure
or process death. These locks do not fence older binaries, external editors, or
replicas on filesystems without shared locking; the deployment cutover must stop
and exclude those writers separately. Nothing here imports or deletes user data.
"""
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import threading

from pantheon.utils.local_data_ownership import (
    LOCK_NAME, MARKER_NAME, CONTROL_FILES, DataFencedError,
    _root, _open, _Lock, _read_marker, _sync_directory,
)


def _write_marker(root, value):
    # No replacement: the exclusive lease already establishes ownership. A
    # partial write fails closed; recovery must not mistake it for an idle root.
    fd = _open(root / MARKER_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(root)


class LegacyDataLease:
    """Shared lifetime leases, including stores discovered after startup."""
    def __init__(self):
        self._locks = {}
        self._mutex = threading.RLock()
        self._closed = False

    def acquire(self, path):
        with self._mutex:
            if self._closed:
                raise DataFencedError('Legacy Agent data lease is closed')
            raw = str(path)
            if raw in self._locks:
                return
            root = _root(path)
            key = str(root)
            if key not in self._locks:
                lease = _Lock(root, exclusive=False)
                try:
                    if _read_marker(root) is not None:
                        raise DataFencedError('Legacy Agent data is reserved for migration; use its Agent App or explicitly roll back')
                except BaseException:
                    lease.close()
                    raise
                self._locks[key] = lease
            self._locks[raw] = self._locks[key]

    def close(self):
        with self._mutex:
            self._closed = True
            for lease in set(self._locks.values()):
                lease.close()
            self._locks.clear()


def fence_identity(roots, *, operation, target, namespace):
    """Describe an exact fence without taking leases or marking source data."""
    for value in (operation, namespace):
        if not isinstance(value, str) or not 0 < len(value) <= 256 or any(ord(c) < 32 for c in value):
            raise ValueError('Migration operation and namespace must be explicit identifiers')
    if not isinstance(target, (str, Path)) or not Path(target).is_absolute():
        raise ValueError('Migration target must be absolute')
    # Preserve the original Path ordering used by published fence identities;
    # sorting strings differs for nested paths beside punctuation-prefixed peers.
    roots = [str(path) for path in sorted({Path(path).resolve() for path in roots})]
    if not roots:
        raise ValueError('Migration requires source roots')
    value = dict(protocol=1, operation=operation, namespace=namespace,
                 target=str(Path(target).resolve()), roots=roots)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    if len(raw) > 60 * 1024:
        raise ValueError('Migration root set is too large')
    return {**value, 'sha256': sha256(raw).hexdigest()}


class MigrationFence:
    """Exclusive source leases and immutable, durable migration ownership.

    close() never removes markers. The same operation/root set/target/namespace
    can reacquire after interruption. release_sources() is an explicit rollback
    step: its caller must have stopped/fenced the destination Agent beforehand.
    This primitive is not a release coordinator or a complete rollback protocol.
    """
    def __init__(self, roots, *, operation, target, namespace):
        roots = list(roots)
        self.identity = fence_identity(roots, operation=operation, target=target, namespace=namespace)
        roots = sorted({_root(path) for path in roots})
        self.roots = roots
        self._stack = ExitStack()
        self._closed = False
        try:
            # Obtain ALL leases and validate ALL owners before marking any root.
            for root in roots:
                self._stack.callback(_Lock(root, exclusive=True).close)
            markers = [_read_marker(root) for root in roots]
            if any(marker is not None and marker != self.identity for marker in markers):
                raise DataFencedError('Migration roots already belong to a different operation or destination')
            for root, marker in zip(roots, markers):
                if marker is None:
                    _write_marker(root, self.identity)
        except BaseException:
            self.close()
            raise

    def assert_owned(self):
        if self._closed:
            raise DataFencedError('Migration fence is closed')
        if any(_read_marker(root) != self.identity for root in self.roots):
            raise DataFencedError('Migration ownership changed; refusing to access source data')

    def release_sources(self):
        self.assert_owned()
        for root in self.roots:
            (root / MARKER_NAME).unlink()
            _sync_directory(root)
        self.close()

    def close(self):
        self._closed = True
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
