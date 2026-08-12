"""The driver: a function, not a class.

`EvolutionTeam` is 1899 lines holding four unrelated things -- the agents that write mutations,
the problem definition, the search state, and the loop -- with eight of its attributes being
scratch state for a single mutation that happens to live on an object shared by every worker.
None of that is a team, and none of it needs to be an object. What is actually needed is a loop
that dispatches work, records what came back, and tells the method about it.

Everything the loop does is infrastructure that every algorithm wants and no algorithm should
have to write: concurrency, budget accounting, checkpointing, and reporting. Every decision --
who to build on, what to keep, what is good, when to stop -- belongs to the method and is not
visible here. If a change to this file is ever needed to add an algorithm, the seam is wrong.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from pantheon.utils.log import logger

from .genome import Genome
from .individual import Individual, Ranking, Store
from .persistence import load_run, save_run
from .method import Budget, EvolveContext, EvolveMethod, Evaluator, Variator
from .work import Create, Failure, Measurement, Remeasure


@dataclass
class RunResult:
    store: Store
    ranking: Ranking
    method_state: Dict[str, Any] = field(default_factory=dict)
    items_run: int = 0
    failures: int = 0
    cost: float = 0.0
    seconds: float = 0.0

    @property
    def best(self) -> Optional[Individual]:
        b = self.ranking.best()
        return self.store.get(b) if b else None


async def _measure(
    evaluators: Dict[str, Evaluator],
    ctx: EvolveContext,
    ind: Individual,
    fidelity: str,
) -> Measurement:
    ev = evaluators.get(ind.kind)
    if ev is None:
        return Measurement(individual_id=ind.id, ok=False, fidelity=fidelity,
                           artifacts={"error": f"no evaluator for kind {ind.kind!r}"})
    return await ev.measure(ctx, ind, fidelity=fidelity)


async def _run_create(
    item: Create,
    ctx: EvolveContext,
    variator: Variator,
    evaluators: Dict[str, Evaluator],
) -> List[Any]:
    """Build up to k genomes, measure each, and report a Failure for every one short of k.

    The shortfall matters: a method tracking a batch of k is waiting for k answers, and a model
    that returns three candidates when asked for four would otherwise leave the batch open
    forever.
    """
    out: List[Any] = []
    try:
        produced = await variator.create(ctx, item)
    except Exception as e:  # a broken variator must not take the run down
        logger.warning(f"variator failed on {item.id}: {type(e).__name__}: {e}")
        produced = []
    for p in produced[: item.k]:
        ind = Individual(
            genome=p.genome,
            kind=item.kind,
            parent_ids=list(p.parent_ids or item.parent_ids),
            anchor_id=p.anchor_id if p.anchor_id is not None else item.anchor_id,
            meta=dict(p.meta),
        )
        parents = [ctx.store.get(i) for i in ind.parent_ids]
        ind.generation = 1 + max([g.generation for g in parents if g], default=-1)
        ind = ctx.store.add(ind)
        if p.measurement is not None:
            # already measured where it was made (the sandbox operator evaluates in the sandbox,
            # which is the whole reason evolved code never touches the host)
            m = p.measurement
            m.individual_id, m.item_id, m.batch_id = ind.id, item.id, item.batch_id
            ctx.store.record(m)
            out.append((ind, m) if m.ok else Failure(
                item_id=item.id, batch_id=item.batch_id, stage="evaluate",
                reason=str(m.artifacts.get("error", "invalid")),
                detail={"individual_id": ind.id}))
            continue
        try:
            m = await _measure(evaluators, ctx, ind, item.fidelity)
        except Exception as e:
            logger.warning(f"evaluator failed on {ind.id}: {type(e).__name__}: {e}")
            out.append(Failure(item_id=item.id, batch_id=item.batch_id, stage="evaluate",
                               reason=type(e).__name__, detail={"individual_id": ind.id}))
            continue
        m.individual_id, m.item_id, m.batch_id = ind.id, item.id, item.batch_id
        ctx.store.record(m)
        out.append((ind, m) if m.ok else Failure(
            item_id=item.id, batch_id=item.batch_id, stage="evaluate",
            reason=str(m.artifacts.get("error", "invalid")), detail={"individual_id": ind.id}))
    for _ in range(item.k - len(produced)):
        out.append(Failure(item_id=item.id, batch_id=item.batch_id, stage="generate",
                           reason="not produced"))
    return out


async def _run_remeasure(
    item: Remeasure, ctx: EvolveContext, evaluators: Dict[str, Evaluator]
) -> List[Any]:
    ind = ctx.store.get(item.individual_id)
    if ind is None:
        return [Failure(item_id=item.id, batch_id=item.batch_id, stage="evaluate",
                        reason="unknown individual")]
    try:
        m = await _measure(evaluators, ctx, ind, item.fidelity)
    except Exception as e:
        return [Failure(item_id=item.id, batch_id=item.batch_id, stage="evaluate",
                        reason=type(e).__name__, detail={"individual_id": ind.id})]
    m.individual_id, m.item_id, m.batch_id = ind.id, item.id, item.batch_id
    ctx.store.record(m)
    return [(ind, m) if m.ok else Failure(
        item_id=item.id, batch_id=item.batch_id, stage="evaluate",
        reason=str(m.artifacts.get("error", "invalid")), detail={"individual_id": ind.id})]


async def evolve(
    method: EvolveMethod,
    variator: Optional[Variator],
    evaluators: Optional[Dict[str, Evaluator]],
    seeds: Sequence[Genome],
    *,
    objective: str = "",
    budget: Optional[Budget] = None,
    concurrency: int = 1,
    store: Optional[Store] = None,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    report_kind: str = "",
    checkpoint_path: Optional[str] = None,
    checkpoint_every: int = 5,
    resume: bool = False,
) -> RunResult:
    """Run `method` until its budget runs out or it says it is done.

    With `checkpoint_path` the store and the method's state are written every `checkpoint_every`
    resolved items; `resume=True` reads them back first. Seeds are only evaluated on a fresh run --
    on a resume they are already in the store, and re-measuring them would spend budget to learn
    something already recorded.

    **The budget is a total for the whole run, not an increment.** Resuming a run that already
    spent 6 items with `max_items=10` buys four more, matching what `max_iterations` meant to the
    loop this replaces. A resume whose budget is already spent does nothing and says so, rather
    than exiting quietly as though it had worked.
    """
    if variator is None:
        variator = method.default_variator()
        if variator is None:
            raise ValueError(
                f"{getattr(method, 'name', method)!r} declares no default_variator() and none was "
                "passed. A method's operator is part of what the algorithm is, so it has to come "
                "from somewhere explicit."
            )
    # The caller owns the problem's evaluators; the method fills in the kinds it invented and
    # evaluates itself. Caller wins on a collision -- an experiment holding measurement fixed
    # across arms has to be able to say so.
    evaluators = {**(method.default_evaluators() or {}), **(evaluators or {})}
    t0 = time.time()
    store = store if store is not None else Store()
    resumed = False
    if resume and checkpoint_path:
        try:
            store, method_state, meta = load_run(checkpoint_path)
            method.load_state_dict(method_state)
            resumed = True
            logger.info(f"resumed {len(store)} individuals from {checkpoint_path}")
        except FileNotFoundError:
            logger.info(f"no checkpoint at {checkpoint_path}; starting fresh")
    budget = budget or Budget(max_items=50)
    ctx = EvolveContext(store=store, budget=budget, objective=objective,
                        concurrency=concurrency)

    seed_inds: List[Individual] = []
    if resumed:
        method.reconcile(ctx)
        seed_inds = [i for i in sorted(store, key=lambda x: x.order) if not i.parent_ids]
        budget.items_used = meta.get("items_used", 0)
        budget.cost_used = meta.get("cost_used", 0.0)
        if budget.exhausted():
            logger.warning(
                f"resumed run has already spent its budget "
                f"({budget.items_used} items, ${budget.cost_used:.4f}); nothing to do. "
                f"The budget is a total for the run -- raise it to continue."
            )
    else:
        for g in seeds:
            ind = store.add(Individual(genome=g, kind=getattr(g, "kind", "code")))
            m = await _measure(evaluators, ctx, ind, "full")
            m.individual_id = ind.id
            store.record(m)
            seed_inds.append(ind)
        await method.start(ctx, seed_inds)
    if on_event:
        on_event("resumed" if resumed else "seeded", {"n": len(seed_inds), "store": len(store)})

    running: Dict[asyncio.Task, Any] = {}
    items_run = failures = 0
    since_checkpoint = 0

    def checkpoint() -> None:
        if not checkpoint_path:
            return
        try:
            save_run(checkpoint_path, store, method,
                     meta={"items_used": budget.items_used, "cost_used": budget.cost_used,
                           "objective": objective})
        except Exception as e:  # a failed checkpoint must not end a run that is going fine
            logger.warning(f"checkpoint failed: {type(e).__name__}: {e}")

    def _sync_ctx() -> None:
        ctx.in_flight = len(running)

    try:
        while True:
            if budget.exhausted() or method.done(ctx):
                break
            _sync_ctx()
            free = max(0, concurrency - len(running))
            asked = await method.ask(ctx, free) if free else []
            for item in asked:
                if budget.exhausted():
                    break
                coro = (_run_remeasure(item, ctx, evaluators)
                        if isinstance(item, Remeasure)
                        else _run_create(item, ctx, variator, evaluators))
                task = asyncio.ensure_future(coro)
                running[task] = item
                budget.items_used += 1
                items_run += 1
            if not running:
                # nothing outstanding and the method wants nothing: it has converged
                if not asked:
                    break
                continue
            done, _ = await asyncio.wait(set(running), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                item = running.pop(task)
                try:
                    results = task.result()
                except Exception as e:
                    logger.warning(f"work item {getattr(item, 'id', '?')} raised: {e}")
                    results = [Failure(item_id=getattr(item, "id", ""),
                                       batch_id=getattr(item, "batch_id", ""),
                                       stage="evaluate", reason=type(e).__name__)]
                _sync_ctx()
                for r in results:
                    if isinstance(r, Failure):
                        failures += 1
                        await method.on_failed(ctx, r)
                        if on_event:
                            on_event("failed", {"reason": r.reason, "stage": r.stage})
                    else:
                        ind, m = r
                        budget.cost_used += m.cost
                        await method.on_measured(ctx, ind, m)
                        if on_event:
                            on_event("measured", {"id": ind.id, "metrics": m.metrics})
                since_checkpoint += 1
                if checkpoint_every and since_checkpoint >= checkpoint_every:
                    checkpoint()
                    since_checkpoint = 0
            budget.seconds_used = time.time() - t0
    finally:
        for task in running:
            task.cancel()
        if running:
            with contextlib.suppress(Exception):
                await asyncio.gather(*running, return_exceptions=True)
        checkpoint()

    ranking = method.rank(ctx, report_kind or (seed_inds[0].kind if seed_inds else "code"))
    return RunResult(
        store=store,
        ranking=ranking,
        method_state=method.state_dict(),
        items_run=items_run,
        failures=failures,
        cost=budget.cost_used,
        seconds=time.time() - t0,
    )
