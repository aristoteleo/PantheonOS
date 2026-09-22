"""Node-local, content-addressed downloads independent of Fleet install hooks.

Only verified bytes enter the cache. Cancellation retains a resumable partial;
neither a failed request nor reopening the manager deletes existing weights.
This module never executes downloaded content or removes external model files.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class Cancelled(Exception):
    pass


def validate_source(source):
    if not isinstance(source, dict):
        raise ValueError('An artifact source is required')
    if set(source) - {'url', 'sha256', 'size', 'name', 'revision', 'format'}:
        raise ValueError('Unknown artifact property')
    url = source.get('url', '')
    if not isinstance(url, str):
        raise ValueError('Artifact URL must be a string')
    parsed = urlsplit(url)
    if len(url) > 8192 or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Artifact source must be HTTPS without embedded credentials')
    if not isinstance(source.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', source['sha256']):
        raise ValueError('An exact SHA256 is required')
    if type(source.get('size')) is not int or not 0 < source['size'] <= 2**40:
        raise ValueError('Declare an exact artifact size between 1 byte and 1 TiB')
    for field in ('name', 'revision', 'format'):
        if not isinstance(source.get(field), str) or not 0 < len(source[field]) <= 256:
            raise ValueError(f'An artifact {field} is required')
    return dict(source)


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            raise ValueError('Artifact redirect must remain HTTPS')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        # The cache only downloads public/signed URLs. No inherited user token.
        if redirected:
            redirected.remove_header('Authorization')
            redirected.remove_header('Cookie')
        return redirected


@contextmanager
def file_lock(path):
    """One writer per job store/blob, including distinct connector processes."""
    with open(path, 'a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if not lock.tell():
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ArtifactCache:
    def __init__(self, root, *, opener=None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.opener = opener or build_opener(HTTPSRedirect())

    def fetch(self, source, cancelled, progress):
        source = validate_source(source)
        digest, size = source['sha256'], source['size']
        target = self.root / digest
        part = self.root / (digest + '.part')
        receipt = self.root / (digest + '.json')
        with file_lock(self.root / (digest + '.lock')):
            if target.exists() and receipt.exists():
                stored, stat = json.loads(receipt.read_text()), target.stat()
                if stored.get('sha256') == digest and stored.get('size') == size == stat.st_size and stored.get('mtime_ns') == stat.st_mtime_ns:
                    progress('ready', size)
                    return target
                raise ValueError('Cached artifact changed; explicitly repair the owned cache before reuse')
            if target.exists():
                # Crash after atomic rename but before receipt commit. Verify
                # these bytes locally instead of redownloading a large model.
                self.verify(target, digest, size, cancelled)
                atomic_json(receipt, {'sha256': digest, 'size': size, 'mtime_ns': target.stat().st_mtime_ns})
                progress('ready', size)
                return target
            done = part.stat().st_size if part.exists() else 0
            if done > size:
                raise ValueError('Partial download is larger than the pinned artifact')
            if cancelled.is_set():
                raise Cancelled()
            if shutil.disk_usage(self.root).free < size - done + max(64 << 20, size // 100):
                raise ValueError('Insufficient disk space for this artifact and verification margin')
            if done < size:
                headers = {'Accept-Encoding': 'identity'}
                if done:
                    headers['Range'] = f'bytes={done}-'
                with self.opener.open(Request(source['url'], headers=headers), timeout=30) as response:
                    status = response.status
                    if status == 206:
                        expected = f'bytes {done}-{size-1}/{size}'
                        if response.headers.get('Content-Range') != expected:
                            raise ValueError('Download range does not match the pinned artifact')
                    elif status == 200:
                        done = 0  # Server ignored Range; overwrite, never append duplicate bytes.
                    else:
                        raise ValueError('Artifact endpoint returned an unexpected status')
                    if response.headers.get('Content-Encoding', 'identity') != 'identity':
                        raise ValueError('Compressed transfer cannot be verified as declared')
                    length = response.headers.get('Content-Length')
                    if length is not None and int(length) != size - done:
                        raise ValueError('Artifact size changed')
                    with open(part, 'ab' if done else 'wb') as output:
                        os.chmod(part, 0o600)
                        progress('downloading', done)
                        while True:
                            if cancelled.is_set():
                                raise Cancelled()
                            block = getattr(response, 'read1', response.read)(1 << 20)
                            if not block:
                                break
                            done += len(block)
                            if done > size:
                                raise ValueError('Artifact exceeds declared size')
                            output.write(block)
                            progress('downloading', done)
                        output.flush()
                        os.fsync(output.fileno())
            if done != size:
                raise ValueError('Download interrupted before the declared size')
            progress('verifying', done)
            try:
                self.verify(part, digest, size, cancelled)
            except ValueError:
                # The corrupt PARTIAL belongs to this cache; published blobs and
                # external model directories are never touched here.
                part.unlink()
                raise ValueError('Artifact checksum mismatch')
            os.chmod(part, 0o400)
            os.replace(part, target)
            atomic_json(receipt, {'sha256': digest, 'size': size, 'mtime_ns': target.stat().st_mtime_ns})
            progress('ready', size)
            return target

    @staticmethod
    def verify(path, digest, size, cancelled):
        if path.stat().st_size != size:
            raise ValueError('Artifact size mismatch')
        checksum = hashlib.sha256()
        with open(path, 'rb') as stream:
            while block := stream.read(1 << 20):
                if cancelled.is_set():
                    raise Cancelled()
                checksum.update(block)
        if checksum.hexdigest() != digest:
            raise ValueError('Artifact checksum mismatch')


class DownloadJobs:
    """Bounded durable job metadata; no media or weights enter control messages.

    Construct once per deployment's stable task directory. A store lock prevents
    a second manager from relabeling a still-running worker after reconnect.
    Explicit resume is required after a process crash or failed/cancelled job.
    """
    def __init__(self, directory, cache):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cache = cache
        self.mutex = threading.RLock()
        self.workers = {}
        self.closed = False
        self.owner = file_lock(self.directory / 'owner.lock')
        self.owner.__enter__()
        self.db = None
        try:
            self.db = sqlite3.connect(self.directory / 'jobs.sqlite3', check_same_thread=False)
            os.chmod(self.directory / 'jobs.sqlite3', 0o600)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, source TEXT NOT NULL, state TEXT NOT NULL, bytes INTEGER NOT NULL, updated REAL NOT NULL, error TEXT NOT NULL)')
            self.db.execute("UPDATE jobs SET state='interrupted', error='Worker stopped; resume the verified partial download' WHERE state IN ('queued','downloading','verifying','installing')")
            self.db.commit()
        except Exception:
            if self.db is not None:
                self.db.close()
            self.owner.__exit__(None, None, None)
            raise

    def list(self):
        with self.mutex:
            rows = self.db.execute('SELECT id,source,state,bytes,updated,error FROM jobs ORDER BY updated DESC LIMIT 256').fetchall()
        return [{'job_id': r[0], 'artifact': {k: v for k, v in json.loads(r[1]).items() if k != 'url'},
                 'state': r[2], 'bytes_done': r[3], 'updated_at': r[4], 'error': r[5]} for r in rows]

    def submit(self, job_id, source=None, *, resume=False):
        # Resume resolves the original (potentially signed) URL on the node.
        # Lists and browser history never need to receive that credential.
        if source is None and resume:
            with self.mutex:
                old = self.db.execute('SELECT source FROM jobs WHERE id=?', (job_id,)).fetchone()
                if not old:
                    raise ValueError('Download no longer exists; refresh its history')
                source = json.loads(old[0])
        source = validate_source(source)
        # Engine jobs use immutable recipe IDs containing semantic versions.
        # IDs are SQLite keys, never paths; keep leading dots and separators out.
        if not re.fullmatch('[a-z0-9][a-z0-9_.-]{0,79}', job_id):
            raise ValueError('Invalid artifact job id')
        encoded = json.dumps(source, sort_keys=True)
        with self.mutex:
            if self.closed:
                raise ValueError('Download worker is closing')
            old = self.db.execute('SELECT source,state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if old and old[0] != encoded:
                raise ValueError('This job id already refers to another artifact')
            if old and (job_id in self.workers or old[1] == 'ready' or not resume):
                return job_id
            if len(self.workers) >= 2:
                raise ValueError('Two downloads are already active; wait or cancel one')
            if not old and self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] >= 256:
                raise ValueError('Download history is full; clear finished jobs first')
            self.db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, error=excluded.error, updated=excluded.updated',
                            (job_id, encoded, 'queued', 0, time.time(), ''))
            self.db.commit()
            cancel = threading.Event()
            thread = threading.Thread(target=self._run, args=(job_id, source, cancel), daemon=True)
            self.workers[job_id] = (cancel, thread)
            thread.start()
            return job_id

    def _run(self, job_id, source, cancel):
        last_write, last_state, done = 0., '', 0
        def update(state, size, error=''):
            nonlocal last_write, last_state, done
            now, done = time.monotonic(), size
            if state == last_state and now - last_write < 1 and not error:
                return
            with self.mutex:
                self.db.execute('UPDATE jobs SET state=?, bytes=?, updated=?, error=? WHERE id=?', (state, size, time.time(), error, job_id))
                self.db.commit()
            last_write, last_state = now, state
        try:
            self.cache.fetch(source, cancel, update)
        except Cancelled:
            update('cancelled', done)
        except (HTTPError, OSError):
            if cancel.is_set():
                update('cancelled', done)
            else:
                update('failed', done, 'Artifact transfer failed; partial bytes retained for explicit resume')
        except Exception as exc:
            # Do not echo signed URLs or vendor response bodies into task logs.
            message = str(exc) if isinstance(exc, ValueError) else 'Artifact verification failed'
            update('failed', done, message[:256])
        finally:
            with self.mutex:
                self.workers.pop(job_id, None)

    def cancel(self, job_id):
        with self.mutex:
            worker = self.workers.get(job_id)
            if worker:
                worker[0].set()
            return bool(worker)

    def forget(self, job_id):
        with self.mutex:
            if job_id in self.workers:
                raise ValueError('Cancel and wait for the download before clearing its record')
            self.db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
            self.db.commit()  # Metadata only; verified artifacts/weights remain.

    def close(self):
        with self.mutex:
            self.closed = True
            workers = list(self.workers.values())
            for cancel, _ in workers:
                cancel.set()
        # Read timeout bounds a quiet transfer. Do not unlock while workers own
        # the database; another process could otherwise misclassify live tasks.
        for _, thread in workers:
            thread.join()
        self.db.close()
        self.owner.__exit__(None, None, None)
