"""Durable Agent identities and provisioning intents, not credentials.

One App data namespace has one local writer. The lifetime sidecar lock protects
ordinary process restarts on the same filesystem, not separate cloud replicas.
The platform must fence a replaced deployment before mounting its data here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from types import MappingProxyType
from uuid import UUID, uuid4

from pantheon.factory.instances import _config, _identifier
from pantheon.platform.registry_lock import registry_lock


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class InstanceIntent:
    instance_id: str
    conversation_id: str
    config_id: str
    config_revision: str
    operation_id: str
    config: object = field(repr=False)

    def identity(self):
        return {key: getattr(self, key) for key in
                ("instance_id", "conversation_id", "config_id", "config_revision")}


class AgentInstanceStore:
    """Atomic reservation before any remote work; no new IDs on retry.

    Config keys currently identify conversation members through the legacy team
    adapter. Config revisions are immutable; editing a member retains its
    instance identity and creates a separate binding/creation intent.
    """
    def __init__(self, root, *, namespace):
        if not _identifier(namespace):
            raise ValueError("Agent data namespace is required")
        self.root = Path(root)
        self.namespace = namespace
        self._mutex = RLock()
        self._closed = True
        self._lock = None
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError("Agent instance data directory must not be a symlink")
        if os.name == "posix":
            info = self.root.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Agent instance data directory must be private")
        self._lock = registry_lock(self.root / "writer.lock", timeout=0)
        self._lock.__enter__()
        try:
            path = self.root / "instances.sqlite3"
            if path.is_symlink():
                raise ValueError("Agent instance database must not be a symlink")
            # Create privately before SQLite opens it, without overwriting an
            # existing database (including corrupt/unsupported ones).
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600) if not path.exists() else None
            if fd is not None:
                os.close(fd)
            if os.name == "posix" and (path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077):
                raise ValueError("Agent instance database must be private")
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA synchronous=FULL")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("Unsupported Agent instance schema")
            with self._db:
                # sqlite3 does not implicitly begin a transaction for DDL.
                # A failed first initialization must not leave half a schema.
                self._db.execute("BEGIN IMMEDIATE")
                if version == 0:
                    if self._db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                        raise ValueError("Unrecognized Agent instance database")
                    self._db.execute("CREATE TABLE metadata (namespace TEXT NOT NULL)")
                    self._db.execute("INSERT INTO metadata VALUES (?)", (namespace,))
                    self._db.execute("""CREATE TABLE instances (
                        instance_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                        config_id TEXT NOT NULL, UNIQUE (conversation_id, config_id))""")
                    self._db.execute("""CREATE TABLE revisions (
                        instance_id TEXT NOT NULL REFERENCES instances(instance_id),
                        revision TEXT NOT NULL, config TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                        PRIMARY KEY (instance_id, revision))""")
                if version < 2:
                    self._db.execute("""CREATE TABLE retirements (
                        conversation_id TEXT PRIMARY KEY, state TEXT NOT NULL)""")
                    self._db.execute("PRAGMA user_version=2")
                if self._db.execute("SELECT namespace FROM metadata").fetchall() != [(namespace,)]:
                    raise ValueError("Agent instance data belongs to a different namespace")
            self._closed = False
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._lock.__exit__(None, None, None)
            self._lock = None
            raise

    def reserve(self, conversation_id, configs):
        if (not _identifier(conversation_id) or not isinstance(configs, dict)
                or not 1 <= len(configs) <= 256 or not all(_identifier(key) for key in configs)):
            raise ValueError("Supply a conversation and its member configurations")
        prepared = {key: _config(value) for key, value in configs.items()}
        if len({value[0]["name"] for value in prepared.values()}) != len(prepared):
            raise ValueError("Conversation member names must be distinct")
        with self._mutex:
            if self._closed:
                raise RuntimeError("Agent instance store is closed")
            result = []
            with self._db:
                if self._db.execute('SELECT 1 FROM retirements WHERE conversation_id=?', (conversation_id,)).fetchone():
                    raise ValueError('Conversation is retiring or retired')
                for key, (config, revision) in prepared.items():
                    row = self._db.execute(
                        "SELECT instance_id FROM instances WHERE conversation_id=? AND config_id=?",
                        (conversation_id, key)).fetchone()
                    identity = row[0] if row else str(uuid4())
                    if str(UUID(identity)) != identity:
                        raise ValueError("Invalid stored Agent identity")
                    if row is None:
                        self._db.execute("INSERT INTO instances VALUES (?, ?, ?)", (identity, conversation_id, key))
                    old = self._db.execute(
                        "SELECT config, operation_id FROM revisions WHERE instance_id=? AND revision=?",
                        (identity, revision)).fetchone()
                    operation = old[1] if old else str(uuid4())
                    if old is not None and _config(json.loads(old[0])) != (config, revision):
                        raise ValueError("Stored Agent revision does not match its content")
                    if str(UUID(operation)) != operation:
                        raise ValueError("Invalid stored Agent provisioning operation")
                    if old is None:
                        self._db.execute("INSERT INTO revisions VALUES (?, ?, ?, ?)",
                            (identity, revision, json.dumps(config, sort_keys=True), operation))
                    result.append(InstanceIntent(identity, conversation_id, key, revision, operation, _freeze(config)))
            return tuple(result)

    def retirements(self):
        with self._mutex:
            if self._closed:
                raise RuntimeError('Agent instance store is closed')
            values = dict(self._db.execute('SELECT conversation_id, state FROM retirements'))
            if any(not _identifier(key) or state not in ('retiring', 'retired') for key, state in values.items()):
                raise ValueError('Invalid stored Agent retirement')
            return values

    def begin_retirement(self, conversation_id):
        if not _identifier(conversation_id):
            raise ValueError('Supply a conversation identity')
        with self._mutex:
            if self._closed:
                raise RuntimeError('Agent instance store is closed')
            with self._db:
                self._db.execute('INSERT OR IGNORE INTO retirements VALUES (?, ?)', (conversation_id, 'retiring'))
                identities = tuple(row[0] for row in self._db.execute(
                    'SELECT instance_id FROM instances WHERE conversation_id=? ORDER BY instance_id', (conversation_id,)))
                if any(str(UUID(identity)) != identity for identity in identities):
                    raise ValueError('Invalid stored Agent identity')
                return identities

    def finish_retirement(self, conversation_id):
        with self._mutex:
            if self._closed:
                raise RuntimeError('Agent instance store is closed')
            with self._db:
                if self._db.execute('UPDATE retirements SET state=? WHERE conversation_id=?',
                                    ('retired', conversation_id)).rowcount != 1:
                    raise ValueError('Conversation has no durable retirement intent')

    def close(self):
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            self._db.close()
            self._lock.__exit__(None, None, None)
            self._lock = None
