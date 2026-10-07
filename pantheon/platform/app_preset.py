"""Explicit, bounded startup of an owner-provided ordinary App deployment.

The existing coordinator owns every durable operation and recovery identity.
This driver only advances pending checkpoints; it never heals failed Apps,
changes recipes, replays tools, or makes platform readiness depend on an App.
"""
import asyncio
import json
import os
import re
import stat
import time
from urllib.parse import urlsplit

from pantheon.apps.dependency_assembly import AssemblyError, DEPLOYMENT_BYTES
from pantheon.apps.deployment import deployment_recipe


def startup_recipe(spec):
    if isinstance(spec, dict) and spec.get('kind') == 'model-services':
        from pantheon.models.bootstrap import recipe
        return recipe(**spec)
    return deployment_recipe(**spec)[0]


async def fetch_hub_preset(url, *, hub, token, owner, transport=None):
    """One authenticated, bounded read from the explicitly paired Hub only."""
    import httpx
    import ssl
    source, origin = urlsplit(url), urlsplit(hub)
    if (origin.scheme != 'https' or not origin.hostname or origin.path not in ('', '/')
            or origin.query or origin.fragment or origin.username or origin.password
            or (source.scheme, source.netloc) != (origin.scheme, origin.netloc)
            or source.username or source.password or source.query or source.fragment
            or not re.fullmatch(r'/api/fleet/apps/startup/[a-z0-9][a-z0-9_-]{0,63}', source.path)
            or not token or not owner):
        raise ValueError('Supply a paired Hub startup endpoint and owner credential')
    async with asyncio.timeout(20), httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False,
                                 verify=ssl.create_default_context(), transport=transport) as client:
        async with client.stream('GET', url, headers={'Authorization': 'Bearer ' + token}) as response:
            if response.status_code != 200:
                raise ValueError('Hub startup preset is unavailable')
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                # Hub uses compact JSON; leave bounded space for its revision
                # envelope around the same full deployment accepted locally.
                if len(data) > DEPLOYMENT_BYTES + 4096:
                    raise ValueError('Hub startup response exceeds its limit')
    result = json.loads(data, object_pairs_hook=_unique_fields)
    if (not isinstance(result, dict) or set(result) != {'protocol', 'revision', 'recipe'}
            or type(result['protocol']) is not int or result['protocol'] != 1
            or type(result['revision']) is not int or not 0 <= result['revision'] < 2**63-1):
        raise ValueError('Invalid Hub startup response')
    if result['recipe'] is None:
        return None
    recipe = result['recipe']
    if not isinstance(recipe, dict) or recipe.get('owner') != owner or result['revision'] < 1:
        raise ValueError('Hub startup owner does not match this platform')
    return startup_recipe(recipe)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate App preset field')
        result[key] = value
    return result


def read_preset(path):
    """Read a bounded owner-private file, without following its final symlink."""
    if os.name != 'posix':
        raise ValueError('App startup presets require the POSIX deployment journal')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > DEPLOYMENT_BYTES):
            raise ValueError('App startup preset must be an owner-private bounded regular file')
        raw = stream.read(DEPLOYMENT_BYTES + 1)
    if len(raw) > DEPLOYMENT_BYTES:
        raise ValueError('App startup preset exceeds the deployment limit')
    spec = json.loads(raw, object_pairs_hook=_unique_fields)
    if not isinstance(spec, dict) or set(spec) not in ({'owner', 'operation_id', 'apps'},
            {'kind', 'owner', 'operation_id', 'apps', 'model_apps'}):
        raise ValueError('Supply an ordinary immutable deployment recipe')
    return startup_recipe(spec)


def _message(exc):
    # Status is shown to the owner: bounded, single line, no credential values
    # (deployment errors name references and phases, never secrets).
    return ' '.join(str(exc).split())[:300] or type(exc).__name__


def _log_failure(stage, exc):
    from pantheon.utils.log import logger
    logger.warning(f'[app-preset] {stage} failed: {_message(exc)}')


class AppPreset:
    def __init__(self, path, *, advance, load=None, interval=1, duration=1800, resume=None, watch_interval=30,
                 applied=None):
        if path is not None and load is not None:
            raise ValueError('Choose one explicit App startup source')
        self.path, self.advance = path, advance
        self.load = load
        self.interval, self.duration = interval, duration
        # resume(recipe) -> (spec | None, done) restarts Apps a replaced node
        # lost; done(spec) is called once that resume is ready. See preset_resume.
        self.resume, self.watch_interval = resume, watch_interval
        self.recipe = None  # the loaded preset, for owner start requests
        self._start = None
        # applied(spec): the recipe whose operation now runs the Apps (the
        # original, or a resume). held pauses resume during a release change.
        self.applied, self.held = applied, False
        self._stop = asyncio.Event()
        self._task = None
        self._status = {'state': 'disabled' if path is None and load is None else 'pending'}
        self.last_error = ''  # the owner-facing reason for needs_attention, kept out of status()

    def status(self):
        return dict(self._status)

    def request_start(self):
        """Ask the watcher to start every stopped App of the ready preset now."""
        if self._start is None or self.resume is None:
            return False
        self._start.set()
        return True

    def start(self):
        if (self.path is not None or self.load is not None) and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            # Do not cancel an accepted deployment mutation. Its coordinator
            # must finish/checkpoint before shutdown can snapshot owner state.
            await asyncio.shield(self._task)

    async def _run(self):
        try:
            recipe = await self.load() if self.load is not None else await asyncio.to_thread(read_preset, self.path)
        except Exception as exc:
            _log_failure('read', exc)
            self.last_error = _message(exc)
            self._status = {'state': 'needs_attention', 'reason': 'preset_unavailable' if self.load else 'invalid_preset'}
            return
        if recipe is None:
            self._status = {'state': 'disabled'}
            return
        self.recipe, self._start = recipe, asyncio.Event()
        ready = await self._drive(recipe)
        if ready and self.applied is not None:
            self.applied(recipe)
        if self.resume is None or self._stop.is_set():
            return
        if not ready and not await self._resume_once(recipe):
            return
        await self._watch(recipe)

    async def _resume_once(self, recipe, everything=False):
        """Start lost Apps once; True when there was nothing to do or it is ready.

        After node loss the completed original operation reports its Apps gone
        (needs_attention). Resume decides from the nodes themselves, so failed
        or owner-stopped Apps still stay with the owner.
        """
        try:
            spec, done = await self.resume(recipe, everything=everything)
        except Exception as exc:
            _log_failure('resume check', exc)
            return False
        if spec is None:
            return self._status.get('state') == 'ready'
        if not await self._drive(spec):
            return False
        done(spec)
        if self.applied is not None:
            self.applied(spec)
        return True

    async def _drive(self, recipe):
        """Advance one recipe to ready; False when it needs the owner or the platform stops."""
        self._status = {'state': 'pending', 'operation_id': recipe['operation_id'],
                        'phase': 'starting', 'observation': 'last-checkpoint'}
        deadline = time.monotonic() + self.duration
        while not self._stop.is_set():
            if time.monotonic() >= deadline:
                self._status.update(state='needs_attention', reason='startup_deadline')
                return False
            try:
                # Send the same immutable recipe after process restart too.
                # The coordinator rejects conflict with its existing journal.
                result = await self.advance(**recipe)
                if (not isinstance(result, dict) or result.get('success') is not True
                        or result.get('state') not in ('pending', 'ready')
                        or result.get('operation_id') != recipe['operation_id']
                        or result.get('phase') not in ('credentials', 'installing', 'preparing', 'starting', 'registering', 'ready')
                        or result.get('app') not in ('', *recipe['apps'], *recipe.get('model_apps', {}))):
                    raise AssemblyError('Inspect the original deployment operation')
            except Exception as exc:
                # An error/unknown outcome is not permission to resubmit under
                # a new ID. Owner recovery uses fleet_app_deploy explicitly.
                _log_failure('advance', exc)
                self.last_error = _message(exc)
                self._status.update(state='needs_attention', reason='inspect_deployment')
                return False
            self._status.update(state=result['state'], phase=result['phase'], app=result['app'])
            if result['state'] == 'ready':
                return True
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval)
            except TimeoutError:
                pass
        self._status.update(state='paused', reason='platform_shutdown')
        return False

    async def _watch(self, recipe):
        """After ready, restart the Apps a replaced node lost (never failed ones)."""
        while True:
            waits = [asyncio.ensure_future(self._stop.wait()), asyncio.ensure_future(self._start.wait())]
            await asyncio.wait(waits, timeout=self.watch_interval, return_when=asyncio.FIRST_COMPLETED)
            for wait in waits:
                wait.cancel()
            if self._stop.is_set():
                self._status.update(state='paused', reason='platform_shutdown')
                return
            everything = self._start.is_set()
            self._start.clear()
            if self.held:
                continue
            if not await self._resume_once(recipe, everything) and self._status.get('state') != 'ready':
                return
