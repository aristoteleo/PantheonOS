"""Mutation tools hosted INSIDE an isolated ordinary App, without an Agent.

The deployment owner must supply the isolation boundary and prepared tool
instances. This module is not a local sandbox and must not run untrusted code on
the controller. Model credentials and the Agent execution client stay outside.
"""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import inspect
import json
import math
from pathlib import Path, PurePosixPath

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.toolset_backend import register_toolset
from pantheon.funcdesc import parse_func
from pantheon.funcdesc.pydantic import function_schema
from pantheon.toolset import parse_tool_desc
from pantheon.utils.owned_io import run_owned_io
from ..evaluator import HybridEvaluator
from ..lifetime import EvolutionCleanupError, EvolutionResources, join_cleanup
from ..program import CodebaseSnapshot, Program
from ..utils.metrics import compute_fitness_score


def _files(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('A nonempty source file map is required')
    for name, content in value.items():
        if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
                or PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/'))
                or not isinstance(content, str)):
            raise ValueError('Source files must use relative, normalized paths and text content')
    names = set(value)
    if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents):
        raise ValueError('A source file cannot also be a directory')
    return dict(value)


class SandboxMutationTools:
    """One mutation workspace, explicitly composed Files/Python/Shell and evaluator.

    Its controller owns durable execution receipts. Neither tool calls nor finish
    may be retried after a lost reply without that controller's reconciliation.
    A workspace identity is single-use; reopening never resets edited code.
    """
    def __init__(self, ctx, *, parent_files, evaluator_code, objective, timeout=600, inspirations=(), function_weight=1, evaluation_timeout=None):
        self.ctx = ctx
        self.parent = _files(parent_files)
        self.inspirations = [{**entry, 'files': _files(entry['files'])} for entry in inspirations]
        if self.inspirations and any(p == 'inspirations' or p.startswith('inspirations/') for p in self.parent):
            raise ValueError('Source paths collide with the inspirations directory')
        self.evaluator_code, self.objective = evaluator_code, objective
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 86400:
            raise ValueError('A finite mutation timeout is required')
        self.timeout = timeout
        if isinstance(function_weight, bool) or not isinstance(function_weight, (int, float)) or not math.isfinite(function_weight) or function_weight < 0:
            raise ValueError('A finite nonnegative function weight is required')
        self.function_weight = function_weight
        self.evaluation_timeout = min(timeout, 120) if evaluation_timeout is None else evaluation_timeout
        if type(self.evaluation_timeout) is not int or not 1 <= self.evaluation_timeout <= 86400:
            raise ValueError('A finite evaluation timeout is required')
        self.root = Path(ctx.workspace).resolve()
        self.resources = EvolutionResources()
        self.functions, self.schemas = {}, {}
        self.active = set()
        self.accepting = False
        self.closing = None
        self.submitted = {}
        self.finished = None
        self.finishing = False

    def _materialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        # Refuse preexisting files/symlinks instead of overwriting another run.
        if any(self.root.iterdir()):
            raise ValueError('A mutation requires a fresh isolated workspace')
        marker = Path(self.ctx.state_dir) / 'mutation-admitted'
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open('x') as stream:
            stream.write('Reconcile this mutation before reusing its workspace.\n')
        for name, content in self.parent.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        if self.inspirations:
            notes = ['# Alternative solutions from other niches. Do not edit these references.']
            for index, entry in enumerate(self.inspirations, 1):
                for name, content in entry['files'].items():
                    path = self.root / 'inspirations' / f'insp_{index}' / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
                notes.append(f"- insp_{index}/ score={entry.get('score')} {str(entry.get('summary') or '')[:100]}")
            (self.root / 'inspirations/README.txt').write_text('\n'.join(notes) + '\n')

    async def setup(self, tool_factory):
        await run_owned_io(self._materialize)
        self.evaluator = HybridEvaluator(self.evaluator_code, function_weight=self.function_weight, llm_weight=0,
            timeout=self.evaluation_timeout, workspace_base=str(Path(self.ctx.state_dir) / 'evaluations'))
        tools = await tool_factory(self.root)
        # Own every returned provider even if a preceding registration fails.
        contexts = {}
        for alias, service in tools.items():
            child = AppContext(self.ctx.app_id, self.root, Path(self.ctx.state_dir), self.ctx._rpc)
            contexts[alias] = child
            async def close_provider(child=child, service=service):
                if child._cleanup is not None:
                    await child._cleanup()
                else:
                    await service.cleanup()
            self.resources.own(close_provider)
        for alias, service in tools.items():
            if not isinstance(alias, str) or not alias or alias == 'evolution':
                raise ValueError('Invalid or reserved tool provider alias')
            child = contexts[alias]
            await register_toolset(child, service)
            self.schemas[alias] = []
            for name, (method, _) in service.tool_functions.items():
                if name == 'list_tools':
                    continue
                self.functions[alias, name] = child._methods[name]
                self.schemas[alias].append(function_schema(
                    getattr(method, '_tool_desc', None) or parse_tool_desc(method)))
        self.schemas['evolution'] = []
        for fn in (self.run_evaluator, self.submit, self.think):
            self.functions['evolution', fn.__name__] = fn
            self.schemas['evolution'].append(function_schema(json.loads(parse_func(fn).to_json())))
        self.accepting = True

    def _current_files(self):
        current = {}
        for name, original in self.parent.items():
            path = self.root / name
            if not path.resolve().is_relative_to(self.root):
                raise ValueError('An evolved source path escapes its workspace')
            current[name] = path.read_text() if path.exists() else original
        return current

    async def _evaluate(self, files, identity):
        return await self.evaluator.evaluate(Program(
            id=identity, snapshot=CodebaseSnapshot(files=files), generation=int(identity == 'child')))

    async def run_evaluator(self) -> dict:
        """Evaluate current code. Higher fitness is better; inspect feedback before submitting."""
        result = await self._evaluate(await run_owned_io(self._current_files), '_probe')
        feedback = {key: value for key, value in result.artifacts.items()
                    if key not in ('llm_feedback', 'issues', 'suggestions') and value}
        return {'success': result.success, 'metrics': result.metrics, 'error': result.error, 'feedback': feedback}

    async def evaluate_initial(self):
        """Evaluate the immutable parent inside this isolation boundary."""
        if not self.accepting or self.finishing:
            raise RuntimeError('Mutation is stopping or already finishing')
        task = asyncio.current_task()
        self.active.add(task)
        try:
            return asdict(await self._evaluate(self.parent, '_initial'))
        finally:
            self.active.discard(task)

    async def submit(self, summary: str) -> str:
        """Record the current code and a short summary once the best valid version is ready."""
        files = await run_owned_io(self._current_files)
        if self.submitted:
            raise ValueError('This mutation has already been submitted')
        self.submitted = {'files': files, 'summary': summary.strip()}
        return 'Submitted. Your result and summary are recorded.'

    def think(self, thought: str) -> str:
        """Think through the problem step by step."""
        return 'Thought recorded.'

    def describe(self):
        return {'tools': deepcopy(self.schemas), 'prompt': (
            f'Objective: {self.objective}\n\nThe code to improve is at the ROOT of your working directory. '
            'Improve it, verify with run_evaluator, and call submit(summary=...) with your best VALID version. '
            'Keep a valid improvement saved as you go; time is limited.'
            + (' The inspirations/ folder contains alternative solutions from other niches; do not edit it.'
               if self.inspirations else ''))}

    async def invoke_tool(self, provider: str, name: str, args: dict):
        if not self.accepting or self.finishing:
            raise RuntimeError('The mutation is no longer accepting tools')
        function = self.functions[provider, name]
        if not isinstance(args, dict):
            raise ValueError('Tool arguments must be an object')
        task = asyncio.current_task()
        self.active.add(task)
        try:
            return (await function(**args) if inspect.iscoroutinefunction(function)
                    else await run_owned_io(function, **args))
        finally:
            self.active.discard(task)

    async def finish(self, error: str = ''):
        if self.finished is not None:
            return deepcopy(self.finished)
        if not self.accepting or self.finishing:
            raise RuntimeError('Mutation is stopping or already finishing')
        self.finishing = True
        task = asyncio.current_task()
        calls = tuple(self.active)
        self.active.add(task)
        try:
            if calls:
                await asyncio.gather(*(asyncio.shield(call) for call in calls))
            if not self.submitted:
                current = await run_owned_io(self._current_files)
                seed = await self._evaluate(self.parent, '_seed')
                candidate = await self._evaluate(current, '_cur')
                if (seed.success and candidate.success and not seed.error and not candidate.error
                        and not seed.artifacts.get('evaluation_error') and not candidate.artifacts.get('evaluation_error')
                        and compute_fitness_score(candidate.metrics, [], None, 1, 0)
                        > compute_fitness_score(seed.metrics, [], None, 1, 0) + 1e-9):
                    self.submitted = {'files': current,
                        'summary': '(auto-submitted best valid version on disk — ran out of time)'}
            result = {'submitted': bool(self.submitted), 'summary': self.submitted.get('summary', ''),
                      'child_files': self.submitted.get('files', {}), 'error': error, 'metrics': {}}
            if self.submitted:
                evaluated = await self._evaluate(self.submitted['files'], 'child')
                result['evaluation'] = asdict(evaluated)
                result['metrics'] = evaluated.metrics
                result['evaluation_success'] = evaluated.success
                result['evaluation_error'] = evaluated.error
            self.finished = deepcopy(result)
            return result
        finally:
            self.active.discard(task)

    async def close(self):
        self.accepting = False
        if self.closing is None:
            async def dispose():
                calls = tuple(self.active)
                for call in calls:
                    call.cancel()
                outcomes = await asyncio.gather(*calls, return_exceptions=True)
                errors = [value for value in outcomes if isinstance(value, EvolutionCleanupError)]
                try:
                    await self.resources.close()
                except Exception as exc:
                    errors.append(exc)
                if errors:
                    raise EvolutionCleanupError(errors)
            self.closing = asyncio.create_task(dispose())
        await join_cleanup(self.closing)


async def register_sandbox_mutation(ctx, *, tool_factory, **configuration):
    """Compose the ordinary backend in the container using explicit configuration."""
    backend = SandboxMutationTools(ctx, **configuration)
    ctx.require_rpc_token = True
    ctx.begin_shutdown = backend.close
    ctx.on_cleanup(backend.close)
    try:
        await backend.setup(tool_factory)
    except BaseException:
        await backend.close()
        raise
    for method in (backend.describe, backend.invoke_tool, backend.finish, backend.evaluate_initial):
        ctx.method(method)
    ctx.concurrent_methods.update(('describe', 'invoke_tool', 'finish', 'evaluate_initial'))
    return backend
