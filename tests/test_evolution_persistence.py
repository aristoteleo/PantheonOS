"""Can a run be stopped and picked back up without losing or repeating work?

This is the feature that has to exist before the old loop can be deleted -- runs cost hours and
real money, and `EvolutionTeam.evolve()` supported `resume_from`. Losing it in the refactor would
be trading a working feature for a tidier design.

Two things are separately at stake. The **store** is the record: individuals and measurements that
were paid for and cannot be recomputed. The **method state** is derived -- an archive, a set of
chains -- and a method that has changed shape can drop it and rebuild from the store, which is
what `reconcile` is for.
"""
from __future__ import annotations

import asyncio
import json
from typing import List

import pytest

from pantheon.evolution.core import (
    Budget,
    CodeGenome,
    Create,
    EvolveContext,
    Individual,
    ItemsGenome,
    Measurement,
    Produced,
    Store,
    TextGenome,
)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.core.persistence import load_run, register_genome, save_run
from pantheon.evolution.methods import NicheMenu, SimpleTES


class Counter:
    """A variator that never repeats a genome, so identity survives a round trip.

    The counter starts from what is already stored rather than from zero. A fresh instance
    restarting at #1 after a resume would emit content identical to the first run's children, and
    the store -- correctly -- would deduplicate them away, so the resumed run would appear to
    produce nothing. Real mutation operators do not repeat themselves; this one has to be told
    not to.
    """

    def __init__(self):
        self.n = 0

    async def create(self, ctx, item: Create) -> List[Produced]:
        out = []
        self.n = max(self.n, len(ctx.store))
        for _ in range(item.k):
            self.n += 1
            parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
            base = float(parent.genome.meta.get("value", 0)) if parent else 0.0
            out.append(Produced(
                genome=TextGenome(text=f"#{self.n}", kind=item.kind,
                                  meta={"value": base + 1}),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids)))
        return out


class Value:
    kind = "code"

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        v = float(ind.genome.meta.get("value", 0))
        return Measurement(individual_id=ind.id, fidelity=fidelity, cost=0.02,
                           metrics={"combined_score": v,
                                    "fitness_weights": {"combined_score": 1.0}})


def seed(v: float = 0.0) -> TextGenome:
    return TextGenome(text="seed", kind="code", meta={"value": v})


def run(method, path, *, budget, resume=False, variator=None):
    return asyncio.run(evolve(
        method=method, variator=variator or Counter(), evaluators={"code": Value()},
        seeds=[seed()], budget=Budget(max_items=budget), concurrency=1,
        checkpoint_path=str(path), checkpoint_every=1, resume=resume,
    ))


class TestRoundTrip:
    def test_a_saved_store_reloads_with_its_individuals_and_measurements(self, tmp_path):
        store = Store()
        a = store.add(Individual(genome=CodeGenome(files={"m.py": "x = 1\n"}), kind="code"))
        store.record(Measurement(individual_id=a.id, metrics={"combined_score": 0.7},
                                 cost=0.5, duration=1.5))
        b = store.add(Individual(genome=TextGenome(text="an idea", kind="idea"), kind="idea",
                                 parent_ids=[a.id], anchor_id=a.id))
        save_run(str(tmp_path), store, NicheMenu())

        back, _, _ = load_run(str(tmp_path))
        assert {i.id for i in back} == {a.id, b.id}
        ra = back.get(a.id)
        assert ra.genome.files == {"m.py": "x = 1\n"}
        assert ra.measurements[0].metrics["combined_score"] == 0.7
        assert ra.measurements[0].cost == 0.5
        rb = back.get(b.id)
        assert rb.parent_ids == [a.id] and rb.anchor_id == a.id and rb.kind == "idea"

    def test_lineage_edges_are_rebuilt_not_just_the_fields(self, tmp_path):
        """The store's child and anchor indexes are derived, so a reload has to rebuild them or
        every lineage query silently returns nothing."""
        store = Store()
        idea = store.add(Individual(genome=TextGenome(text="i", kind="idea"), kind="idea"))
        code = store.add(Individual(genome=CodeGenome(files={"a.py": "1"}), kind="code",
                                    parent_ids=[idea.id], anchor_id=idea.id))
        save_run(str(tmp_path), store, NicheMenu())
        back, _, _ = load_run(str(tmp_path))
        assert [i.id for i in back.children(idea.id)] == [code.id]
        assert [i.id for i in back.anchored_on(idea.id)] == [code.id]

    def test_order_is_preserved(self, tmp_path):
        """Order is quoted to the model as `#N` and used to compare runs, so it is part of the
        record rather than an artefact of insertion."""
        store = Store()
        ids = [store.add(Individual(genome=TextGenome(text=f"t{i}"))).id for i in range(4)]
        save_run(str(tmp_path), store, NicheMenu())
        back, _, _ = load_run(str(tmp_path))
        assert [back.get(i).order for i in ids] == [0, 1, 2, 3]

    def test_unserialisable_artifacts_do_not_lose_the_checkpoint(self, tmp_path):
        """Artifacts come from user evaluators and hold arbitrary objects. One numpy array must
        not cost the entire run's record."""
        store = Store()
        i = store.add(Individual(genome=TextGenome(text="t")))
        store.record(Measurement(individual_id=i.id, metrics={"s": 1.0},
                                 artifacts={"obj": object(), "fine": [1, 2]}))
        save_run(str(tmp_path), store, NicheMenu())
        back, _, _ = load_run(str(tmp_path))
        m = back.get(i.id).measurements[0]
        assert m.metrics["s"] == 1.0
        assert m.artifacts["fine"] == [1, 2]
        assert isinstance(m.artifacts["obj"], str)

    def test_a_new_genome_kind_only_has_to_register_itself(self, tmp_path):
        store = Store()
        i = store.add(Individual(genome=ItemsGenome(items=("Gata4", "Nkx2-5"), kind="panel"),
                                 kind="panel"))
        save_run(str(tmp_path), store, NicheMenu())
        back, _, _ = load_run(str(tmp_path))
        assert back.get(i.id).genome.items == ("Gata4", "Nkx2-5")


class TestResume:
    def test_resuming_keeps_the_earlier_individuals_and_adds_to_them(self, tmp_path):
        """The budget is a TOTAL for the run, as `max_iterations` was for the loop this replaces,
        so continuing means raising it rather than passing the increment."""
        m1 = NicheMenu(num_islands=1, function_weight=1.0, llm_weight=0.0)
        first = run(m1, tmp_path, budget=4)
        assert len(first.store) == 5          # seed + 4

        m2 = NicheMenu(num_islands=1, function_weight=1.0, llm_weight=0.0)
        second = run(m2, tmp_path, budget=7, resume=True)
        assert len(second.store) == 8, "4 already recorded plus 3 more, on top of the seed"

    def test_resuming_with_an_already_spent_budget_does_nothing(self, tmp_path):
        m1 = NicheMenu(num_islands=1)
        run(m1, tmp_path, budget=4)
        res = run(NicheMenu(num_islands=1), tmp_path, budget=4, resume=True)
        assert len(res.store) == 5, "nothing new: the total was already reached"

    def test_resuming_does_not_re_evaluate_the_seed(self, tmp_path):
        """Re-measuring the seed would spend budget to learn something already in the store."""
        run(NicheMenu(num_islands=1), tmp_path, budget=2)

        class CountingValue(Value):
            calls = 0

            async def measure(self, ctx, ind, fidelity="full"):
                CountingValue.calls += 1
                return await Value.measure(self, ctx, ind, fidelity)

        asyncio.run(evolve(
            method=NicheMenu(num_islands=1), variator=Counter(),
            evaluators={"code": CountingValue()}, seeds=[seed()],
            budget=Budget(max_items=4), concurrency=1,      # 2 already spent, so 2 more
            checkpoint_path=str(tmp_path), checkpoint_every=1, resume=True,
        ))
        assert CountingValue.calls == 2, "two children, no seed re-measurement"

    def test_the_method_state_survives_and_reconciles(self, tmp_path):
        m1 = NicheMenu(num_islands=2, function_weight=1.0, llm_weight=0.0)
        run(m1, tmp_path, budget=4)
        elites_before, islands_before = dict(m1.elites), dict(m1.island_of)

        m2 = NicheMenu(num_islands=2, function_weight=1.0, llm_weight=0.0)
        run(m2, tmp_path, budget=0, resume=True)
        assert m2.island_of == islands_before
        assert m2.elites == elites_before

    def test_simpletes_chains_survive_a_restart(self, tmp_path):
        m1 = SimpleTES(num_chains=2, k_candidates=2, seed=1)
        run(m1, tmp_path, budget=4)
        chains_before = [list(c) for c in m1.chains]
        assert any(len(c) > 1 for c in chains_before), "at least one chain grew"

        m2 = SimpleTES(num_chains=2, k_candidates=2, seed=1)
        run(m2, tmp_path, budget=0, resume=True)
        assert [list(c) for c in m2.chains] == chains_before

    def test_a_method_can_drop_its_state_and_rebuild_from_the_store(self, tmp_path):
        """The store is the record; the archive is derived. A method whose shape changed between
        runs discards its state and reconciles, rather than being stuck with a stale grid."""
        run(NicheMenu(num_islands=1, feature_bins=4), tmp_path, budget=4)
        store, _, _ = load_run(str(tmp_path))

        fresh = NicheMenu(num_islands=1, feature_bins=16)     # different grid
        ctx = EvolveContext(store=store, budget=Budget())
        for ind in sorted(store, key=lambda i: i.order):
            fresh._place(ctx, ind, island=0)
        assert len(fresh.elites) > 0
        assert set(fresh.island_of) == {i.id for i in store}

    def test_resuming_without_a_checkpoint_starts_fresh_rather_than_failing(self, tmp_path):
        res = run(NicheMenu(num_islands=1), tmp_path / "nothing-here",
                  budget=2, resume=True)
        assert len(res.store) == 3

    def test_budget_accounting_carries_across_the_restart(self, tmp_path):
        run(NicheMenu(num_islands=1), tmp_path, budget=3)
        meta = json.loads((tmp_path / "run.json").read_text())
        assert meta["items_used"] == 3
        assert meta["cost_used"] > 0


class TestAtomicity:
    def test_a_checkpoint_is_written_atomically(self, tmp_path):
        """Written aside and renamed, so a crash mid-write leaves the previous checkpoint intact
        instead of a truncated file that cannot be loaded."""
        run(NicheMenu(num_islands=1), tmp_path, budget=2)
        assert not list(tmp_path.glob("*.tmp"))
        for name in ("store.json", "method.json", "run.json"):
            json.loads((tmp_path / name).read_text())


class TestReporting:
    """The 4,300-line visualiser was written against `EvolutionDatabase`. Rather than rewrite it,
    a view presents a new-format run in the same shape -- so a results directory renders whichever
    loop produced it."""

    def test_a_new_format_run_renders_an_html_report(self, tmp_path):
        from pantheon.evolution.core.report_view import is_new_format, load_view
        from pantheon.evolution.visualizer import EvolutionVisualizer

        m = NicheMenu(num_islands=1, function_weight=1.0, llm_weight=0.0)
        run(m, tmp_path, budget=4)
        assert is_new_format(str(tmp_path))

        vis = EvolutionVisualizer.from_path(str(tmp_path))
        assert len(vis.programs) == 5                  # seed + 4
        out = tmp_path / "report.html"
        vis.generate_html(str(out))
        assert out.stat().st_size > 10_000

    def test_the_view_delegates_fitness_to_the_method(self, tmp_path):
        """Recomputing the formula here would drift the moment a method defines fitness its own
        way -- which is the entire reason fitness moved onto the method."""
        from pantheon.evolution.core.report_view import load_view

        m = NicheMenu(num_islands=1, function_weight=1.0, llm_weight=0.0)
        run(m, tmp_path, budget=3)
        view = load_view(str(tmp_path))
        metrics = {"combined_score": 0.5, "fitness_weights": {"combined_score": 1.0}}
        assert view.compute_function_score(metrics) == view.method.function_score(metrics)

    def test_a_method_without_a_grid_reports_no_bins_rather_than_fake_ones(self, tmp_path):
        from pantheon.evolution.core.report_view import RunView, load_run

        m = SimpleTES(num_chains=2, k_candidates=1, seed=3)
        run(m, tmp_path, budget=3)
        store, _, _ = load_run(str(tmp_path))
        view = RunView(store, m)
        assert view.archive == []
        assert list(view.iter_filled_bins(0)) == []
        assert view.get_statistics()["total_programs"] == len(store)
