"""Evolution-owned resources with reasoning provided by an ordinary Agent App.

The composition root supplies the execution client, private receipt mount and
owned tool factory. This module neither constructs Agents nor discovers models.
It retains mutation state before effects; interrupted mutations require owner
reconciliation rather than silently sampling another parent or wiping files.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3

from pantheon.apps.agent_execution_runner import AgentExecutionRunner, ExecutionEnded, ExecutionRecoveryRequired
from pantheon.funcdesc import parse_func
from pantheon.funcdesc.pydantic import function_schema
from pantheon.toolset import parse_tool_desc
from pantheon.utils.owned_io import run_owned_io
from .lifetime import EvolutionCleanupError, EvolutionResources, join_cleanup


@dataclass
class OwnedMutationTool:
    toolset: object
    cancel_on_stop: bool = False
    reset_after_iteration: bool = False


class RemoteEvolutionBinding:
    """Explicit runtime binding, borrowed by Evolution and its worker children.

    receipt_root is a private App data mount outside the mutation workspaces.
    run_id belongs to the durable Evolution run; keep it across reconnects.
    tool_factory(workdir) returns {provider_alias: OwnedMutationTool}. The
    returned instances are exclusively owned by that worker, including cleanup.
    """
    def __init__(self, client, receipt_root, *, run_id, binding_id, tool_factory, analyzer_tool_factory=None):
        from pantheon.apps.agent_execution_runner import _identity
        self.run_id, self.binding_id = _identity(run_id), _identity(binding_id)
        self.client, self.root, self.tool_factory = client, Path(receipt_root).resolve(), tool_factory
        self.analyzer_tool_factory = analyzer_tool_factory

    def run_lease(self, team):
        from .remote_run import EvolutionRunLease
        workspace = Path(team.config.workspace_path).resolve()
        if self.root == workspace or self.root.is_relative_to(workspace):
            raise ValueError('Evolution receipts must live outside its workspaces')
        return EvolutionRunLease(self, team.config.to_dict())

    async def create_reasoner(self, team, *, role, instructions, model, timeout, functions=(), tool_factory=None):
        from .remote_reasoning import RemoteEvolutionReasoner
        from pantheon.apps.agent_execution_runner import _identity
        _identity(role)
        workspace = Path(team.config.workspace_path).resolve()
        if self.root == workspace or self.root.is_relative_to(workspace):
            raise ValueError('Evolution receipts must live outside its workspaces')
        key = hashlib.sha256(str(workspace).encode()).hexdigest()[:24]
        reasoner = RemoteEvolutionReasoner(self, self.root / self.run_id / '_helpers' / key / role,
            instructions=instructions, model=model, timeout=timeout, functions=functions, tool_factory=tool_factory)
        team._resources.own(reasoner.close, early=True)
        try:
            await reasoner.setup()
        except Exception as exc:
            raise EvolutionCleanupError([exc]) from exc
        return reasoner

    async def create(self, team, functions, before, after):
        workdir = team._mut_workdir.resolve()
        if self.root == workdir or self.root.is_relative_to(workdir):
            raise ValueError('Evolution receipts must live outside the mutation workspace')
        key = hashlib.sha256(str(workdir).encode()).hexdigest()[:24]
        owned = RemoteEvolutionMutation(self, self.root / self.run_id / key, team, before, after)
        # Register cleanup before setup, so partial failures cannot orphan tools.
        team._resources.own(owned.close, early=True)
        try:
            await owned.setup(functions)
        except Exception as exc:
            raise EvolutionCleanupError([exc]) from exc
        return owned


class RemoteEvolutionMutation:
    def __init__(self, binding, root, team, before, after):
        self.binding, self.root, self.team = binding, root, team
        self.before, self.after = before, after
        self.tools, self.functions, self.schemas = {}, {}, {}
        self.resources = EvolutionResources()
        self._state_lock = asyncio.Lock()
        self._active = None
        self._started = False
        self._session = None
        self._closing = None

    async def setup(self, functions):
        self._session = AgentExecutionRunner(self.binding.client, self.root,
            binding_id=self.binding.binding_id, tool_handler=self._invoke)
        self._path = self.root / 'mutation-state.sqlite3'
        def initialize():
            if self._path.is_symlink(): raise ValueError('Mutation journal cannot be a symlink')
            self._path.touch(mode=0o600, exist_ok=True)
            with sqlite3.connect(self._path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS mutations (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        await run_owned_io(initialize)
        provided = await self.binding.tool_factory(self.team._mut_workdir)
        # Own every returned resource BEFORE initializing any one of them.
        for owned in provided.values():
            self.resources.own(owned.toolset.cleanup)
        self.tools = provided
        for provider, owned in provided.items():
            await owned.toolset.run_setup()
            schemas = []
            for name, (method, _) in owned.toolset.tool_functions.items():
                if name == 'list_tools': continue
                schema = function_schema(getattr(method, '_tool_desc', None) or parse_tool_desc(method))
                self.functions[provider, name] = method
                schemas.append(schema)
            self.schemas[provider] = schemas
        self.schemas['evolution'] = []
        for function in functions:
            schema = function_schema(json.loads(parse_func(function).to_json()))
            self.schemas['evolution'].append(schema)
            self.functions['evolution', function.__name__] = function
        self._session.cancel_tools = frozenset(
            (provider, name) for provider, name in self.functions
            if provider in self.tools and self.tools[provider].cancel_on_stop) | {('evolution', 'run_evaluator')}

    def _snapshot(self):
        return {key: getattr(self.team, key) for key in (
            '_mut_parent_files', '_mut_submitted', '_mut_eval_count', '_mut_best', '_mut_tool_calls_used')}

    async def _save(self, *, phase=None, result=None):
        record = dict(self._active)
        record['state'] = deepcopy(self._snapshot())
        if phase: record['phase'] = phase
        if result is not None: record['result'] = result
        raw = json.dumps(record, allow_nan=False)
        def write():
            with sqlite3.connect(self._path) as db:
                updated = db.execute('UPDATE mutations SET record=? WHERE id=?', (raw, record['id']))
                if updated.rowcount != 1:
                    raise ExecutionRecoveryRequired('The saved mutation disappeared; tool execution is blocked')
        await run_owned_io(write)
        self._active = record

    async def begin(self, iteration, parent, prompt):
        namespace = hashlib.sha256((self.binding.run_id + ':' + self.root.name).encode()).hexdigest()
        identity = f'mutation-{namespace}-{iteration}'
        record = {'id': identity, 'parent': parent.to_dict(), 'prompt': prompt,
                  'iteration': iteration, 'config': self.team.config.to_dict(),
                  'phase': 'preparing', 'state': deepcopy(self._snapshot())}
        def insert():
            with sqlite3.connect(self._path) as db:
                if db.execute('SELECT 1 FROM mutations WHERE id=?', (identity,)).fetchone():
                    raise ExecutionRecoveryRequired('Reconcile the saved mutation before resampling or resetting its workspace')
                db.execute('INSERT INTO mutations VALUES (?,?)', (identity, json.dumps(record, allow_nan=False)))
        try:
            await run_owned_io(insert)
            self._active = record
            self._started = False
        except Exception as exc:
            raise EvolutionCleanupError([exc]) from exc

    async def _invoke(self, provider, name, args):
        function = self.functions[provider, name]
        # Charge and persist before entering the tool; other parallel callbacks
        # may execute while this callback awaits its own evaluator/kernel.
        async with self._state_lock:
            blocked = await self.before(f'{provider}__{name}', args)
            await self._save()
        if blocked is not None:
            return {'ok': True, 'value': blocked}
        try:
            if inspect.iscoroutinefunction(function):
                value = await function(**args)
            else:
                value = await run_owned_io(function, **args)
            async with self._state_lock:
                modified = await self.after(f'{provider}__{name}', args, value)
                await self._save()
            return {'ok': True, 'value': value if modified is None else modified}
        except BaseException:
            async with self._state_lock:
                await self._save()
            raise

    async def run(self, prompt, *, max_turns, turn_messages):
        spec = {'prompt': prompt, 'instructions': self.team.config.mutation_system_prompt,
                'model': self.team.config.mutator_model, 'tools': self.schemas,
                'max_turns': None if max_turns == float('inf') else max_turns,
                'timeout_seconds': self.team.config.mutation_timeout, 'turn_messages': turn_messages}
        if not spec['instructions']:
            from .team import MUTATION_AGENT_SYSTEM_PROMPT
            spec['instructions'] = MUTATION_AGENT_SYSTEM_PROMPT
        try:
            await self._save(phase='reasoning')
            self._started = True
            response = await self._session.run(self._active['id'], spec)
            await self._save(phase='reasoned')
            return response
        except asyncio.CancelledError:
            if not self._started:
                raise
            try:
                await join_cleanup(asyncio.create_task(self._session.cancel(self._active['id'])))
            except ExecutionEnded:
                pass
            except ExecutionRecoveryRequired as exc:
                raise EvolutionCleanupError([exc]) from exc
            raise
        except ExecutionEnded as exc:
            if exc.error == 'execution_timeout': raise asyncio.TimeoutError from exc
            raise RuntimeError(f'Mutation inference ended: {exc.state}') from exc
        except Exception as exc:
            raise EvolutionCleanupError([exc]) from exc

    async def settle(self):
        errors = []
        for owned in self.tools.values():
            if owned.reset_after_iteration:
                try:
                    await owned.toolset.cleanup()
                except BaseException as exc:
                    errors.append(exc)
        if errors: raise EvolutionCleanupError(errors)

    async def finalize(self, result):
        try:
            await self._save(phase='finalized', result=vars(result))
            await self._session.release(self._active['id'])
            self._active = None
        except Exception as exc:
            raise EvolutionCleanupError([exc]) from exc

    async def close(self):
        if self._closing is None:
            async def finish():
                errors = []
                if self._session is not None and self._active is not None and self._started:
                    try:
                        await self._session.cancel(self._active['id'])
                    except ExecutionEnded:
                        pass
                    except BaseException as exc:
                        errors.append(exc)
                # Hold the caller's journal lock through kernel/tool shutdown,
                # not just through completion of individual tool callbacks.
                await self.resources.close()
                if self._session is not None:
                    try:
                        await self._session.close()
                    except BaseException as exc:
                        errors.append(exc)
                if errors: raise EvolutionCleanupError(errors)
            self._closing = asyncio.create_task(finish())
        await join_cleanup(self._closing)
