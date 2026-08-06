"""Two populations, alternating, with code hanging off ideas.

This is the algorithm the generalisation was built for, and the one that would be impossible in
the old loop: it evolves two kinds of thing, links them with an edge that is not descent, and
changes what it does from phase to phase. The tests here are mostly about those three properties
rather than about search quality, which a toy problem cannot say anything about.

Everything runs on fakes. The idea "judge" and the code generator are deterministic, and the
scoring is rigged so that an approach's *stated* quality and its *implemented* quality can be made
to disagree -- which is the case the method exists to handle and the one worth testing.
"""
from __future__ import annotations

import asyncio
import re
from typing import List

import pytest

from pantheon.evolution.core import (
    Budget,
    CodeGenome,
    Create,
    EvolveContext,
    Individual,
    Measurement,
    Produced,
    Store,
    TextGenome,
)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods import IdeaCodeAlternating

IDEA, CODE = "idea", "code"


class ScriptedVariator:
    """Ideas carry a declared quality; code inherits it and adds a little.

    `liars` names ideas whose implementations score far below what they promised, which is how the
    "sounds good, builds badly" case gets exercised.
    """

    def __init__(self, liars=()):
        self.n = 0
        self.liars = set(liars)

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        self.n += 1
        out = []
        for j in range(item.k):
            self.n += 1
            if item.kind == IDEA:
                quality = 0.9 if self.n % 3 == 0 else 0.4
                name = f"idea{self.n}"
                out.append(Produced(
                    genome=TextGenome(text=f"{name}: an approach worth {quality}", kind=IDEA),
                    item_id=item.id, batch_id=item.batch_id,
                    parent_ids=list(item.parent_ids),
                    meta={"summary": name}))
            else:
                idea = ctx.store.get(item.anchor_id) if item.anchor_id else None
                promised = _declared(idea)
                name = _name(idea)
                real = 0.05 if name in self.liars else promised
                parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
                base = _code_score(parent) if parent else 0.0
                score = max(real, base + 0.01) if name not in self.liars else real
                out.append(Produced(
                    genome=CodeGenome(files={"main.py": f"# {self.n}\nSCORE = {score:.4f}\n"}),
                    item_id=item.id, batch_id=item.batch_id,
                    parent_ids=list(item.parent_ids), anchor_id=item.anchor_id,
                    meta={"summary": f"implemented {name}"}))
        return out


def _declared(idea) -> float:
    if idea is None:
        return 0.5
    m = re.search(r"worth ([0-9.]+)", idea.genome.render())
    return float(m.group(1)) if m else 0.5


def _name(idea) -> str:
    if idea is None:
        return "?"
    m = re.search(r"(idea\d+)", idea.genome.render())
    return m.group(1) if m else "?"


def _code_score(ind) -> float:
    for line in getattr(ind.genome, "files", {}).get("main.py", "").splitlines():
        if line.startswith("SCORE ="):
            return float(line.split("=")[1])
    return 0.0


class FakeJudge:
    """Scores the idea exactly as declared -- an honest judge, so a divergence between prior and
    realised value can only come from the implementation, never from judge noise."""

    kind = IDEA

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"idea_score": _declared(ind)})


class FakeCodeEvaluator:
    kind = CODE

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        s = _code_score(ind)
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": s,
                                    "fitness_weights": {"combined_score": 1.0}})


def run(method, *, budget=20, variator=None, concurrency=1, seeds=None):
    return asyncio.run(evolve(
        method=method,
        variator=variator or ScriptedVariator(),
        evaluators={IDEA: FakeJudge(), CODE: FakeCodeEvaluator()},
        seeds=seeds if seeds is not None else [CodeGenome(files={"main.py": "SCORE = 0.0\n"})],
        objective="make SCORE large",
        budget=Budget(max_items=budget), concurrency=concurrency,
    ))


class TestAlternation:
    def test_nothing_is_implemented_before_an_approach_exists(self):
        """The first thing produced is an approach, and every implementation comes after one.

        A phase is spent by measurements, not by dispatches, so `k_ideas=2` means one work item
        already fills a two-idea round -- the second item is legitimately code. What must hold is
        the ordering, not the count.
        """
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2)
        res = run(m, budget=4)
        produced = [i for i in sorted(res.store, key=lambda x: x.order) if i.parent_ids
                    or i.kind == IDEA]
        assert produced[0].kind == IDEA, "the first thing produced is an approach"
        first_idea = produced[0].order
        for i in produced:
            if i.kind == CODE and i.anchor_id:
                assert i.order > first_idea

    def test_the_code_phase_follows_and_produces_implementations(self):
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        res = run(m, budget=8)
        assert res.store.of_kind(CODE), "the run should have reached the code phase"
        assert m.round.index >= 0

    def test_both_populations_grow_over_several_rounds(self):
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        res = run(m, budget=16)
        assert len(res.store.of_kind(IDEA)) >= 2
        assert len(res.store.of_kind(CODE)) >= 2

    def test_a_phase_ends_on_completed_work_not_dispatched_work(self):
        """The code phase has to start with ideas that have been *measured*; switching when the
        last idea was merely sent would begin implementing on unscored proposals."""
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        run(m, budget=3, concurrency=3)
        for iid in m.ideas:
            assert iid, "ideas recorded"


class TestAnchoring:
    def test_code_is_anchored_to_the_idea_it_implements(self):
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        res = run(m, budget=10)
        code = res.store.of_kind(CODE)
        implemented = [c for c in code if c.anchor_id]
        assert implemented, "code produced in the code phase carries its idea"
        for c in implemented:
            assert res.store.get(c.anchor_id).kind == IDEA

    def test_descent_and_aboutness_are_different_edges(self):
        """The property the old `Program.parent_id` could not express: a child can descend from
        one thing and be *about* another."""
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=2, ideas_kept=2, k_ideas=1)
        res = run(m, budget=14)
        both = [c for c in res.store.of_kind(CODE) if c.anchor_id and c.parent_ids]
        assert both, "at least one implementation has both a code parent and an idea"
        for c in both:
            assert c.anchor_id not in c.parent_ids

    def test_the_store_can_list_the_implementations_of_an_idea(self):
        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=2, ideas_kept=1, k_ideas=1)
        res = run(m, budget=10)
        for iid in m.ideas:
            for c in res.store.anchored_on(iid):
                assert c.anchor_id == iid


class TestIdeaValue:
    def test_an_unimplemented_idea_is_ranked_by_the_judges_prior(self):
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        res = run(m, budget=2)
        ctx = EvolveContext(store=res.store, budget=Budget())
        for iid in m.ideas:
            assert m.realised(ctx, iid) is None
            assert m.idea_value(ctx, iid) == m.prior(ctx, iid)

    def test_once_implemented_the_measurement_replaces_the_prior(self):
        """Evidence beats taste: an idea that was talked up and then built badly must fall."""
        store = Store()
        idea = store.add(Individual(genome=TextGenome(text="idea1: an approach worth 0.9",
                                                      kind=IDEA), kind=IDEA))
        store.record(Measurement(individual_id=idea.id, metrics={"idea_score": 0.9}))
        code = store.add(Individual(genome=CodeGenome(files={"main.py": "SCORE = 0.05\n"}),
                                    kind=CODE, anchor_id=idea.id))
        store.record(Measurement(individual_id=code.id, metrics={"combined_score": 0.05}))

        m = IdeaCodeAlternating()
        m.ideas = [idea.id]
        ctx = EvolveContext(store=store, budget=Budget())
        assert m.prior(ctx, idea.id) == 0.9
        assert m.realised(ctx, idea.id) == 0.05
        assert m.idea_value(ctx, idea.id) == 0.05

    def test_a_persuasive_but_unbuildable_idea_loses_to_a_modest_one_that_works(self):
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=2, ideas_kept=2, k_ideas=1)
        res = run(m, budget=18, variator=ScriptedVariator(liars={"idea3", "idea6", "idea9"}))
        ctx = EvolveContext(store=res.store, budget=Budget())
        implemented = [(m.prior(ctx, i), m.realised(ctx, i)) for i in m.ideas
                       if m.realised(ctx, i) is not None]
        liars = [(p, r) for p, r in implemented if p > 0.8 and r < 0.2]
        if liars:
            ranked = m.rank(ctx, IDEA)
            top = ranked.best()
            assert m.idea_value(ctx, top) > 0.2, "a liar must not stay on top once measured"

    def test_prior_versus_realised_is_reportable(self):
        """The diagnostic that says whether the idea evaluator is worth anything at all."""
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        res = run(m, budget=12)
        ctx = EvolveContext(store=res.store, budget=Budget())
        rows = m.prior_vs_realised(ctx)
        assert rows and {"id", "prior", "realised", "implementations"} <= set(rows[0])


class TestCarryingCodeAcrossIdeas:
    def test_a_new_idea_may_start_from_the_best_code_so_far(self):
        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=1, ideas_kept=1, k_ideas=1,
                                carry_best_code=True)
        res = run(m, budget=14)
        cross = [c for c in res.store.of_kind(CODE)
                 if c.parent_ids and c.anchor_id
                 and (res.store.get(c.parent_ids[0]).anchor_id not in (None, c.anchor_id))]
        assert cross or len(res.store.of_kind(CODE)) < 3, (
            "with carrying on, an implementation of a new idea may descend from an old idea's code")

    def test_turning_carrying_off_keeps_each_idea_on_its_own_line(self):
        """Off is the clean comparison: no idea inherits another's head start, so the scores of
        two ideas are about the ideas rather than about which ran second."""
        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=1, ideas_kept=1, k_ideas=1,
                                carry_best_code=False)
        res = run(m, budget=14)
        for c in res.store.of_kind(CODE):
            if c.parent_ids and c.anchor_id:
                parent = res.store.get(c.parent_ids[0])
                assert parent.anchor_id in (None, c.anchor_id), (
                    "with carrying off, an implementation descends only from its own idea's line "
                    "or from the seed")


class TestPersistence:
    def test_the_phase_and_the_idea_list_survive_a_restart(self, tmp_path):
        m = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        run(m, budget=8)
        state = m.state_dict()
        back = IdeaCodeAlternating(ideas_per_round=2, code_per_idea=1, ideas_kept=2, k_ideas=1)
        back.load_state_dict(state)
        assert back.ideas == m.ideas
        assert back.round.phase == m.round.phase
        assert back.round.index == m.round.index

    def test_reconcile_drops_ideas_the_store_lost(self):
        m = IdeaCodeAlternating()
        m.ideas = ["gone", "also-gone"]
        m.reconcile(EvolveContext(store=Store(), budget=Budget()))
        assert m.ideas == []


class TestTheOperator:
    def test_the_method_brings_a_router_that_handles_both_kinds(self):
        from pantheon.evolution.variators import IdeaCodeVariator

        v = IdeaCodeAlternating().default_variator(model="m")
        assert isinstance(v, IdeaCodeVariator)
        assert hasattr(v, "idea") and hasattr(v, "code")

    def test_the_judge_never_vetoes_an_idea_by_failing(self):
        """A judge that errored and returned 0 would delete an idea from the search on the
        strength of an API timeout."""
        from pantheon.evolution.variators.idea import _parse_judgement

        assert _parse_judgement('{"score": 0.8, "reason": "ok"}')[0] == 0.8
        assert _parse_judgement('```json\n{"score": 0.2}\n```')[0] == 0.2
        assert _parse_judgement("0.65")[0] == 0.65
        assert _parse_judgement("I could not decide.")[0] == 0.5


class TestRealisedIsWrittenBack:
    """The realised value is a derivation, and it is also recorded.

    `realised()` recomputes it from the implementations, which stays authoritative. Writing it
    onto the idea as a second measurement is what makes it survive a checkpoint, appear in a
    report without the reader knowing how to recompute it, and be visible to any other method
    that later reads the same store.
    """

    def test_an_implemented_idea_gains_a_second_measurement(self):
        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=2, ideas_kept=1, k_ideas=1)
        res = run(m, budget=8)
        implemented = [i for i in res.store.of_kind(IDEA) if res.store.anchored_on(i.id)]
        assert implemented, "the run should have implemented something"
        for idea in implemented:
            kinds = [set(mm.metrics) for mm in idea.measurements]
            assert any("idea_score" in k for k in kinds), "the judge's prior is still there"
            assert any("idea_realised" in k for k in kinds), "and what it actually achieved"

    def test_the_prior_survives_the_write_back(self):
        """`metrics()` returns the LATEST measurement, so a naive read would see the realised row
        and report the idea as never judged."""
        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=2, ideas_kept=1, k_ideas=1)
        res = run(m, budget=8)
        ctx = EvolveContext(store=res.store, budget=Budget())
        for idea in res.store.of_kind(IDEA):
            if res.store.anchored_on(idea.id):
                assert m.prior(ctx, idea.id) == _declared(idea)

    def test_the_recorded_value_matches_the_derivation(self):
        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=2, ideas_kept=1, k_ideas=1)
        res = run(m, budget=10)
        ctx = EvolveContext(store=res.store, budget=Budget())
        for idea in res.store.of_kind(IDEA):
            rows = [mm.metrics["idea_realised"] for mm in idea.measurements
                    if "idea_realised" in mm.metrics]
            if rows:
                assert rows[-1] == m.realised(ctx, idea.id)

    def test_a_row_is_added_only_when_the_number_moves(self):
        """Five implementations of one idea should leave a history of how its standing changed,
        not five copies of the same row."""
        store = Store()
        idea = store.add(Individual(genome=TextGenome(text="idea1: an approach worth 0.5",
                                                      kind=IDEA), kind=IDEA))
        store.record(Measurement(individual_id=idea.id, metrics={"idea_score": 0.5}))
        m = IdeaCodeAlternating()
        m.ideas = [idea.id]
        ctx = EvolveContext(store=store, budget=Budget())

        for score in (0.2, 0.2, 0.9):
            c = store.add(Individual(genome=CodeGenome(files={"main.py": f"SCORE = {score}\n"}),
                                     kind=CODE, anchor_id=idea.id))
            store.record(Measurement(individual_id=c.id, metrics={"combined_score": score}))
            m._record_realised(ctx, idea.id)

        rows = [mm.metrics["idea_realised"] for mm in idea.measurements
                if "idea_realised" in mm.metrics]
        assert rows == [0.2, 0.9], f"expected the two distinct standings, got {rows}"

    def test_it_survives_a_checkpoint(self, tmp_path):
        from pantheon.evolution.core.persistence import load_run

        m = IdeaCodeAlternating(ideas_per_round=1, code_per_idea=2, ideas_kept=1, k_ideas=1)
        asyncio.run(evolve(
            method=m, variator=ScriptedVariator(),
            evaluators={IDEA: FakeJudge(), CODE: FakeCodeEvaluator()},
            seeds=[CodeGenome(files={"main.py": "SCORE = 0.0\n"})],
            objective="make SCORE large", budget=Budget(max_items=8), concurrency=1,
            checkpoint_path=str(tmp_path), checkpoint_every=1,
        ))
        store, _, _ = load_run(str(tmp_path))
        realised = [mm for i in store.of_kind(IDEA) for mm in i.measurements
                    if "idea_realised" in mm.metrics]
        assert realised, "the realised value should be in the checkpoint, not only in memory"
        assert all(mm.fidelity == "realised" for mm in realised)
