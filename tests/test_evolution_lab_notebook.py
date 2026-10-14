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


class ScreenVariator(FakeVariator):
    """Candidate j of a prompt carries a full value and a deliberately DIFFERENT cheap value, so a
    test can tell which reading picked the promoted candidate: cheap order is the reverse of full."""

    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        self.instructions.append(item.context.instruction)
        parent = ctx.store.get(item.parent_ids[0])
        base = float(next(iter(parent.genome.files.values())).split("value=")[1].split()[0])
        out = []
        for j in range(item.k):
            self.serial += 1
            full, low = base + 0.001 * (j + 1), base + 0.001 * (item.k - j)
            out.append(Produced(
                genome=CodeGenome(files={"solution.py": f"# v{self.serial} value={full} low={low}\n"}),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids), anchor_id=item.anchor_id))
        return out


class ScreenEvaluator(FakeEvaluator):
    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        src = next(iter(ind.genome.files.values()), "")
        key = "low=" if fidelity != "full" and "low=" in src else "value="
        v = float(src.split(key)[1].split()[0]) if key in src else 0.0
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": v, "validity": 1.0}, cost=0.01)


def run_screen(method: LabNotebook, budget: int = 40, workers: int = 4):
    method._llm = FakeLLM()  # type: ignore[assignment]
    var = ScreenVariator()
    res = asyncio.run(evolve(
        method=method, variator=var, evaluators={CODE: ScreenEvaluator()},
        seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
        objective="maximise value", budget=Budget(max_items=budget), concurrency=workers))
    return res, var


class TestFidelityCascade:
    def test_screen_then_promote_best_by_cheap_reading(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=2, k_candidates=2, seed=1,
                        screen_fidelity="low", k_screen=4, promote_top=1)
        res, _ = run_screen(m)
        kids = [i for i in res.store.of_kind(CODE) if i.parent_ids]
        low = [k for k in kids if k.measured("low")]
        full = [k for k in kids if k.measured("full")]
        assert low and len(low) == len(kids), "every candidate is first read at the cheap fidelity"
        screens = [e for e in m.events if e["kind"] == "screen"]
        assert screens and all(e["screened"] == 4 and len(e["promoted"]) == 1 for e in screens)
        assert len(full) == len(screens), "exactly promote_top full measurements per screened step"
        # cheap order is the reverse of full order within a batch: the promoted candidate is the
        # one with the highest CHEAP reading, which is not the one a full reading would pick
        for e in screens:
            promoted = res.store.get(e["promoted"][0])
            assert round(promoted.measured("low").metrics["combined_score"], 6) == max(e["screen_scores"])

    def test_only_full_measurements_count(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=2, k_candidates=2, seed=1,
                        screen_fidelity="low", k_screen=4, promote_top=1)
        res, _ = run_screen(m)
        ranked = m.rank(_ctx(res), CODE).scores
        for ind_id in ranked:
            assert res.store.get(ind_id).measured("full") is not None
        for t in m.trajectories:
            for node in t.chain:
                assert res.store.get(node).measured("full") is not None, "a cheap reading joined a chain"

    def test_off_by_default(self):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=2, k_candidates=2, seed=1)
        res, _ = run_screen(m, budget=8)
        assert not any(i.measured("low") for i in res.store.of_kind(CODE))
        assert not any(e["kind"] == "screen" for e in m.events)


def _ctx(res):
    class _C:
        store = res.store
    return _C()


def _reply(content, reasoning="", finish="stop"):
    from types import SimpleNamespace
    msg = SimpleNamespace(content=content, reasoning=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                           usage=None)


class TestCutOffReply:
    """The method's own calls recover a reply whose budget went to thinking, the way the code
    operator does: continue from the captured reasoning with thinking off."""

    def _method(self, replies):
        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=1, k_candidates=2, seed=1)
        calls = []

        async def chat(messages, thinking=True):
            calls.append((messages, thinking))
            return replies.pop(0)
        m._chat = chat  # type: ignore[assignment]
        return m, calls

    def test_continues_from_reasoning(self):
        from pantheon.evolution.methods.lab_notebook import IDEAS_SYSTEM
        from pantheon.evolution.variators import usage
        before = usage.snapshot()
        m, calls = self._method([_reply("", "plan: try A then B", "length"),
                                 _reply('[{"title": "A", "idea": "do A"}]')])
        text = asyncio.run(m._llm(IDEAS_SYSTEM, "propose"))
        assert text.startswith("[{")
        assert len(calls) == 2 and calls[0][1] is True and calls[1][1] is False
        second = calls[1][0]
        assert second[:2] == calls[0][0], "the continuation re-sends the original prompt"
        assert "plan: try A then B" in second[2]["content"] and second[2]["role"] == "assistant"
        assert "Do not analyse" in second[3]["content"] and "EVOLVE-BLOCK" not in second[3]["content"]
        ev = [e for e in m.events if e["kind"] == "continue"]
        assert ev == [{"kind": "continue", "cycle": 0, "what": "ideas",
                       "reasoning_chars": len("plan: try A then B"), "ok": True}]
        after = usage.snapshot()
        assert after["continuations"] - before["continuations"] == 1
        assert after["continuations_ok"] - before["continuations_ok"] == 1

    def test_no_reasoning_no_continuation(self):
        m, calls = self._method([_reply("", "")])
        assert asyncio.run(m._llm("sys", "p")) == ""
        assert len(calls) == 1 and not m.events

    def test_answer_left_alone(self):
        m, calls = self._method([_reply("the notebook", "long thoughts")])
        assert asyncio.run(m._llm("sys", "p")) == "the notebook"
        assert len(calls) == 1

    def test_failed_continuation_is_recorded(self):
        m, calls = self._method([_reply("", "thoughts", "length"), _reply("", "")])
        assert asyncio.run(m._llm("sys", "p")) == ""
        assert m.events[-1]["ok"] is False and m.events[-1]["what"] == "other"


def test_usage_counts_length_stops():
    from pantheon.evolution.variators import usage
    before = usage.snapshot()["length_stops"]
    usage.add_response(_reply("", "x", "length"))
    usage.add_response(_reply("ok"))
    assert usage.snapshot()["length_stops"] - before == 1


class FamilyLLM(FakeLLM):
    """Cycle 1: K ideas from K families, family i worth gain 0.001*(i+1) per step. A portfolio
    cycle: ideas inside the family it was asked to develop."""

    def __init__(self):
        super().__init__()
        self.prompts: List[str] = []

    async def __call__(self, system: str, prompt: str) -> str:
        import re as _re
        if "proposing the next experiments" not in system:
            return await super().__call__(system, prompt)
        self.prompts.append(prompt)
        n = int(_re.search(r"Propose exactly K=(\d+)", prompt).group(1))
        fam = _re.search(r"The method family '([^']+)'", prompt)
        if fam:
            f = fam.group(1)
            ideas = [{"title": f"{f} variant {i}", "idea": f"Refine {f} gain=0.003",
                      "predicted_gain": 0.0, "family": f} for i in range(n)]
        else:
            ideas = [{"title": f"family {i} idea", "idea": f"Use method {i} gain={0.001 * (i + 1)}",
                      "predicted_gain": 0.0, "family": f"Family {i}"} for i in range(n)]
        return json.dumps(ideas)


class GainVariator(FakeVariator):
    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        import re as _re
        self.instructions.append(item.context.instruction)
        g = _re.search(r"gain=([0-9.]+)", item.context.instruction)
        bump = float(g.group(1)) if g else 0.0005
        parent = ctx.store.get(item.parent_ids[0])
        base = float(next(iter(parent.genome.files.values())).split("value=")[1].split()[0])
        out = []
        for _ in range(item.k):
            self.serial += 1
            out.append(Produced(
                genome=CodeGenome(files={"solution.py": f"# v{self.serial} value={base + bump}\n"}),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids), anchor_id=item.anchor_id))
        return out


def run_families(method: LabNotebook, budget: int, evaluator=None):
    llm = FamilyLLM()
    method._llm = llm  # type: ignore[assignment]
    res = asyncio.run(evolve(
        method=method, variator=GainVariator(), evaluators={CODE: evaluator or FakeEvaluator()},
        seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
        objective="maximise value", budget=Budget(max_items=budget), concurrency=4))
    return res, llm


class TestFamilyPortfolio:
    def _cycles(self, m):
        return [e for e in m.events if e["kind"] == "cycle"]

    def test_cycle_one_spans_families_then_top_two_develop_side_by_side(self):
        m = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=1, k_candidates=1, seed=1,
                        family_cycles=3, family_keep=2)
        res, llm = run_families(m, budget=16)
        cyc = self._cycles(m)
        assert len(cyc) == 4
        assert len(set(cyc[0]["families"])) == 4 and not cyc[0]["portfolio"]
        assert "DIFFERENT method family" in llm.prompts[0]
        # cycle 1's two best families (gains 0.004, 0.003) are developed in cycles 2 and 3
        for e in cyc[1:3]:
            assert e["portfolio"]
            assert sorted(e["families"]) == ["family 2", "family 2", "family 3", "family 3"]
            for fam, pid in zip(e["families"], e["parents"]):
                parent = res.store.get(pid)
                assert m.ideas[parent.anchor_id]["family"] == fam, "each family from its own best"
        assert len(set(cyc[1]["parents"])) == 2
        # the proposer sees the family table and is asked to stay inside the family
        assert any("# Method families tried" in p and "The method family 'family 3'" in p
                   for p in llm.prompts)
        # after the portfolio cycles: one champion-led cycle as usual
        assert not cyc[3]["portfolio"] and len(set(cyc[3]["parents"])) == 1
        assert all(h.get("family") for h in m.history)

    def test_deltas_are_against_each_trajectorys_own_parent(self):
        m = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=1, k_candidates=1, seed=1,
                        family_cycles=2, family_keep=2)
        res, _ = run_families(m, budget=12)
        rows = [h for h in m.history if h["cycle"] == 2]
        assert rows
        for h in rows:
            parent = res.store.get(h["parent_id"])
            assert h["parent_score"] == pytest.approx(m._score(parent))
            assert h["delta"] == pytest.approx(0.003)

    def test_off_by_default(self):
        m = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=1, k_candidates=1, seed=1)
        res, llm = run_families(m, budget=8)
        assert not any("family" in d for d in m.ideas.values())
        assert not any("method family" in p.lower() for p in llm.prompts)
        assert all("portfolio" not in e for e in self._cycles(m))

    def test_with_trajectory_cascade_every_parent_is_reread_cheaply(self):
        from pantheon.evolution.core import Measurement as _M

        class LowEval(FakeEvaluator):
            async def measure(self, ctx, ind, fidelity="full"):
                m = await super().measure(ctx, ind, fidelity)
                return _M(individual_id=ind.id, fidelity=fidelity, metrics=m.metrics, cost=0.01)
        m = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=1, k_candidates=1, seed=1,
                        family_cycles=3, family_keep=2, trajectory_fidelity="low")
        res, _ = run_families(m, budget=40, evaluator=LowEval())
        port = [e for e in self._cycles(m) if e.get("portfolio")]
        assert port
        for e in port:
            for pid in set(e["parents"]):
                assert res.store.get(pid).measured("low") is not None

    def test_state_round_trip_keeps_families(self):
        m = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=1, k_candidates=1, seed=1,
                        family_cycles=2)
        run_families(m, budget=8)
        m2 = LabNotebook(ideas_per_cycle=4, steps_per_trajectory=1, k_candidates=1, seed=1,
                         family_cycles=2)
        m2.load_state_dict(json.loads(json.dumps(m.state_dict())))
        assert {d.get("family") for d in m2.ideas.values()} == {d.get("family") for d in m.ideas.values()}

    def test_bad_args(self):
        with pytest.raises(ValueError):
            LabNotebook(family_cycles=-1)
        with pytest.raises(ValueError):
            LabNotebook(family_keep=0)
