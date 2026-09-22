"""Download correctness without depending on public registries or large weights."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest

spec = importlib.util.spec_from_file_location('model_artifacts_test', Path(__file__).parents[1] / 'apps/model-service/artifacts.py')
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


def source(body=b'model weights'):
    return {'url': 'https://models.example/immutable/model.gguf?signature=private',
            'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
            'name': 'example', 'revision': 'exact-model-revision', 'format': 'gguf'}


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {'Content-Length': str(len(body))}


class Opener:
    def __init__(self, body, *, ignore_range=False, corrupt_range=False):
        self.body = body
        self.requests = []
        self.ignore_range = ignore_range
        self.corrupt_range = corrupt_range

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 30
        assert request.get_header('Authorization') is None
        assert request.get_header('User-agent') == 'Pantheon-Fleet/1.0'
        assert request.get_header('Accept-encoding') == 'identity'
        start = int(request.get_header('Range').split('=')[1][:-1]) if request.has_header('Range') else 0
        if start and not self.ignore_range:
            return Response(self.body[start:], 206, {'Content-Length': str(len(self.body)-start),
                'Content-Range': f'bytes {start+int(self.corrupt_range)}-{len(self.body)-1}/{len(self.body)}'})
        return Response(self.body)


def wait_job(jobs, state=None):
    for _ in range(300):
        rows = jobs.list()
        if rows and rows[0]['state'] == state:
            return rows[0]
        if rows and state is None and rows[0]['state'] in ('ready', 'failed', 'cancelled', 'interrupted'):
            return rows[0]
        time.sleep(.01)
    pytest.fail(f'job did not reach {state}: {rows}')


@pytest.mark.parametrize('ignore_range', [False, True])
def test_verified_resume_cache_and_crash_receipt_recovery(tmp_path, ignore_range):
    body = b'0123456789' * 1000
    item = source(body)
    opener = Opener(body, ignore_range=ignore_range)
    cache = artifacts.ArtifactCache(tmp_path, opener=opener)
    part = tmp_path / (item['sha256'] + '.part')
    part.write_bytes(body[:100])
    states = []
    target = cache.fetch(item, threading.Event(), lambda *p: states.append(p))
    assert target.read_bytes() == body
    assert opener.requests[0].get_header('Range') == 'bytes=100-'
    assert states[-1] == ('ready', len(body))
    assert not part.exists()
    # Independent cache object/restart keeps the same verified bytes.
    second = artifacts.ArtifactCache(tmp_path, opener=opener)
    assert second.fetch(item, threading.Event(), lambda *p: None) == target
    assert len(opener.requests) == 1
    # Recover a rename committed before its completion receipt, offline.
    (tmp_path / (item['sha256'] + '.json')).unlink()
    assert second.fetch(item, threading.Event(), lambda *p: None) == target
    assert len(opener.requests) == 1


def test_range_mismatch_and_checksum_failure_never_publish(tmp_path):
    body = b'abcdef'
    item = source(body)
    part = tmp_path / (item['sha256'] + '.part')
    part.write_bytes(body[:2])
    cache = artifacts.ArtifactCache(tmp_path, opener=Opener(body, corrupt_range=True))
    with pytest.raises(ValueError, match='range'):
        cache.fetch(item, threading.Event(), lambda *p: None)
    assert part.read_bytes() == b'ab'
    cache.opener = Opener(b'badbad')
    with pytest.raises(ValueError, match='checksum'):
        cache.fetch(item, threading.Event(), lambda *p: None)
    assert not (tmp_path / item['sha256']).exists()
    assert not part.exists()


def test_cancel_keeps_partial_and_explicit_resume(tmp_path):
    body = b'x' * (3 << 20)
    item = source(body)
    cancel = threading.Event()
    cache = artifacts.ArtifactCache(tmp_path, opener=Opener(body))
    def progress(state, done):
        if state == 'downloading' and done >= 1 << 20:
            cancel.set()
    with pytest.raises(artifacts.Cancelled):
        cache.fetch(item, cancel, progress)
    part = tmp_path / (item['sha256'] + '.part')
    assert part.stat().st_size == 1 << 20
    assert not (tmp_path / item['sha256']).exists()
    cancel.clear()
    assert cache.fetch(item, cancel, lambda *p: None).read_bytes() == body
    assert cache.opener.requests[-1].get_header('Range') == 'bytes=1048576-'


def test_durable_jobs_dedup_lock_metadata_and_forget_preserves_weights(tmp_path):
    item = source()
    cache = artifacts.ArtifactCache(tmp_path / 'cache', opener=Opener(b'model weights'))
    jobs = artifacts.DownloadJobs(tmp_path / 'jobs', cache)
    try:
        with pytest.raises(OSError):
            artifacts.DownloadJobs(tmp_path / 'jobs', cache)
        assert jobs.submit('download-1', item) == 'download-1'
        row = wait_job(jobs, 'ready')
        assert row['bytes_done'] == item['size']
        assert 'signature' not in json.dumps(row) and 'url' not in row['artifact']
        assert row['artifact']['format'] == 'gguf'
        jobs.submit('download-1', item)
        assert len(cache.opener.requests) == 1
        with pytest.raises(ValueError, match='another artifact'):
            jobs.submit('download-1', {**item, 'revision': 'changed'})
    finally:
        jobs.close()
    restored = artifacts.DownloadJobs(tmp_path / 'jobs', cache)
    try:
        assert restored.list()[0]['state'] == 'ready'
        restored.forget('download-1')
        assert restored.list() == []
        assert (cache.root / item['sha256']).read_bytes() == b'model weights'
    finally:
        restored.close()


def test_interrupted_worker_requires_explicit_resume(tmp_path):
    item = source()
    cache = artifacts.ArtifactCache(tmp_path / 'cache', opener=Opener(b'model weights'))
    jobs = artifacts.DownloadJobs(tmp_path / 'jobs', cache)
    jobs.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', ('crash', json.dumps(item, sort_keys=True), 'verifying', 5, time.time(), ''))
    jobs.db.commit()
    jobs.close()
    restored = artifacts.DownloadJobs(tmp_path / 'jobs', cache)
    try:
        assert restored.list()[0]['state'] == 'interrupted'
        restored.submit('crash', item)
        assert restored.list()[0]['state'] == 'interrupted'
        assert not cache.opener.requests
        restored.submit('crash', resume=True)
        assert wait_job(restored, 'ready')['bytes_done'] == item['size']
    finally:
        restored.close()


def test_blob_single_writer_and_bounded_parallel_workers(tmp_path):
    class BlockedCache:
        def fetch(self, source, cancelled, progress):
            cancelled.wait(5)
            raise artifacts.Cancelled()
    jobs = artifacts.DownloadJobs(tmp_path / 'jobs', BlockedCache())
    try:
        jobs.submit('a', source())
        jobs.submit('b', source(b'other'))
        with pytest.raises(ValueError, match='Two downloads'):
            jobs.submit('c', source(b'third'))
        with pytest.raises(ValueError, match='Cancel'):
            jobs.forget('a')
        assert jobs.cancel('a')
        wait_job(jobs, 'cancelled')
    finally:
        jobs.close()
    with artifacts.file_lock(tmp_path / 'blob.lock'):
        with pytest.raises(OSError):
            with artifacts.file_lock(tmp_path / 'blob.lock'):
                pytest.fail('same artifact has two writers')


@pytest.mark.parametrize('change', [
    {'url': 'http://models.example/file'}, {'url': 'https://secret@models.example/file'},
    {'sha256': '../escape'}, {'size': True}, {'size': 0}, {'size': 2**41},
    {'format': ''}, {'command': 'run arbitrary code'}, {'url': 12}, {'sha256': None},
])
def test_artifact_contract_rejects_unsafe_or_unpinned_sources(change):
    with pytest.raises(ValueError):
        artifacts.validate_source({**source(), **change})


def test_failed_job_store_open_releases_ownership(tmp_path):
    import sqlite3
    jobs_dir = tmp_path / 'jobs'
    jobs_dir.mkdir()
    (jobs_dir / 'jobs.sqlite3').write_bytes(b'not a database')
    cache = artifacts.ArtifactCache(tmp_path / 'cache')
    with pytest.raises(sqlite3.DatabaseError):
        artifacts.DownloadJobs(jobs_dir, cache)
    with artifacts.file_lock(jobs_dir / 'owner.lock'):
        pass


def test_cancel_during_blocked_read_is_cancelled_not_network_failure(tmp_path):
    class ReadTimeout:
        def fetch(self, source, cancelled, progress):
            cancelled.wait(5)
            raise OSError('timed out')
    jobs = artifacts.DownloadJobs(tmp_path / 'jobs', ReadTimeout())
    try:
        jobs.submit('cancel-read', source())
        jobs.cancel('cancel-read')
        assert wait_job(jobs, 'cancelled')['error'] == ''
    finally:
        jobs.close()
