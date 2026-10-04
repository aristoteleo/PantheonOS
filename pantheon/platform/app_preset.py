"""Explicit, bounded startup of an owner-provided ordinary App deployment.

The existing coordinator owns every durable operation and recovery identity.
This driver only advances pending checkpoints; it never heals failed Apps,
changes recipes, replays tools, or makes platform readiness depend on an App.
"""
import asyncio
import json
import os
import stat
import time

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment import deployment_recipe


def read_preset(path):
    """Read a bounded owner-private file, without following its final symlink."""
    if os.name != 'posix':
        raise ValueError('App startup presets require the POSIX deployment journal')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 64 * 1024):
            raise ValueError('App startup preset must be an owner-private bounded regular file')
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError('App startup preset exceeds the deployment limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate App preset field')
            result[key] = value
        return result
    spec = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(spec, dict) or set(spec) != {'owner', 'operation_id', 'apps'}:
        raise ValueError('Supply an ordinary immutable deployment recipe')
    return deployment_recipe(**spec)[0]


class AppPreset:
    def __init__(self, path, *, advance, interval=1, duration=1800):
        self.path, self.advance = path, advance
        self.interval, self.duration = interval, duration
        self._stop = asyncio.Event()
        self._task = None
        self._status = {'state': 'disabled' if path is None else 'pending'}

    def status(self):
        return dict(self._status)

    def start(self):
        if self.path is not None and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            # Do not cancel an accepted deployment mutation. Its coordinator
            # must finish/checkpoint before shutdown can snapshot owner state.
            await asyncio.shield(self._task)

    async def _run(self):
        try:
            recipe = await asyncio.to_thread(read_preset, self.path)
        except Exception:
            self._status = {'state': 'needs_attention', 'reason': 'invalid_preset'}
            return
        self._status = {'state': 'pending', 'operation_id': recipe['operation_id'],
                        'phase': 'starting', 'observation': 'last-checkpoint'}
        deadline = time.monotonic() + self.duration
        while not self._stop.is_set():
            if time.monotonic() >= deadline:
                self._status.update(state='needs_attention', reason='startup_deadline')
                return
            try:
                # Send the same immutable recipe after process restart too.
                # The coordinator rejects conflict with its existing journal.
                result = await self.advance(**recipe)
                if (not isinstance(result, dict) or result.get('success') is not True
                        or result.get('state') not in ('pending', 'ready')
                        or result.get('operation_id') != recipe['operation_id']
                        or result.get('phase') not in ('installing', 'preparing', 'starting', 'ready')
                        or result.get('app') not in ('', *recipe['apps'])):
                    raise AssemblyError('Inspect the original deployment operation')
            except Exception:
                # An error/unknown outcome is not permission to resubmit under
                # a new ID. Owner recovery uses fleet_app_deploy explicitly.
                self._status.update(state='needs_attention', reason='inspect_deployment')
                return
            self._status.update(state=result['state'], phase=result['phase'], app=result['app'])
            if result['state'] == 'ready':
                return
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval)
            except TimeoutError:
                pass
        self._status.update(state='paused', reason='platform_shutdown')
