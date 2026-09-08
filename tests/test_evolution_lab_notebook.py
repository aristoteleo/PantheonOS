"""LabNotebook: understanding-guided trajectory search, on fakes.

The claims tested are structural: each cycle proposes K ideas and runs one short trajectory per
idea; the idea rides in every step's instruction; a trajectory's best joins the references
tagged with its idea; the history carries numbers next to the digest; the notebook is rewritten
at cycle boundaries; state round-trips. Nothing here claims the search is good.
"""
from __future__ import annotations

import asyncio
import json
from typing import List

import pytest

from pantheon.evolution.core import (
    Budget,
    CodeGenome,
    EvolveContext,
    Individual,
    Measurement,
    Produced,
)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods import LabNotebook
from pantheon.evolution.methods.lab_notebook import CODE, IDEA, _extract_json


class FakeVariator:
    """Each candidate scores its parent's value plus a small idea-dependent step."""

    def __init__(self):
        self.serial = 0
        self.instructions: List[str] = []

    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        self.instructions.append(item.context.instruction)
        out = []
        for j in range(item.k):
            self.serial += 1
            parent = ctx.store.get(item.parent_ids[0])
            src = next(iter(parent.genome.files.values()))
            base = float(src.split("value=")[1].split()[0])
            bump = 0.02 if "faster" in item.context.instruction else 0.005
            # the two candidates of one prompt score IDENTICALLY: ties are routine in the real
            # run and a tuple-max over (score, Individual) raised on exactly this
            out.append(Produced(
                genome=CodeGenome(files={"solution.py":
                                         f"# v{self.serial} value={base + bump}\n"}),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids), anchor_id=item.anchor_id))
        return out


class FakeEvaluator:
    kind = CODE

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        src = next(iter(ind.genome.files.values()), "")
        v = float(src.split("value=")[1].split()[0]) if "value=" in src else 0.0
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": v, "validity": 1.0}, cost=0.01)


class FakeLLM:
    """Scripted replies for the method's own calls, keyed on the system prompt."""

    def __init__(self):
        self.calls: List[str] = []

    async def __call__(self, system: str, prompt: str) -> str:
        self.calls.append(system[:40])
        if "proposing the next experiments" in system:
            ideas = [{"title": f"idea {i} faster" if i == 0 else f"idea {i}",
                      "idea": f"Do thing {i} to the program.", "predicted_gain": 0.01 * i}
                     for i in range(4)]
            return "```json\n" + json.dumps(ideas) + "\n```"
        if "writing up one experiment" in system:
            return json.dumps({"outcome": "went up", "why": "because", "adherence": 0.8,
                               "lesson": "keep doing it"})
        if "revising your lab notebook" in system:
            return "## Problem structure\nrevised notebook\n"
        return "## Problem structure\ninitial notebook\n"


def run(method: LabNotebook, budget: int = 12, workers: int = 4):
    fake = FakeLLM()
    method._llm = fake  # type: ignore[assignment]
    var = FakeVariator()
    res = asyncio.run(evolve(
        method=method, variator=var, evaluators={CODE: FakeEvaluator()},
        seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
        objective="maximise value", budget=Budget(max_items=budget), concurrency=workers))
    return res, fake, var


class TestLabNotebook:
    def test_cycle_structure(self):
        m = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=2, k_candidates=2, seed=1)
        res, fake, var = run(m, budget=8)
        ideas = res.store.of_kind(IDEA)
        assert len(ideas) == 4, "one cycle proposes exactly K ideas"
        assert m.understanding.startswith("## Problem structure")
        # 4 trajectories x 2 steps = 8 items: exactly one cycle fits the budget
        assert res.items_run == 8
        # every code child anchors on an idea and the idea rides in every instruction
        kids = [i for i in res.store.of_kind(CODE) if i.parent_ids]
        assert kids and all(k.anchor_id in {i.id for i in ideas} for k in kids)
        assert all("The ONE idea this trajectory tests" in s for s in var.instructions)

    def test_references_and_history_carry_numbers(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=2)
        res, fake, var = run(m, budget=4)      # two cycles of 2 items each
        assert m.cycle == 2
        assert len(m.history) >= 2
        row = m.history[0]
        for key in ("parent_score", "best_score", "delta", "predicted", "adherence",
                    "outcome", "lesson"):
            assert key in row
        assert row["delta"] == pytest.approx(row["best_score"] - row["parent_score"])
        # each trajectory's best joined the references, tagged with its idea
        assert len(m.references) >= 3
        tagged = [res.store.get(i).anchor_id for i in m.references[1:]]
        assert all(t in m.ideas for t in tagged)
        # notebook was rewritten at the cycle boundary
        assert "revised notebook" in m.understanding
        assert any(c.startswith("You are a research scientist revising") for c in fake.calls)

    def test_parent_selection_leaves_the_champion_sometimes(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2,
                        p_best_parent=0.0, seed=3)
        res, fake, var = run(m, budget=6)
        parents = [e["parent"] for e in m.events if e.get("kind") == "cycle"]
        best = res.ranking.best()
        # with p_best_parent=0 a later cycle must start from a non-champion reference
        assert len(parents) >= 2 and parents[-1] != best

    def test_state_round_trip(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=4)
        res, fake, var = run(m, budget=2)
        st = json.loads(json.dumps(m.state_dict()))
        m2 = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=4)
        m2.load_state_dict(st)
        assert m2.understanding == m.understanding
        assert m2.history == m.history
        assert m2.references == m.references
        assert [t.idea_id for t in m2.trajectories] == [t.idea_id for t in m.trajectories]
        ctx = EvolveContext(store=res.store, budget=Budget(max_items=1))
        m2.reconcile(ctx)
        assert m2.seed_id == m.seed_id

    def test_json_extraction_tolerates_prose_and_fences(self):
        assert _extract_json('here you go:\n```json\n[{"a": 1}]\n```\nthanks') == [{"a": 1}]
        assert _extract_json('{"x": 2} trailing', "object") == {"x": 2}
        assert _extract_json("no json here") is None

    def test_default_variator_is_simpletes_operator(self):
        from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator
        m = LabNotebook()
        v = m.default_variator(model="m", target_file="solution.py", max_output_tokens=1000)
        assert isinstance(v, UpstreamCompletionVariator)
        assert m.model == "m" and m.max_tokens == 1000 and m.evolve_file == "solution.py"


class RecordingLLM(FakeLLM):
    """FakeLLM that keeps the full system prompt of every call."""

    def __init__(self):
        super().__init__()
        self.systems: List[str] = []

    async def __call__(self, system: str, prompt: str) -> str:
        self.systems.append(system)
        return await super().__call__(system, prompt)


def run_recording(method: LabNotebook, budget: int = 12, workers: int = 4):
    fake = RecordingLLM()
    method._llm = fake  # type: ignore[assignment]
    var = FakeVariator()
    res = asyncio.run(evolve(
        method=method, variator=var, evaluators={CODE: FakeEvaluator()},
        seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
        objective="maximise value", budget=Budget(max_items=budget), concurrency=workers))
    return res, fake, var


class TestAblationSwitches:
    """Each switch removes exactly its own model call and nothing else."""

    def test_no_ideas_runs_plain_parallel_trajectories(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=1,
                        ideas="none")
        _, fake, var = run_recording(m)
        assert not any("proposing the next experiments" in s for s in fake.systems)
        assert var.instructions and all("ONE idea" not in i for i in var.instructions)
        assert m.history and all(r["title"].startswith("trajectory") for r in m.history)
        # the digest still ran: the notebook keeps learning even without steering
        assert any("writing up one experiment" in s for s in fake.systems)

    def test_generic_ideas_come_from_the_fixed_list_without_a_model_call(self):
        from pantheon.evolution.methods.lab_notebook import GENERIC_IDEAS
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=1,
                        ideas="generic")
        _, fake, var = run_recording(m)
        assert not any("proposing the next experiments" in s for s in fake.systems)
        titles = {t for t, _ in GENERIC_IDEAS}
        assert m.history and all(r["title"] in titles for r in m.history)
        assert all("ONE idea" in i for i in var.instructions)

    def test_no_notebook_skips_understand_and_update(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=1,
                        notebook=False)
        _, fake, _ = run_recording(m)
        assert m.understanding == "(notebook disabled)"
        assert not any("revising your lab notebook" in s for s in fake.systems)
        assert any("proposing the next experiments" in s for s in fake.systems)
        assert any("writing up one experiment" in s for s in fake.systems)

    def test_no_digest_keeps_the_numbers_and_drops_the_prose(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=1,
                        digest=False)
        _, fake, _ = run_recording(m)
        assert not any("writing up one experiment" in s for s in fake.systems)
        assert m.history
        for r in m.history:
            assert r["outcome"] == "" and r["lesson"] == ""
            assert isinstance(r["delta"], float) and r["n_candidates"] >= 1
        assert any("revising your lab notebook" in s for s in fake.systems)

    def test_bad_ideas_mode_is_rejected(self):
        with pytest.raises(ValueError):
            LabNotebook(ideas="random")


class TestSelectorKnob:
    """The inner steps can use any of the port's selectors; the tree policy only matters once a
    trajectory outgrows the inspiration count."""

    def test_rpucg_runs_and_round_trips_when_chains_outgrow_inspirations(self):
        m = LabNotebook(ideas_per_cycle=1, steps_per_trajectory=4, k_candidates=2,
                        num_inspirations=2, seed=3, selector="rpucg")
        assert m.selector.name == "rpucg"
        res, fake, var = run_recording(m, budget=10)
        assert res.items_run > 0
        st = m.state_dict()
        assert st["selector"] == "rpucg" and isinstance(st["selector_state"], dict)
        m2 = LabNotebook(ideas_per_cycle=1, steps_per_trajectory=4, k_candidates=2,
                         num_inspirations=2, seed=3, selector="rpucg")
        m2.load_state_dict(st)
        assert m2.selector.state_dict() == m.selector.state_dict()

    def test_unknown_selector_is_rejected(self):
        with pytest.raises(ValueError):
            LabNotebook(selector="random")
