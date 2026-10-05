"""Controller-side reasoning for an explicitly owned isolated tool App.

The launcher supplies a pinned backend invocation and a termination operation
which confirms that exact container has stopped. It must journal container
creation before constructing this binding. No container creation, inference
provider discovery or ambient credentials are hidden in this module.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from pantheon.apps.agent_execution_runner import (
    AgentExecutionRunner, ExecutionEnded, ExecutionRecoveryRequired, ToolReceiptJournal,
    _encode, _identity,
)
from pantheon.utils.owned_io import run_owned_io
from ..lifetime import EvolutionCleanupError, join_cleanup


class SandboxAgentExecution:
    """One durable reasoning/finalization identity; arbitrary code stays remote.

    invoke(method, args) returns the unwrapped ordinary App result. terminate()
    must return {backend_id: ..., stopped: True} after confirmed termination,
    not merely after sending a stop request. Both callbacks are async and owned
    by this binding through completion. The Agent client is borrowed.

    Observer cancellation does not stop work. Explicit close stops reasoning,
    terminates the tool backend, then joins all calls before releasing ownership.
    Existing uncertain records require reconciliation and never replay tools or
    final evaluation. Completed records can be read without reconnecting tools.
    """
    def __init__(self, client, root, *, binding_id, execution_id, backend_id, invoke, terminate):
        self.identity = _identity(execution_id)
        if not isinstance(backend_id, str) or not backend_id or len(backend_id) > 512:
            raise ValueError('Supply the pinned backend instance identity')
        if not callable(invoke) or not callable(terminate):
            raise ValueError('Supply owned backend invocation and confirmed termination')
        self.backend_id, self.invoke, self.terminate = backend_id, invoke, terminate
        self.root = Path(root)
        self.owner = ToolReceiptJournal(self.root, binding_id)
        self.session = None
        try:
            with sqlite3.connect(self.owner.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS sandbox_execution ('
                           'id TEXT PRIMARY KEY, request TEXT NOT NULL, phase TEXT NOT NULL, result TEXT)')
            self.session = AgentExecutionRunner(client, self.root / 'agent',
                binding_id=binding_id, tool_handler=self._tool)
        except BaseException:
            self.owner.close()
            raise
        self.task = self.closing = self.termination = None
        self.reasoning_stop = None
        self._request = None
        self._rpc_tasks = set()
        self._started = self._stopped = self._closed = self._recovery = False
        self._reasoning_done = False

    def _admit(self, request):
        with sqlite3.connect(self.owner.path) as db:
            row = db.execute('SELECT request,phase,result FROM sandbox_execution WHERE id=?',
                             (self.identity,)).fetchone()
            if row:
                if row[0] != request:
                    raise ExecutionRecoveryRequired('The saved execution belongs to another request or backend')
                if row[1] == 'completed':
                    return json.loads(row[2])
                raise ExecutionRecoveryRequired('Reconcile the saved sandbox execution before running it again')
            if db.execute("SELECT 1 FROM sandbox_execution WHERE phase != 'completed'").fetchone():
                raise ExecutionRecoveryRequired('Another sandbox execution requires reconciliation')
            db.execute("INSERT INTO sandbox_execution VALUES (?,?,'admitted',NULL)", (self.identity, request))

    def _save(self, phase, result=None):
        raw = _encode(result, 16 * 1024 * 1024) if result is not None else None
        with sqlite3.connect(self.owner.path) as db:
            saved = db.execute('UPDATE sandbox_execution SET phase=?,result=COALESCE(?,result) WHERE id=?',
                               (phase, raw, self.identity))
            if saved.rowcount != 1:
                raise ExecutionRecoveryRequired('The sandbox execution record disappeared')

    async def _call(self, method, args):
        task = asyncio.create_task(self.invoke(method, args))
        self._rpc_tasks.add(task)
        # Losing the caller must not detach the actual request. Stop joins these
        # after confirmed container termination, including pending socket IO.
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._rpc_tasks.discard(task)

    async def _tool(self, provider, name, args):
        return {'ok': True, 'value': await self._call('invoke_tool',
            {'provider': provider, 'name': name, 'args': args})}

    async def _terminate(self):
        if self._stopped:
            return
        if self.termination is None:
            self.termination = asyncio.create_task(self.terminate())
        receipt = await asyncio.shield(self.termination)
        if (not isinstance(receipt, dict) or set(receipt) != {'backend_id', 'stopped'}
                or receipt['backend_id'] != self.backend_id or receipt['stopped'] is not True):
            raise ExecutionRecoveryRequired('Sandbox termination was not confirmed for this instance')
        self._stopped = True

    async def _execute(self, request, *, instructions, model, timeout, evaluate_initial, configuration=None, evaluation_only=False):
        try:
            saved = await run_owned_io(self._admit, request)
            if saved is not None:
                # Completion was recorded only after confirmed stop and release.
                self._stopped = True
                return saved
            if configuration is not None:
                await run_owned_io(self._save, 'initializing')
                initialized = await self._call('initialize', configuration)
                if not isinstance(initialized, dict) or initialized.get('initialized') is not True:
                    raise ExecutionRecoveryRequired('Tool App initialization was not confirmed')
                await run_owned_io(self._save, 'initialized')
            initial = None
            if evaluate_initial:
                await run_owned_io(self._save, 'initial_evaluation')
                initial = await self._call('evaluate_initial', {})
                await run_owned_io(self._save, 'initial_evaluated', {'initial': initial})
            if evaluation_only:
                result = {'backend_id': self.backend_id, 'initial': initial}
                await run_owned_io(self._save, 'result_recorded', result)
                await self._terminate()
                await run_owned_io(self._save, 'completed', result)
                return result
            description = await self._call('describe', {})
            if not isinstance(description, dict) or not isinstance(description.get('tools'), dict) or not isinstance(description.get('prompt'), str):
                raise ValueError('The isolated tool App returned an invalid description')
            specification = {'prompt': description['prompt'], 'instructions': instructions, 'model': model,
                'tools': description['tools'], 'timeout_seconds': timeout, 'max_turns': None}
            # Persist the exact tool contract before inference, not just settings.
            await run_owned_io(self._save, 'reasoning', {'initial': initial, 'specification': specification})
            self._started = True
            inference_error, response = '', None
            try:
                response = await self.session.run(self.identity, specification)
            except ExecutionEnded as exc:
                # A confirmed deadline/provider failure can use the original
                # sandbox's on-disk salvage. Uncertain effects cannot.
                if exc.state == 'cancelled':
                    raise
                inference_error = exc.error or exc.state
            self._reasoning_done = True
            await run_owned_io(self._save, 'finalizing', {'initial': initial,
                'response': response, 'inference_error': inference_error})
            mutation = await self._call('finish', {'error': inference_error})
            if not isinstance(mutation, dict) or type(mutation.get('submitted')) is not bool:
                raise ValueError('The isolated tool App returned an invalid mutation result')
            result = {'backend_id': self.backend_id, 'initial': initial, 'response': response, 'mutation': mutation}
            await run_owned_io(self._save, 'result_recorded', result)
            await self._terminate()
            await run_owned_io(self._save, 'backend_stopped')
            await self.session.release(self.identity)
            await run_owned_io(self._save, 'completed', result)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._recovery = True
            raise EvolutionCleanupError([exc]) from exc

    async def run(self, *, instructions, model, timeout=600, evaluate_initial=False, configuration=None):
        if self._closed:
            raise RuntimeError('Sandbox execution is closing')
        if type(timeout) is not int or not 1 <= timeout <= 86400 or type(evaluate_initial) is not bool:
            raise ValueError('Supply a finite timeout and explicit initial-evaluation policy')
        if configuration is not None and not isinstance(configuration, dict):
            raise ValueError('Tool App initialization must be an object')
        data = {'backend_id': self.backend_id, 'instructions': instructions,
                'model': model, 'timeout': timeout, 'evaluate_initial': evaluate_initial}
        if configuration is not None:
            data['configuration'] = configuration
        return await self._start(data)

    async def evaluate(self, *, configuration):
        """Evaluate the seed remotely without admitting any Agent execution."""
        if self._closed:
            raise RuntimeError('Sandbox execution is closing')
        if not isinstance(configuration, dict):
            raise ValueError('Tool App initialization must be an object')
        return await self._start({'backend_id': self.backend_id, 'instructions': '', 'model': None,
            'timeout': 600, 'evaluate_initial': True, 'evaluation_only': True, 'configuration': configuration})

    async def _start(self, data):
        request = _encode(data, 16 * 1024 * 1024)
        if self.task is None:
            self._request = request
            # Own the exact snapshotted inputs; callers can no longer mutate
            # source/configuration after the receipt has been admitted.
            frozen = json.loads(request)
            frozen.pop('backend_id')
            self.task = asyncio.create_task(self._execute(request, **frozen))
            self.task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        elif request != self._request:
            raise ValueError('Execution identity already has another request')
        return deepcopy(await asyncio.shield(self.task))

    async def close(self):
        self._closed = True
        if self.closing is None:
            async def dispose():
                if self.task is not None and not self.task.done():
                    self.task.cancel()
                    if self._started and not self._reasoning_done:
                        # Signal inference stop even if container termination
                        # fails. Do not wait here: its tools may need that stop
                        # to finish. Retain the task and both ownership locks.
                        async def stop_reasoning():
                            try:
                                await self.session.cancel(self.identity)
                            except ExecutionEnded:
                                pass
                        self.reasoning_stop = asyncio.create_task(stop_reasoning())
                        self.reasoning_stop.add_done_callback(
                            lambda done: done.exception() if not done.cancelled() else None)
                # Stop the real tool container before joining its outstanding
                # calls or releasing either journal. Failed stop keeps ownership.
                await self._terminate()
                if self.task is not None:
                    outcome = await asyncio.gather(self.task, return_exceptions=True)
                    self._recovery |= isinstance(outcome[0], EvolutionCleanupError)
                await asyncio.gather(*self._rpc_tasks, return_exceptions=True)
                self._rpc_tasks.clear()
                errors = []
                if self.reasoning_stop is not None:
                    outcome = await asyncio.gather(self.reasoning_stop, return_exceptions=True)
                    if isinstance(outcome[0], Exception):
                        errors.append(outcome[0])
                try:
                    await self.session.close()
                except Exception as exc:
                    errors.append(exc)
                if self._recovery:
                    errors.append(ExecutionRecoveryRequired('Sandbox records require reconciliation'))
                # The backend is confirmed gone and IO joined; saved incomplete
                # records still fence re-execution after the local lock closes.
                self.owner.close()
                if errors:
                    raise EvolutionCleanupError(errors)
            self.closing = asyncio.create_task(dispose())
        await join_cleanup(self.closing)
