"""Orchestration API injected into the sandboxed Leader script (Task 7).

This module builds the five names a Leader script calls — ``node``,
``parallel``, ``pipeline``, ``phase``, ``log`` — bound (per workflow run) to a
:class:`~pantheon.workflow.journal.Journal`, a
:class:`~pantheon.workflow.runner.NodeRunner`, a
:class:`~pantheon.workflow.events.WorkflowEventPublisher` + ``chat_id``, the
:class:`~pantheon.workflow.runner.NodeRunContext`, a concurrency semaphore, a
shared **node_id counter**, and a small mutable :class:`RunState`.

node_id ordering guarantee (resume correctness)
===============================================
Resume correctness depends on the Nth ``node()`` call this run being assigned
``node_id == N`` — the same N it had on the recorded run — so that
``journal.lookup(node_id, key)`` consults the right prior position and the
prefix-cascade boundary stays meaningful. ``Journal.lookup`` additionally
REQUIRES that calls arrive in ascending ``node_id`` order.

We guarantee BOTH with one rule: **node_id allocation and the journal lookup
happen synchronously at the very top of ``node()``, before its first
``await``.** A Python coroutine runs synchronously from the moment it is
awaited (or stepped) until it hits its first real suspension point. ``node()``
does its allocate-and-lookup with NO ``await`` in between, so once a particular
``node()`` coroutine begins executing it cannot be interleaved with another
coroutine until after it has both allocated its id and recorded its lookup.

The remaining question is the ORDER in which ``node()`` coroutines first begin
executing. The script issues calls in deterministic source order:

* Sequential ``await node(...)``: trivially in order.
* ``parallel(thunks)``: we invoke the thunks **in list order** and immediately
  ``await`` step each resulting coroutine up to its first suspension before
  moving to the next — see :func:`_start_in_order`. Concretely we create the
  coroutine for thunk[i], wrap it in a task, and yield control so it runs its
  synchronous prologue (allocate + lookup) before thunk[i+1] is invoked. Thus
  node_ids are allocated 0,1,2,... matching thunk/source order regardless of
  how long each node's runner work later takes.
* ``pipeline(items, *stages)``: each item is an independent serial chain; the
  chains are likewise started in item order via :func:`_start_in_order`, so
  the stage-1 ``node()`` of item 0 allocates before item 1's, etc. (Stages
  beyond the first allocate when reached; in a no-barrier pipeline the global
  allocation order across items is therefore data-dependent, BUT a *resume*
  re-executes the identical script with the identical stage timing model, so
  the same allocation order reproduces. The invariant we rely on is only that,
  for a fixed deterministic script, the allocation order is reproducible — and
  the synchronous-prologue rule plus in-order start gives exactly that.)

The counter is a plain ``int`` in the closure incremented inside the
synchronous prologue; because the prologue cannot be interleaved, no lock is
needed within a single event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import events as ev
from .journal import Journal
from .models import (
    JournalEntry,
    NodeCall,
    NodeResult,
    compute_node_key,
)
from .runner import NodeRunContext, NodeRunner

#: Default cap on concurrent ``runner.run`` calls.
API_DEFAULT_CONCURRENCY = 8


class NodeError(Exception):
    """Raised when a node finishes with ``status == "failed"``.

    Carries the addressing ``node_id``, the display ``label``, and the runner's
    ``error`` string so script control flow (and ``parallel`` with
    ``return_exceptions``) can react.
    """

    def __init__(self, node_id: int, label: str, error: str | None) -> None:
        self.node_id = node_id
        self.label = label
        self.error = error
        super().__init__(f"node {node_id} ({label!r}) failed: {error}")


@dataclass
class RunState:
    """Mutable run-state the engine can read back (phase + progress)."""

    current_phase: str = ""
    progress: dict = field(default_factory=lambda: {"total": 0, "done": 0})


def make_api(
    journal: Journal,
    runner: NodeRunner,
    publisher: Any,
    chat_id: str,
    ctx: NodeRunContext,
    *,
    concurrency: int = API_DEFAULT_CONCURRENCY,
) -> dict:
    """Build the orchestration API bound to one workflow run.

    Returns a dict with keys ``node``, ``parallel``, ``pipeline``, ``phase``,
    ``log`` (the injected names) plus ``run_state`` (the :class:`RunState`) so
    the engine can read the current phase/progress.
    """
    wf_id = ctx.workflow_id
    storage = ctx.storage
    semaphore = asyncio.Semaphore(concurrency)
    run_state = RunState()
    counter = _Counter()

    # --- helpers ----------------------------------------------------------- #

    def _input_hashes(inputs: tuple[str, ...]) -> list[str]:
        """SHA-256 each declared input file's bytes (missing -> "").

        Inputs are relative path segments under the workflow ``context/`` dir
        (typically earlier nodes' ``n{id}.json``). Resolution goes through
        ``storage.context_dir`` + ``safe_node_path`` confinement; a path that
        escapes confinement hashes as "" rather than crashing key computation.
        """
        from .storage import safe_node_path

        out: list[str] = []
        context_dir = storage.context_dir(wf_id)
        for rel in inputs:
            try:
                parts = [p for p in str(rel).split("/") if p]
                path = safe_node_path(context_dir, *parts)
                data = path.read_bytes()
            except (OSError, ValueError):
                out.append("")  # missing/unsafe input -> empty hash
            else:
                out.append(hashlib.sha256(data).hexdigest())
        return out

    def _load_result_ref(result_ref: str | None) -> Any:
        """Load and unwrap the ``{"kind","result"}`` envelope at ``result_ref``."""
        if not result_ref:
            return None
        import json

        from .storage import safe_node_path

        parts = [p for p in result_ref.split("/") if p]
        # result_ref is like "context/n{id}.json" — confine under workflow dir.
        path = safe_node_path(storage.workflow_dir(wf_id), *parts)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("result")

    async def _publish(event: dict) -> None:
        await publisher.publish(chat_id, event)

    # --- node -------------------------------------------------------------- #

    async def node(
        instruction: str,
        *,
        template: str = "generic",
        schema: dict | None = None,
        inputs: tuple[str, ...] = (),
        label: str = "",
        phase: str = "",
        model: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        # ---- SYNCHRONOUS PROLOGUE (no await before the lookup) ------------- #
        # Allocating node_id and consulting the journal happen here, with NO
        # await in between, so this prologue cannot interleave with another
        # coroutine. This is what guarantees node_ids are allocated, and
        # journal.lookup is called, in ascending source order. See module
        # docstring.
        node_id = counter.next()
        input_hashes = _input_hashes(tuple(inputs))
        key = compute_node_key(instruction, template, schema, model, input_hashes)
        cached = journal.lookup(node_id, key)
        # ---- END SYNCHRONOUS PROLOGUE ------------------------------------- #

        eff_phase = phase or run_state.current_phase

        if cached is not None:
            # Cache hit: reflect status via events, never call the runner.
            await _publish(
                ev.make_node_started(wf_id, node_id, label, eff_phase)
            )
            await _publish(
                ev.make_node_finished(
                    wf_id, node_id, label, cached.status, cached.result_ref
                )
            )
            if cached.status == "skipped":
                return None
            return _load_result_ref(cached.result_ref)

        # Cache miss: run the node.
        await _publish(ev.make_node_started(wf_id, node_id, label, eff_phase))

        node_call = NodeCall(
            node_id=node_id,
            instruction=instruction,
            template=template,
            schema=schema,
            inputs=tuple(inputs),
            label=label,
            phase=eff_phase,
            model=model,
            timeout=timeout,
        )

        # The semaphore wraps ONLY runner.run — not the journal/event work.
        async with semaphore:
            result: NodeResult = await runner.run(node_call, ctx)

        journal.record(
            JournalEntry(
                node_id=node_id,
                key=key,
                label=label,
                status=result.status,
                result_ref=result.result_ref,
                token_cost=result.token_cost,
            )
        )
        await _publish(
            ev.make_node_finished(
                wf_id, node_id, label, result.status, result.result_ref
            )
        )

        # Update progress + emit a status event.
        run_state.progress["done"] = run_state.progress.get("done", 0) + 1
        await _publish(
            ev.make_status(wf_id, "running", dict(run_state.progress))
        )

        if result.status == "failed":
            raise NodeError(node_id, label, result.error)

        # Prefer the in-memory result the runner already holds (avoids a
        # re-read of result_ref); fall back to the envelope if absent.
        if result.result is not None:
            return result.result
        return _load_result_ref(result.result_ref)

    # --- parallel ---------------------------------------------------------- #

    async def parallel(thunks) -> list:
        """Run zero-arg coroutine-returning thunks concurrently.

        Thunks are invoked in list order and each is stepped through its
        synchronous prologue (node_id allocation + journal lookup) before the
        next is invoked — preserving source-order node_id allocation. Uses
        ``return_exceptions=True``: a failing thunk yields its exception in the
        result list and does NOT cancel its siblings (Claude-Code semantics).
        """
        tasks = await _start_in_order([lambda t=t: t() for t in thunks])
        return await asyncio.gather(*tasks, return_exceptions=True)

    # --- pipeline ---------------------------------------------------------- #

    async def pipeline(items, *stages) -> list:
        """Run each item through ``stages`` as an independent serial chain.

        There is NO barrier between stages: item A may be in stage 3 while item
        B is still in stage 1. Each stage is ``Callable(prev, item, index) ->
        awaitable`` (a stage that only takes ``prev`` is also supported). A
        stage raising drops that item to the exception and skips its remaining
        stages; other items are unaffected. The returned list is aligned to
        ``items``.
        """
        items = list(items)

        def _chain_thunk(item, idx):
            async def _chain():
                prev = item
                for stage in stages:
                    prev = await _call_stage(stage, prev, item, idx)
                return prev

            return _chain

        thunks = [_chain_thunk(item, idx) for idx, item in enumerate(items)]
        tasks = await _start_in_order(thunks)
        return await asyncio.gather(*tasks, return_exceptions=True)

    # --- phase / log (sync, fire-and-forget publish) ----------------------- #

    def phase(title: str) -> None:
        """Set the current phase and emit ``phase_changed`` (sync, fire-and-forget).

        Claude-Code scripts call ``phase()`` without ``await``; we schedule the
        async publish via ``asyncio.create_task`` so there is no unawaited
        coroutine. The run-state update is synchronous and immediate.
        """
        run_state.current_phase = title
        _fire(_publish(ev.make_phase_changed(wf_id, title)))

    def log(message: str) -> None:
        """Emit a free-form ``workflow.log`` event (sync, fire-and-forget)."""
        _fire(_publish(ev.make_log(wf_id, message)))

    return {
        "node": node,
        "parallel": parallel,
        "pipeline": pipeline,
        "phase": phase,
        "log": log,
        "run_state": run_state,
    }


# --- module-level helpers -------------------------------------------------- #


class _Counter:
    """Monotonic node_id counter. Incremented inside the synchronous prologue.

    Safe without a lock within a single event loop because the prologue that
    calls :meth:`next` does not ``await`` between read and increment.
    """

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        nid = self._n
        self._n += 1
        return nid


async def _start_in_order(
    thunks: list[Callable[[], Awaitable]],
) -> list[asyncio.Task]:
    """Invoke ``thunks`` in list order, each stepped past its sync prologue.

    For each thunk we build its coroutine, wrap it in a task, then yield
    control (``await asyncio.sleep(0)``) so the task runs its synchronous
    prologue — which for ``node()`` is node_id allocation + journal lookup —
    before the next thunk is invoked. This makes node_id allocation order
    follow thunk/source order even though the tasks then proceed concurrently.
    """
    tasks: list[asyncio.Task] = []
    for thunk in thunks:
        task = asyncio.ensure_future(thunk())
        tasks.append(task)
        # Let the freshly-scheduled task run up to its first await (its
        # synchronous prologue) before creating the next one.
        await asyncio.sleep(0)
    return tasks


def _fire(coro: Awaitable) -> None:
    """Schedule a fire-and-forget coroutine on the running loop.

    Used by the synchronous ``phase``/``log`` to publish events without an
    ``await`` and without leaving an un-scheduled coroutine. If there is no
    running loop (e.g. called outside the engine), close the coroutine to avoid
    a "coroutine was never awaited" warning.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        getattr(coro, "close", lambda: None)()
        return
    loop.create_task(coro)


def _call_stage(stage, prev, item, index):
    """Call a pipeline stage, supporting both (prev, item, index) and (prev,).

    The signature is inspected up front (not via catching ``TypeError``) so a
    genuine ``TypeError`` raised inside the stage body is never swallowed.
    Returns the stage's awaitable.
    """
    import inspect

    try:
        sig = inspect.signature(stage)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
        ]
        has_varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
        npos = len(params)
    except (TypeError, ValueError):
        has_varargs, npos = True, 3

    if has_varargs or npos >= 3:
        return stage(prev, item, index)
    if npos == 2:
        return stage(prev, item)
    return stage(prev)
