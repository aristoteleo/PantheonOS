"""Private, atomically checkpointed owner journals on one local POSIX host.

The sidecar lock is acquired by each coordinator. This storage primitive does
not provide distributed fencing or permission to act on recorded resources.
"""
import asyncio
import json
import os
from pathlib import Path
import stat
import tempfile


class OwnerJournalError(RuntimeError):
    pass


class OwnerJournal:
    error_type = OwnerJournalError

    def __init__(self, root: Path):
        self.root = Path(root)

    def _private(self, path, directory=False):
        info = path.lstat()
        if (os.name != 'posix' or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))):
            raise self.error_type('Dependency attempt storage must be private to the platform owner')

    def _write(self, path, value):
        raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        if len(raw) > 256 * 1024:
            raise self.error_type('Dependency attempt exceeds storage limit')
        fd, name = tempfile.mkstemp(prefix=path.stem + '-', suffix='.tmp', dir=path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            tmp.unlink(missing_ok=True)

    async def _checkpoint(self, path, record):
        # Do not release the attempt lock while a cancelled observer's storage
        # thread is still publishing. A retry must see the durable checkpoint.
        task = asyncio.get_running_loop().run_in_executor(None, self._write, path, record)
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
