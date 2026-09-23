"""Owner-scoped durable intent for coordinated Fleet starts.

The caller supplies a path on persistent storage, never a temporary directory.
No RPC may precede the successful SQLite commit which claims that RPC. Starts
are never replayed; aborts use node-side fences and exact idempotent stops.
Keep this journal while any group exists.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import uuid


class GroupConflict(RuntimeError):
    pass


class GroupJournal:
    def __init__(self, path, owner):
        if not isinstance(owner, str) or not owner or len(owner) > 200:
            raise ValueError('A concrete Fleet owner is required')
        self.path, self.owner = str(Path(path)), owner
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS model_groups '
                       '(owner TEXT, group_id TEXT, revision INTEGER, record TEXT, '
                       'PRIMARY KEY(owner, group_id))')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def plan(owner, group_id, targets):
        """Allocate all operation identities without touching storage or Fleet."""
        if not isinstance(owner, str) or not owner or len(owner) > 200:
            raise ValueError('A concrete Fleet owner is required')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', group_id):
            raise ValueError('Invalid group id')
        if not isinstance(targets, list) or not 2 <= len(targets) <= 16:
            raise ValueError('Declare 2..16 explicit nodes')
        members, nodes = [], set()
        for target in targets:
            if set(target) != {'node_id', 'digest', 'scope', 'generation'}:
                raise ValueError('Pin node, installed digest, scope and generation')
            node, digest, scope, generation = (target[k] for k in ('node_id', 'digest', 'scope', 'generation'))
            if (not isinstance(node, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', node)
                    or node in nodes or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
                    or not isinstance(scope, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', scope)
                    or type(generation) is not int or not 0 <= generation < 2**63):
                raise ValueError('Invalid or duplicate group target')
            nodes.add(node)
            preparation = uuid.uuid4().hex
            request = dict(protocol=1, digest=digest, scope=scope, generation=generation)
            members.append(dict(target=deepcopy(target), prepare=dict(sent=False, request={**request,
                'action': 'prepare_start', 'operation_id': preparation}), start=dict(sent=False,
                request={**request, 'action': 'start', 'generation': generation + 1,
                         'operation_id': uuid.uuid4().hex, 'start_preparation_id': preparation}), stop=None))
        row = dict(protocol=1, owner=owner, group_id=group_id, revision=1,
                   phase='preparing', members=members)
        return row

    def create(self, group_id, targets):
        row = self.plan(self.owner, group_id, targets)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM model_groups WHERE owner=?', (self.owner,)).fetchone()[0] >= 128:
                raise GroupConflict('Group journal limit reached; archive confirmed stopped groups first')
            try:
                db.execute('INSERT INTO model_groups VALUES (?, ?, ?, ?)',
                           (self.owner, group_id, 1, json.dumps(row)))
            except sqlite3.IntegrityError as exc:
                raise GroupConflict('Group already exists; resume its original intent') from exc
        return row

    def load(self, group_id):
        with closing(self.connect()) as db:
            result = db.execute('SELECT record FROM model_groups WHERE owner=? AND group_id=?',
                                (self.owner, group_id)).fetchone()
        if result is None:
            raise KeyError(group_id)
        row = json.loads(result[0])
        if row.get('protocol') != 1 or row.get('owner') != self.owner or row.get('group_id') != group_id:
            raise GroupConflict('Group journal identity or protocol changed')
        return row

    def save(self, row):
        if row.get('owner') != self.owner:
            raise GroupConflict('Group belongs to another owner')
        updated = deepcopy(row)
        updated['revision'] += 1
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT record FROM model_groups WHERE owner=? AND group_id=? AND revision=?',
                                  (self.owner, row['group_id'], row['revision'])).fetchone()
            if previous is None:
                raise GroupConflict('Group changed; observe its current intent before continuing')
            old = json.loads(previous[0])
            allowed = {'preparing': {'preparing', 'committing', 'aborting'},
                       'committing': {'committing', 'ready', 'aborting'},
                       'ready': {'ready', 'committing', 'aborting'},
                       'aborting': {'aborting', 'stopped'}, 'stopped': {'stopped'}}
            if (row.get('protocol') != 1 or row['phase'] not in allowed[old['phase']]
                    or len(old['members']) != len(row['members'])):
                raise GroupConflict('Invalid group transition')
            for before, after in zip(old['members'], row['members']):
                if before['target'] != after['target']:
                    raise GroupConflict('Group targets are immutable')
                for key in ('prepare', 'start', 'stop'):
                    if before[key] and (not after[key] or before[key]['request'] != after[key]['request']
                                       or (before[key]['sent'] and not after[key]['sent'])):
                        raise GroupConflict('Group operation identity and delivery claims are immutable')
            result = db.execute('UPDATE model_groups SET revision=?, record=? '
                                'WHERE owner=? AND group_id=? AND revision=?',
                                (updated['revision'], json.dumps(updated), self.owner,
                                 row['group_id'], row['revision']))
            if result.rowcount != 1:
                raise GroupConflict('Group changed; observe its current intent before continuing')
        return updated
