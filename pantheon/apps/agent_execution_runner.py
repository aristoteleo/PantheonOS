"""Caller-owned tools for agent-execution@1; no embedded Agent or model client.

The application supplies authorized tools and a durable binding identity. Tool
callbacks return protocol replies and must join their effects before returning.
An exception means effects are uncertain. Cancellation is opt-in per tool and
requires the same join guarantee. Observation cancellation never cancels work.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from pantheon.utils.owned_io import run_owned_io


class ExecutionRecoveryRequired(RuntimeError):
    """Do not reuse a workspace or start a replacement execution after this."""


class ExecutionEnded(RuntimeError):
    def __init__(self, state, error=None):
        self.state, self.error = state, error
        super().__init__(f'Agent execution ended: {state}')


def _encode(value, limit):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                     separators=(',', ':'))
    if len(raw.encode()) > limit:
        raise ValueError('Execution receipt exceeds its size limit')
    return raw


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('Supply a durable execution identity')
    return value


class ToolReceiptJournal:
    """One process owns this private mount until all accepted tools have joined.

    This is local fencing, not fencing independent replicas on separate mounts.
    The launcher must retain the same logical provider/consumer binding on reopen.
    """
    def __init__(self, root, binding_id):
        _identity(binding_id)
        root = Path(root).absolute()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or os.name == 'posix' and (
                root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077):
            raise ValueError('Tool receipt storage must be private')
        self.path = root / 'tool-receipts.sqlite3'
        self._fd = None
        try:
            self._fd = self._open(root / 'writer.lock')
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self._fd).st_size == 0:
                    os.write(self._fd, b'0')
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(self._open(self.path))
            with sqlite3.connect(self.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS binding (id TEXT NOT NULL)')
                rows = db.execute('SELECT id FROM binding').fetchall()
                if rows and rows != [(binding_id,)]:
                    raise ValueError('Receipt storage belongs to another execution binding')
                if not rows:
                    db.execute('INSERT INTO binding VALUES (?)', (binding_id,))
                db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, spec TEXT NOT NULL, '
                           'state TEXT NOT NULL, result TEXT, error TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS calls (run TEXT, id TEXT, request TEXT NOT NULL, '
                           'worker TEXT NOT NULL, state TEXT NOT NULL, response TEXT, PRIMARY KEY(run,id))')
                # A crash between effect and saved result cannot be inferred away.
                db.execute("UPDATE calls SET state='unknown' WHERE state='executing'")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _open(path):
        if path.is_symlink():
            raise ValueError('Receipt storage must not be a symlink')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        if os.name == 'posix':
            os.fchmod(fd, 0o600)
        return fd

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def prepare(self, identity, spec):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT spec,state,result,error FROM runs WHERE id=?', (identity,)).fetchone()
            if row:
                if row[0] != spec:
                    raise ValueError('Execution identity belongs to another specification')
                if db.execute("SELECT 1 FROM calls WHERE run=? AND state IN ('executing','unknown')",
                              (identity,)).fetchone():
                    raise ExecutionRecoveryRequired('Tool outcome is unknown; reconcile the saved execution')
                return row[1], json.loads(row[2]) if row[2] else None, row[3]
            db.execute("INSERT INTO runs(id,spec,state) VALUES(?,?,'active')", (identity, spec))
            return 'active', None, None

    def call(self, run, call):
        request = _encode({key: call[key] for key in ('id', 'provider', 'name', 'args')}, 256 * 1024)
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT request,worker,state,response FROM calls WHERE run=? AND id=?',
                             (run, call['id'])).fetchone()
            if row:
                if row[0] != request:
                    raise ExecutionRecoveryRequired('Agent changed a saved tool request')
                return row[1], row[2], json.loads(row[3]) if row[3] else None
            worker = uuid.uuid4().hex
            db.execute("INSERT INTO calls VALUES (?,?,?,?,'prepared',NULL)", (run, call['id'], request, worker))
            return worker, 'prepared', None

    def pending_replies(self, run):
        with sqlite3.connect(self.path) as db:
            return [(identity, worker, json.loads(response)) for identity, worker, response in db.execute(
                "SELECT id,worker,response FROM calls WHERE run=? AND state='completed'", (run,))]

    def update_call(self, run, identity, state, response=None):
        raw = _encode(response, 192 * 1024) if response is not None else None
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE calls SET state=?,response=COALESCE(?,response) WHERE run=? AND id=?',
                       (state, raw, run, identity))

    def finish(self, run, state, result=None, error=None):
        raw = _encode(result, 16 * 1024 * 1024) if result is not None else None
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE runs SET state=?,result=?,error=? WHERE id=?', (state, raw, error, run))

    def unfinished(self):
        with sqlite3.connect(self.path) as db:
            return {identity for (identity,) in db.execute("SELECT id FROM runs WHERE state='active'")}

    def release(self, run, *, commit=False):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT state FROM runs WHERE id=?', (run,)).fetchone()
            if not row or row[0] == 'active' or db.execute(
                    "SELECT 1 FROM calls WHERE run=? AND state NOT IN ('replied','withdrawn')", (run,)).fetchone():
                raise ExecutionRecoveryRequired('Settle the execution before releasing its receipts')
            if commit:
                db.execute("UPDATE runs SET state='released',result=NULL WHERE id=?", (run,))
                db.execute('DELETE FROM calls WHERE run=?', (run,))
            return row[0]


class AgentExecutionRunner:
    """Drive inference while retaining caller-owned tool effects and receipts.

    `binding_id` identifies the pinned logical Agent/consumer relationship, not
    a rotating credential. `tool_handler(provider, name, args)` is async and
    returns {ok: True, value: ...} or {ok: False, error: ...}. Exceptions fail
    closed. Only tools explicitly listed in cancel_tools may be cancelled; all
    others drain. The owner closes tools and the borrowed client AFTER close().
    """
    def __init__(self, client, root, *, binding_id, tool_handler, cancel_tools=(),
                 poll_interval=.05, stop_timeout=30):
        if not callable(tool_handler) or poll_interval <= 0 or stop_timeout <= 0:
            raise ValueError('Supply an owned tool handler and positive polling limits')
        self.client, self.tool_handler = client, tool_handler
        self.cancel_tools = frozenset(cancel_tools)
        self.poll_interval, self.stop_timeout = poll_interval, stop_timeout
        self.journal = ToolReceiptJournal(root, binding_id)
        self._runs, self._stops, self._tools, self._specs = {}, {}, {}, {}
        self._callbacks = {}
        self._releases = {}
        self._recovery = None
        try:
            self._restore = self.journal.unfinished()
        except BaseException:
            self.journal.close()
            raise
        self._closed, self._closing = False, None

    async def run(self, execution_id, specification):
        if self._closed:
            raise RuntimeError('Execution caller is closing')
        if self._recovery or self._restore and execution_id not in self._restore:
            raise ExecutionRecoveryRequired('Reconcile saved executions before admitting new work')
        if execution_id in self._releases:
            await asyncio.shield(self._releases[execution_id])
            raise ExecutionEnded('released')
        _identity(execution_id)
        spec = json.loads(_encode(specification, 256 * 1024))
        spec.setdefault('tools', {})
        spec.setdefault('max_turns', 40)
        spec.setdefault('timeout_seconds', 600)
        raw = _encode(spec, 256 * 1024)
        if execution_id in self._runs:
            if self._specs[execution_id] != raw:
                raise ValueError('Execution identity belongs to another specification')
        else:
            self._specs[execution_id] = raw
            self._stops[execution_id] = asyncio.Event()
            self._tools[execution_id] = {}
            self._callbacks[execution_id] = {}
            task = self._runs[execution_id] = asyncio.create_task(self._drive(execution_id, spec, raw))
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        # Losing an HTTP observer is not permission to restart or abandon tools.
        return await asyncio.shield(self._runs[execution_id])

    async def _reply(self, run, identity, worker, response):
        receipt = await self.client.reply(run, identity, worker, response)
        if receipt != {'accepted': True}:
            raise ExecutionRecoveryRequired('Tool reply was not acknowledged')
        await run_owned_io(self.journal.update_call, run, identity, 'replied')

    async def _tool(self, run, call, spec):
        identity = _identity(call['id'])
        functions = {item['name']: item for item in spec['tools'].get(call['provider'], [])}
        params = functions.get(call['name'], {}).get('parameters', {})
        if (call['name'] not in functions or not isinstance(call['args'], dict)
                or not call['args'].keys() <= params.get('properties', {}).keys()
                or not set(params.get('required', [])) <= call['args'].keys()):
            raise ExecutionRecoveryRequired('Tool request is outside the submitted contract')
        worker, state, response = await run_owned_io(self.journal.call, run, call)
        if state in {'executing', 'unknown'}:
            raise ExecutionRecoveryRequired('Tool outcome is unknown; do not replay')
        if state in {'completed', 'replied'}:
            await self._reply(run, identity, worker, response)
            return
        claimed = await self.client.claim(run, identity, worker)
        if claimed.get('claimed') is False:
            await run_owned_io(self.journal.update_call, run, identity, 'withdrawn')
            return
        if claimed.get('claimed') is not True or type(claimed.get('recovered')) is not bool:
            raise ExecutionRecoveryRequired('Tool claim outcome is unconfirmed')
        actual = claimed.get('call', {})
        if (actual.get('worker_id') != worker or actual.get('state') != 'claimed'
                or any(actual.get(key) != call[key] for key in ('id', 'provider', 'name', 'args'))):
            raise ExecutionRecoveryRequired('Claim does not match its durable tool intent')
        # A recovered claim is safe ONLY with this journal's prepared state:
        # exclusive local ownership and a durable executing fence precede effects.
        if self._stops[run].is_set():
            response = {'ok': False, 'error': 'Caller stopped before executing this tool'}
        else:
            await run_owned_io(self.journal.update_call, run, identity, 'executing')
            try:
                if self._stops[run].is_set():
                    response = {'ok': False, 'error': 'Caller stopped before executing this tool'}
                else:
                    callback = asyncio.create_task(self.tool_handler(call['provider'], call['name'], call['args']))
                    self._callbacks[run][identity] = callback
                    response = await asyncio.shield(callback)
            except asyncio.CancelledError:
                if (call['provider'], call['name']) not in self.cancel_tools or not self._stops[run].is_set():
                    await run_owned_io(self.journal.update_call, run, identity, 'unknown')
                    raise ExecutionRecoveryRequired('Tool cancelled without an owned stop receipt') from None
                response = {'ok': False, 'error': 'Caller cancelled and joined this tool'}
            except BaseException as exc:
                await run_owned_io(self.journal.update_call, run, identity, 'unknown')
                raise ExecutionRecoveryRequired('Tool effects require reconciliation') from exc
            finally:
                self._callbacks[run].pop(identity, None)
        if (not isinstance(response, dict) or type(response.get('ok')) is not bool
                or set(response) != ({'ok', 'value'} if response['ok'] else {'ok', 'error'})
                or not response['ok'] and not isinstance(response['error'], str)):
            raise ExecutionRecoveryRequired('Tool did not return a durable protocol outcome')
        await run_owned_io(self.journal.update_call, run, identity, 'completed', response)
        await self._reply(run, identity, worker, response)

    async def _settle_tools(self, run):
        tasks = self._tools[run]
        for identity, (task, cancellable) in tasks.items():
            # Cancel only the callback, never its receipt/reply writer.
            if cancellable and not task.done():
                callback = self._callbacks[run].get(identity)
                if callback is not None and not callback.done() and not callback.cancelling():
                    callback.cancel()
        results = await asyncio.gather(*(task for task, _ in tasks.values()), return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise ExecutionRecoveryRequired('Tool receipts require recovery') from errors[0]

    async def _drive(self, run, spec, raw):
        # A conflicting caller request must not cancel the original execution.
        try:
            state, result, error = await run_owned_io(self.journal.prepare, run, raw)
        except ValueError:
            raise
        except BaseException as exc:
            self._recovery = exc
            raise ExecutionRecoveryRequired('Cannot admit work without its durable receipt') from exc
        if state != 'active':
            self._restore.discard(run)
            if state == 'completed':
                return result
            raise ExecutionEnded(state, error)
        try:
            # Same durable ID, never a replacement run. Service admission is
            # idempotent even if the previous submit response was lost.
            await self.client.submit(run, spec)
            for identity, worker, response in await run_owned_io(self.journal.pending_replies, run):
                await self._reply(run, identity, worker, response)
            cancelled_at = None
            while True:
                if self._stops[run].is_set() and cancelled_at is None:
                    cancelled_at = asyncio.get_running_loop().time()
                    await self.client.cancel(run)
                    await self._settle_tools(run)
                if cancelled_at is not None and asyncio.get_running_loop().time() - cancelled_at > self.stop_timeout:
                    raise ExecutionRecoveryRequired('Agent stop is unconfirmed')
                for task, _ in self._tools[run].values():
                    if task.done():
                        task.result()
                status = await self.client.poll(run)
                if status.get('protocol') != 1:
                    raise ExecutionRecoveryRequired('Unsupported Agent execution status')
                call = status.get('call')
                if call and call['id'] not in self._tools[run]:
                    if status['state'] != 'running':
                        self._stops[run].set()
                    task = asyncio.create_task(self._tool(run, call, spec))
                    self._tools[run][call['id']] = (task, (call['provider'], call['name']) in self.cancel_tools)
                if status['state'] not in {'running', 'cancelling'}:
                    self._stops[run].set()
                    await self._settle_tools(run)
                    if status.get('pending_tools'):
                        # A late receipt just settled it; observe before deciding.
                        await asyncio.sleep(self.poll_interval)
                        continue
                    result = await self.client.read_result(run) if status['state'] == 'completed' else None
                    await run_owned_io(self.journal.finish, run, status['state'], result, status.get('error'))
                    self._restore.discard(run)
                    if status['state'] != 'completed':
                        raise ExecutionEnded(status['state'], status.get('error'))
                    return result
                await asyncio.sleep(self.poll_interval)
        except ExecutionEnded:
            raise
        except BaseException as exc:
            self._recovery = exc
            self._stops[run].set()
            # Even unreachable Agent or failed persistence cannot abandon local
            # effects. Keep their receipts; do not acknowledge a successful stop.
            try:
                await self.client.cancel(run)
            except BaseException:
                pass  # The original failure already requires recovery.
            try:
                await self._settle_tools(run)
            except BaseException:
                pass  # Every local task was joined; failed receipts remain.
            raise ExecutionRecoveryRequired('Execution requires recovery under its original identity') from exc

    async def cancel(self, execution_id):
        if execution_id not in self._runs:
            raise ValueError('Execution is not owned by this caller')
        self._stops[execution_id].set()
        return await asyncio.shield(self._runs[execution_id])

    async def release(self, execution_id):
        """Owner calls after archiving the result; retain identity, discard bodies.

        Losing this observer doesn't detach release. A lost remote receipt can
        be reconciled after reopening using the same identity. Never releases
        active or uncertain tool effects, nor closes the borrowed model client.
        """
        _identity(execution_id)
        if self._closed or self._recovery:
            raise ExecutionRecoveryRequired('Caller is closed or needs recovery')
        task = self._runs.get(execution_id)
        if task is not None and not task.done():
            raise ValueError('Execution is still running')
        if execution_id not in self._releases:
            async def finish():
                await run_owned_io(self.journal.release, execution_id)
                try:
                    status = await self.client.release(execution_id)
                    if status.get('state') != 'released':
                        raise ExecutionRecoveryRequired('Agent did not acknowledge release')
                    await run_owned_io(self.journal.release, execution_id, commit=True)
                    for table in (self._runs, self._stops, self._tools, self._callbacks, self._specs):
                        table.pop(execution_id, None)
                except BaseException as exc:
                    self._recovery = exc
                    raise ExecutionRecoveryRequired('Execution release needs reconciliation') from exc
            release = self._releases[execution_id] = asyncio.create_task(finish())
            release.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(self._releases[execution_id])

    async def close(self):
        self._closed = True
        if self._closing is None:
            async def finish():
                for stop in self._stops.values():
                    stop.set()
                results = await asyncio.gather(*self._runs.values(), *self._releases.values(), return_exceptions=True)
                self.journal.close()
                errors = [result for result in results if isinstance(result, BaseException)
                          and not isinstance(result, ExecutionEnded)]
                if errors:
                    raise ExecutionRecoveryRequired('Execution caller needs recovery') from errors[0]
            self._closing = asyncio.create_task(finish())
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        result = self._closing.result()
        if cancelled:
            raise asyncio.CancelledError
        return result
