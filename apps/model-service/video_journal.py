"""Crash-safe upstream identity for the asynchronous video worker.

This journal stores no request payload and never authorizes a new POST after a
restart. An uncertain submission remains outstanding even without an engine ID.
The worker must retain admission/resource ownership while outstanding is true.
"""
import json
import re


class VideoJournal:
    def __init__(self, store):
        self.store = store
        with store.transaction():
            store.db.execute('''CREATE TABLE IF NOT EXISTS video_jobs (
                job TEXT PRIMARY KEY, record TEXT NOT NULL)''')

    def _read(self, job):
        if not isinstance(job, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', job):
            raise ValueError('Invalid video job identity')
        row = self.store.db.execute('SELECT record FROM video_jobs WHERE job=?', (job,)).fetchone()
        if not row:
            raise KeyError('Unknown video job')
        return json.loads(row[0])

    def _write(self, job, record):
        self.store.db.execute('INSERT OR REPLACE INTO video_jobs VALUES (?,?)',
                             (job, json.dumps(record, allow_nan=False, separators=(',', ':'))))

    def create(self, job, revision, output_id):
        with self.store.transaction():
            return self.stage(job, revision, output_id)

    def stage(self, job, revision, output_id):
        """Share the transaction that admits inference and reserves its output."""
        if not self.store.db.in_transaction:
            raise ValueError('Video admission requires a transaction')
        if (not isinstance(job, str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', job)
                or not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision)
                or not isinstance(output_id, str) or not re.fullmatch('[a-f0-9]{32}', output_id)):
            raise ValueError('Invalid video binding or reservation')
        old = self.store.db.execute('SELECT record FROM video_jobs WHERE job=?', (job,)).fetchone()
        if old:
            record = json.loads(old[0])
            if record['revision'] != revision or record['output_id'] != output_id:
                raise ValueError('Video job identity already used')
            return record
        record = {'revision': revision, 'output_id': output_id, 'phase': 'prepared',
                  'upstream_id': None, 'upstream_state': None, 'progress': 0,
                  'cancel_requested': False, 'outstanding': False}
        self._write(job, record)
        return record

    def read(self, job, revision):
        with self.store.lock:
            record = self._read(job)
            if record['revision'] != revision:
                raise ValueError('Video binding changed; do not contact another engine')
            return record

    def begin(self, job, revision):
        """Commit before network submission, never after it or on a retry."""
        with self.store.transaction():
            record = self.read(job, revision)
            if record['phase'] != 'prepared' or record['cancel_requested']:
                raise ValueError('Video creation cannot be replayed')
            record.update(phase='uncertain', outstanding=True)
            self._write(job, record)
            return record

    def observe(self, job, revision, receipt):
        # Only the protocol parser's small normalized receipt is admitted here.
        if (not isinstance(receipt, dict) or set(receipt) != {'id', 'state', 'progress'}
                or not isinstance(receipt['id'], str)
                or not re.fullmatch('[A-Za-z0-9_-]{1,128}', receipt['id'])
                or receipt['state'] not in {'queued', 'in_progress', 'completed', 'failed'}
                or type(receipt['progress']) not in (int, float)
                or not 0 <= receipt['progress'] <= 100):
            raise ValueError('Invalid video observation')
        with self.store.transaction():
            record = self.read(job, revision)
            if record['phase'] not in {'uncertain', 'observing', 'terminal'}:
                raise ValueError('Video has not been submitted')
            if record['upstream_id'] is not None and record['upstream_id'] != receipt['id']:
                raise ValueError('Upstream video identity changed')
            if record['phase'] == 'terminal' and record['upstream_state'] != receipt['state']:
                raise ValueError('Terminal video changed state')
            terminal = receipt['state'] in {'completed', 'failed'}
            record.update(upstream_id=receipt['id'], upstream_state=receipt['state'],
                          progress=receipt['progress'], outstanding=not terminal,
                          phase='terminal' if terminal else 'observing')
            self._write(job, record)
            return record

    def cancel(self, job, revision):
        with self.store.transaction():
            record = self.read(job, revision)
            record['cancel_requested'] = True
            if record['phase'] == 'prepared':
                record['phase'] = 'not_submitted'
            # After submission, this is intent only. Keep the engine identity and
            # outstanding flag until a terminal observation proves GPU work ended.
            self._write(job, record)
            return record

    def recovery(self, job, revision):
        record = self.read(job, revision)
        return {'action': 'observe' if record['phase'] == 'observing' else
                         'collect' if record['phase'] == 'terminal' else
                         'unknown' if record['phase'] == 'uncertain' else 'not_submitted',
                **record}

    def remove(self, job, revision):
        with self.store.transaction():
            record = self.read(job, revision)
            if record['outstanding']:
                raise ValueError('Upstream GPU work has not been confirmed terminal')
            self.store.db.execute('DELETE FROM video_jobs WHERE job=?', (job,))
