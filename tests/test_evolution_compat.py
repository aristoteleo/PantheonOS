"""The old `EvolutionTeam` API, on the new loop.

Thirteen call sites in this repo build `EvolutionTeam(config=...)` and await `.evolve(...)`, and
none of them were changed by the refactor. That only holds if the shim really returns an
`EvolutionResult` shaped the way callers read it -- several of them print `get_summary()`, which
derives its numbers from `iteration_results`, so an empty or placeholder list produces a run that
looks like it did nothing.

No model is called: the variator is replaced with a deterministic fake.
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from pantheon.evolution import compat
from pantheon.evolution.compat import EvolutionTeam
from pantheon.evolution.config import EvolutionConfig
from pantheon.evolution.core import Create, Individual, Measurement, Produced
from pantheon.evolution.program import CodebaseSnapshot


class FakeVariator:
    """One child per Create, its score encoded in the file it writes."""

    def __init__(self):
        self.n = 0

    async def create(self, ctx, item: Create) -> List[Produced]:
        from pantheon.evolution.core import CodeGenome

        self.n += 1
        parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
        base = float((parent.metrics() or {}).get("combined_score", 0.0)) if parent else 0.0
        value = base + 0.1
        return [Produced(
            genome=CodeGenome(files={"main.py": f"# v{self.n}\nSCORE = {value}\n"}),
            item_id=item.id, batch_id=item.batch_id, parent_ids=list(item.parent_ids),
            meta={"summary": f"raised it to {value:.2f}", "cost": 0.05,
                  "mutation_seconds": 1.25},
        )]


class FakeEvaluator:
    kind = "code"

    async def evaluate_files(self, files):
        score = 0.0
        for line in files.get("main.py", "").splitlines():
            if line.startswith("SCORE ="):
                score = float(line.split("=")[1])
        return {"success": True, "metrics": {"combined_score": score,
                                             "fitness_weights": {"combined_score": 1.0}},
                "artifacts": {}, "error": None}

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        out = await self.evaluate_files(getattr(ind.genome, "files", {}))
        return Measurement(individual_id=ind.id, metrics=out["metrics"], fidelity=fidelity,
                           ok=True, duration=0.5)


@pytest.fixture
def fake_variator(monkeypatch):
    v = FakeVariator()
    monkeypatch.setattr(compat, "variator_from_config", lambda cfg, ev, code="": v)
    return v


def run_team(cfg, seed_score=0.0, evaluator=None):
    team = EvolutionTeam(config=cfg, evaluator=evaluator or FakeEvaluator())
    return team, asyncio.run(team.evolve(
        initial_code=CodebaseSnapshot(files={"main.py": f"SCORE = {seed_score}\n"}),
        evaluator_code="def evaluate(p): return {}",
        objective="raise SCORE",
    ))


class TestTheOldApiStillWorks:
    def test_a_run_produces_a_result_with_a_best_program(self, fake_variator):
        _, res = run_team(EvolutionConfig(max_iterations=4, num_islands=1, num_workers=1))
        assert res.best_program is not None
        assert isinstance(res.best_program.snapshot, CodebaseSnapshot)
        assert res.best_program.metrics["combined_score"] > 0

    def test_the_summary_reports_the_iterations_that_ran(self, fake_variator):
        """`get_summary()` is what most callers print. It counts `iteration_results`, so leaving
        that list empty makes a working run read as a failed one."""
        _, res = run_team(EvolutionConfig(max_iterations=4, num_islands=1, num_workers=1))
        assert res.total_iterations == 4
        assert res.successful_iterations == 4
        summary = res.get_summary()
        assert "Total iterations: 4" in summary
        assert "Successful: 4" in summary

    def test_per_iteration_timing_and_cost_are_carried_through(self, fake_variator):
        """The variator stamps its wall time and cost onto the child and the measurement carries
        evaluation time; all three have to reach `IterationResult` or every cost report is $0."""
        _, res = run_team(EvolutionConfig(max_iterations=3, num_islands=1, num_workers=1))
        assert res.total_cost == pytest.approx(3 * 0.05)
        for r in res.iteration_results:
            assert r.mutation_time == pytest.approx(1.25)
            assert r.evaluation_time == pytest.approx(0.5)
            assert r.llm_cost == pytest.approx(0.05)

    def test_improvements_are_counted(self, fake_variator):
        _, res = run_team(EvolutionConfig(max_iterations=3, num_islands=1, num_workers=1))
        assert res.improvements >= 1, "each child scores above its parent in this fake"

    def test_acceptance_reflects_the_methods_decision(self, fake_variator):
        """`accepted` is whether the child took a MAP-Elites bin, which only the method knows --
        the grid afterwards remembers who holds a bin now, not who ever did."""
        team, res = run_team(EvolutionConfig(max_iterations=4, num_islands=1, num_workers=1))
        accepted = {r.child_id for r in res.iteration_results if r.accepted}
        assert accepted == team.method.admitted_ids & accepted
        assert accepted, "at least one child should have taken a bin"

    def test_score_history_has_an_entry_per_individual(self, fake_variator):
        _, res = run_team(EvolutionConfig(max_iterations=3, num_islands=1, num_workers=1))
        assert len(res.score_history) == 4          # seed + 3
        assert res.best_score_history == sorted(res.best_score_history)

    def test_config_reaches_the_method(self, fake_variator):
        team, _ = run_team(EvolutionConfig(max_iterations=2, num_islands=3, feature_bins=7,
                                           num_workers=1))
        assert team.method.num_islands == 3
        assert team.method.feature_bins == 7


class TestCheckpointing:
    def test_db_path_still_means_checkpoint_here(self, fake_variator, tmp_path):
        """`db_path` was where the old loop wrote its state; callers already pass it."""
        run_team(EvolutionConfig(max_iterations=2, num_islands=1, num_workers=1,
                                 db_path=str(tmp_path)))
        assert (tmp_path / "store.json").exists()
        assert (tmp_path / "method.json").exists()

    def test_resume_from_continues_an_earlier_run(self, fake_variator, tmp_path):
        run_team(EvolutionConfig(max_iterations=2, num_islands=1, num_workers=1,
                                 db_path=str(tmp_path)))
        team = EvolutionTeam(config=EvolutionConfig(max_iterations=5, num_islands=1,
                                                    num_workers=1, db_path=str(tmp_path)),
                             evaluator=FakeEvaluator())
        asyncio.run(team.evolve(
            initial_code=CodebaseSnapshot(files={"main.py": "SCORE = 0.0\n"}),
            evaluator_code="def evaluate(p): return {}",
            objective="raise SCORE",
            resume_from=str(tmp_path),
        ))
        assert len(team._last_run.store) == 6, "seed + 2 from before + 3 more"


class TestSandboxOperator:
    """`sandbox_mutation` swaps the operator for one that runs the mutation off this machine.

    Its defining property is not where the agent runs but where the child is *scored*: the sandbox
    evaluates it there, so evolved code never executes on the host. That only holds if the loop
    accepts the measurement it is handed instead of making its own.
    """

    def test_the_flag_selects_the_sandbox_operator(self):
        from pantheon.evolution.compat import variator_from_config
        from pantheon.evolution.variators import AgentVariator, SandboxVariator

        assert isinstance(variator_from_config(EvolutionConfig(), None, ""), AgentVariator)
        assert isinstance(
            variator_from_config(EvolutionConfig(sandbox_mutation=True), None, "code"),
            SandboxVariator)

    def test_a_sandbox_measured_child_is_not_re_evaluated_on_the_host(self, monkeypatch):
        from pantheon.evolution.core import Budget, CodeGenome
        from pantheon.evolution.core.loop import evolve
        from pantheon.evolution.methods import NicheMenu
        from pantheon.evolution.variators import SandboxVariator
        import pantheon.evolution.sandbox as sandbox_mod

        async def fake_run(parent_files, evaluator_code, objective, system_prompt, **kw):
            return {"ok": True, "submitted": True, "summary": "did a thing",
                    "child_files": {"main.py": "SCORE = 0.9\n"},
                    "metrics": {"combined_score": 0.9,
                                "fitness_weights": {"combined_score": 1.0}},
                    "sandbox": "sb-123", "cost": 0.07}

        monkeypatch.setattr(sandbox_mod, "run_mutation_in_sandbox", fake_run)

        class SeedOnly:
            """Measures the seed and nothing else: a child reaching here is the failure."""

            kind = "code"

            async def measure(self, ctx, ind, fidelity="full"):
                assert not ind.parent_ids, "the host must not evaluate a sandbox-measured child"
                return Measurement(individual_id=ind.id, fidelity=fidelity,
                                   metrics={"combined_score": 0.0,
                                            "fitness_weights": {"combined_score": 1.0}})

        res = asyncio.run(evolve(
            method=NicheMenu(num_islands=1, function_weight=1.0, llm_weight=0.0),
            variator=SandboxVariator(evaluator_code="x", model="m"),
            evaluators={"code": SeedOnly()},
            seeds=[CodeGenome(files={"main.py": "SCORE = 0.0\n"})],
            budget=Budget(max_items=2), concurrency=1,
        ))
        # the seed still needs a local measurement; children must not
        assert res.failures == 0
        child = [i for i in res.store if i.parent_ids][0]
        assert child.metrics()["combined_score"] == 0.9
        assert child.meta["sandbox"] == "sb-123"
        assert child.meta["cost"] == 0.07
