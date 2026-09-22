"""Bounded request metadata. Never retain prompts, completions or provider errors."""
import json
import os
from pathlib import Path
import sqlite3
import time


class Activity:
    HISTORY = 128

    def __init__(self, directory, lock):
        self.lock = lock
        path = Path(directory) / 'activity.sqlite3'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        # A crashed process cannot know whether an upstream accepted/completed
        # a generation. In particular, don't replay it when the service restarts.
        with self.lock, self.db:
            for request_id, raw in self.db.execute('SELECT id,record FROM requests').fetchall():
                row = json.loads(raw)
                if row['state'] in {'queued', 'running'}:
                    row.update(state='unknown', reason='connector_restarted', ended_at=time.time())
                    self._put(request_id, row)
            self._prune()

    def _put(self, request_id, row):
        self.db.execute('INSERT OR REPLACE INTO requests VALUES (?,?)',
                        (request_id, json.dumps(row, separators=(',', ':'))))

    def create(self, request_id, model, operation, revision):
        with self.lock, self.db:
            if self.db.execute('SELECT 1 FROM requests WHERE id=?', (request_id,)).fetchone():
                return False
            self._put(request_id, dict(request_id=request_id, model=model, operation=operation,
                config_revision=revision, state='queued', queued_at=time.time()))
            self._prune()
            return True

    def update(self, request_id, **fields):
        with self.lock, self.db:
            existing = self.db.execute('SELECT record FROM requests WHERE id=?', (request_id,)).fetchone()
            if existing:
                row = json.loads(existing[0])
                row.update(fields)
                self._put(request_id, row)
                self._prune()

    def _prune(self):
        # Active rows aren't evicted. Admission separately bounds them to 48.
        terminal = [(row_id, json.loads(raw)) for row_id, raw in
                    self.db.execute('SELECT id,record FROM requests').fetchall()
                    if json.loads(raw)['state'] not in {'queued', 'running'}]
        terminal.sort(key=lambda item: item[1].get('ended_at', item[1]['queued_at']), reverse=True)
        self.db.executemany('DELETE FROM requests WHERE id=?', [(i,) for i, _ in terminal[self.HISTORY:]])

    def list(self):
        with self.lock:
            rows = [json.loads(raw) for raw, in self.db.execute('SELECT record FROM requests')]
        return sorted(rows, key=lambda r: r['queued_at'], reverse=True)


class StreamMetrics:
    """Observe small SSE fields without retaining content or changing the stream."""
    def __init__(self):
        self.pending = bytearray()
        self.event = []
        self.event_size = 0
        self.done = False
        self.failed = False
        self.first_token = False
        self.usage = {}
        self.disabled = False

    def feed(self, block):
        if self.disabled:
            return
        self.pending.extend(block)
        while b'\n' in self.pending:
            line, _, rest = self.pending.partition(b'\n')
            self.pending = bytearray(rest)
            line = line.rstrip(b'\r')
            if line.startswith(b'data:'):
                self.event.append(line[5:].lstrip())
                self.event_size += len(line)
            elif not line and self.event:
                raw = b'\n'.join(self.event)
                self.event, self.event_size = [], 0
                if raw == b'[DONE]':
                    self.done = True
                    continue
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        continue
                    if value.get('error'):
                        self.failed = True
                    for choice in value.get('choices') or []:
                        delta = choice.get('delta') or {}
                        self.first_token |= any(delta.get(k) for k in
                            ('content', 'reasoning_content', 'reasoning', 'tool_calls'))
                    usage = value.get('usage') or {}
                    self.usage.update({k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                       if type(usage.get(k)) is int and 0 <= usage[k] <= 10**12})
                except (ValueError, TypeError, AttributeError):
                    pass
            if self.event_size + len(self.pending) > 1024 * 1024:
                self.disabled = True
                self.pending.clear()
                self.event.clear()
                break
        if len(self.pending) > 1024 * 1024:
            self.disabled = True
            self.pending.clear()
