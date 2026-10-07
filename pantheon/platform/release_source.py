"""Obtain a pinned App release set for a remote platform's first-run setup.

The release set is identified by an exact SHA-256 of its archive. It is
downloaded once into the platform's private state directory, verified, and
extracted without links or paths outside its directory. Nothing here stages,
installs or starts an App; that stays with release_set and the deployment
coordinator.
"""
import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.parse import urlsplit

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.release_set import INDEX, _read_index

ARCHIVE_BYTES = 512 * 1024 * 1024
EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024


def _pin(url, sha256):
    parts = urlsplit(url or '')
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or not re.fullmatch(r'[a-f0-9]{64}', sha256 or '')):
        raise AssemblyError('Pin the release set to an HTTPS URL and its SHA-256')
    return url, sha256


def _extract(archive, destination):
    """Extract regular files and directories only, inside destination."""
    total = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            parts = [p for p in name.parts if p not in ('', '.')]
            if (name.is_absolute() or any(p == '..' for p in parts)
                    or not (member.isfile() or member.isdir())):
                raise AssemblyError('Release set archive contains an unsafe entry')
            if not parts:
                continue
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += member.size
            if total > EXTRACTED_BYTES:
                raise AssemblyError('Release set archive expands beyond its limit')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, open(target, 'wb') as output:
                shutil.copyfileobj(source, output)
            # Keep executables executable (the Shell App ships a native binary).
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)


async def _download(url, path, *, transport=None):
    import httpx
    digest, size = hashlib.sha256(), 0
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, trust_env=False,
                                 transport=transport) as client:
        async with client.stream('GET', url) as response:
            if response.status_code != 200:
                raise AssemblyError(f'Release set download failed: HTTP {response.status_code}')
            with open(path, 'wb') as output:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > ARCHIVE_BYTES:
                        raise AssemblyError('Release set archive exceeds its limit')
                    digest.update(chunk)
                    output.write(chunk)
    return digest.hexdigest()


async def release_set(url, sha256, cache, *, transport=None):
    """Return a verified local release-set directory for the pinned archive."""
    url, sha256 = _pin(url, sha256)
    cache = Path(cache)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    final = cache / sha256
    if (final / INDEX).is_file():
        await asyncio.to_thread(_read_index, final)
        return final
    with tempfile.TemporaryDirectory(prefix='.release-', dir=cache) as temporary:
        archive = Path(temporary) / 'release.tar.gz'
        actual = await _download(url, archive, transport=transport)
        if actual != sha256:
            raise AssemblyError('Release set archive does not match its pinned SHA-256')
        staging = Path(temporary) / 'release'
        staging.mkdir()
        await asyncio.to_thread(_extract, archive, staging)
        await asyncio.to_thread(_read_index, staging)
        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
    return final
