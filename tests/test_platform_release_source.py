import hashlib
import io
import os
from pathlib import Path
import tarfile

import httpx
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.platform.release_source import release_set

RELEASE = os.environ.get('AGENT_RELEASE_ARCHIVE')


def _transport(payload, calls):
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=payload)
    return httpx.MockTransport(handler)


def _archive(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == 'link':
                info.type, info.linkname = tarfile.SYMTYPE, '/etc/passwd'
            else:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if kind == 'file' else None)
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('entries', [[('../escape', b'x', 'file')], [('/abs', b'x', 'file')],
                                     [('link', b'', 'link')]])
async def test_unsafe_archives_are_rejected(tmp_path, entries):
    payload = _archive(entries)
    with pytest.raises(AssemblyError, match='unsafe'):
        await release_set('https://releases.test/set.tar.gz', hashlib.sha256(payload).hexdigest(),
                          tmp_path, transport=_transport(payload, []))
    assert not (tmp_path / hashlib.sha256(payload).hexdigest()).exists()


@pytest.mark.asyncio
async def test_pin_must_match_and_be_https(tmp_path):
    payload = _archive([('release-set.json', b'{}', 'file')])
    with pytest.raises(AssemblyError, match='pinned SHA-256'):
        await release_set('https://releases.test/set.tar.gz', '0' * 64, tmp_path, transport=_transport(payload, []))
    with pytest.raises(AssemblyError, match='HTTPS'):
        await release_set('http://releases.test/set.tar.gz', hashlib.sha256(payload).hexdigest(), tmp_path)


@pytest.mark.asyncio
@pytest.mark.skipif(not RELEASE, reason='set AGENT_RELEASE_ARCHIVE to a built release-set archive')
async def test_real_release_set_is_verified_extracted_and_cached(tmp_path):
    payload = Path(RELEASE).read_bytes()
    sha, calls = hashlib.sha256(payload).hexdigest(), []
    root = await release_set('https://releases.test/set.tar.gz', sha, tmp_path, transport=_transport(payload, calls))
    assert (root / 'release-set.json').is_file() and len([p for p in root.iterdir() if p.is_dir()]) == 13
    assert os.access(next(root.rglob('shell*')), os.R_OK)
    again = await release_set('https://releases.test/set.tar.gz', sha, tmp_path, transport=_transport(payload, calls))
    assert again == root and len(calls) == 1
