"""Bounded node-local media, separate from model weights and control messages.

This is storage, not an authorization layer. Only the owning connector's
authenticated workload routes may expose it. References are opaque IDs, never
provider URLs or filesystem paths. Drivers and uploads use the same binary
chunk protocol; unfinished bytes are not readable as an inference result.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid


CHUNK = 1024 * 1024
MIME = {
    'image': {'image/png', 'image/jpeg', 'image/webp'},
    'audio': {'audio/wav', 'audio/mpeg', 'audio/flac', 'audio/ogg', 'audio/mp4'},
    'video': {'video/mp4', 'video/webm'},
}


class MediaArtifacts:
    def __init__(self, directory, *, quota=2 << 30, max_artifact=512 << 20, max_items=256):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.quota, self.max_artifact, self.max_items = quota, max_artifact, max_items
        self.lock = threading.RLock()
        database = self.root / 'media.sqlite3'
        # Create with restricted permissions before SQLite creates WAL files.
        fd = os.open(database, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                raise ValueError('Invalid media ledger')
        finally:
            os.close(fd)
        os.chmod(database, 0o600)
        self.db = sqlite3.connect(database, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS media (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
            mime TEXT NOT NULL, purpose TEXT NOT NULL, size INTEGER NOT NULL,
            received INTEGER NOT NULL, expected_sha TEXT NOT NULL, sha256 TEXT NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS leases (
            artifact TEXT NOT NULL, job TEXT NOT NULL, PRIMARY KEY(artifact, job))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS generated_media (
            artifact TEXT PRIMARY KEY, job TEXT NOT NULL, max_size INTEGER NOT NULL)''')
        self.db.commit()
        os.chmod(self.root / 'media.sqlite3', 0o600)

    def close(self):
        with self.lock:
            self.db.close()

    def sync_directory(self):
        if os.name != 'nt':
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @contextmanager
    def transaction(self):
        # SQLite serializes writers across connector processes too. A crashed
        # append may leave an uncommitted file tail; the next append truncates
        # only that tail, using the durable received count as its authority.
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def row(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch('[a-f0-9]{32}', artifact_id):
            raise ValueError('Invalid media reference')
        row = self.db.execute('SELECT * FROM media WHERE id=?', (artifact_id,)).fetchone()
        if row is None:
            raise ValueError('Media reference is unavailable on this service')
        return row

    @staticmethod
    def metadata(row):
        return {k: row[k] for k in ('id', 'kind', 'mime', 'purpose', 'size', 'received',
                                    'sha256', 'state', 'created')}

    def get(self, artifact_id):
        with self.lock:
            return self.metadata(self.row(artifact_id))

    @contextmanager
    def blob(self, artifact_id, *, write=False, create=False):
        # All callers have already resolved the ID in the ledger. Reject links
        # and non-files rather than accidentally reading arbitrary local data.
        path = self.root / (artifact_id + '.blob')
        flags = (os.O_RDWR if write else os.O_RDONLY) | getattr(os, 'O_NOFOLLOW', 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        else:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Invalid media storage object')
        fd = os.open(path, flags, 0o600)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ValueError('Invalid media storage object')
            if not create and (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('Media storage object changed')
            with os.fdopen(fd, 'r+b' if write else 'rb') as stream:
                fd = None
                yield stream
        finally:
            if fd is not None:
                os.close(fd)

    def create(self, request_key, *, kind, mime, purpose, size, sha256=''):
        if (not isinstance(request_key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', request_key)
                or not isinstance(kind, str) or kind not in MIME
                or not isinstance(mime, str) or mime not in MIME[kind]
                or not isinstance(purpose, str) or purpose not in {'input', 'output'}
                or type(size) is not int or not 0 < size <= self.max_artifact
                or not isinstance(sha256, str) or (sha256 and not re.fullmatch('[a-f0-9]{64}', sha256))):
            raise ValueError('Invalid media declaration')
        with self.transaction():
            old = self.db.execute('SELECT * FROM media WHERE request_key=?', (request_key,)).fetchone()
            identity = (kind, mime, purpose, size, sha256)
            if old:
                if tuple(old[k] for k in ('kind', 'mime', 'purpose', 'size', 'expected_sha')) != identity:
                    raise ValueError('Media request key already has a different declaration')
                return self.metadata(old)
            reserved, count = self.db.execute('SELECT COALESCE(SUM(size),0),COUNT(*) FROM media').fetchone()
            if reserved + size > self.quota or count >= self.max_items:
                raise ValueError('Media storage budget is full; remove unused media first')
            artifact_id = uuid.uuid4().hex
            # Reserving declared size before writing prevents simultaneous
            # uploads from oversubscribing the disk budget.
            self.db.execute('INSERT INTO media VALUES (?,?,?,?,?,?,0,?,?,?,?)',
                (artifact_id, request_key, kind, mime, purpose, size, sha256, '', 'writing', time.time()))
            # File creation is lazy, after this reservation is committed; a
            # process crash cannot leave unaccounted bytes from create().
            return self.metadata(self.row(artifact_id))

    def reserve_output(self, job, *, kind, mime, max_size):
        """Internal only: caller holds the SAME transaction as job admission.

        The cap is charged before the upstream runs. A generated stream can be
        shorter; only its owning driver may finalize the actual length.
        """
        if (not self.db.in_transaction or not re.fullmatch('[A-Za-z0-9_-]{1,110}', job)
                or kind not in MIME or mime not in MIME[kind]
                or type(max_size) is not int or not 0 < max_size <= self.max_artifact):
            raise ValueError('Invalid output reservation')
        reserved, count = self.db.execute('SELECT COALESCE(SUM(size),0),COUNT(*) FROM media').fetchone()
        if reserved + max_size > self.quota or count >= self.max_items:
            raise ValueError('Media storage budget is full; remove unused media first')
        artifact = uuid.uuid4().hex
        self.db.execute('INSERT INTO media VALUES (?,?,?,?,?,?,0,?,?,?,?)',
            (artifact, 'generated-' + artifact, kind, mime, 'output', max_size, '', '', 'writing', time.time()))
        self.db.execute('INSERT INTO generated_media VALUES (?,?,?)', (artifact, job, max_size))
        self.db.execute('INSERT INTO leases VALUES (?,?)', (artifact, job))
        return self.metadata(self.row(artifact))

    def output_owner(self, artifact_id, owner):
        row = self.db.execute('SELECT job FROM generated_media WHERE artifact=?', (artifact_id,)).fetchone()
        if row and row['job'] != owner:
            raise ValueError('Only the generating job may write its output')
        return bool(row)

    def outputs(self, job):
        with self.lock:
            return [r[0] for r in self.db.execute('SELECT artifact FROM generated_media WHERE job=?', (job,))]

    def append(self, artifact_id, offset, data, *, owner=''):
        if type(offset) is not int or offset < 0 or not isinstance(data, bytes) or not 0 < len(data) <= CHUNK:
            raise ValueError('Invalid binary media chunk')
        with self.transaction():
            row = self.row(artifact_id)
            self.output_owner(artifact_id, owner)
            if row['state'] != 'writing' or offset > row['received'] or offset + len(data) > row['size']:
                raise ValueError('Media upload state or offset changed')
            path = self.root / (artifact_id + '.blob')
            with self.blob(artifact_id, write=True, create=not path.exists() and row['received'] == 0) as stream:
                length = os.fstat(stream.fileno()).st_size
                if length < row['received']:
                    raise ValueError('Committed media bytes are missing')
                if offset < row['received']:
                    stream.seek(offset)
                    if offset + len(data) > row['received'] or stream.read(len(data)) != data:
                        raise ValueError('Repeated media chunk differs from committed bytes')
                    return self.metadata(row)
                stream.truncate(row['received'])
                stream.seek(offset)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.sync_directory()
            self.db.execute('UPDATE media SET received=? WHERE id=?', (offset + len(data), artifact_id))
            return self.metadata(self.row(artifact_id))

    def seal(self, artifact_id, *, owner='', generated=False):
        with self.transaction():
            row = self.row(artifact_id)
            owned_output = self.output_owner(artifact_id, owner)
            if generated and not owned_output:
                raise ValueError('Only a reserved output can finalize its actual size')
            if row['state'] == 'ready':
                return self.metadata(row)
            if generated and row['state'] == 'writing' and 0 < row['received'] <= row['size']:
                self.db.execute('UPDATE media SET size=received WHERE id=?', (artifact_id,))
                row = self.row(artifact_id)
            if row['state'] != 'writing' or row['received'] != row['size']:
                raise ValueError('Media upload is not complete')
            with self.blob(artifact_id) as stream:
                if os.fstat(stream.fileno()).st_size != row['size']:
                    raise ValueError('Media storage length differs from its receipt')
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(CHUNK), b''):
                    hasher.update(chunk)
                digest = hasher.hexdigest()
            if row['expected_sha'] and digest != row['expected_sha']:
                raise ValueError('Media checksum mismatch')
            self.db.execute("UPDATE media SET state='ready',sha256=? WHERE id=?", (digest, artifact_id))
            return self.metadata(self.row(artifact_id))

    def read(self, artifact_id, offset=0, length=CHUNK):
        if type(offset) is not int or offset < 0 or type(length) is not int or not 0 < length <= CHUNK:
            raise ValueError('Invalid media read range')
        # A binary slice, not base64 in a status response. The HTTP adapter can
        # turn this into a bounded Range response with the stable digest ETag.
        with self.transaction():
            row = self.row(artifact_id)
            if row['state'] != 'ready' or offset > row['size']:
                raise ValueError('Media is not ready or its range is invalid')
            with self.blob(artifact_id) as stream:
                if os.fstat(stream.fileno()).st_size != row['size']:
                    raise ValueError('Media storage length differs from its receipt')
                stream.seek(offset)
                data = stream.read(min(length, row['size'] - offset))
            return self.metadata(row), data

    def remove(self, artifact_id):
        with self.transaction():
            self.row(artifact_id)
            if self.db.execute('SELECT 1 FROM leases WHERE artifact=? LIMIT 1', (artifact_id,)).fetchone():
                raise ValueError('Media is retained by a model job')
            self.db.execute("UPDATE media SET state='deleting' WHERE id=?", (artifact_id,))
        # Keep the reservation until unlink is confirmed. A crash here can be
        # reconciled by repeating remove(), without exposing partial media.
        (self.root / (artifact_id + '.blob')).unlink(missing_ok=True)
        self.sync_directory()
        with self.transaction():
            self.db.execute('DELETE FROM media WHERE id=?', (artifact_id,))
            self.db.execute('DELETE FROM generated_media WHERE artifact=?', (artifact_id,))

    def retain(self, artifact_id, job_id):
        """Internal job ownership, never a method exposed to an upload client."""
        if not isinstance(job_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', job_id):
            raise ValueError('Invalid media job reference')
        with self.transaction():
            if self.row(artifact_id)['state'] != 'ready':
                raise ValueError('Only completed media can be used by a model job')
            self.db.execute('INSERT OR IGNORE INTO leases VALUES (?,?)', (artifact_id, job_id))

    def release(self, job_id):
        """Called only after the exact owning job is terminal or forgotten."""
        with self.transaction():
            self.db.execute('DELETE FROM leases WHERE job=?', (job_id,))
