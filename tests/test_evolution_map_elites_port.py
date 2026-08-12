"""Does the ported MAP-Elites behave like the one it replaces?

`test_evolution_search_semantics.py` pins what `EvolutionDatabase` does today. This file asserts
the same properties of `NicheMenu`, which is the port. Where the two must agree, they are
checked against each other on the same inputs rather than against numbers copied by hand.

The port is not a rewrite of the algorithm, so a difference here is a bug in the port -- except
for the one place it deliberately differs (fitness is computed by the method, not the individual),
which is called out where it shows.
"""
from __future__ import annotations

import asyncio
import random
from typing import List

import pytest

from pantheon.evolution.config import EvolutionConfig
from pantheon.evolution.core import (
    Budget,
    Create,
    EvolveContext,
    Individual,
    Measurement,
    Produced,
    Store,
    TextGenome,
)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.database import EvolutionDatabase
from pantheon.evolution.methods import NicheMenu
from pantheon.evolution.program import CodebaseSnapshot, Program


# --------------------------------------------------------------------------- helpers

def ind(pid: str, score: float, *, code: str | None = None, parent: str | None = None,
        f1: float = 0.5, f2: float = 0.5, **metrics) -> Individual:
    """`f1`/`f2` are the MAP-Elites feature coordinates, set explicitly.

    Feature dimensions that appear in the metrics are used verbatim, so driving them from the test
    makes "same bin" a decision rather than a side effect of a code-complexity heuristic. The
    genome text is unique per id because the store deduplicates by content, and two individuals
    sharing a bin must still be two individuals.
    """
    m = {"combined_score": score, "f1": f1, "f2": f2,
         "fitness_weights": {"combined_score": 1.0}}
    m.update(metrics)
    i = Individual(genome=TextGenome(text=code if code is not None else f"# {pid}\n", kind="code"),
                   id=pid, kind="code", parent_ids=[parent] if parent else [])
    i.measurements.append(Measurement(individual_id=pid, metrics=m))
    return i


def method(**kw) -> NicheMenu:
    kw.setdefault("num_islands", 1)
    kw.setdefault("function_weight", 1.0)
    kw.setdefault("llm_weight", 0.0)
    kw.setdefault("feature_bins", 4)
    kw.setdefault("feature_dimensions", ["f1", "f2"])
    return NicheMenu(**kw)


def ctx_with(store: Store) -> EvolveContext:
    return EvolveContext(store=store, budget=Budget(max_items=100))


def place(m: NicheMenu, ctx: EvolveContext, i: Individual, island=0) -> bool:
    # use what the store hands back: an identical genome resolves to the individual already
    # stored, and placing the discarded copy would put an unknown id on the grid
    return m._place(ctx, ctx.store.add(i), island=island)


class SerialVariator:
    """One child per Create, value = parent + 1, distinct content every time."""

    def __init__(self):
        self.n = 0

    async def create(self, ctx, item: Create) -> List[Produced]:
        self.n += 1
        parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
        base = float(parent.genome.meta.get("value", 0)) if parent else 0.0
        v = base + 1
        return [Produced(
            genome=TextGenome(text=f"#{self.n}", kind=item.kind, meta={"value": v}),
            item_id=item.id, batch_id=item.batch_id, parent_ids=list(item.parent_ids),
        )]


class ValueEvaluator:
    kind = "code"

    async def measure(self, ctx, i: Individual, fidelity: str = "full") -> Measurement:
        v = float(i.genome.meta.get("value", 0))
        return Measurement(individual_id=i.id, fidelity=fidelity, cost=0.01,
                           metrics={"combined_score": v,
                                    "fitness_weights": {"combined_score": 1.0}})


# --------------------------------------------------------------------------- tests

class TestAdmissionMatchesTheOriginal:
    def test_first_program_in_an_empty_bin_is_admitted(self):
        m, ctx = method(), ctx_with(Store())
        assert place(m, ctx, ind("a", 0.5)) is True

    def test_a_worse_program_in_the_same_bin_is_not_admitted(self):
        m, ctx = method(), ctx_with(Store())
        place(m, ctx, ind("a", 0.9))
        assert place(m, ctx, ind("b", 0.1)) is False

    def test_a_better_program_in_the_same_bin_takes_the_slot(self):
        m, ctx = method(), ctx_with(Store())
        place(m, ctx, ind("a", 0.1))
        assert place(m, ctx, ind("b", 0.9)) is True

    def test_a_rejected_child_is_still_stored(self):
        m, ctx = method(), ctx_with(Store())
        place(m, ctx, ind("a", 0.9))
        place(m, ctx, ind("b", 0.1))
        assert {"a", "b"} <= set(ctx.store.ids())

    def test_admission_agrees_with_evolution_database_on_the_same_sequence(self):
        """The port and the original, fed identical programs, must make the same accept/reject
        calls. This is the check that matters; the rest of the class is documentation."""
        seq = [("a", 0.5), ("b", 0.2), ("c", 0.9), ("d", 0.1), ("e", 0.95)]

        db = EvolutionDatabase(config=EvolutionConfig(
            num_islands=1, feature_dimensions=["complexity", "diversity"], feature_bins=4,
            function_weight=1.0, llm_weight=0.0))
        old = [db.add(Program(
            id=pid, snapshot=CodebaseSnapshot.from_single_file("main.py", "x = 1\n"),
            metrics={"combined_score": s, "fitness_weights": {"combined_score": 1.0}},
        ), target_island=0) for pid, s in seq]

        m, ctx = method(), ctx_with(Store())
        new = [place(m, ctx, ind(pid, s)) for pid, s in seq]

        assert new == old, f"port {new} != original {old}"


class TestGrid:
    def test_programs_with_different_features_occupy_different_bins(self):
        m, ctx = method(feature_bins=10), ctx_with(Store())
        place(m, ctx, ind("low", 0.5, f1=0.0, f2=0.0))
        place(m, ctx, ind("high", 0.5, f1=1.0, f2=1.0))
        assert len(m.elites) == 2, "two distinct feature profiles should fill two bins"

    def test_widening_a_feature_range_rebins_everything(self):
        """Bins are a function of the observed range, so a new extreme moves programs that were
        already placed. Anything that caches a bin without invalidating it drifts."""
        m, ctx = method(feature_bins=4), ctx_with(Store())
        for i in range(4):
            place(m, ctx, ind(f"p{i}", 0.5, f1=i / 10, f2=0.5))
        before = dict(m.elites)
        place(m, ctx, ind("outlier", 0.5, f1=50.0, f2=0.5))
        assert m.elites != before

    def test_islands_hold_independent_elites(self):
        m, ctx = method(num_islands=2), ctx_with(Store())
        place(m, ctx, ind("a", 0.9), island=0)
        assert place(m, ctx, ind("b", 0.1), island=1) is True

    def test_coverage_is_the_filled_fraction_of_the_grid(self):
        m, ctx = method(num_islands=1, feature_bins=4), ctx_with(Store())
        assert m.coverage() == 0.0
        place(m, ctx, ind("a", 0.5))
        assert 0 < m.coverage() <= 1.0


class TestFitness:
    def test_fitness_needs_the_evaluators_weights(self):
        """No `fitness_weights` means the evaluation did not say what to optimise, and the score
        is 0 -- the same convention the original uses to mark a failed evaluation."""
        m, ctx = method(), ctx_with(Store())
        bare = Individual(genome=TextGenome(text="x", kind="code"), id="bare", kind="code")
        bare.measurements.append(Measurement(individual_id="bare", metrics={"combined_score": 9.0}))
        ctx.store.add(bare)
        assert m.fitness(bare) == 0.0

    def test_a_programs_fitness_falls_when_a_much_better_one_appears(self):
        m, ctx = method(), ctx_with(Store())
        a = ind("a", 1.0)
        place(m, ctx, a)
        before = m.fitness(a)
        place(m, ctx, ind("b", 100.0))
        assert m.fitness(a) < before

    def test_fitness_matches_the_originals_number(self):
        cfg = EvolutionConfig(num_islands=1, feature_dimensions=["complexity", "diversity"],
                              feature_bins=4, function_weight=0.8, llm_weight=0.2)
        db = EvolutionDatabase(config=cfg)
        metrics = {"combined_score": 0.4, "other": 0.9,
                   "fitness_weights": {"combined_score": 0.7, "other": 0.3}}
        p = Program(id="p", snapshot=CodebaseSnapshot.from_single_file("main.py", "x = 1\n"),
                    metrics=dict(metrics))
        db.add(p)
        db.add(Program(id="q", snapshot=CodebaseSnapshot.from_single_file("main.py", "y = 2\n"),
                       metrics={"combined_score": 0.9, "other": 0.1,
                                "fitness_weights": {"combined_score": 0.7, "other": 0.3}}))

        m = NicheMenu(num_islands=1, feature_bins=4, function_weight=0.8, llm_weight=0.2)
        ctx = ctx_with(Store())
        place(m, ctx, ind("p", 0.4, other=0.9,
                          fitness_weights={"combined_score": 0.7, "other": 0.3}))
        place(m, ctx, ind("q", 0.9, other=0.1,
                          fitness_weights={"combined_score": 0.7, "other": 0.3}))

        expected = p.fitness_score(cfg.feature_dimensions, db.metric_ranges, 0.8, 0.2)
        assert m.fitness(ctx.store.get("p")) == pytest.approx(expected, abs=1e-9)


class TestSelection:
    def test_the_parent_is_always_a_stored_individual(self):
        m, ctx = method(), ctx_with(Store())
        for i in range(6):
            place(m, ctx, ind(f"p{i}", i / 10, f1=i / 10))
        for _ in range(20):
            assert m._sample_parent(ctx).id in ctx.store

    def test_inspirations_exclude_the_parent(self):
        m, ctx = method(), ctx_with(Store())
        for i in range(6):
            place(m, ctx, ind(f"p{i}", i / 10, f1=i / 10))
        parent = ctx.store.get("p3")
        assert parent.id not in {i.id for i in m._inspirations(ctx, parent, 3)}

    def test_full_exploration_still_returns_a_real_individual(self):
        m, ctx = method(exploration_ratio=1.0), ctx_with(Store())
        place(m, ctx, ind("a", 0.5))
        assert m._sample_parent(ctx).id == "a"

    def test_an_empty_grid_yields_no_work(self):
        m = method()
        assert asyncio.run(m.ask(ctx_with(Store()), 4)) == []


class TestMigration:
    def test_migration_moves_individuals_between_islands(self):
        m, ctx = method(num_islands=2, migration_rate=1.0), ctx_with(Store())
        for i in range(6):
            place(m, ctx, ind(f"p{i}", i / 10, f1=i / 10), island=0)
        m._migrate()
        assert len(m.islands[1]) > 0

    def test_migration_fires_on_the_interval(self):
        m = method(num_islands=2, migration_interval=3, migration_rate=1.0)
        ctx = ctx_with(Store())
        for i in range(3):
            i_ = ind(f"p{i}", i / 10, f1=i / 10)
            ctx.store.add(i_)
            asyncio.run(m.on_measured(ctx, i_, i_.measurements[0]))
        assert m.steps == 3
        assert len(m.islands[1]) > 0, "the third step should have triggered a migration"


class TestEndToEnd:
    def test_a_full_run_improves_and_respects_its_budget(self):
        m = method(num_islands=2, exploration_ratio=0.0)
        res = asyncio.run(evolve(
            method=m, variator=SerialVariator(), evaluators={"code": ValueEvaluator()},
            seeds=[TextGenome(text="seed", kind="code", meta={"value": 0.0})],
            objective="go up", budget=Budget(max_items=10), concurrency=2,
        ))
        assert res.items_run == 10
        assert res.best.metrics()["combined_score"] > 0

    def test_children_inherit_their_parents_island(self):
        """Islands only mean something if descent stays on one. If children were scattered at
        random the model would degenerate into one population with extra bookkeeping."""
        m = method(num_islands=3, migration_interval=0, exploration_ratio=0.0)
        res = asyncio.run(evolve(
            method=m, variator=SerialVariator(), evaluators={"code": ValueEvaluator()},
            seeds=[TextGenome(text="seed", kind="code", meta={"value": 0.0})],
            budget=Budget(max_items=6), concurrency=1,
        ))
        for i in res.store:
            if i.parent_id:
                assert m.island_of[i.id] == m.island_of[i.parent_id]


class TestPersistence:
    def test_state_round_trips(self):
        m, ctx = method(num_islands=2), ctx_with(Store())
        for i in range(5):
            place(m, ctx, ind(f"p{i}", i / 10, f1=i / 10), island=i % 2)
        back = method(num_islands=2)
        back.load_state_dict(m.state_dict())
        assert back.elites == m.elites
        assert back.island_of == m.island_of
        assert back.best_id == m.best_id

    def test_reconcile_drops_individuals_the_store_lost(self):
        m, ctx = method(), ctx_with(Store())
        place(m, ctx, ind("a", 0.5))
        m.island_of["ghost"] = 0
        m.islands[0].add("ghost")
        m.coords["ghost"] = {"complexity": 0.5, "diversity": 0.5}
        m.reconcile(ctx)
        assert "ghost" not in m.island_of
        assert "ghost" not in m.islands[0]
        assert all(v != "ghost" for v in m.elites.values())
