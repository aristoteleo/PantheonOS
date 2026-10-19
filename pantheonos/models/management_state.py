"""App-owned model deployment plans and background engine operations.

Closing a management App drains its local operations. It does not stop deployed
models or billable nodes; those remain durable services managed by Fleet/Hub.
A cancelled remote RPC has an uncertain outcome, reconciled by the existing
status path on reopen rather than replayed here.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import stat
import tempfile


class ManagementState:
    def __init__(self, root, controller):
        root = Path(root)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError('Model management needs an existing private App state directory')
        if root.stat().st_mode & 0o077:
            raise ValueError('Model management state must be private')
        if not callable(getattr(controller, 'request', None)):
            raise ValueError('Model management needs an explicit Controller client')
        self.root, self.controller = root, controller
        self._tasks = {'modal': {}, 'deploy': {}}
        self._closing = False
        self._drain = None

    def _open(self):
        if self._closing:
            raise RuntimeError('Model management is closing')

    def engine_tasks(self, kind):
        self._open()
        return self._tasks[kind]

    async def controller_request(self, path, body):
        self._open()
        return await self.controller.request(path, body)

    def _path(self, deployment_id):
        if not isinstance(deployment_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', deployment_id):
            raise ValueError('Invalid deployment id')
        return self.root / (deployment_id + '.json')

    def save_plan(self, plan):
        self._open()
        path = self._path(plan['deployment_id'])
        data = json.dumps(plan, sort_keys=True, allow_nan=False).encode()
        if len(data) > 1024 * 1024:
            raise ValueError('Model deployment plan is too large')
        if path.is_symlink():
            raise ValueError('Model deployment plan must be a regular private file')
        fd, name = tempfile.mkstemp(prefix='.plan-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def load_plan(self, deployment_id):
        self._open()
        path = self._path(deployment_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
                raise ValueError('Model deployment plan must be a bounded private file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError('Model deployment plan is too large')
            plan = json.loads(data)
            if not isinstance(plan, dict) or plan.get('deployment_id') != deployment_id:
                raise ValueError('Model deployment plan identity does not match')
            return plan
        finally:
            os.close(fd)

    async def _join(self):
        tasks = {task for group in self._tasks.values() for task in group.values()}
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for group in self._tasks.values():
            group.clear()

    async def close(self):
        self._closing = True
        if self._drain is None:
            self._drain = asyncio.create_task(self._join())
        # Caller cancellation cannot abandon the workers or permit connections
        # to close underneath them. A later close can observe the same drain.
        cancelled = False
        while not self._drain.done():
            try:
                await asyncio.shield(self._drain)
            except asyncio.CancelledError:
                cancelled = True
        self._drain.result()
        if cancelled:
            raise asyncio.CancelledError
