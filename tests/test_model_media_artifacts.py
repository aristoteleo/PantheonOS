"""Real filesystem/SQLite tests for the media data-plane storage boundary."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading

import pytest

spec = importlib.util.spec_from_file_location('model_media_artifacts_test',
    Path(__file__).parents[1] / 'apps/model-service/media_artifacts.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


def create(store, key='upload', body=b'media contents', **kwargs):
    return store.create(key, kind='image', mime='image/png', purpose='input', size=len(body),
                        sha256=hashlib.sha256(body).hexdigest(), **kwargs)


def test_restart_resume_lost_ack_and_bounded_binary_reads(tmp_path):
    body = b'media' * (media.CHUNK // 2)
    store = media.MediaArtifacts(tmp_path)
    first = create(store, body=body)
    artifact = first['id']
    assert create(store, body=body) == first
    with pytest.raises(ValueError, match='not ready'):
        store.read(artifact)
    partial = store.append(artifact, 0, body[:media.CHUNK])
    assert store.append(artifact, 0, body[:media.CHUNK]) == partial
    store.close()
    # Simulate a process dying after file write but before SQLite commit.
    with (tmp_path / (artifact + '.blob')).open('ab') as file:
        file.write(b'uncommitted tail')
    store = media.MediaArtifacts(tmp_path)
    try:
        for offset in range(media.CHUNK, len(body), media.CHUNK):
            store.append(artifact, offset, body[offset:offset + media.CHUNK])
        ready = store.seal(artifact)
        assert ready['state'] == 'ready' and ready['sha256'] == hashlib.sha256(body).hexdigest()
        assert store.seal(artifact) == ready
        received = b''
        while len(received) < len(body):
            metadata, chunk = store.read(artifact, len(received))
            assert metadata == ready and len(chunk) <= media.CHUNK
            received += chunk
        assert received == body
        assert store.read(artifact, len(body))[1] == b''
        assert set(ready) == {'id', 'kind', 'mime', 'purpose', 'size', 'received', 'sha256', 'state', 'created'}
        assert len(json.dumps(ready)) < 512  # no paths, provider URLs or base64 media
    finally:
        store.close()


def test_two_independent_connections_cannot_overbook_disk(tmp_path):
    stores = [media.MediaArtifacts(tmp_path, quota=12) for _ in range(2)]
    barrier = threading.Barrier(2)
    def reserve(index):
        barrier.wait()
        try:
            return create(stores[index], key=str(index), body=b'0123456789')['id']
        except ValueError as exc:
            assert 'budget' in str(exc)
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        assert sum(bool(result) for result in results) == 1
        winner = next(result for result in results if result)
        stores[0].remove(winner)  # release even an upload with no chunks yet
        assert create(stores[1], key='after-delete', body=b'0123456789')['state'] == 'writing'
    finally:
        for store in stores:
            store.close()


def test_invalid_retry_and_checksum_never_publish(tmp_path):
    store = media.MediaArtifacts(tmp_path)
    try:
        artifact = create(store, body=b'abcd')['id']
        with pytest.raises(ValueError, match='different declaration'):
            create(store, body=b'other')
        store.append(artifact, 0, b'ab')
        with pytest.raises(ValueError, match='differs'):
            store.append(artifact, 0, b'xx')
        with pytest.raises(ValueError, match='not complete'):
            store.seal(artifact)
        with pytest.raises(ValueError, match='offset'):
            store.append(artifact, 3, b'd')
        store.append(artifact, 2, b'zz')
        with pytest.raises(ValueError, match='checksum'):
            store.seal(artifact)
        assert store.get(artifact)['state'] == 'writing'
        with pytest.raises(ValueError, match='not ready'):
            store.read(artifact)
    finally:
        store.close()


@pytest.mark.parametrize('link', ['symlink', 'hardlink'])
def test_blob_cannot_redirect_reader_or_writer_to_external_files(tmp_path, link):
    root = tmp_path / 'owned'
    store = media.MediaArtifacts(root)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'abcd')
    try:
        artifact = create(store, body=b'abcd')['id']
        path = root / (artifact + '.blob')
        if link == 'symlink':
            path.symlink_to(outside)
        else:
            os.link(outside, path)
        with pytest.raises(ValueError, match='storage object'):
            store.append(artifact, 0, b'xxxx')
        # Unlink only the stored entry; never traverse it during deletion.
        store.remove(artifact)
        assert outside.read_bytes() == b'abcd'
    finally:
        store.close()


def test_deletion_reconciliation_and_missing_committed_bytes(tmp_path):
    store = media.MediaArtifacts(tmp_path)
    try:
        artifact = create(store, body=b'abcd')['id']
        store.append(artifact, 0, b'ab')
        path = tmp_path / (artifact + '.blob')
        path.write_bytes(b'a')
        with pytest.raises(ValueError, match='Committed media bytes'):
            store.append(artifact, 2, b'cd')
        with store.transaction():
            store.db.execute("UPDATE media SET state='deleting' WHERE id=?", (artifact,))
        store.remove(artifact)
        assert not path.exists()
        with pytest.raises(ValueError, match='unavailable'):
            store.get(artifact)
    finally:
        store.close()


def test_limits_and_immutable_sealed_artifact(tmp_path):
    store = media.MediaArtifacts(tmp_path, max_artifact=4, max_items=1)
    try:
        with pytest.raises(ValueError, match='declaration'):
            create(store, body=b'12345')
        artifact = create(store, body=b'abcd')['id']
        with pytest.raises(ValueError, match='budget'):
            create(store, key='second', body=b'a')
        store.append(artifact, 0, b'abcd')
        store.seal(artifact)
        with pytest.raises(ValueError, match='state'):
            store.append(artifact, 0, b'abcd')
        for offset, length in [(-1, 1), (True, 1), (0, media.CHUNK + 1), (5, 1)]:
            with pytest.raises(ValueError):
                store.read(artifact, offset, length)
        for invalid in ['../outside', 'x' * 32, None]:
            with pytest.raises(ValueError, match='reference'):
                store.get(invalid)
    finally:
        store.close()


def test_running_job_retains_its_inputs_across_connector_restart(tmp_path):
    store = media.MediaArtifacts(tmp_path)
    artifact = create(store, body=b'abcd')['id']
    with pytest.raises(ValueError, match='completed media'):
        store.retain(artifact, 'job-a')
    store.append(artifact, 0, b'abcd')
    store.seal(artifact)
    store.retain(artifact, 'job-a')
    store.retain(artifact, 'job-a')
    store.retain(artifact, 'job-b')
    store.close()
    store = media.MediaArtifacts(tmp_path)
    try:
        with pytest.raises(ValueError, match='retained'):
            store.remove(artifact)
        store.release('job-a')
        with pytest.raises(ValueError, match='retained'):
            store.remove(artifact)
        assert store.read(artifact)[1] == b'abcd'
        store.release('job-b')
        store.remove(artifact)
    finally:
        store.close()
