"""Pin the search semantics of the current evolution algorithm, before it moves behind an interface.

There are no tests on `pantheon/evolution` (10k lines), and the only end-to-end check is a
benchmark that costs real LLM calls. That is not a net you can refactor against. These tests cover
the part that is about to be lifted out of `EvolutionDatabase` and `EvolutionTeam` into a pluggable
method: which parent gets selected, which child is admitted, how MAP-Elites bins and islands
behave, and how fitness is normalised as observed metric ranges grow.

They deliberately do NOT touch the LLM mutation path. That code is moving house, not changing
meaning; what must not change is the search.

Written to describe today's behaviour, including the parts that look surprising -- a test that
encodes a bug is still a net, and the surprise is called out in the docstring so a later change
is a decision rather than an accident.
"""
from __future__ import annotations

import random

import pytest

from pantheon.evolution.config import EvolutionConfig
from pantheon.evolution.database import EvolutionDatabase
from pantheon.evolution.program import CodebaseSnapshot, Program


def make_program(pid: str, score: float, *, generation: int = 0,
                 parent_id: str | None = None, code: str = "x = 1\n",
                 **metrics) -> Program:
    """A program whose fitness is driven by an explicit `combined_score`.

    `fitness_weights` is not decoration: without it `compute_function_score` returns 0.0 and every
    program ties. Which metrics count, and how much, is declared by the EVALUATOR alongside the
    metrics -- not by the config. Any replacement method has to keep that contract.
    """
    m = {"combined_score": score, "fitness_weights": {"combined_score": 1.0}}
    m.update(metrics)
    return Program(
        id=pid,
        snapshot=CodebaseSnapshot.from_single_file("main.py", code),
        generation=generation,
        parent_id=parent_id,
        metrics=m,
    )


def make_db(**overrides) -> EvolutionDatabase:
    # score on the function metrics alone; llm_weight>0 mixes in a constant 0.5
    # placeholder for programs that were never given LLM feedback, which would make
    # these assertions about ordering meaningless
    overrides.setdefault('function_weight', 1.0)
    overrides.setdefault('llm_weight', 0.0)
    cfg = EvolutionConfig(
        num_islands=overrides.pop("num_islands", 1),
        feature_dimensions=overrides.pop("feature_dimensions", ["complexity", "diversity"]),
        feature_bins=overrides.pop("feature_bins", 4),
        **overrides,
    )
    return EvolutionDatabase(config=cfg)


class TestAdmission:
    """`add()` returns whether the program is elite in its MAP-Elites bin."""

    def test_first_program_in_an_empty_bin_is_admitted(self):
        db = make_db()
        assert db.add(make_program("a", 0.5)) is True
        assert "a" in db.programs

    def test_every_program_is_stored_even_when_not_admitted(self):
        """Admission and storage are separate: a rejected child is still in `programs`, which is
        what makes lineage and the visualiser work. Only the bin's elite slot is contested."""
        db = make_db()
        db.add(make_program("a", 0.9, code="x = 1\n"))
        db.add(make_program("b", 0.1, code="x = 1\n"))
        assert set(db.programs) == {"a", "b"}

    def test_a_worse_program_in_the_same_bin_is_not_admitted(self):
        db = make_db()
        db.add(make_program("a", 0.9, code="x = 1\n"))
        assert db.add(make_program("b", 0.1, code="x = 1\n")) is False

    def test_a_better_program_in_the_same_bin_is_admitted(self):
        db = make_db()
        db.add(make_program("a", 0.1, code="x = 1\n"))
        assert db.add(make_program("b", 0.9, code="x = 1\n")) is True

    def test_total_improved_never_increments_which_is_a_bug(self):
        """BUG, pinned so a fix is deliberate. `add()` stores the program and puts it in the bin
        BEFORE asking `_get_best_in_bin` who the incumbent is, so the incumbent it finds is the
        new program itself whenever the new program is better. That takes the
        `existing_best_id == program.id` branch, which admits without counting. When the new
        program is worse the fitness comparison fails instead. Both paths skip the counter, so
        `total_improved` is always 0 -- and it is reported in `get_statistics()`."""
        db = make_db()
        db.add(make_program("a", 0.1, code="x = 1\n"))
        db.add(make_program("b", 0.9, code="x = 1\n"))   # a genuine improvement
        assert db.total_improved == 0
        assert db.get_statistics()["total_improved"] == 0

    def test_order_is_assigned_sequentially_in_insertion_order(self):
        db = make_db()
        for i, pid in enumerate("abc"):
            db.add(make_program(pid, 0.5))
            assert db.programs[pid].order == i


class TestBestTracking:
    def test_best_program_follows_the_highest_fitness(self):
        db = make_db()
        db.add(make_program("a", 0.2))
        db.add(make_program("b", 0.8))
        db.add(make_program("c", 0.5))
        assert db.best_program_id == "b"
        assert db.get_best_program().id == "b"

    def test_best_is_unchanged_by_a_later_worse_program(self):
        db = make_db()
        db.add(make_program("a", 0.8))
        db.add(make_program("b", 0.1))
        assert db.best_program_id == "a"


class TestIslands:
    def test_a_program_lands_on_the_island_it_is_given(self):
        db = make_db(num_islands=3)
        db.add(make_program("a", 0.5), target_island=2)
        assert db.programs["a"].island_id == 2
        assert "a" in db.islands[2]

    def test_islands_have_independent_elites(self):
        """The same bin on two islands holds two different elites -- this is what island models
        buy, and a method that flattens the islands would silently lose it."""
        db = make_db(num_islands=2)
        db.add(make_program("a", 0.9, code="x = 1\n"), target_island=0)
        admitted = db.add(make_program("b", 0.1, code="x = 1\n"), target_island=1)
        assert admitted is True, "island 1's bin was empty, so b is elite there"

    def test_migration_moves_programs_between_islands(self):
        db = make_db(num_islands=2)
        for i in range(10):
            db.add(make_program(f"p{i}", i / 10, code=f"x = {i}\n"), target_island=0)
        random.seed(0)
        moved = db.migrate(migration_rate=1.0)
        assert moved > 0


class TestSampling:
    def test_sampling_returns_a_stored_program_and_distinct_inspirations(self):
        db = make_db()
        for i in range(8):
            db.add(make_program(f"p{i}", i / 10, code=f"x = {i}\n"))
        random.seed(0)
        parent, inspirations = db.sample(num_inspirations=3)
        assert parent.id in db.programs
        assert len(inspirations) <= 3
        assert parent.id not in {p.id for p in inspirations}

    def test_sampling_an_empty_database_raises(self):
        with pytest.raises((ValueError, IndexError, KeyError)):
            make_db().sample(num_inspirations=1)

    def test_exploration_ratio_of_one_still_returns_a_real_program(self):
        db = make_db(exploration_ratio=1.0)
        db.add(make_program("a", 0.5))
        random.seed(0)
        parent, _ = db.sample(num_inspirations=0)
        assert parent.id == "a"


class TestMetricNormalisation:
    """Fitness is normalised against the *observed* range of each metric, so the same program's
    fitness changes as the run discovers wider values. Anything that caches a fitness across
    iterations is therefore wrong -- the loop recomputes the incumbent's score for exactly this
    reason, and a replacement method has to keep doing so."""

    def test_observed_metric_range_widens_with_new_values(self):
        db = make_db()
        db.add(make_program("a", 0.5))
        lo1, hi1 = db.metric_ranges["combined_score"]
        db.add(make_program("b", 5.0))
        lo2, hi2 = db.metric_ranges["combined_score"]
        assert hi2 > hi1 and lo2 <= lo1

    def test_a_programs_fitness_falls_when_a_much_better_one_appears(self):
        db = make_db()
        a = make_program("a", 1.0)
        db.add(a)
        before = a.fitness_score(db.config.feature_dimensions, db.metric_ranges, 1.0, 0.0)
        db.add(make_program("b", 100.0))
        after = a.fitness_score(db.config.feature_dimensions, db.metric_ranges, 1.0, 0.0)
        assert after < before


class TestLineage:
    def test_children_are_found_by_parent_id(self):
        db = make_db()
        db.add(make_program("root", 0.5))
        db.add(make_program("kid", 0.6, generation=1, parent_id="root"))
        assert [p.id for p in db.get_children("root")] == ["kid"]

    def test_ancestor_chain_excludes_both_the_root_and_the_target(self):
        """Documented behaviour, and easy to misread: the chain from g2 is [g1], not [g0, g1, g2].
        The root is dropped because it has no parent, and the target because it is not its own
        ancestor. Anything that walks lineage for prompt context has to know this."""
        db = make_db()
        db.add(make_program("g0", 0.1))
        db.add(make_program("g1", 0.2, generation=1, parent_id="g0"))
        db.add(make_program("g2", 0.3, generation=2, parent_id="g1"))
        assert [p.id for p in db.get_ancestor_chain("g2")] == ["g1"]

    def test_a_program_carries_exactly_one_parent_today(self):
        """The single `parent_id` is the constraint the generalised model has to break: a code
        individual needs both a code-lineage parent and the idea it implements."""
        p = make_program("kid", 0.5, parent_id="root")
        assert p.parent_id == "root"
        assert not hasattr(p, "parent_ids")
        assert not hasattr(p, "anchor_id")


class TestPersistence:
    def test_a_saved_database_reloads_with_its_programs_and_best(self, tmp_path):
        db = make_db(num_islands=2)
        db.add(make_program("a", 0.3), target_island=0)
        db.add(make_program("b", 0.9), target_island=1)
        db.save(str(tmp_path))
        back = EvolutionDatabase.load(str(tmp_path))
        assert set(back.programs) == {"a", "b"}
        assert back.best_program_id == "b"
        assert back.programs["b"].island_id == 1

    def test_statistics_report_what_was_added_and_improved(self):
        db = make_db()
        db.add(make_program("a", 0.1, code="x = 1\n"))
        db.add(make_program("b", 0.9, code="x = 1\n"))
        stats = db.get_statistics()
        assert stats["total_programs"] == 2
        assert stats["total_added"] == 2
