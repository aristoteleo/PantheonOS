"""Build an App's package from its repository (docs/agent/agent-app-repository.md §1).

An App whose package is assembled from source (a compiled frontend, pinned
binaries) declares ``"build": {"argv": [...], "timeout_seconds": N}``. The
command runs the repository's own code in a copy of one commit, on the owner's
workspace node, and fills ``$APP_BUILD_OUTPUT`` with the runnable package:
an ``app.json`` of the same App and a ``fleet.json`` execution declaration.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .schema import Build

LOG_TAIL = 16 * 1024


def declared(manifest: dict) -> dict | None:
    """The manifest's build declaration, validated; None for a committed package."""
    build = manifest.get('build')
    return None if build is None else Build.model_validate(build).model_dump()


def tail(log: Path) -> str:
    try:
        with log.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - LOG_TAIL))
            return stream.read().decode(errors='replace')
    except OSError:
        return ''


async def run(source: Path, output: Path, *, manifest: dict, platform: str, revision: str,
              cache: Path, log: Path) -> dict:
    """Run the declared build of one immutable source tree; return the built manifest.

    The source snapshot is never modified: the command runs in a copy. Output
    must be a new directory. Failure leaves no output behind.
    """
    build = declared(manifest)
    if build is None:
        raise ValueError('This App declares no build')
    if output.exists():
        raise FileExistsError(output)
    cache.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = list(build['argv'])
    if argv[0] in ('python', 'python3'):
        argv[0] = sys.executable  # a known interpreter, not whichever is first on PATH
    with tempfile.TemporaryDirectory(prefix='app-build-') as temp:
        work = Path(temp) / 'source'
        shutil.copytree(source, work, symlinks=True)
        staging = Path(temp) / 'output'
        staging.mkdir()
        # Tools keep their caches under HOME (npm's npx store is full of
        # symlinks). The builder's own HOME is its App data, which must stay
        # plain files so the next revision can copy it; a build's HOME is in
        # its cache instead.
        home = Path(cache) / 'home'
        home.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'APP_BUILD_OUTPUT': str(staging), 'APP_BUILD_PLATFORM': platform,
               'APP_BUILD_REVISION': revision, 'APP_BUILD_CACHE': str(cache), 'HOME': str(home),
               'XDG_CACHE_HOME': str(home / '.cache'), 'npm_config_cache': str(home / '.npm')}
        with log.open('ab') as stream:
            proc = await asyncio.create_subprocess_exec(*argv, cwd=work, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=stream, stderr=asyncio.subprocess.STDOUT,
                                                        start_new_session=True)
            try:
                code = await asyncio.wait_for(proc.wait(), build['timeout_seconds'])
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"Build did not finish within {build['timeout_seconds']} s")
        if code != 0:
            raise RuntimeError(f'Build failed (exit {code}):\n' + tail(log)[-2000:])
        built = next((staging / n for n in ('app.json', 'atrium.json') if (staging / n).is_file()), None)
        if built is None or not (staging / 'fleet.json').is_file():
            raise RuntimeError('Build produced no package: app.json and fleet.json are required')
        result = json.loads(built.read_text())
        if result.get('id') != manifest.get('id'):
            raise RuntimeError('Build produced another App')
        if any(p.is_symlink() for p in staging.rglob('*')):
            raise RuntimeError('Built packages cannot contain symbolic links')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), output)
        return result
