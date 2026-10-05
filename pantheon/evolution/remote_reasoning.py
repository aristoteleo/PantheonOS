"""Owned, stateless Evolution helpers using the ordinary Agent execution SDK.

No provider or Agent implementation is imported here. Each accepted invocation
has a persisted identity and receipt; uncertain previous work blocks fresh calls.
This is not automatic recovery of the surrounding Evolution archive/checkpoint.
"""
import asyncio
import json
import inspect
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

from pantheon.apps.agent_execution_runner import AgentExecutionRunner, ExecutionEnded
from pantheon.utils.owned_io import run_owned_io
from .lifetime import EvolutionCleanupError, EvolutionResources, join_cleanup


class RemoteEvolutionReasoner:
    def __init__(self, binding, root, *, instructions, model, timeout, functions=(), tool_factory=None):
        self.binding, self.root = binding, Path(root)
        self.instructions, self.model, self.timeout = instructions, model, timeout
        self.session = None
        self._functions = functions
        self._tool_factory = tool_factory
        self.tools, self.schemas, self.methods = {}, {}, {}
        self.resources = EvolutionResources()
        self._jobs = set()
        self._closed, self._recovery, self._closing = False, False, None

    async def setup(self):
        self.session = AgentExecutionRunner(self.binding.client, self.root,
            binding_id=self.binding.binding_id, tool_handler=self._invoke)
        self.path = self.root / 'helper-calls.sqlite3'
        def initialize():
            if self.path.is_symlink(): raise ValueError('Helper journal cannot be a symlink')
            self.path.touch(mode=0o600, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, '
                           'spec TEXT NOT NULL, phase TEXT NOT NULL, result TEXT)')
                return db.execute("SELECT 1 FROM calls WHERE phase != 'settled' LIMIT 1").fetchone() is not None
        self._recovery = await run_owned_io(initialize)
        if self._recovery:
            raise EvolutionCleanupError([RuntimeError('Reconcile the saved helper call before evaluating again')])
        if self._tool_factory is not None:
            self.tools = await self._tool_factory()
            for owned in self.tools.values():
                self.resources.own(owned.toolset.cleanup)
            for provider, owned in self.tools.items():
                await owned.toolset.run_setup()
                self.schemas[provider] = []
                for name, (method, _) in owned.toolset.tool_functions.items():
                    if name == 'list_tools': continue
                    from .remote_execution import function_schema
                    from pantheon.toolset import parse_tool_desc
                    self.schemas[provider].append(function_schema(
                        getattr(method, '_tool_desc', None) or parse_tool_desc(method)))
                    self.methods[provider, name] = method
        if self._functions:
            from .remote_execution import function_schema
            from pantheon.funcdesc import parse_func
            if 'evolution' in self.schemas:
                raise ValueError('The evolution helper namespace is reserved')
            self.schemas['evolution'] = []
            for function in self._functions:
                self.schemas['evolution'].append(function_schema(json.loads(parse_func(function).to_json())))
                self.methods['evolution', function.__name__] = function
        self.session.cancel_tools = frozenset((provider, name) for provider, name in self.methods
            if provider in self.tools and self.tools[provider].cancel_on_stop)

    async def _invoke(self, provider, name, args):
        method = self.methods[provider, name]
        if inspect.iscoroutinefunction(method):
            result = await method(**args)
        else:
            result = await run_owned_io(method, **args)
        return {'ok': True, 'value': result}

    async def settle(self):
        if self._jobs:
            raise EvolutionCleanupError([RuntimeError('Helper calls must join before their tools reset')])
        errors = []
        for owned in self.tools.values():
            if owned.reset_after_iteration:
                try:
                    await owned.toolset.cleanup()
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            self._recovery = True
            raise EvolutionCleanupError(errors)

    def _insert(self, identity, spec):
        raw = json.dumps(spec, allow_nan=False)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO calls VALUES (?,?,'accepted',NULL)", (identity, raw))

    def _save(self, identity, phase, result):
        raw = json.dumps(result, allow_nan=False)
        with sqlite3.connect(self.path) as db:
            saved = db.execute('UPDATE calls SET phase=?,result=? WHERE id=?', (phase, raw, identity))
            if saved.rowcount != 1:
                raise RuntimeError('The helper request receipt disappeared')

    async def _commit(self, identity, result, *, started=True):
        await run_owned_io(self._save, identity, 'recorded', result)
        if started:
            await self.session.release(identity)
        await run_owned_io(self._save, identity, 'settled', result)

    async def _execute(self, identity, spec):
        started = False
        commit = None
        try:
            await run_owned_io(self._insert, identity, spec)
            started = True
            try:
                response = await self.session.run(identity, spec)
            except ExecutionEnded as exc:
                commit = asyncio.create_task(self._commit(identity, {'state': exc.state, 'error': exc.error}))
                await join_cleanup(commit)
                if exc.error == 'execution_timeout': raise asyncio.TimeoutError from exc
                raise RuntimeError(f'Helper reasoning ended: {exc.state}') from exc
            commit = asyncio.create_task(self._commit(identity, {'state': 'completed', 'response': response}))
            await join_cleanup(commit)
            return SimpleNamespace(**response)
        except asyncio.CancelledError:
            try:
                if commit is not None:
                    await join_cleanup(commit)
                else:
                    if started:
                        try:
                            await join_cleanup(asyncio.create_task(self.session.cancel(identity)))
                        except ExecutionEnded:
                            pass
                    await join_cleanup(asyncio.create_task(self._commit(
                        identity, {'state': 'cancelled'}, started=started)))
            except BaseException as exc:
                self._recovery = True
                raise EvolutionCleanupError([exc]) from exc
            raise
        except Exception as exc:
            # A confirmed failed/timeout result keeps the evaluator's legacy
            # fallback. Persistence/transport failure must stop the owner.
            if commit is not None and commit.done() and not commit.cancelled() and commit.exception() is None:
                raise
            self._recovery = True
            raise EvolutionCleanupError([exc]) from exc

    async def run(self, prompt, *, update_memory=False):
        if update_memory:
            raise ValueError('Evolution helpers require fresh per-call memory')
        if self._closed or self._recovery:
            raise EvolutionCleanupError([RuntimeError('Helper is closed or requires receipt reconciliation')])
        identity = f'helper-{uuid.uuid4().hex}'
        spec = {'prompt': prompt, 'instructions': self.instructions, 'model': self.model,
                'tools': self.schemas, 'max_turns': None, 'timeout_seconds': self.timeout}
        task = asyncio.create_task(self._execute(identity, spec))
        self._jobs.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done() and not task.cancelling(): task.cancel()
            await join_cleanup(task)
            raise
        finally:
            self._jobs.discard(task)

    async def close(self):
        self._closed = True
        if self._closing is None:
            async def finish():
                for task in self._jobs:
                    if not task.done() and not task.cancelling(): task.cancel()
                results = await asyncio.gather(*self._jobs, return_exceptions=True)
                errors = [item for item in results if isinstance(item, EvolutionCleanupError)]
                # Retain exclusive receipt ownership until kernels and all
                # returned tool resources have actually finished shutdown.
                await self.resources.close()
                if self.session is not None:
                    try:
                        await self.session.close()
                    except BaseException as exc:
                        errors.append(exc)
                if self._recovery:
                    errors.append(RuntimeError('Helper receipts require recovery'))
                if errors: raise EvolutionCleanupError(errors)
            self._closing = asyncio.create_task(finish())
        await join_cleanup(self._closing)
