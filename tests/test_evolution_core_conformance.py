"""Does the method interface actually hold a second algorithm?

The claim being tested is narrow and falsifiable: SimpleTES -- an algorithm designed by someone
else, with no MAP-Elites grid, no islands, and a best-of-K commit rule the current loop has no
concept of -- can be written against `pantheon.evolution.core` without editing anything outside
`methods/`. If that is false the seam is in the wrong place, and it is cheaper to find out now
than after four algorithms have been built on it.

Everything runs on a deterministic fake variator and fake evaluator: no model calls, no money, no
flakiness. The toy problem is hill-climbing an integer, which is enough to exercise selection,
batching, commit and failure handling without pretending to be a benchmark.
"""
from __future__ import annotations

import asyncio
import random
from typing import Dict, List

import pytest

from pantheon.evolution.core import (
    Budget,
    Create,
    EvolveContext,
    Individual,
    Measurement,
    Produced,
    Ranking,
    Store,
    TextGenome,
)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods import PUCTSelector, RPUCGSelector, SimpleTES


class CountingVariator:
    """Produces `k` children per Create, each the parent's value plus a small step.

    `fail_every` drops a candidate on a fixed schedule so the batch-shortfall path is exercised:
    a model returning fewer candidates than asked for is routine, and a method that cannot see it
    deadlocks.
    """

    def __init__(self, fail_every: int = 0, step: int = 1):
        self.calls = 0
        self.serial = 0
        self.fail_every = fail_every
        self.step = step
        self.rng = random.Random(0)

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        self.calls += 1
        parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
        base = int(parent.genome.meta.get("value", 0)) if parent else 0
        out: List[Produced] = []
        for j in range(item.k):
            if self.fail_every and (self.calls + j) % self.fail_every == 0:
                continue                      # the model simply did not return this one
            # a serial keeps every candidate a distinct individual even when two of them happen
            # to score the same -- identity is the content, not the score, and without this the
            # store's content dedup silently merges a batch
            self.serial += 1
            value = base + self.rng.randint(0, self.step)
            out.append(Produced(
                genome=TextGenome(text=f"#{self.serial} v={value}", kind=item.kind,
                                  meta={"value": value}),
                item_id=item.id,
                batch_id=item.batch_id,
                parent_ids=list(item.parent_ids),
            ))
        return out


class ValueEvaluator:
    """Score is the genome's value. Deterministic, free, and total."""

    kind = "code"

    def __init__(self, kind: str = "code"):
        self.kind = kind
        self.calls = 0

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        self.calls += 1
        v = float(ind.genome.meta.get("value", 0))
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": v}, cost=0.01)


def seed(value: int = 0, kind: str = "code") -> TextGenome:
    return TextGenome(text=f"v={value}", kind=kind, meta={"value": value})


def run(method, variator=None, evaluator=None, *, budget=12, concurrency=2):
    variator = variator or CountingVariator()
    evaluator = evaluator or ValueEvaluator()
    return asyncio.run(evolve(
        method=method,
        variator=variator,
        evaluators={"code": evaluator, "idea": evaluator},
        seeds=[seed(0)],
        objective="maximise the value",
        budget=Budget(max_items=budget),
        concurrency=concurrency,
    ))


class TestSimpleTESRuns:
    def test_it_runs_and_improves_on_the_seed(self):
        res = run(SimpleTES(num_chains=2, k_candidates=3, seed=1))
        assert res.items_run > 0
        best = res.best
        assert best is not None
        assert best.metrics()["combined_score"] > 0

    def test_the_budget_is_what_stops_it(self):
        res = run(SimpleTES(num_chains=2, k_candidates=2, seed=1), budget=5)
        assert res.items_run == 5

    def test_each_batch_commits_exactly_one_child_to_its_chain(self):
        """Best-of-K is the whole point of the algorithm: k candidates are measured, one joins the
        chain, and the others stay in the store unattached rather than being deleted."""
        m = SimpleTES(num_chains=1, k_candidates=4, seed=3)
        res = run(m, budget=3, concurrency=1)
        chain = m.chains[0]
        committed = len(chain) - 1                       # minus the seed
        assert committed == 3, f"3 batches should commit 3 children, got {committed}"
        assert len(res.store) > len(chain), "the losing candidates are kept in the store"

    def test_the_committed_child_is_the_best_of_its_batch(self):
        m = SimpleTES(num_chains=1, k_candidates=4, seed=5)
        res = run(m, budget=4, concurrency=1)
        for cid in m.chains[0][1:]:
            child = res.store.get(cid)
            siblings = res.store.children(child.parent_ids[0])
            best = max(s.metrics().get("combined_score", -1e9) for s in siblings)
            assert child.metrics()["combined_score"] == best

    def test_a_batch_still_closes_when_the_model_returns_too_few(self):
        """Without `on_failed` the batch counter never reaches k and the chain stalls at the seed."""
        m = SimpleTES(num_chains=1, k_candidates=3, seed=7)
        res = run(m, CountingVariator(fail_every=2), budget=4, concurrency=1)
        assert res.failures > 0
        assert len(m.chains[0]) > 1, "the chain grew despite candidates going missing"

    def test_chains_are_independent(self):
        m = SimpleTES(num_chains=3, k_candidates=2, seed=11)
        run(m, budget=9, concurrency=3)
        grown = [len(c) for c in m.chains]
        assert sum(g > 1 for g in grown) >= 2, f"expected several chains to grow, got {grown}"

    @pytest.mark.parametrize("selector", ["balance", "puct", "rpucg"])
    def test_every_selector_variant_runs(self, selector):
        res = run(SimpleTES(num_chains=2, k_candidates=2, selector=selector, seed=13), budget=6)
        assert res.best is not None


class TestBackpressure:
    def test_a_chain_with_an_open_batch_is_not_asked_for_more_work(self):
        """One open batch per chain is how SimpleTES avoids conditioning on stale history. With
        one chain and concurrency 4, the loop must still only ever have one item in flight."""
        m = SimpleTES(num_chains=1, k_candidates=2, seed=17)

        async def go():
            peak = 0
            ctx = EvolveContext(store=Store(), budget=Budget(max_items=10))
            await m.start(ctx, [])
            m.chains[0] = ["seed"]
            ctx.store.add(Individual(genome=seed(0), id="seed"))
            ctx.store.record(Measurement(individual_id="seed", metrics={"combined_score": 0.0}))
            for _ in range(3):
                items = await m.ask(ctx, 4)
                peak = max(peak, len(items))
            return peak

        assert asyncio.run(go()) == 1


class TestStatePersistence:
    def test_state_round_trips_and_reconcile_drops_unknown_ids(self):
        m = SimpleTES(num_chains=2, k_candidates=2, selector="rpucg", seed=19)
        res = run(m, budget=6)
        state = m.state_dict()

        restored = SimpleTES(num_chains=2, k_candidates=2, selector="rpucg", seed=19)
        restored.load_state_dict(state)
        assert restored.chains == m.chains

        restored.chains[0].append("does-not-exist")
        ctx = EvolveContext(store=res.store, budget=Budget())
        restored.reconcile(ctx)
        assert "does-not-exist" not in restored.chains[0]

    def test_rpucg_keeps_its_depth_table_across_a_restore(self):
        m = SimpleTES(num_chains=1, k_candidates=2, selector="rpucg", seed=23)
        run(m, budget=4, concurrency=1)
        assert isinstance(m.selector, RPUCGSelector)
        assert m.selector.depth, "depth should have been recorded on commit"
        restored = SimpleTES(num_chains=1, k_candidates=2, selector="rpucg")
        restored.load_state_dict(m.state_dict())
        assert restored.selector.depth == m.selector.depth


class TestTheAbstractionItself:
    def test_the_method_never_sees_a_fitness_it_did_not_compute(self):
        """Measurements carry raw metrics only. If the loop attached a score, multi-objective and
        adversarial methods would inherit a ranking they did not choose."""
        ev = ValueEvaluator()
        res = run(SimpleTES(num_chains=1, k_candidates=2, seed=29), evaluator=ev, budget=3)
        for ind in res.store:
            for m in ind.measurements:
                assert set(m.metrics) == {"combined_score"}
                assert not hasattr(m, "fitness")

    def test_ranking_is_produced_by_the_method(self):
        res = run(SimpleTES(num_chains=1, k_candidates=2, seed=31), budget=3)
        assert isinstance(res.ranking, Ranking)
        assert res.ranking.order
        assert res.ranking.best() in res.store

    def test_individuals_support_multiple_parents_and_a_cross_population_anchor(self):
        """The two edges the current `Program` cannot express: descent, and what a thing is about.
        A code individual has a code parent and the idea it implements."""
        store = Store()
        idea = store.add(Individual(genome=seed(1, kind="idea"), kind="idea"))
        a = store.add(Individual(genome=seed(2), kind="code"))
        b = store.add(Individual(genome=seed(3), kind="code"))
        child = store.add(Individual(genome=seed(4), kind="code",
                                     parent_ids=[a.id, b.id], anchor_id=idea.id))
        assert child.parent_ids == [a.id, b.id]
        assert [i.id for i in store.anchored_on(idea.id)] == [child.id]
        assert child.parent_id == a.id

    def test_identical_genomes_are_deduplicated_by_content(self):
        store = Store()
        first = store.add(Individual(genome=seed(7)))
        again = store.add(Individual(genome=seed(7)))
        assert again.id == first.id
        assert len(store) == 1

    def test_an_individual_can_hold_measurements_at_several_fidelities(self):
        """What cascade evaluation and successive halving need: the cheap reading and the real one
        are two observations of the same thing, and the gap between them is itself a signal."""
        store = Store()
        ind = store.add(Individual(genome=seed(5)))
        store.record(Measurement(individual_id=ind.id, fidelity="cheap",
                                 metrics={"combined_score": 0.4}))
        store.record(Measurement(individual_id=ind.id, fidelity="full",
                                 metrics={"combined_score": 0.9}))
        assert len(ind.measurements) == 2
        assert ind.metrics("cheap")["combined_score"] == 0.4
        assert ind.metrics("full")["combined_score"] == 0.9
        assert ind.metrics()["combined_score"] == 0.9      # latest wins by default


class TestTheOperatorBelongsToTheAlgorithm:
    """An algorithm is its search policy AND the operator it mutates with.

    SimpleTES generates with one completion that cannot run anything and never sees a score;
    Pantheon-Evolve's MAP-Elites uses a coding agent that verifies its edit before submitting. Run
    SimpleTES's policy on the agent and it scores better and is no longer SimpleTES. So the method
    names its own operator, and a caller who wants to swap it has to say so.
    """

    def test_each_method_declares_the_operator_it_is_defined_with(self):
        from pantheon.evolution.methods import MapElitesIslands, SimpleTES
        from pantheon.evolution.variators import (
            AgentVariator, CompletionVariator, SandboxVariator)

        assert isinstance(SimpleTES().default_variator(model="m"), CompletionVariator)
        assert isinstance(MapElitesIslands().default_variator(model="m"), AgentVariator)
        assert isinstance(MapElitesIslands().default_variator(model="m", sandbox=True),
                          SandboxVariator)

    def test_the_loop_uses_the_methods_operator_when_none_is_passed(self):
        from pantheon.evolution.core import Budget
        from pantheon.evolution.core.loop import evolve
        from pantheon.evolution.methods import SimpleTES

        used = {}

        class Recording:
            async def create(self, ctx, item):
                used["called"] = True
                return []

        m = SimpleTES(num_chains=1, k_candidates=1)
        m.default_variator = lambda **kw: Recording()          # type: ignore[assignment]
        asyncio.run(evolve(method=m, variator=None, evaluators={"code": ValueEvaluator()},
                           seeds=[seed(0)], budget=Budget(max_items=1), concurrency=1))
        assert used.get("called"), "the loop should have asked the method for its operator"

    def test_a_method_with_no_operator_refuses_to_run_rather_than_guessing(self):
        from pantheon.evolution.core import Budget
        from pantheon.evolution.core.loop import evolve
        from pantheon.evolution.core.method import BaseMethod

        class Nameless(BaseMethod):
            name = "nameless"

            async def ask(self, ctx, n):
                return []

            async def on_measured(self, ctx, ind, m):
                return None

            def rank(self, ctx, kind="code"):
                from pantheon.evolution.core import Ranking
                return Ranking()

        with pytest.raises(ValueError, match="default_variator"):
            asyncio.run(evolve(method=Nameless(), variator=None,
                               evaluators={"code": ValueEvaluator()}, seeds=[seed(0)],
                               budget=Budget(max_items=1)))


# --------------------------------------------------- operator knob forwarding ---
def _agent_of(variator):
    """The AgentVariator a method built, whether directly or behind a router."""
    from pantheon.evolution.variators.agent import AgentVariator

    if isinstance(variator, AgentVariator):
        return variator
    inner = getattr(variator, "code", None)
    return inner if isinstance(inner, AgentVariator) else None


def test_default_variator_forwards_every_operator_knob():
    """A method must hand the caller's operator budget to the operator it builds.

    `AnnealedIdeaCode.default_variator` dropped `max_tool_calls`, so its agent ran with an
    unlimited action budget while every other arm in a comparison ran on 14. The resulting
    difference in feasibility and wall clock read as a property of the search policy and was
    nothing of the kind. A method may choose WHICH operator it is defined with; it does not get to
    quietly change how much that operator may spend.
    """
    from pantheon.evolution.methods import AnnealedIdeaCode, MapElitesIslands

    kw = dict(evaluator=object(), model="m", timeout=1234,
              max_tool_calls=14, max_evaluations=5, max_turns=9,
              warm_start_file="warm.json", workspace_root="/tmp/ws",
              target_file="sequence.py")
    checked = 0
    for method in (AnnealedIdeaCode(), MapElitesIslands(feature_dimensions=["complexity"])):
        agent = _agent_of(method.default_variator(**kw))
        if agent is None:
            continue                       # this method is not defined with an agent
        checked += 1
        name = type(method).__name__
        assert agent.max_tool_calls == 14, f"{name} dropped max_tool_calls"
        assert agent.max_evaluations == 5, f"{name} dropped max_evaluations"
        assert agent.workspace_root == "/tmp/ws", f"{name} dropped workspace_root"
    assert checked >= 2, "expected at least two agent-defined methods to check"
