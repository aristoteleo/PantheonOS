"""Background builds of library App versions (docs/agent-app-repository.md §1).

A build is kept per repository, commit and platform, so a commit is built once.
Builds run in this Desktop process; a build interrupted by a restart reports so
and can be started again.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from pantheonos.apps import app_build


class AppBuilds:
    # One task per build directory across the short-lived AppPlacement objects.
    _tasks: dict[str, asyncio.Task] = {}

    def __init__(self, manager):
        self.manager = manager
        self.root = manager.records / 'builds'

    def _resolve(self, app_id: str, revision: dict | None) -> dict:
        revision = revision or {}
        return self.manager.versions.resolve(app_id, revision.get('scope', ''),
            revision.get('commit') or 'latest', revision.get('repository_id', ''))

    def _directory(self, resolved: dict, platform: str) -> Path:
        if not re.fullmatch(r'(linux|darwin|windows)-(amd64|arm64)', platform or ''):
            raise ValueError('Build for a node platform such as linux-amd64')
        revision = resolved['revision']
        key = revision.get('repository_id') or resolved['manifest']['id']
        return self.root / revision['scope'] / key / revision['commit'] / platform

    def _status(self, directory: Path) -> dict:
        try:
            status = json.loads((directory / 'status.json').read_text())
        except (OSError, ValueError):
            return {'state': 'none'}
        task = self._tasks.get(str(directory))
        if status.get('state') == 'running' and (task is None or task.done()):
            status = {**status, 'state': 'failed', 'error': 'The build was interrupted; start it again'}
        return {**status, 'log': app_build.tail(directory / 'build.log')}

    def _write(self, directory: Path, status: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f'.status.{uuid.uuid4().hex}.tmp'
        tmp.write_text(json.dumps(status))
        tmp.replace(directory / 'status.json')

    def status(self, app_id: str, revision: dict | None, platform: str) -> dict:
        resolved = self._resolve(app_id, revision)
        if app_build.declared(resolved['manifest']) is None:
            return {'state': 'none', 'declared': False}
        return {**self._status(self._directory(resolved, platform)), 'declared': True,
                'revision': resolved['revision']}

    def package(self, app_id: str, revision: dict | None, platform: str) -> Path | None:
        """The finished package of this version, or None."""
        resolved = self._resolve(app_id, revision)
        directory = self._directory(resolved, platform)
        if self._status(directory).get('state') != 'succeeded':
            return None
        return directory / 'package'

    async def start(self, app_id: str, revision: dict | None, platform: str) -> dict:
        resolved = await asyncio.to_thread(self._resolve, app_id, revision)
        if app_build.declared(resolved['manifest']) is None:
            raise ValueError('This App declares no build; its repository is its package')
        directory = self._directory(resolved, platform)
        current = self._status(directory)
        if current['state'] in ('running', 'succeeded'):
            return {**current, 'declared': True, 'revision': resolved['revision']}
        if directory.exists():
            shutil.rmtree(directory)
        started = time.time()
        self._write(directory, {'state': 'running', 'started': started, 'platform': platform,
                                'revision': resolved['revision']})
        cache = self.manager.records / 'build-cache' / resolved['manifest']['id']

        async def build():
            try:
                manifest = await app_build.run(Path(resolved['dir']), directory / 'package',
                    manifest=resolved['manifest'], platform=platform, revision=resolved['revision']['commit'],
                    cache=cache, log=directory / 'build.log')
                self._write(directory, {'state': 'succeeded', 'started': started, 'finished': time.time(),
                                        'platform': platform, 'revision': resolved['revision'],
                                        'version': manifest.get('version')})
            except BaseException as exc:
                self._write(directory, {'state': 'failed', 'started': started, 'finished': time.time(),
                                        'platform': platform, 'revision': resolved['revision'],
                                        'error': str(exc) or type(exc).__name__})
                if not isinstance(exc, Exception):
                    raise

        self._tasks[str(directory)] = asyncio.get_running_loop().create_task(build())
        return {**self._status(directory), 'declared': True, 'revision': resolved['revision']}
