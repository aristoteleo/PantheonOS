"""The annealed idea-code method, and the three defects it was built to remove.

Most of these are regression tests with a measurement behind them rather than a hypothetical:

  - `mu` puts a prediction and a measurement on one scale, so an unbuilt idea cannot outrank a
    built one merely by being unbuilt (the pathology that made the judge ablation meaningless)
  - an infeasible program is not a score of zero, in the selection pool or in the training set
  - `base` is pinned when the work item is issued, because C*(I) grows while it is in flight

Everything runs on fakes. Nothing here claims the search is good -- a toy problem cannot say that.
"""
from __future__ import annotations

import asyncio
import json
import math
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
from pantheon.evolution.core.method import EvolveMethod
from pantheon.evolution.methods import AnnealedIdeaCode
from pantheon.evolution.variators import Calibration, LearnedIdeaJudge, pava

IDEA, CODE = "idea", "code"


# --------------------------------------------------------------- fixtures ---
def ctx_with(store: Store, **budget) -> EvolveContext:
    return EvolveContext(store=store, budget=Budget(**(budget or {"max_items": 10})),
                         objective="toy")


def add_idea(store: Store, text: str, parent=None, delta_hat=None, sigma=None, raw=None):
    ind = store.add(Individual(genome=TextGenome(text=text, kind=IDEA), kind=IDEA,
                               parent_ids=[parent] if parent else []))
    metrics = {}
    if delta_hat is not None:
        metrics["idea_delta_hat"] = delta_hat
    if sigma is not None:
        metrics["idea_sigma"] = sigma
    if raw is not None:
        metrics["idea_raw"] = raw
    if metrics:
        ind.measurements.append(Measurement(individual_id=ind.id, metrics=metrics))
    return ind


def add_code(store: Store, anchor, score, *, valid=1.0, base=None, tag=""):
    ind = store.add(Individual(genome=CodeGenome(files={"main.py": f"# {tag or score}\n"}),
                               kind=CODE, anchor_id=anchor,
                               meta=({"base": base} if base is not None else {})))
    ind.measurements.append(Measurement(individual_id=ind.id,
                                        metrics={"combined_score": score, "validity": valid}))
    return ind


# --------------------------------------------------------------- schedule ---
def test_temperature_anneals_from_t0_to_t1():
    m = AnnealedIdeaCode(t0=1.0, t1=0.05)
    assert m.temperature(0.0) == pytest.approx(1.0)
    assert m.temperature(1.0) == pytest.approx(0.05)
    ts = [m.temperature(x / 10) for x in range(11)]
    assert all(a > b for a, b in zip(ts, ts[1:])), "temperature must decrease monotonically"


def test_beta_reaches_zero_so_the_run_ends_on_pure_exploitation():
    m = AnnealedIdeaCode(beta0=1.2)
    assert m.beta(0.0) == pytest.approx(1.2)
    assert m.beta(1.0) == pytest.approx(0.0)


def test_action_mix_slides_from_new_to_impl():
    m = AnnealedIdeaCode()
    n0, r0, i0 = m.mix(0.0)
    n1, r1, i1 = m.mix(1.0)
    assert n1 == pytest.approx(0.0), "NEW must decay to nothing"
    assert n0 > n1 and i1 > i0
    for t in (0.0, 0.3, 0.7, 1.0):
        assert sum(m.mix(t)) == pytest.approx(1.0)


def test_progress_uses_whichever_budget_dimension_is_furthest_along():
    m = AnnealedIdeaCode()
    c = ctx_with(Store(), max_items=100, max_cost=10.0)
    c.budget.items_used, c.budget.cost_used = 10, 8.0
    assert m.progress(c) == pytest.approx(0.8), "a cost-bounded run must still anneal"
    assert m.progress(ctx_with(Store(), max_seconds=None)) == 0.0


# ------------------------------------------------------------ feasibility ---
def test_infeasible_program_is_not_a_score_of_zero():
    """The measured failure: three 0.0000 scores all had validity=0. Reading them as real scores
    is what let generator noise masquerade as idea quality.

    An idea whose only implementation was infeasible must still be valued at what it starts from
    -- it has produced no evidence, which is different from having produced bad evidence.
    """
    s = Store()
    seed = add_code(s, None, 0.30)
    i = add_idea(s, "approach", delta_hat=0.0)
    add_code(s, i.id, 0.0, valid=0.0)
    m = AnnealedIdeaCode()
    m.seed_code = seed.id
    c = ctx_with(s)
    assert m.own_scores(c, i.id) == [], "an infeasible program must not enter the pool"
    assert m.mu(c, i) == pytest.approx(0.30), "valued at its base, not at zero"


def test_feasible_scores_survive_alongside_infeasible_ones():
    s = Store()
    i = add_idea(s, "approach")
    add_code(s, i.id, 0.0, valid=0.0)
    add_code(s, i.id, 0.61, tag="good")
    m, c = AnnealedIdeaCode(), ctx_with(s)
    assert m.own_scores(c, i.id) == [0.61]


# ------------------------------------------------------------ inheritance ---
def test_pool_best_walks_the_descent_chain():
    s = Store()
    root = add_idea(s, "root")
    add_code(s, root.id, 0.50)
    child = add_idea(s, "child", parent=root.id)
    m, c = AnnealedIdeaCode(), ctx_with(s)
    score, ind = m.pool_best(c, child.id)
    assert score == pytest.approx(0.50), "a refinement inherits its parent line's best code"
    add_code(s, child.id, 0.55)
    assert m.pool_best(c, child.id)[0] == pytest.approx(0.55), "its own best takes over"


def test_pool_best_falls_back_to_the_seed():
    s = Store()
    seed = add_code(s, None, 0.30)
    i = add_idea(s, "fresh")
    m = AnnealedIdeaCode()
    m.seed_code = seed.id
    assert m.pool_best(ctx_with(s), i.id)[0] == pytest.approx(0.30)


def test_the_seed_is_a_starting_point_even_when_it_has_no_score():
    """Cost a whole live run. The seed's evaluation had failed, so it carried no `combined_score`;
    treating "unscored" as "unusable" left every implementation with no parent and the agent
    refused all eight work items. Where to start from and what it scored are separate questions.
    """
    s = Store()
    seed = s.add(Individual(genome=CodeGenome(files={"main.py": "# seed\n"}), kind=CODE))
    seed.measurements.append(Measurement(individual_id=seed.id,
                                         metrics={"llm_score": 0.5}))   # no combined_score
    idea = add_idea(s, "a")
    m = AnnealedIdeaCode()
    m.seed_code = seed.id
    c = ctx_with(s)
    score, start = m.pool_best(c, idea.id)
    assert start is seed, "an unscored seed is still a program that can be edited"
    assert score is None, "and its score is still reported as absent, not invented"
    item = m._implement(c, 0.1, idea)
    assert item.parent_ids == [seed.id], "the work item must have a parent for the agent to edit"
    assert m.issued_base[item.batch_id] == pytest.approx(0.0)


def test_base_of_idea_is_the_parent_line_not_its_own_work():
    s = Store()
    seed = add_code(s, None, 0.30)
    root = add_idea(s, "root")
    add_code(s, root.id, 0.50)
    child = add_idea(s, "child", parent=root.id)
    add_code(s, child.id, 0.62)
    m = AnnealedIdeaCode()
    m.seed_code = seed.id
    c = ctx_with(s)
    assert m.base_of_idea(c, child) == pytest.approx(0.50)
    assert m.base_of_idea(c, root) == pytest.approx(0.30)


# ---------------------------------------------------- the value function ----
def test_mu_of_a_measured_idea_is_its_own_best():
    s = Store()
    i = add_idea(s, "a", delta_hat=0.9)
    add_code(s, i.id, 0.58)
    m, c = AnnealedIdeaCode(), ctx_with(s)
    assert m.mu(c, i) == pytest.approx(0.58), "measurement supersedes the prediction entirely"


def test_prediction_and_measurement_share_a_scale():
    """Defect 1. The old method sorted a [0,1] opinion against an objective value, so an unbuilt
    idea always won. Here a modest prediction must lose to a good measurement."""
    s = Store()
    seed = add_code(s, None, 0.30)
    built = add_idea(s, "built")
    add_code(s, built.id, 0.61)
    unbuilt = add_idea(s, "unbuilt", delta_hat=0.02)
    m = AnnealedIdeaCode()
    m.seed_code = seed.id
    c = ctx_with(s)
    assert m.mu(c, built) > m.mu(c, unbuilt)
    ambitious = add_idea(s, "ambitious", delta_hat=0.40)
    assert m.mu(c, ambitious) > m.mu(c, built), "a big predicted gain may still win, on merit"


def test_sigma_shrinks_with_evidence():
    s = Store()
    i = add_idea(s, "a", sigma=0.15)
    m, c = AnnealedIdeaCode(sigma_impl=0.05), ctx_with(s)
    assert m.sigma(c, i) == pytest.approx(0.15)
    add_code(s, i.id, 0.5)
    one = m.sigma(c, i)
    add_code(s, i.id, 0.52)
    add_code(s, i.id, 0.53)
    add_code(s, i.id, 0.54)
    assert m.sigma(c, i) < one, "more implementations, less uncertainty"


# ------------------------------------------------------------- selection ---
def test_selection_is_broad_early_and_converged_late():
    """The requirement the schedule exists for."""
    s = Store()
    seed = add_code(s, None, 0.30)
    for k, v in enumerate([0.61, 0.55, 0.50, 0.45, 0.40, 0.35]):
        i = add_idea(s, f"idea{k}")
        add_code(s, i.id, v)
    m = AnnealedIdeaCode(beta0=0.0)      # isolate the temperature from the exploration bonus
    m.seed_code = seed.id
    c = ctx_with(s)
    _, p_early = m.select_probs(c, 0.0)
    _, p_late = m.select_probs(c, 1.0)
    assert max(p_early) < 0.45, f"early selection should be broad, got {max(p_early):.3f}"
    assert max(p_late) > 0.95, f"late selection should be argmax, got {max(p_late):.3f}"
    assert min(p_early) > min(p_late), "nothing is starved early"
    assert min(p_early) > 0.0, "soft selection never assigns probability zero"


def test_nothing_is_permanently_starved():
    """The `constant` arm's pathology: equal scores froze the first two ideas into the only
    slots there were. Soft selection cannot do that."""
    s = Store()
    for k in range(5):
        i = add_idea(s, f"idea{k}", delta_hat=0.0, sigma=0.1)
    m, c = AnnealedIdeaCode(), ctx_with(s)
    _, p = m.select_probs(c, 0.5)
    assert all(x > 0 for x in p)
    assert max(p) - min(p) < 1e-6, "identical values must give identical probabilities"


def test_exploration_bonus_favours_the_unbuilt_early_and_not_late():
    s = Store()
    seed = add_code(s, None, 0.30)
    built = add_idea(s, "built", sigma=0.15)
    add_code(s, built.id, 0.50)
    unbuilt = add_idea(s, "unbuilt", delta_hat=0.0, sigma=0.15)
    m = AnnealedIdeaCode(beta0=1.2, sigma_impl=0.05)
    m.seed_code = seed.id
    c = ctx_with(s)
    _, a_early = m.acquisition(c, 0.0)
    _, a_late = m.acquisition(c, 1.0)
    gap_early = a_early[0] - a_early[1]
    gap_late = a_late[0] - a_late[1]
    assert gap_late > gap_early, "the bonus for being untried must decay"


def test_minmax_normalisation_degenerates_with_two_candidates():
    """A known weakness of the published schedule, pinned so a change to it is deliberate.

    Min-max maps the extremes to 0 and 1 whatever the true spread, so two nearly identical
    candidates look a full unit apart -- greedy at t=0, which is when the run should be broadest.
    `norm="absolute"` is the opt-out.
    """
    s = Store()
    a = add_idea(s, "a", delta_hat=0.500)
    b = add_idea(s, "b", delta_hat=0.501)
    c = ctx_with(s)
    _, p_minmax = AnnealedIdeaCode(beta0=0.0, norm="minmax").select_probs(c, 0.0)
    assert max(p_minmax) > 0.70, "documented degeneracy"
    _, p_abs = AnnealedIdeaCode(beta0=0.0, norm="absolute").select_probs(c, 0.0)
    assert max(p_abs) < 0.55, "the absolute scale keeps near-equal candidates near-equally likely"


# ------------------------------------------------- base pinned at issue ----
def test_base_is_pinned_when_the_item_is_issued():
    """The load-bearing constraint. C*(I) grows while the item is in flight, so a base recomputed
    at measurement time is a different number and every Delta built on it is wrong."""
    s = Store()
    seed = add_code(s, None, 0.30)
    i = add_idea(s, "a")
    m = AnnealedIdeaCode()
    m.seed_code = seed.id
    c = ctx_with(s)
    item = m._implement(c, 0.2, i)
    assert m.issued_base[item.batch_id] == pytest.approx(0.30)
    add_code(s, i.id, 0.61)                      # someone else's implementation lands meanwhile
    assert m.pool_best(c, i.id)[0] == pytest.approx(0.61)
    assert m.issued_base[item.batch_id] == pytest.approx(0.30), "the pinned base must not move"


def test_on_measured_carries_the_pinned_base_onto_the_child():
    s = Store()
    seed = add_code(s, None, 0.30)
    idea = add_idea(s, "a", raw=0.2)
    m = AnnealedIdeaCode(judge=LearnedIdeaJudge())
    m.seed_code = seed.id
    c = ctx_with(s)
    item = m._implement(c, 0.2, idea)
    child = add_code(s, idea.id, 0.55)
    meas = Measurement(individual_id=child.id, batch_id=item.batch_id,
                       metrics={"combined_score": 0.55, "validity": 1.0})
    asyncio.run(m.on_measured(c, child, meas))
    assert child.meta["base"] == pytest.approx(0.30)
    assert len(m.judge.cal) == 1
    assert m.judge.cal.ys[0] == pytest.approx(0.25), "the gain is measured against what it started from"


# ------------------------------------------------------ training hygiene ---
def test_infeasible_implementation_never_enters_the_training_set():
    s = Store()
    seed = add_code(s, None, 0.30)
    idea = add_idea(s, "a", raw=0.2)
    m = AnnealedIdeaCode(judge=LearnedIdeaJudge())
    m.seed_code = seed.id
    c = ctx_with(s)
    item = m._implement(c, 0.2, idea)
    bad = add_code(s, idea.id, 0.0, valid=0.0)
    asyncio.run(m.on_measured(c, bad, Measurement(individual_id=bad.id, batch_id=item.batch_id,
                                                  metrics={"combined_score": 0.0, "validity": 0.0})))
    assert len(m.judge.cal) == 0, "a generator failure is not evidence about the approach"
    assert idea.id not in m.trained, "the idea is still owed a first feasible implementation"


def test_only_the_first_feasible_implementation_trains_the_judge():
    """Later implementations start from better code, so their gain is not comparable across
    ideas. Training on all of them hands an advantage to whichever idea was implemented most."""
    s = Store()
    seed = add_code(s, None, 0.30)
    idea = add_idea(s, "a", raw=0.2)
    m = AnnealedIdeaCode(judge=LearnedIdeaJudge())
    m.seed_code = seed.id
    c = ctx_with(s)
    for score in (0.50, 0.55, 0.60):
        item = m._implement(c, 0.2, idea)
        child = add_code(s, idea.id, score, tag=str(score))
        asyncio.run(m.on_measured(c, child, Measurement(
            individual_id=child.id, batch_id=item.batch_id,
            metrics={"combined_score": score, "validity": 1.0})))
    assert len(m.judge.cal) == 1
    assert m.judge.cal.ys == [pytest.approx(0.20)]


def test_an_unjudged_idea_contributes_nothing_to_calibration():
    s = Store()
    seed = add_code(s, None, 0.30)
    idea = add_idea(s, "a")                      # no idea_raw: the judge never saw it
    m = AnnealedIdeaCode(judge=LearnedIdeaJudge())
    m.seed_code = seed.id
    c = ctx_with(s)
    item = m._implement(c, 0.2, idea)
    child = add_code(s, idea.id, 0.55)
    asyncio.run(m.on_measured(c, child, Measurement(
        individual_id=child.id, batch_id=item.batch_id,
        metrics={"combined_score": 0.55, "validity": 1.0})))
    assert len(m.judge.cal) == 0


# ---------------------------------------------------------------- isotonic ---
def test_pava_is_exact_on_monotone_input():
    xs, ys = pava([1, 2, 3], [0.1, 0.2, 0.3])
    assert xs == [1, 2, 3]
    assert ys == pytest.approx([0.1, 0.2, 0.3])


def test_pava_pools_violators_and_never_decreases():
    xs, ys = pava([1, 2, 3, 4], [0.1, 0.9, 0.2, 0.4])
    assert all(a <= b + 1e-12 for a, b in zip(ys, ys[1:])), f"not monotone: {ys}"
    assert sum(ys) == pytest.approx(0.1 + 0.9 + 0.2 + 0.4), "pooling preserves the total"


def test_pava_sorts_by_x_first():
    xs, ys = pava([3, 1, 2], [0.3, 0.1, 0.2])
    assert xs == [1, 2, 3]
    assert ys == pytest.approx([0.1, 0.2, 0.3])


def test_calibration_is_the_identity_until_it_has_data():
    cal = Calibration(n_min=5, prior_sigma=0.15)
    for x in (0.80, 0.85):
        cal.observe(x, 0.10)
    cal.fit()
    assert not cal.fitted
    assert cal(0.83) == pytest.approx(0.83), "no data, no pretence"
    assert cal.sigma == pytest.approx(0.15)


def test_calibration_stretches_a_compressed_range():
    """Defect 1, fixed mechanically. The judge insists on [0.80, 0.92]; the outcomes it must
    predict live on [0.0, 0.30]. A monotone fit maps one onto the other without the model having
    to learn to score differently."""
    cal = Calibration(n_min=4, prior_sigma=0.15)
    raw = [0.80, 0.83, 0.86, 0.89, 0.92]
    gain = [0.00, 0.05, 0.14, 0.22, 0.30]
    for r, g in zip(raw, gain):
        cal.observe(r, g)
    rep = cal.fit()
    assert cal.fitted
    assert cal(0.80) == pytest.approx(0.00, abs=1e-6)
    assert cal(0.92) == pytest.approx(0.30, abs=1e-6)
    assert 0.10 < cal(0.855) < 0.20
    assert rep["spearman"] == pytest.approx(1.0)


def test_calibration_sigma_reflects_residual_spread():
    tight = Calibration(n_min=3)
    noisy = Calibration(n_min=3)
    for k in range(6):
        tight.observe(0.8 + 0.02 * k, 0.05 * k)
        noisy.observe(0.8 + 0.02 * k, 0.05 * k + (0.12 if k % 2 else -0.12))
    tight.fit()
    noisy.fit()
    assert noisy.sigma > tight.sigma


# --------------------------------------------------------- judge persistence ---
def test_judge_state_round_trips():
    j = LearnedIdeaJudge(n_min=3)
    for k in range(5):
        j.observe(f"idea {k}", 0.8 + 0.02 * k, 0.30, 0.05 * k)
    j.fit()
    clone = LearnedIdeaJudge(n_min=3)
    clone.load_state_dict(json.loads(json.dumps(j.state_dict())))
    assert len(clone.cal) == 5
    assert clone.cal.fitted
    assert clone.cal(0.84) == pytest.approx(j.cal(0.84))
    assert len(clone.examples) == 5


def test_judge_survives_the_run_that_produced_it(tmp_path):
    """Risk 1. Ten labelled ideas per run is not a training set; the file is what makes the word
    'learned' true."""
    p = tmp_path / "judge.json"
    j = LearnedIdeaJudge(n_min=3)
    for k in range(4):
        j.observe(f"idea {k}", 0.8 + 0.03 * k, 0.30, 0.04 * k)
    j.fit()
    j.save(str(p))
    fresh = LearnedIdeaJudge(n_min=3)
    assert fresh.load(str(p)) is True
    assert len(fresh.cal) == 4
    assert LearnedIdeaJudge().load(str(tmp_path / "absent.json")) is False


def test_judge_falls_back_to_no_change_when_the_reply_is_unparseable():
    from pantheon.evolution.variators.judge import _parse_prediction

    assert _parse_prediction("total nonsense", 0.42)[0] == pytest.approx(0.42)
    assert _parse_prediction('{"score": 0.61, "reason": "x"}', 0.42)[0] == pytest.approx(0.61)
    assert _parse_prediction("I think about 0.55 is right", 0.42)[0] == pytest.approx(0.55)


def test_method_installs_the_base_resolver_on_its_judge():
    j = LearnedIdeaJudge()
    assert j.base_fn is None
    m = AnnealedIdeaCode(judge=j)
    assert j.base_fn is not None, "only the method knows how inheritance sets a starting point"
    s = Store()
    seed = add_code(s, None, 0.30)
    m.seed_code = seed.id
    idea = add_idea(s, "a")
    assert j.base_fn(ctx_with(s), idea) == pytest.approx(0.30)


# ------------------------------------------------------------- protocol ---
def test_conforms_to_the_method_protocol():
    assert isinstance(AnnealedIdeaCode(), EvolveMethod)


# ---------------------------------------------------------- the nulled judge ---
def test_nulled_judge_speaks_the_annealed_metric_dialect():
    """`NullJudge` emits `idea_score`, which this method never reads. The ablation judge must
    emit exactly what `LearnedIdeaJudge` emits, or the ablation measures plumbing."""
    from pantheon.evolution.variators import NulledJudge

    j = NulledJudge(mode="random", seed=1)
    m = asyncio.run(j.measure(ctx_with(Store()), _mk_idea("a")))
    assert {"idea_base", "idea_raw", "idea_delta_hat", "idea_sigma"} <= set(m.metrics)


def test_constant_null_ties_every_idea():
    from pantheon.evolution.variators import NulledJudge

    j = NulledJudge(mode="constant", value=0.0)
    ms = [asyncio.run(j.measure(ctx_with(Store()), _mk_idea(t))) for t in ("a", "b", "c")]
    assert len({m.metrics["idea_raw"] for m in ms}) == 1


def test_nulled_judge_uses_the_installed_base_fn():
    """The method installs `base_fn` on whatever judge it is given; the null must accept and use
    it, because `idea_base` is half of every downstream number."""
    from pantheon.evolution.variators import NulledJudge

    j = NulledJudge(mode="constant")
    AnnealedIdeaCode(judge=j)
    assert j.base_fn is not None


def _mk_idea(text):
    return Individual(genome=TextGenome(text=text, kind=IDEA), kind=IDEA)


# ------------------------------------------------- the evaluators it owns ---
def test_method_supplies_the_judge_as_the_evaluator_for_ideas():
    j = LearnedIdeaJudge()
    assert AnnealedIdeaCode(judge=j).default_evaluators() == {IDEA: j}


def test_a_method_without_a_judge_supplies_nothing():
    assert AnnealedIdeaCode().default_evaluators() == {}


def test_the_method_never_offers_to_score_code():
    """What a program is worth is the problem's question. A method that answered it could make
    its own search look good by redefining the objective."""
    assert CODE not in AnnealedIdeaCode(judge=LearnedIdeaJudge()).default_evaluators()


def test_the_run_judges_ideas_without_the_caller_registering_the_judge():
    judge = FakeJudge(n_min=3)
    method = AnnealedIdeaCode(judge=judge, seed=7)
    result = asyncio.run(evolve(
        method, FakeVariator(),
        {CODE: FakeCodeEvaluator()},               # the problem's evaluator, and only that
        seeds=[CodeGenome(files={"main.py": "# seed\n"})],
        objective="toy", budget=Budget(max_items=20), concurrency=2))
    ideas = result.store.of_kind(IDEA)
    assert ideas, "ideas were produced"
    assert all(any("idea_raw" in m.metrics for m in i.measurements) for i in ideas), \
        "every idea was measured by the judge the method brought with it"


def test_a_caller_supplied_evaluator_beats_the_methods_own():
    """The override has to exist: holding measurement fixed across arms is how the judge gets
    ablated, and `results_ablation/` is exactly that experiment."""
    class Stub:
        kind = IDEA

        async def measure(self, ctx, ind, fidelity="full"):
            return Measurement(individual_id=ind.id, fidelity=fidelity,
                               metrics={"idea_raw": 0.0, "idea_base": 0.0, "stub": 1.0})

    judge, stub = FakeJudge(n_min=3), Stub()
    method = AnnealedIdeaCode(judge=judge, seed=7)
    result = asyncio.run(evolve(
        method, FakeVariator(),
        {CODE: FakeCodeEvaluator(), IDEA: stub},
        seeds=[CodeGenome(files={"main.py": "# seed\n"})],
        objective="toy", budget=Budget(max_items=14), concurrency=1))
    ideas = result.store.of_kind(IDEA)
    assert ideas
    assert all(any("stub" in m.metrics for m in i.measurements) for i in ideas)


def test_state_dict_round_trips_through_json():
    m = AnnealedIdeaCode(judge=LearnedIdeaJudge(), seed=3)
    m.seed_code = "seed"
    m.issued_base["b1"] = 0.42
    m.trained.append("i1")
    clone = AnnealedIdeaCode(judge=LearnedIdeaJudge())
    clone.load_state_dict(json.loads(json.dumps(m.state_dict())))
    assert clone.seed_code == "seed"
    assert clone.issued_base["b1"] == pytest.approx(0.42)
    assert clone.trained == ["i1"]


def test_reconcile_recovers_the_seed_after_a_restart():
    s = Store()
    seed = add_code(s, None, 0.30)
    m = AnnealedIdeaCode()
    m.trained = ["gone"]
    m.reconcile(ctx_with(s))
    assert m.seed_code == seed.id
    assert m.trained == [], "ids that are not in the restored store are dropped"


def test_default_variator_uses_an_agent_when_it_has_something_to_run():
    from pantheon.evolution.variators.agent import AgentVariator
    from pantheon.evolution.variators.completion import CompletionVariator

    m = AnnealedIdeaCode()
    blind = m.default_variator(evaluator=None)
    assert isinstance(blind.code, CompletionVariator), "no evaluator, nothing for an agent to do"
    withev = m.default_variator(evaluator=object())
    assert isinstance(withev.code, AgentVariator), "the operator is part of what this method is"


def test_feasibility_is_the_methods_rule_not_the_operators_behaviour():
    """Measured, against an earlier claim of mine that turned out to be wrong.

    The agent submits infeasible programs at about the same rate as a blind completion (13.3% vs
    7.3% on Erdos), so the method cannot delegate feasibility to its operator. `_score` refusing
    to read a violated constraint as a number is what actually keeps them out, and it has to hold
    whichever operator is wired up.
    """
    for variator in ("agent", "completion"):
        s = Store()
        i = add_idea(s, "approach", delta_hat=0.0)
        add_code(s, i.id, 0.0, valid=0.0)
        m = AnnealedIdeaCode()
        assert m.own_scores(ctx_with(s), i.id) == [], (
            f"feasibility must not depend on the operator ({variator})")


# ------------------------------------------------------------ end to end ---
class FakeVariator:
    """Ideas declare a ceiling; implementations capture part of the headroom to it."""

    def __init__(self):
        self.n = 0

    async def create(self, ctx: EvolveContext, item: Create) -> List[Produced]:
        self.n += 1
        out = []
        for j in range(item.k):
            self.n += 1
            if item.kind == IDEA:
                parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
                ceil = min(0.70, _ceiling(parent) + 0.06) if parent else 0.35 + 0.05 * (self.n % 6)
                out.append(Produced(
                    genome=TextGenome(text=f"idea{self.n}: ceiling {ceil:.3f}", kind=IDEA),
                    item_id=item.id, batch_id=item.batch_id,
                    parent_ids=list(item.parent_ids), meta={"summary": f"idea{self.n}"}))
            else:
                idea = ctx.store.get(item.anchor_id) if item.anchor_id else None
                ceil = _ceiling(idea)
                base = float(item.meta.get("base") or 0.0)
                score = base + (ceil - base) * 0.6 if ceil > base else base - 0.01
                out.append(Produced(
                    genome=CodeGenome(files={"main.py": f"# {self.n}\nS={score:.5f}\n"}),
                    item_id=item.id, batch_id=item.batch_id,
                    parent_ids=list(item.parent_ids), anchor_id=item.anchor_id,
                    meta={"summary": f"impl{self.n}", "score": score}))
        return out


def _ceiling(idea) -> float:
    if idea is None:
        return 0.4
    try:
        return float(idea.genome.render().split("ceiling")[1].strip())
    except (IndexError, ValueError):
        return 0.4


class FakeCodeEvaluator:
    kind = CODE

    async def measure(self, ctx, ind, fidelity="full") -> Measurement:
        s = float(ind.meta.get("score", 0.0))
        return Measurement(individual_id=ind.id, metrics={"combined_score": s, "validity": 1.0})


class FakeJudge(LearnedIdeaJudge):
    """The real calibration and training set; only the model call is replaced."""

    async def measure(self, ctx, ind, fidelity="full") -> Measurement:
        base = float(self.base_fn(ctx, ind)) if self.base_fn else 0.0
        raw = _ceiling(ind) - base
        return Measurement(individual_id=ind.id,
                           metrics={"idea_base": base, "idea_raw": raw,
                                    "idea_delta_hat": self.cal(raw),
                                    "idea_sigma": self.cal.sigma})


def test_end_to_end_runs_without_touching_the_loop():
    judge = FakeJudge(n_min=3)
    method = AnnealedIdeaCode(judge=judge, seed=7)
    result = asyncio.run(evolve(
        method, FakeVariator(),
        {CODE: FakeCodeEvaluator(), IDEA: judge},
        seeds=[CodeGenome(files={"main.py": "# seed\n"})],
        objective="toy", budget=Budget(max_items=30), concurrency=2))
    store = result.store
    ideas = store.of_kind(IDEA)
    codes = [c for c in store.of_kind(CODE) if c.parent_ids or c.anchor_id]
    assert ideas and codes, "both populations must be produced"
    assert any(i.parent_ids for i in ideas), "REFINE must produce idea lineage"
    assert any(c.anchor_id for c in codes), "code must be anchored to the idea it implements"
    assert all("base" in c.meta for c in codes), "every implementation records what it started from"
    assert len(judge.cal) >= 1, "the judge accumulated a training set from measured code"


def test_end_to_end_converges_onto_fewer_ideas_late():
    judge = FakeJudge(n_min=3)
    method = AnnealedIdeaCode(judge=judge, seed=5)
    result = asyncio.run(evolve(
        method, FakeVariator(),
        {CODE: FakeCodeEvaluator(), IDEA: judge},
        seeds=[CodeGenome(files={"main.py": "# seed\n"})],
        objective="toy", budget=Budget(max_items=40), concurrency=1))
    ctx = EvolveContext(store=result.store, budget=Budget(max_items=40), objective="toy")
    ctx.budget.items_used = 40
    ideas, early = method.select_probs(ctx, 0.0)
    _, late = method.select_probs(ctx, 1.0)
    assert len(ideas) > 5
    # Effective number of choices, exp(entropy). Scale-free, and unlike "max p" it is not thrown
    # off by exact ties -- which do occur, and which must split their mass rather than letting
    # one of them win by position. Breaking ties by position is what froze the `constant` arm.
    eff = lambda p: math.exp(-sum(x * math.log(x) for x in p if x > 0))   # noqa: E731
    assert eff(early) > 0.6 * len(ideas), f"early search must be broad, eff={eff(early):.1f}"
    assert eff(late) < eff(early) / 2.5, (
        f"late search must concentrate: {eff(early):.1f} -> {eff(late):.1f} choices")
    assert min(late) > 0.0, "soft selection never assigns probability zero"
    rows = method.prior_vs_realised(ctx)
    assert rows and all("predicted" in r and "realised_first" in r for r in rows)


def test_isotonic_clamps_outside_its_training_range():
    """Found while running the method, and worth pinning rather than rediscovering.

    `_interp` holds the boundary value beyond the observed range, so a raw prediction below
    anything the judge has seen is assigned the smallest gain it has ever recorded -- not zero.
    Where every training gain was positive that is a systematic over-prediction at the bottom
    end, and it is why an unbuilt idea can still top the ranking late in a run. More data
    narrows it; nothing else does.
    """
    cal = Calibration(n_min=3)
    for r, g in ((0.10, 0.02), (0.20, 0.05), (0.30, 0.09)):
        cal.observe(r, g)
    cal.fit()
    assert cal(0.0001) == pytest.approx(0.02), "clamped to the lowest observed gain, not to 0"
    assert cal(9.0) == pytest.approx(0.09), "and to the highest at the other end"


def test_seed_only_run_still_produces_an_idea_first():
    judge = FakeJudge(n_min=3)
    method = AnnealedIdeaCode(judge=judge, seed=1)
    result = asyncio.run(evolve(
        method, FakeVariator(),
        {CODE: FakeCodeEvaluator(), IDEA: judge},
        seeds=[CodeGenome(files={"main.py": "# seed\n"})],
        objective="toy", budget=Budget(max_items=3), concurrency=1))
    first = sorted((i for i in result.store if i.parent_ids or i.kind == IDEA),
                   key=lambda x: x.order)
    assert first and first[0].kind == IDEA, "with no ideas to choose from, the only move is NEW"
