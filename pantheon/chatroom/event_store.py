"""Bounded, restart-persistent Agent event replay over ordinary App RPC.

Chat history remains authoritative. Retention gaps explicitly require a history
refresh; clients never interpret a missing range as successful delivery. Large
messages are fragmented so an event page fits the scoped RPC gateway envelope.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from pantheon.chatroom.event_hooks import ChatEventHooks

FRAGMENT = 16 * 1024
PAGE = 128 * 1024


class AgentEventStore(ChatEventHooks):
    """One App-owned writer; the Agent data lifetime lock must already be held."""
    def __init__(self, root, *, retain_bytes=16 * 1024 * 1024, retain_rows=8192):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._check_private(self.root, directory=True)
        self.path = self.root / 'events.sqlite3'
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        for path in (self.path, Path(str(self.path)+'-wal'), Path(str(self.path)+'-shm')):
            if path.exists() or path.is_symlink():
                self._check_private(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        try:
            self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS metadata (epoch TEXT NOT NULL)')
            if self.db.execute('SELECT epoch FROM metadata').fetchone() is None:
                self.db.execute('INSERT INTO metadata VALUES (?)', (uuid.uuid4().hex,))
            self.epoch = self.db.execute('SELECT epoch FROM metadata').fetchone()[0]
            self.db.execute('''CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL,
                event_id TEXT NOT NULL, part INTEGER NOT NULL, total INTEGER NOT NULL,
                payload TEXT NOT NULL)''')
            self.db.execute('CREATE INDEX IF NOT EXISTS chat_sequence ON events (chat_id, seq)')
            self.db.execute('CREATE INDEX IF NOT EXISTS event_parts ON events (event_id, seq)')
            self.db.commit()
            self.rows, self.bytes = self.db.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)),0) FROM events').fetchone()
        except BaseException:
            self.db.close()
            raise
        self.retain_bytes, self.retain_rows = retain_bytes, retain_rows
        self.lock = asyncio.Lock()
        self.closed = False

    @staticmethod
    def _check_private(path, directory=False):
        import stat
        info = path.lstat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('Agent events must live in private App data')

    async def _call(self, fn):
        async with self.lock:
            if self.closed:
                raise RuntimeError('Agent event stream is closed')
            task = asyncio.create_task(asyncio.to_thread(fn))
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def publish(self, chat_id, message_type, data):
        if not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 256:
            raise ValueError('Invalid event chat identity')
        raw = json.dumps(dict(type=message_type, timestamp=time.time(),
                              data={**data, 'chat_id': chat_id}), ensure_ascii=True, allow_nan=False,
                         separators=(',', ':'))
        event_id = uuid.uuid4().hex
        parts = [raw[i:i+FRAGMENT] for i in range(0, len(raw), FRAGMENT)]

        def append():
            with self.db:
                self.db.executemany('INSERT INTO events (chat_id,event_id,part,total,payload) VALUES (?,?,?,?,?)',
                    ((chat_id, event_id, i, len(parts), part) for i, part in enumerate(parts)))
                # Keep complete events. A single newest event may exceed the
                # retention target, but it is never silently truncated.
                count, size = self.rows + len(parts), self.bytes + len(raw)
                while count > self.retain_rows or size > self.retain_bytes:
                    oldest = self.db.execute('SELECT event_id FROM events ORDER BY seq LIMIT 1').fetchone()[0]
                    if oldest == event_id:
                        break
                    rows, length, last = self.db.execute(
                        'SELECT COUNT(*), SUM(LENGTH(payload)), MAX(seq) FROM events WHERE event_id = ?',
                        (oldest,)).fetchone()
                    self.db.execute('DELETE FROM events WHERE seq <= ?', (last,))
                    count -= rows
                    size -= length
            self.rows, self.bytes = count, size
            self.db.execute('PRAGMA incremental_vacuum(64)')
        await self._call(append)

    async def read(self, chat_id, cursor=None, limit=128):
        if (not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 256
                or type(limit) is not int or not 1 <= limit <= 128):
            raise ValueError('Supply a chat identity and a bounded event page')
        if cursor is not None and (not isinstance(cursor, dict) or set(cursor) != {'epoch', 'sequence'}
                or not isinstance(cursor['epoch'], str) or type(cursor['sequence']) is not int
                or not 0 <= cursor['sequence'] < 2**63):
            raise ValueError('Use an event cursor returned by this App')

        def fetch():
            first, latest = self.db.execute('SELECT MIN(seq), MAX(seq) FROM events').fetchone()
            latest = latest or 0
            after = cursor['sequence'] if cursor else 0
            reset = ((cursor is not None and cursor['epoch'] != self.epoch) or after > latest
                     or first is not None and after < first-1)
            events, more = [], False
            if not reset:
                rows = self.db.execute('''SELECT seq,event_id,part,total,payload FROM events
                    WHERE seq > ? AND chat_id = ? ORDER BY seq LIMIT ?''',
                    (after, chat_id, limit+1)).fetchall()
                size = 0
                for seq, event, part, total, payload in rows:
                    if len(events) == limit or size + len(payload) > PAGE:
                        more = True
                        break
                    events.append(dict(sequence=seq, event_id=event, part=part, total=total,
                                       json_fragment=payload))
                    size += len(payload)
            return dict(protocol=1, chat_id=chat_id, reset_required=bool(reset), events=events,
                        cursor=dict(epoch=self.epoch, sequence=events[-1]['sequence'] if more else latest),
                        has_more=more)
        return await self._call(fetch)

    async def close(self):
        async with self.lock:
            if not self.closed:
                self.closed = True
                # No admitted disk thread remains once this lock is acquired.
                self.db.close()
