"""Bounded, restart-persistent Agent event replay over ordinary App RPC.

Chat history remains authoritative. Retention gaps explicitly require a history
refresh; clients never interpret a missing range as successful delivery. Large
messages are fragmented so an event page fits the scoped RPC gateway envelope.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from pantheon.chatroom.event_hooks import ChatEventHooks

FRAGMENT = 16 * 1024
PAGE = 128 * 1024
HISTORY_TTL = 10 * 60
MAX_SNAPSHOTS = 8


class AgentEventStore(ChatEventHooks):
    """One App-owned writer; the Agent data lifetime lock must already be held."""
    def __init__(self, root, *, retain_bytes=16 * 1024 * 1024, retain_rows=8192):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._check_private(self.root, directory=True)
        self.path = self.root / 'events.sqlite3'
        history_path = self.root / 'histories.sqlite3'
        for database in (self.path, history_path):
            try:
                fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            for path in (database, Path(str(database)+'-wal'), Path(str(database)+'-shm')):
                if path.exists() or path.is_symlink():
                    self._check_private(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.history_db = None
        try:
            # History serialization must not hold the live event journal's
            # transaction/lock and make streaming hooks time out.
            self.history_db = sqlite3.connect(history_path, check_same_thread=False)
            self.history_db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            self.history_db.execute('PRAGMA journal_mode=WAL')
            self.history_db.execute('PRAGMA synchronous=FULL')
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
            self.db.execute('''CREATE TABLE IF NOT EXISTS inflight (
                seq INTEGER PRIMARY KEY, chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL, payload TEXT NOT NULL)''')
            self.db.execute('CREATE INDEX IF NOT EXISTS inflight_chat ON inflight (chat_id, message_id)')
            self.history_db.execute('''CREATE TABLE IF NOT EXISTS histories (
                id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, expires REAL NOT NULL,
                descriptor TEXT NOT NULL)''')
            self.history_db.execute('''CREATE TABLE IF NOT EXISTS history_parts (
                id TEXT NOT NULL, part INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (id, part))''')
            self.db.commit()
            self.history_db.commit()
            self.rows, self.bytes = self.db.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)),0) FROM events').fetchone()
        except BaseException:
            self.db.close()
            if self.history_db is not None:
                self.history_db.close()
            raise
        self.retain_bytes, self.retain_rows = retain_bytes, retain_rows
        self.lock = asyncio.Lock()
        self.history_lock = asyncio.Lock()
        self.closed = False

    @staticmethod
    def _check_private(path, directory=False):
        import stat
        info = path.lstat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('Agent events must live in private App data')

    async def _call(self, fn, *, history=False):
        async with self.history_lock if history else self.lock:
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
                last = self.db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
                # History only contains completed messages. Preserve the active
                # stream prefix separately from replay retention so reconnecting
                # mid-response does not start halfway through text/tool arguments.
                chunk = data.get('chunk', {}) if message_type == 'chunk' else data
                message_id = chunk.get('message_id') if isinstance(chunk, dict) else None
                if message_type in ('chunk', 'tool_delta') and isinstance(message_id, str):
                    self.db.execute('INSERT INTO inflight VALUES (?,?,?,?)', (last, chat_id, message_id, raw))
                elif message_type == 'step':
                    message = data.get('step_message', {})
                    if isinstance(message, dict) and isinstance(message.get('id'), str):
                        self.db.execute('DELETE FROM inflight WHERE chat_id=? AND message_id=?',
                                        (chat_id, message['id']))
                elif message_type == 'chat_finished':
                    self.db.execute('DELETE FROM inflight WHERE chat_id=?', (chat_id,))
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

    async def position(self):
        """Capture BEFORE copying history: overlapping events are preferable to loss."""
        def fetch():
            latest = self.db.execute('SELECT MAX(seq) FROM events').fetchone()[0] or 0
            return dict(epoch=self.epoch, sequence=latest)
        return await self._call(fetch)

    async def history_checkpoint(self, chat_id):
        """An atomic event position AND active message prefixes at that position."""
        def fetch():
            latest = self.db.execute('SELECT MAX(seq) FROM events').fetchone()[0] or 0
            active = [json.loads(row[0]) for row in self.db.execute(
                'SELECT payload FROM inflight WHERE chat_id=? ORDER BY seq', (chat_id,))]
            return dict(epoch=self.epoch, sequence=latest), active
        return await self._call(fetch)

    async def recover_interrupted_streams(self):
        """Native host startup only, before any new producers are admitted.

        An App process restart cannot resume Python execution. Publish that fact
        rather than advertising the previous process's partial stream as live.
        """
        chats = await self._call(lambda: [row[0] for row in self.db.execute('SELECT DISTINCT chat_id FROM inflight')])
        for chat_id in chats:
            await self.publish(chat_id, 'chat_finished', dict(type='chat_finished', status='interrupted',
                               metadata={'reason': 'Agent process restarted'}))

    def _prune_histories(self):
        self.history_db.execute('DELETE FROM history_parts WHERE id IN '
                        '(SELECT id FROM histories WHERE expires <= ?)', (time.time(),))
        self.history_db.execute('DELETE FROM histories WHERE expires <= ?', (time.time(),))

    async def save_history(self, chat_id, messages, cursor, inflight=()):
        """Immutable, expiring snapshot, including fields too large for one RPC.

        Caller owns a detached message copy. Never pass live memory dictionaries.
        A returned cursor is a lower bound, NOT an atomic history/event cut:
        consumers reconcile overlapping message IDs, and refresh after replay
        gaps. The snapshot is durable across App process restarts within its TTL.
        """
        if not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 256:
            raise ValueError('Invalid history chat identity')
        if (not isinstance(cursor, dict) or set(cursor) != {'epoch', 'sequence'}
                or cursor['epoch'] != self.epoch or type(cursor['sequence']) is not int
                or not 0 <= cursor['sequence'] < 2**63):
            raise ValueError('Use a history cursor from this App')

        def save():
            # Expiry is fixed: repeatedly reading an abandoned snapshot does
            # not pin storage forever. Never evict another active reader.
            with self.history_db:
                self._prune_histories()
            if self.history_db.execute('SELECT COUNT(*) FROM histories').fetchone()[0] >= MAX_SNAPSHOTS:
                raise RuntimeError('Too many active history snapshots; release one or wait for expiry')
            identity = uuid.uuid4().hex
            expires = time.time() + HISTORY_TTL
            digest, count, size, pending = hashlib.sha256(), 0, 0, ''
            encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(',', ':'))
            with self.history_db:
                for piece in encoder.iterencode(dict(messages=messages, total=len(messages), inflight=inflight)):
                    # Slice a huge single string token before concatenation.
                    for offset in range(0, len(piece), PAGE):
                        pending += piece[offset:offset+PAGE]
                        while len(pending) >= PAGE:
                            part, pending = pending[:PAGE], pending[PAGE:]
                            self.history_db.execute('INSERT INTO history_parts VALUES (?,?,?)', (identity, count, part))
                            digest.update(part.encode('ascii'))
                            size += len(part)
                            count += 1
                if pending:
                    self.history_db.execute('INSERT INTO history_parts VALUES (?,?,?)', (identity, count, pending))
                    digest.update(pending.encode('ascii'))
                    size += len(pending)
                    count += 1
                descriptor = dict(protocol=1, snapshot_id=identity, chat_id=chat_id, cursor=cursor,
                                  parts=count, size=size, sha256=digest.hexdigest(),
                                  total=len(messages), expires_at=expires)
                self.history_db.execute('INSERT INTO histories VALUES (?,?,?,?)',
                                (identity, chat_id, expires, json.dumps(descriptor)))
            return descriptor
        return await self._call(save, history=True)

    @staticmethod
    def _history_identity(chat_id, snapshot_id):
        if (not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 256
                or not isinstance(snapshot_id, str) or len(snapshot_id) != 32
                or any(c not in '0123456789abcdef' for c in snapshot_id)):
            raise ValueError('Use a history snapshot returned by this App')

    async def read_history(self, chat_id, snapshot_id, part):
        self._history_identity(chat_id, snapshot_id)
        if type(part) is not int or not 0 <= part < 2**63:
            raise ValueError('Invalid history part')
        def fetch():
            with self.history_db:
                self._prune_histories()
            row = self.history_db.execute('SELECT descriptor FROM histories WHERE id=? AND chat_id=?',
                                  (snapshot_id, chat_id)).fetchone()
            if row is None:
                raise ValueError('History snapshot expired or unavailable; open a new snapshot')
            descriptor = json.loads(row[0])
            if part >= descriptor['parts']:
                raise ValueError('Invalid history part')
            payload = self.history_db.execute('SELECT payload FROM history_parts WHERE id=? AND part=?',
                                      (snapshot_id, part)).fetchone()[0]
            return dict(protocol=1, snapshot_id=snapshot_id, chat_id=chat_id,
                        part=part, parts=descriptor['parts'], json_fragment=payload)
        return await self._call(fetch, history=True)

    async def release_history(self, chat_id, snapshot_id):
        self._history_identity(chat_id, snapshot_id)
        def release():
            with self.history_db:
                # Bind the token to its conversation, including deletion.
                self.history_db.execute('DELETE FROM history_parts WHERE id IN '
                    '(SELECT id FROM histories WHERE id=? AND chat_id=?)', (snapshot_id, chat_id))
                self.history_db.execute('DELETE FROM histories WHERE id=? AND chat_id=?', (snapshot_id, chat_id))
            return dict(success=True)
        return await self._call(release, history=True)

    async def close(self):
        async with self.history_lock, self.lock:
            if not self.closed:
                self.closed = True
                # No admitted disk thread remains once this lock is acquired.
                self.db.close()
                self.history_db.close()
