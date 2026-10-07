"""FidelityController (methods/fidelity.py) and LabNotebook's adaptive cascade, on fakes.

Claims: the calibration recovers a known cheap->full relation; the screen width grows with the cost
ratio and the rank agreement and screening stops when the cheap reading stops ranking like the full
one; promotions always include the best cheap reading, add candidates whose optimistic prediction
reaches the incumbent, and explore at the configured rate; inside LabNotebook only full readings
reach the chains, and a misleading cheap reading turns the cascade off.
"""
from __future__ import annotations

import asyncio
import random
from typing import List

import pytest

from pantheon.evolution.core import Budget, CodeGenome, EvolveContext, Individual, Measurement, Produced
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods import LabNotebook
from pantheon.evolution.methods.fidelity import FidelityController
from pantheon.evolution.methods.lab_notebook import CODE

from test_evolution_lab_notebook import FakeLLM, FakeVariator, _ctx


def feed(ctl: FidelityController, n: int, noise: float, sign: float = 1.0, seed: int = 0):
    rng = random.Random(seed)
    for _ in range(n):
        full = rng.uniform(0.2, 0.4)
        ctl.observe_pair(sign * full + rng.gauss(0, noise), full)


class TestController:
    def test_calibration_recovers_the_line(self):
        ctl = FidelityController()
        rng = random.Random(1)
        for _ in range(200):
            x = rng.uniform(0, 1)
            ctl.observe_pair(x, 0.1 + 0.5 * x + rng.gauss(0, 0.01))
        cal = ctl.calibration()
        assert cal["a"] == pytest.approx(0.1, abs=0.01) and cal["b"] == pytest.approx(0.5, abs=0.02)
        assert cal["sigma"] == pytest.approx(0.01, abs=0.003) and cal["rho"] > 0.98

    def test_warm_up_is_the_conservative_fixed_cascade(self):
        ctl = FidelityController(warm_k=4, warm_promote=2, min_pairs=8)
        assert ctl.warm() and ctl.k_screen() == 4
        d = ctl.promote([("a", 0.3), ("b", 0.2), ("c", 0.1)], incumbent=0.5)
        assert d["rule"] == "warm-up" and d["promote"][:2] == ["a", "b"]

    def test_screen_width_follows_cost_ratio_and_agreement(self):
        cheap_and_good = FidelityController(k_max=12)
        feed(cheap_and_good, 40, noise=0.005)
        for _ in range(10):
            cheap_and_good.observe_cost("low", 10.0)
            cheap_and_good.observe_cost("full", 60.0)
        pricey = FidelityController(k_max=12)
        feed(pricey, 40, noise=0.005)
        for _ in range(10):
            pricey.observe_cost("low", 40.0)
            pricey.observe_cost("full", 60.0)
        noisy = FidelityController(k_max=12)
        feed(noisy, 40, noise=0.04)
        for _ in range(10):
            noisy.observe_cost("low", 10.0)
            noisy.observe_cost("full", 60.0)
        k_good, k_pricey, k_noisy = cheap_and_good.k_screen(), pricey.k_screen(), noisy.k_screen()
        assert k_good > k_pricey and k_good > (k_noisy or 0)

    def test_screening_stops_when_the_cheap_reading_misleads(self):
        ctl = FidelityController(rho_off=0.5)
        feed(ctl, 30, noise=0.005, sign=-1.0)          # cheap ranks in the opposite order
        assert not ctl.screening() and ctl.k_screen() is None

    def test_promotion_rule(self):
        ctl = FidelityController(promote_max=3, z=0.0, explore=0.0)
        rng = random.Random(0)
        for _ in range(50):
            x = rng.uniform(0, 1)
            ctl.observe_pair(x, x)                     # full = cheap exactly
        d = ctl.promote([("a", 0.9), ("b", 0.8), ("c", 0.3)], incumbent=0.7)
        assert d["rule"] == "ucb" and d["promote"] == ["a", "b"], "best always, plus those that reach the incumbent"
        d = ctl.promote([("a", 0.5), ("b", 0.4)], incumbent=0.9)
        assert d["promote"] == ["a"], "the step still makes progress when nothing reaches the incumbent"

    def test_exploration_rate(self):
        ctl = FidelityController(explore=0.25, z=0.0, promote_max=1, seed=3)
        feed(ctl, 20, noise=0.001)
        n = sum(ctl.promote([("a", 0.9), ("b", 0.1), ("c", 0.0)], incumbent=1.0)["explored"] is not None
                for _ in range(2000))
        assert 0.2 < n / 2000 < 0.3

    def test_state_round_trip(self):
        ctl = FidelityController()
        feed(ctl, 12, noise=0.01)
        ctl.observe_cost("low", 5.0)
        ctl2 = FidelityController()
        ctl2.load_state_dict(ctl.state_dict())
        assert ctl2.calibration() == ctl.calibration() and ctl2.cost("low") == 5.0


class NoisyVariator(FakeVariator):
    """Candidates whose cheap reading is the full value plus noise (sign=-1: the reverse)."""

    def __init__(self, noise: float, sign: float = 1.0):
        super().__init__()
        self.noise, self.sign, self.rng = noise, sign, random.Random(7)

    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        self.instructions.append(item.context.instruction)
        parent = ctx.store.get(item.parent_ids[0])
        base = float(next(iter(parent.genome.files.values())).split("value=")[1].split()[0])
        out = []
        for _ in range(item.k):
            self.serial += 1
            full = base + self.rng.uniform(-0.01, 0.02)
            low = self.sign * full + self.rng.gauss(0, self.noise)
            out.append(Produced(
                genome=CodeGenome(files={"solution.py": f"# v{self.serial} value={full} low={low}\n"}),
                item_id=item.id, batch_id=item.batch_id,
                parent_ids=list(item.parent_ids), anchor_id=item.anchor_id))
        return out


class TimedEvaluator:
    kind = CODE

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        src = next(iter(ind.genome.files.values()), "")
        key = "low=" if fidelity != "full" and "low=" in src else "value="
        v = float(src.split(key)[1].split()[0]) if key in src else 0.0
        return Measurement(individual_id=ind.id, fidelity=fidelity, cost=0.01,
                           metrics={"combined_score": v, "validity": 1.0,
                                    "eval_secs": 10.0 if fidelity != "full" else 60.0})


def run_adaptive(var, budget: int = 80, **kw):
    m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=2, k_candidates=2, seed=1,
                    screen_fidelity="low", fidelity_policy="adaptive",
                    fidelity_opts={"min_pairs": 6, **kw})
    m._llm = FakeLLM()  # type: ignore[assignment]
    res = asyncio.run(evolve(
        method=m, variator=var, evaluators={CODE: TimedEvaluator()},
        seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
        objective="maximise value", budget=Budget(max_items=budget), concurrency=4))
    return m, res


class TestLabNotebookAdaptive:
    def test_trustworthy_cheap_reading_keeps_screening_and_only_full_counts(self):
        m, res = run_adaptive(NoisyVariator(noise=0.001))
        cal = m.fidelity_ctl.calibration()
        assert cal["n"] >= 6 and cal["rho"] > 0.7
        assert m.fidelity_ctl.cost_ratio() == pytest.approx(6.0)
        screens = [e for e in m.events if e["kind"] == "screen"]
        assert any(e["rule"] == "ucb" for e in screens), "the controller took over after warm-up"
        for t in m.trajectories:
            for node in t.chain:
                assert res.store.get(node).measured("full") is not None
        for ind_id in m.rank(_ctx(res), CODE).scores:
            assert res.store.get(ind_id).measured("full") is not None

    def test_misleading_cheap_reading_turns_the_cascade_off(self):
        m, res = run_adaptive(NoisyVariator(noise=0.001, sign=-1.0), budget=120)
        assert not m.fidelity_ctl.screening()
        kids = [i for i in res.store.of_kind(CODE) if i.parent_ids]
        plain = [k for k in kids if k.measured("full") and not k.measured("low")]
        assert plain, "after the cheap reading proved misleading, steps measured in full directly"

    def test_policy_validated(self):
        with pytest.raises(ValueError):
            LabNotebook(screen_fidelity="low", fidelity_policy="clever")


class TestDigestUnderCascade:
    def test_screened_candidates_are_not_reported_as_invalid(self):
        from test_evolution_lab_notebook import ScreenVariator, ScreenEvaluator

        class PromptLLM(FakeLLM):
            def __init__(self):
                super().__init__()
                self.prompts = []

            async def __call__(self, system, prompt):
                self.prompts.append((system, prompt))
                return await super().__call__(system, prompt)

        m = LabNotebook(ideas_per_cycle=2, steps_per_trajectory=2, k_candidates=2, seed=1,
                        screen_fidelity="low", k_screen=4, promote_top=1)
        rec = PromptLLM()
        m._llm = rec  # type: ignore[assignment]
        asyncio.run(evolve(
            method=m, variator=ScreenVariator(), evaluators={CODE: ScreenEvaluator()},
            seeds=[CodeGenome(files={"solution.py": "# seed value=1.0\n"})],
            objective="maximise value", budget=Budget(max_items=40), concurrency=4))
        digests = [p for sys_, p in rec.prompts if "writing up one experiment" in sys_]
        assert digests, "a cycle closed"
        assert all("score=invalid" not in d for d in digests)
        assert any("screened only, cheap reading" in d for d in digests)
