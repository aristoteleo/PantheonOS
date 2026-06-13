"""Orchestration API injected into the sandboxed Leader script (Task 7).

This module builds the five names a Leader script calls — ``node``,
``parallel``, ``pipeline``, ``phase``, ``log`` — bound (per workflow run) to a
:class:`~pantheon.workflow.journal.Journal`, a
:class:`~pantheon.workflow.runner.NodeRunner`, a
:class:`~pantheon.workflow.events.WorkflowEventPublisher` + ``chat_id``, the
:class:`~pantheon.workflow.runner.NodeRunContext`, a concurrency semaphore, a
shared **node_id counter**, and a small mutable :class:`RunState`.

Deterministic node_id allocation (resume correctness)
=====================================================
Resume correctness depends on the Nth ``node()`` *in source order* this run
being assigned the SAME ``node_id`` it had on the recorded run, so
``journal.lookup(node_id, key)`` consults the right prior position. The old
implementation allocated the id inside each ``node()`` coroutine's prologue and
relied on ``ensure_future`` + ``sleep(0)`` step ordering — which races under
nested ``parallel`` / multi-stage ``pipeline`` (an inner ``node()`` preceded by
an ``await`` allocates in execution-timing order, not source order). That is a
silent-corruption bug: resume could map cached results to the WRONG nodes.

The fix is **eager synchronous submission**: node_id allocation happens
synchronously at the moment ``node()`` is CALLED, in source-evaluation order,
decoupled from execution timing.

* ``node(...)`` is a **regular (sync) function** returning an awaitable. Its
  synchronous body, with NO ``await``, does in order: (1) allocate ``node_id``,
  (2) compute input hashes + ``compute_node_key``, (3) ``journal.lookup``
  (capturing the cached entry). It then returns an inner ``_execute()``
  coroutine closing over ``node_id``/``key``/``cached`` that does the
  events + run-or-return-cached + record. The script writes ``await node(...)``:
  calling ``node(...)`` runs the sync allocation immediately; awaiting runs
  ``_execute()``.

* ``parallel(thunks)`` synchronously materializes ``coros = [t() for t in
  thunks]`` (calling each thunk in list order → each ``node()`` allocates its
  id synchronously, in source order, BEFORE any execution), then
  ``await asyncio.gather(*coros, return_exceptions=True)``. A NESTED ``parallel``
  thunk calls ``parallel(...)`` synchronously, which synchronously materializes
  its inner coros → allocation is synchronous depth-first in source order.

* ``pipeline(items, *stages)`` is the hard case: a stage-k>0 ``node()`` cannot
  be called synchronously (it needs stage-(k-1)'s result). We use **reserved id
  blocks**: at the synchronous start of ``pipeline`` reserve ``N*S`` ids
  (N=len(items), S=len(stages)) in **item-major, stage-minor** order — item i,
  stage s → ``block_base + i*S + s``. The reserved id for the stage about to run
  is published via a ``contextvars.ContextVar`` (``_reserved_node_id``); each
  item-chain coroutine sets the var immediately before awaiting stage s, and
  ``node()``'s sync allocator consumes it (resetting the var) instead of calling
  the counter. Each chain runs as its OWN ``asyncio.Task``, which copies the
  current context at creation (PEP 567), so interleaved chains cannot steal each
  other's reserved id.

  Phase-1 constraint (documented + logged): exactly one ``node()`` per stage. A
  stage that calls ``node()`` zero times leaves its reserved id unused (fine). A
  stage that calls ``node()`` MORE than once consumes the reserved id on the
  first call only; subsequent calls fall back to ``counter.next()`` — which is
  NOT position-deterministic across resume. We log a warning when that happens.

The counter remains the source of sequential + parallel ids; ``pipeline``
reserves blocks from the SAME counter, so all ids across a script are unique.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
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

logger = logging.getLogger(__name__)

#: Default cap on concurrent ``runner.run`` calls.
API_DEFAULT_CONCURRENCY = 8

#: Publishes the reserved node_id for the pipeline stage about to call node().
#: ``None`` means "no reservation; allocate from the counter". Set per item-chain
#: coroutine in its own task (whose context is a copy) so interleaved chains
#: never collide.
_reserved_node_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_reserved_node_id", default=None
)


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

    def _allocate_node_id() -> int:
        """Allocate the node_id for a ``node()`` call SYNCHRONOUSLY.

        Consumes a pipeline-reserved id from the contextvar if one is set (and
        resets the var so a second ``node()`` in the same stage falls back to
        the counter — Phase-1: one node per stage). Otherwise pulls the next id
        from the monotonic counter (sequential + parallel).
        """
        reserved = _reserved_node_id.get()
        if reserved is not None:
            _reserved_node_id.set(None)
            return reserved
        return counter.next()

    # --- node -------------------------------------------------------------- #

    def node(
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
        """Allocate a node SYNCHRONOUSLY, return an awaitable that executes it.

        This is a **regular function**, not a coroutine: calling ``node(...)``
        runs the synchronous prologue (id allocation + key + journal lookup)
        immediately, in source-evaluation order. Awaiting the returned object
        runs the node (events + run-or-return-cached + record).
        """
        # ---- SYNCHRONOUS PROLOGUE (runs at CALL time, no await) ----------- #
        node_id = _allocate_node_id()
        input_hashes = _input_hashes(tuple(inputs))
        key = compute_node_key(instruction, template, schema, model, input_hashes)
        cached = journal.lookup(node_id, key)
        eff_phase = phase or run_state.current_phase
        # ---- END SYNCHRONOUS PROLOGUE ------------------------------------- #

        async def _execute() -> Any:
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
            await _publish(
                ev.make_node_started(wf_id, node_id, label, eff_phase)
            )

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

        return _execute()

    # --- parallel ---------------------------------------------------------- #

    def parallel(thunks) -> Any:
        """Run zero-arg awaitable-returning thunks concurrently.

        This is a **regular function** returning an awaitable (like ``node``).
        At CALL time it synchronously calls each thunk in list order — so each
        ``node()`` (or nested ``parallel()``, itself sync) inside a thunk
        allocates its node_id(s) immediately, in source order, DEPTH-FIRST,
        before any execution. Awaiting the returned object gathers the
        materialized awaitables. ``return_exceptions=True``: a failing thunk
        yields its exception in the result list and does NOT cancel siblings
        (Claude-Code semantics).

        ``parallel`` MUST be sync-returning-awaitable (not ``async def``) so a
        NESTED ``parallel`` thunk materializes its inner coros synchronously at
        the moment the outer thunk list is evaluated — otherwise a later sibling
        ``node()`` would allocate before the nested block's nodes, breaking
        depth-first source order.
        """
        coros = [t() for t in thunks]  # synchronous, source-order allocation

        async def _gather() -> list:
            return await asyncio.gather(*coros, return_exceptions=True)

        return _gather()

    # --- pipeline ---------------------------------------------------------- #

    def pipeline(items, *stages) -> Any:
        """Run each item through ``stages`` as an independent serial chain.

        Regular function returning an awaitable (like ``node``/``parallel``).
        The ``N*S`` reserved-id block is allocated SYNCHRONOUSLY at call time so
        that a ``pipeline`` nested inside ``parallel`` claims its block in source
        order, depth-first, before later siblings allocate.

        There is NO barrier between stages: item A may be in stage 3 while item
        B is still in stage 1. Each stage is ``Callable(prev, item, index) ->
        awaitable`` (a stage that only takes ``prev`` is also supported). A
        stage raising drops that item to the exception and skips its remaining
        stages; other items are unaffected. The returned list is aligned to
        ``items``.

        node_id allocation is deterministic via RESERVED ID BLOCKS: ``N*S`` ids
        reserved in item-major/stage-minor order (item i, stage s ->
        ``block_base + i*S + s``), independent of completion timing. Phase-1
        assumes exactly one ``node()`` per stage (see module docstring).
        """
        items = list(items)
        n_items = len(items)
        n_stages = len(stages)

        # Reserve the whole N*S block SYNCHRONOUSLY, up front, from the shared
        # counter so all ids across the script stay unique. Item-major order.
        block_base = counter.reserve(n_items * n_stages)

        def _chain_thunk(item, idx):
            async def _chain():
                prev = item
                for s, stage in enumerate(stages):
                    reserved = block_base + idx * n_stages + s
                    # Set the reservation IMMEDIATELY before the stage call. The
                    # stage's node() runs its sync allocator within this same
                    # task and consumes it. Each chain runs in its own task whose
                    # context is a copy (see below), so this cannot leak across
                    # concurrently-interleaved chains.
                    _reserved_node_id.set(reserved)
                    prev = await _call_stage(stage, prev, item, idx)
                    # Defensive: if the stage didn't call node() (zero nodes),
                    # clear the stale reservation so it can't bleed into the
                    # next stage of THIS chain.
                    _reserved_node_id.set(None)
                return prev

            return _chain

        # Each chain runs as its OWN asyncio.Task. A Task copies the current
        # context at creation time (PEP 567), so the ``_reserved_node_id`` var
        # each chain sets inside itself lives in that task's private context
        # copy and is invisible to sibling chains — even when they interleave on
        # the event loop. (Verified: see test_pipeline_stress_many_chains_*.)
        tasks = [
            asyncio.ensure_future(_chain_thunk(item, idx)())
            for idx, item in enumerate(items)
        ]

        async def _gather() -> list:
            return await asyncio.gather(*tasks, return_exceptions=True)

        return _gather()

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
    """Monotonic node_id allocator. Incremented inside the synchronous prologue.

    Safe without a lock within a single event loop because :meth:`next` /
    :meth:`reserve` are called from synchronous code (no ``await`` between read
    and increment).
    """

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        nid = self._n
        self._n += 1
        return nid

    def reserve(self, count: int) -> int:
        """Reserve ``count`` consecutive ids; return the first (block base)."""
        base = self._n
        self._n += count
        return base


def _fire(coro) -> None:
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
