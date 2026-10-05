"""Durable, caller-scoped execution requests inside the ordinary Agent App.

The gateway must bind consumer_id on every method to the calling dependency's
logical owner. A consumer may supply tool schemas, never credentials or local
tool factories. Claiming a tool transfers responsibility for its effects to that
consumer. Cancellation stops inference; it does NOT assert that claimed tools
have stopped. They remain visible until the consumer acknowledges their outcome.

Restart never replays inference or tools. An interrupted request keeps its ID,
pending claims and immutable replies for reconciliation, even after release.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from pantheon.dependency_provider import DependencyToolProvider
from pantheon.chatroom.execution_engine import AgentExecutionCleanupError
from pantheon.utils.owned_io import run_owned_io


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_ACTIVE = {'running', 'cancelling'}
_PAYLOAD_LIMIT = 192 * 1024


def _json(value, limit=_PAYLOAD_LIMIT):
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                         separators=(',', ':')).encode()
        if len(raw) > limit:
            raise ValueError
        return raw
    except (TypeError, ValueError, RecursionError):
        raise ValueError('Execution payload is invalid or exceeds its size limit') from None


def _spec(value):
    value = json.loads(_json(value, 256 * 1024))
    if (not isinstance(value, dict) or not {'prompt', 'instructions', 'model'} <= value.keys()
            or value.keys() - {'prompt', 'instructions', 'model', 'tools', 'max_turns', 'timeout_seconds'}
            or not isinstance(value['instructions'], str)
            or not isinstance(value['prompt'], (str, list))
            or not isinstance(value['model'], str) or not 1 <= len(value['model']) <= 512):
        raise ValueError('Invalid Agent execution specification')
    value.setdefault('tools', {})
    value.setdefault('max_turns', 40)
    value.setdefault('timeout_seconds', 600)
    if (type(value['max_turns']) is not int or not 1 <= value['max_turns'] <= 500
            or type(value['timeout_seconds']) is not int or not 1 <= value['timeout_seconds'] <= 86400
            or not isinstance(value['tools'], dict) or len(value['tools']) > 16):
        raise ValueError('Invalid execution limits or tools')
    for name, functions in value['tools'].items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name) or '__' in name:
            raise ValueError('Invalid execution tool provider name')
        value['tools'][name] = list(DependencyToolProvider._validate_functions(functions).values())
    return value


class ExecutionJournal:
    """Private journal, protected by the parent Agent App's data-directory lock."""
    def __init__(self, root):
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / 'requests.sqlite3'
        if root.is_symlink() or self.path.is_symlink():
            raise ValueError('Execution storage must be owned by the Agent App')
        # Create privately before SQLite opens it (including restrictive umasks).
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS executions '
                       '(owner TEXT, id TEXT, record TEXT NOT NULL, result BLOB, PRIMARY KEY(owner,id))')
            # A previous process may have sent model/tool requests. Never infer
            # from an absent process that those requests had no effects.
            for owner, identity, raw in db.execute('SELECT owner,id,record FROM executions').fetchall():
                record = json.loads(raw)
                if record['state'] in _ACTIVE:
                    record['state'] = 'interrupted'
                    for call in record['calls'].values():
                        if call['state'] == 'queued':
                            call['state'] = 'withdrawn'
                            call.pop('args', None)
                    db.execute('UPDATE executions SET record=? WHERE owner=? AND id=?',
                               (json.dumps(record), owner, identity))

    def read(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT record FROM executions WHERE owner=? AND id=?', key).fetchone()
            return json.loads(row[0]) if row else None

    def write(self, key, record, result=None):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO executions(owner,id,record,result) VALUES(?,?,?,?) '
                       'ON CONFLICT(owner,id) DO UPDATE SET record=excluded.record, '
                       'result=COALESCE(excluded.result,executions.result)',
                       (*key, json.dumps(record, allow_nan=False), result))

    def release(self, key, record):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE executions SET record=?, result=NULL WHERE owner=? AND id=?',
                       (json.dumps(record), *key))

    def retained(self):
        with sqlite3.connect(self.path) as db:
            return sum(json.loads(raw)['state'] != 'released'
                       for (raw,) in db.execute('SELECT record FROM executions'))

    def result(self, key, offset):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT substr(result,?,32768) FROM executions WHERE owner=? AND id=?',
                             (offset + 1, *key)).fetchone()
            return row[0] if row and row[0] is not None else b''


class AgentExecutions:
    def __init__(self, journal, engine, *, max_active=16, max_retained=2048):
        self.journal, self.engine = journal, engine
        self.max_active, self.max_retained = max_active, max_retained
        self._lock = asyncio.Lock()
        self._runs, self._waiters, self._work = {}, {}, {}
        self._cancelled = set()
        self._operations = set()
        self._stopping = False
        self._closed = False
        self._failure = None
        self._closing = None

    @staticmethod
    def _key(consumer_id, execution_id):
        if not all(isinstance(value, str) and _ID.fullmatch(value)
                   for value in (consumer_id, execution_id)):
            raise ValueError('Supply caller and execution identities')
        return consumer_id, execution_id

    async def _owned(self, operation):
        # Losing a submit/reply observer must not split persistence from delivery.
        if self._closed:
            operation.close()
            raise RuntimeError('Agent execution service is stopping or closed')
        task = asyncio.create_task(operation)
        self._operations.add(task)
        def done(completed):
            self._operations.discard(completed)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _read(self, key):
        record = await run_owned_io(self.journal.read, key)
        if record is None:
            raise ValueError('Execution is absent for this caller')
        return record

    async def _write(self, key, record, result=None):
        try:
            await run_owned_io(self.journal.write, key, record, result)
        except BaseException as exc:
            self._failure = exc
            raise

    @staticmethod
    def _status(record):
        pending = [call for call in record['calls'].values() if call['state'] in {'queued', 'claimed'}]
        # One bounded request per observation; acknowledged calls disappear.
        # Claim ownership is explicit, so a repeated observation is not a retry.
        next_call = next((call for call in pending if call['state'] == 'queued'),
                         pending[0] if pending else None)
        return {'protocol': 1, 'state': record['state'], 'pending_tools': len(pending),
                'call': deepcopy(next_call),
                'result': record.get('result'), 'error': record.get('error')}

    async def submit(self, consumer_id, execution_id, specification):
        key, spec = self._key(consumer_id, execution_id), _spec(specification)
        digest = hashlib.sha256(_json(spec, 256 * 1024)).hexdigest()
        async def accept():
            async with self._lock:
                record = await run_owned_io(self.journal.read, key)
                if record:
                    if record['digest'] != digest:
                        raise ValueError('Execution identity already belongs to a different request')
                    return self._status(record)
                if self._stopping or self._failure:
                    raise RuntimeError('Agent execution service is stopping or needs recovery')
                if (len(self._runs) >= self.max_active
                        or await run_owned_io(self.journal.retained) >= self.max_retained):
                    raise RuntimeError('Execution capacity reached; settle/release existing requests')
                record = {'digest': digest, 'state': 'running', 'calls': {}}
                await self._write(key, record)
                task = self._runs[key] = asyncio.create_task(self._execute(key, spec))
                def finished(done):
                    self._runs.pop(key, None)
                    self._cancelled.discard(key)
                    if not done.cancelled() and done.exception() is not None:
                        self._failure = done.exception()
                task.add_done_callback(finished)
                return self._status(record)
        return await self._owned(accept())

    async def poll(self, consumer_id, execution_id):
        async with self._lock:
            return self._status(await self._read(self._key(consumer_id, execution_id)))

    async def _invoke(self, key, provider, name, args):
        args = json.loads(_json(args))
        identity = uuid.uuid4().hex
        waiter = asyncio.get_running_loop().create_future()
        async with self._lock:
            record = await self._read(key)
            if record['state'] != 'running' or self._stopping:
                raise asyncio.CancelledError
            if len(record['calls']) >= 10000:
                raise RuntimeError('Execution tool request limit reached')
            if sum(call['state'] in {'queued', 'claimed'} for call in record['calls'].values()) >= 64:
                raise RuntimeError('Execution has too many outstanding tool requests')
            record['calls'][identity] = {'id': identity, 'provider': provider, 'name': name,
                                         'args': args, 'state': 'queued'}
            await self._write(key, record)
            self._waiters[key, identity] = waiter
        try:
            response = await asyncio.shield(waiter)
            if not response['ok']:
                raise RuntimeError(response['error'])
            return response['value']
        finally:
            self._waiters.pop((key, identity), None)

    async def claim(self, consumer_id, execution_id, call_id, worker_id):
        key = self._key(consumer_id, execution_id)
        self._key(call_id, worker_id)
        async def accept():
            async with self._lock:
                record = await self._read(key)
                call = record['calls'].get(call_id)
                if call is None:
                    raise ValueError('Tool request is absent')
                if call['state'] == 'claimed':
                    if call['worker_id'] != worker_id:
                        raise ValueError('Tool request is already owned by another worker')
                    # Receipt recovery only; caller must use its own durable
                    # operation ledger before starting or resuming tool effects.
                    return {'claimed': True, 'recovered': True, 'call': deepcopy(call)}
                if call['state'] != 'queued' or record['state'] != 'running' or self._stopping:
                    return {'claimed': False}
                call.update(state='claimed', worker_id=worker_id)
                await self._write(key, record)
                return {'claimed': True, 'recovered': False, 'call': deepcopy(call)}
        return await self._owned(accept())

    async def reply(self, consumer_id, execution_id, call_id, worker_id, response):
        key = self._key(consumer_id, execution_id)
        self._key(call_id, worker_id)
        raw = _json(response)
        response = json.loads(raw)
        if (not isinstance(response, dict) or type(response.get('ok')) is not bool
                or set(response) != ({'ok', 'value'} if response['ok'] else {'ok', 'error'})
                or not response['ok'] and not isinstance(response['error'], str)):
            raise ValueError('Invalid execution tool response')
        digest = hashlib.sha256(raw).hexdigest()
        async def accept():
            async with self._lock:
                record = await self._read(key)
                call = record['calls'].get(call_id)
                if not call or call.get('worker_id') != worker_id:
                    raise ValueError('Tool request does not belong to this worker')
                if call['state'] == 'settled':
                    if call['reply_digest'] != digest:
                        raise ValueError('Tool request already has a different response')
                    return {'accepted': True}
                if call['state'] != 'claimed':
                    raise ValueError('Tool request was not claimed')
                call.update(state='settled', reply_digest=digest)
                call.pop('args', None)
                await self._write(key, record)
                waiter = self._waiters.get((key, call_id))
                if waiter is not None and not waiter.done():
                    waiter.set_result(response)
                return {'accepted': True}
        return await self._owned(accept())

    async def cancel(self, consumer_id, execution_id):
        key = self._key(consumer_id, execution_id)
        async def accept():
            async with self._lock:
                record = await self._read(key)
                if record['state'] == 'running':
                    record['state'] = 'cancelling'
                    await self._write(key, record)
                    self._cancel_work(key)
                return self._status(record)
        return await self._owned(accept())

    async def read_result(self, consumer_id, execution_id, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError('Supply a nonnegative byte offset')
        key = self._key(consumer_id, execution_id)
        async with self._lock:
            record = await self._read(key)
            meta = record.get('result')
            if not meta or offset > meta['size']:
                raise ValueError('Execution result is unavailable at this offset')
            raw = await run_owned_io(self.journal.result, key, offset)
            return {**meta, 'offset': offset, 'data': base64.b64encode(raw).decode(),
                    'next_offset': offset + len(raw)}

    async def release(self, consumer_id, execution_id):
        key = self._key(consumer_id, execution_id)
        async def accept():
            async with self._lock:
                record = await self._read(key)
                if record['state'] in _ACTIVE or any(c['state'] == 'claimed' for c in record['calls'].values()):
                    raise ValueError('Settle execution and claimed tool outcomes before release')
                record.update(state='released', result=None)
                # Keep immutable call/reply identities, preventing duplicate
                # accepted replies or submissions from recreating an execution.
                await run_owned_io(self.journal.release, key, record)
                return self._status(record)
        return await self._owned(accept())

    async def _execute(self, key, spec):
        state, result, error = 'completed', None, None
        try:
            async def invoke(provider, name, args):
                functions = {f['name']: f for f in spec['tools'].get(provider, [])}
                params = functions.get(name, {}).get('parameters', {})
                if (not isinstance(args, dict) or name not in functions
                        or not args.keys() <= params['properties'].keys()
                        or not set(params.get('required', [])) <= args.keys()):
                    raise ValueError('Tool arguments are absent from this execution schema')
                return await self._invoke(key, provider, name, args)
            if key in self._cancelled:
                raise asyncio.CancelledError
            task = self._work[key] = asyncio.create_task(
                asyncio.wait_for(self.engine(spec, invoke), spec['timeout_seconds']))
            result = _json(await task, 16 * 1024 * 1024)
        except asyncio.CancelledError:
            state = 'cancelled'
        except asyncio.TimeoutError:
            state, error = 'failed', 'execution_timeout'
        except AgentExecutionCleanupError as exc:
            self._failure = exc
            state, error = 'failed', 'execution_cleanup_failed'
        except Exception:
            state, error = 'failed', 'execution_failed'
        finally:
            self._work.pop(key, None)
            async with self._lock:
                record = await self._read(key)
                record.update(state=state, error=error)
                for call in record['calls'].values():
                    if call['state'] == 'queued':
                        call['state'] = 'withdrawn'
                        call.pop('args', None)
                if result is not None:
                    record['result'] = {'size': len(result), 'sha256': hashlib.sha256(result).hexdigest()}
                await self._write(key, record, result)

    def _cancel_work(self, key):
        # Never cancel the owner that writes the final receipt, including the
        # interval before it starts or after its engine has already returned.
        self._cancelled.add(key)
        task = self._work.get(key)
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    async def close(self):
        self._stopping = True
        if self._closing is None:
            async def finish():
                # Complete accepted submissions before enumerating engines.
                while self._operations:
                    await asyncio.gather(*tuple(self._operations), return_exceptions=True)
                pending = list(self._runs.values())
                for key in self._runs:
                    self._cancel_work(key)
                await asyncio.gather(*pending, return_exceptions=True)
                while self._operations:
                    await asyncio.gather(*tuple(self._operations), return_exceptions=True)
                self._closed = True
                if self._failure:
                    raise RuntimeError('Agent execution journal needs recovery') from self._failure
            self._closing = asyncio.create_task(finish())
        await asyncio.shield(self._closing)
