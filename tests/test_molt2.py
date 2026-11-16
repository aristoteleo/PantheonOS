"""Molt2's experimental claims require real, paired, usable measurements.

No models, networking, biological data or paid evaluations are used. The fixtures
exercise callback order, invalid/censored feedback, source contrast and restart.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math

import pytest

from pantheon.evolution.core import Budget, EvolveContext, Individual, Measurement, Store
from pantheon.evolution.core.genome import CodeGenome
from pantheon.evolution.core.work import Failure
from pantheon.evolution.methods.molt2 import Molt2, toggle_pair, usable_score


PY_TEMPLATE = """# MOLT2_HYPOTHESIS: alter the neighborhood representation
MOLT2_TREATMENT = 0

def solve(x):
    return x + (1 if MOLT2_TREATMENT else 0)
"""
CPP_TEMPLATE = """// MOLT2_HYPOTHESIS: alter the neighborhood representation
static constexpr bool MOLT2_TREATMENT = false;
int solve(int x) { return x + (MOLT2_TREATMENT ? 1 : 0); }
"""


def run(coro):
    return asyncio.run(coro)


def make(**overrides):
    store = Store()
    seed = store.add(Individual(
        genome=CodeGenome(files={"solution.py": "def solve(x):\n    return x\n"}),
        kind="code"))
    seed.measurements.append(Measurement(
        individual_id=seed.id,
        metrics={"combined_score": .6, "validity": 1.0}, fidelity="full"))
    options = dict(num_chains=2, k_candidates=2, num_inspirations=2,
                   selector="rpucg", seed=0, experiment_interval=1,
                   warmup=2, experiment_fraction=.6, control_tolerance=.02)
    options.update(overrides)
    method = Molt2(**options)
    ctx = EvolveContext(store=store, budget=Budget(max_items=100),
                        objective="TRAIN ONLY: fixed toy cases", concurrency=2)
    run(method.start(ctx, [seed]))
    return method, ctx, seed


def measured(ctx, item, files, score=.6, validity=1.0, *, artifacts=None,
             meta=None, fidelity="full", ok=True):
    ind = ctx.store.add(Individual(genome=CodeGenome(files=files), kind="code",
                                   parent_ids=list(item.parent_ids), meta=meta or {}))
    metrics = {"validity": validity}
    if score is not None:
        metrics["combined_score"] = score
    measurement = Measurement(individual_id=ind.id, metrics=metrics,
        artifacts=artifacts or {}, fidelity=fidelity, ok=ok,
        batch_id=item.batch_id, item_id=item.id, duration=1.25)
    ind.measurements.append(measurement)
    return ind, measurement


def resolve_base(method, ctx, items):
    for item in items:
        if item.meta.get("molt2_experiment"):
            continue
        for i in range(item.k):
            files = {"solution.py": f"# {item.batch_id}-{i}\ndef solve(x):\n    return x\n"}
            ind, measurement = measured(ctx, item, files)
            run(method.on_measured(ctx, ind, measurement))


def get_experiment(method, ctx):
    for _ in range(12):
        items = run(method.ask(ctx, 2))
        experiments = [item for item in items if item.meta.get("molt2_experiment")]
        resolve_base(method, ctx, items)
        if experiments:
            assert len(experiments) == 1
            return experiments[0]
    raise AssertionError("fixture did not dispatch an experiment")


def resolve_pair(method, ctx, item, control_score=.6, treatment_score=.7,
                 *, control_artifacts=None, treatment_validity=1.0):
    control, treatment, _ = toggle_pair({"solution.py": PY_TEMPLATE}, "solution.py")
    control_ind, control_m = measured(ctx, item, control, control_score,
        artifacts=control_artifacts)
    treatment_ind, treatment_m = measured(ctx, item, treatment, treatment_score,
        validity=treatment_validity)
    # Reverse arrival deliberately; source controls the arm, not callback order.
    run(method.on_measured(ctx, treatment_ind, treatment_m))
    run(method.on_measured(ctx, control_ind, control_m))
    return control_ind, control_m, treatment_ind, treatment_m


@pytest.mark.parametrize("path,template", [("solution.py", PY_TEMPLATE),
                                            ("solution.cpp", CPP_TEMPLATE)])
def test_toggle_is_one_source_contrast_and_preserves_input(path, template):
    files = {path: template, "helper.txt": "unchanged scaffold\n"}
    original = copy.deepcopy(files)
    control, treatment, pair_hash = toggle_pair(files, path)
    assert files == original
    assert control != treatment
    assert control["helper.txt"] == treatment["helper.txt"] == files["helper.txt"]
    assert toggle_pair(control, path) == (control, treatment, pair_hash)
    assert toggle_pair(treatment, path) == (control, treatment, pair_hash)
    if path.endswith(".py"):
        assert treatment[path] == control[path].replace("MOLT2_TREATMENT = 0", "MOLT2_TREATMENT = 1", 1)
    else:
        assert treatment[path] == control[path].replace("MOLT2_TREATMENT = false", "MOLT2_TREATMENT = true", 1)


@pytest.mark.parametrize("code", [
    "def solve(x):\n    return x\n",
    "MOLT2_TREATMENT = 0\ndef solve(x):\n    return x\n",
    "MOLT2_TREATMENT = 0\n# MOLT2_TREATMENT is not used\ndef solve(x):\n    return x\n",
    "MOLT2_TREATMENT = 0\ntext = 'MOLT2_TREATMENT'\ndef solve(x):\n    return x\n",
    "MOLT2_TREATMENT = 0\nMOLT2_TREATMENT = 1\ndef solve(x):\n    return MOLT2_TREATMENT\n",
    "def solve(x):\n    MOLT2_TREATMENT = 0\n    return x + MOLT2_TREATMENT\n",
])
def test_python_absent_unused_or_ambiguous_switch_is_rejected(code):
    with pytest.raises(ValueError):
        toggle_pair({"solution.py": code}, "solution.py")


@pytest.mark.parametrize("metrics,ok,fidelity", [
    ({"combined_score": .7, "validity": 0}, True, "full"),
    ({"function_score": 0, "llm_score": .5}, True, "full"),
    ({"combined_score": math.nan, "validity": 1}, True, "full"),
    ({"combined_score": math.inf, "validity": 1}, True, "full"),
    ({"combined_score": .7, "validity": math.nan}, True, "full"),
    ({"combined_score": .7, "validity": 1}, False, "full"),
    ({"combined_score": .7, "validity": 1}, True, "cheap"),
])
def test_invalid_or_censored_feedback_is_not_a_numeric_effect(metrics, ok, fidelity):
    m = Measurement(individual_id="x", metrics=metrics, ok=ok, fidelity=fidelity)
    assert usable_score(m) is None


def test_zero_is_a_real_score_if_explicitly_valid():
    m = Measurement(individual_id="x", metrics={"combined_score": 0., "validity": 1.}, fidelity="full")
    assert usable_score(m) == 0.


def test_reverse_arrival_records_matched_delta_and_closes_once():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    _, control_m, treatment, treatment_m = resolve_pair(method, ctx, item)
    assert len(method.evidence) == 1
    row = method.evidence[0]
    assert row["status"] == "supported_on_observed_pair"
    assert row["control_score"] == .6 and row["treatment_score"] == .7
    assert row["delta"] == pytest.approx(.1)
    assert row["anchor_reproduced"] and row["one_switch_source_contrast"]
    assert row["control_source_sha256"] != row["treatment_source_sha256"]
    assert "conditional" in row["scope"]
    assert len(method.chains) == 2
    assert any(treatment.id in chain for chain in method.chains)
    # A duplicate delivery after closure cannot invent another observation.
    run(method.on_measured(ctx, treatment, treatment_m))
    assert len(method.evidence) == 1


def test_same_source_store_dedup_does_not_depend_on_stale_metadata():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    control, treatment, _ = toggle_pair({"solution.py": PY_TEMPLATE}, "solution.py")
    old = ctx.store.add(Individual(genome=CodeGenome(files=control), kind="code",
                                   parent_ids=[], meta={"molt2_arm": "treatment"}))
    ind, m = measured(ctx, item, control, .6, meta={"molt2_arm": "control"})
    assert ind is old and ind.meta["molt2_arm"] == "treatment"
    run(method.on_measured(ctx, ind, m))
    # Duplicate first arm also must not resolve the missing treatment.
    run(method.on_measured(ctx, ind, m))
    assert not method.evidence
    t, tm = measured(ctx, item, treatment, .68, meta={"molt2_arm": "control"})
    run(method.on_measured(ctx, t, tm))
    assert method.evidence[0]["control_id"] == old.id
    assert method.evidence[0]["delta"] == pytest.approx(.08)


def test_mismatching_templates_are_not_one_factor_evidence():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    control, treatment, _ = toggle_pair({"solution.py": PY_TEMPLATE}, "solution.py")
    treatment["solution.py"] += "\n# unrelated source change in one arm\n"
    c, cm = measured(ctx, item, control, .6)
    t, tm = measured(ctx, item, treatment, .9)
    run(method.on_measured(ctx, c, cm)); run(method.on_measured(ctx, t, tm))
    row = method.evidence[0]
    assert row["status"] == "inconclusive" and row["delta"] is None
    assert not row["one_switch_source_contrast"]


def test_off_anchor_control_is_recorded_as_confounded_not_supported():
    method, ctx, _ = make(control_tolerance=.02)
    item = get_experiment(method, ctx)
    resolve_pair(method, ctx, item, control_score=.4, treatment_score=.8)
    row = method.evidence[0]
    assert row["status"] == "confounded_control"
    assert not row["anchor_reproduced"]
    assert row["delta"] == pytest.approx(.4)
    assert row["anchor_score"] == .6


def test_negative_result_is_retained_and_does_not_replace_anchor():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    _, _, treatment, _ = resolve_pair(method, ctx, item, treatment_score=.4)
    row = method.evidence[0]
    assert row["status"] == "negative_on_observed_pair"
    assert row["delta"] == pytest.approx(-.2)
    assert not any(treatment.id in chain for chain in method.chains)
    followups = run(method.ask(ctx, 2))
    assert any("negative_on_observed_pair" in i.context.extra.get("molt2_memory", "") for i in followups)


def test_outer_timeout_is_inconclusive_and_its_actual_error_survives():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    control, treatment, _ = toggle_pair({"solution.py": PY_TEMPLATE}, "solution.py")
    c, cm = measured(ctx, item, control, .6)
    t, tm = measured(ctx, item, treatment, None, artifacts={"evaluation_error": "Evaluation timed out"})
    run(method.on_measured(ctx, c, cm)); run(method.on_measured(ctx, t, tm))
    row = method.evidence[0]
    assert row["status"] == "inconclusive" and row["delta"] is None
    assert row["treatment_score"] is None
    assert "Evaluation timed out" in row["errors"]


def test_generation_shortfall_closes_pair_and_backbone_can_continue():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    # Two genuine shortfalls have the same work/batch ID by the core driver contract.
    for _ in range(item.k):
        run(method.on_failed(ctx, Failure(item_id=item.id, batch_id=item.batch_id,
                                         stage="generate", reason="not produced")))
    assert len(method.evidence) == 1
    assert method.evidence[0]["status"] == "inconclusive"
    assert len(method.evidence[0]["failures"]) == 2
    assert run(method.ask(ctx, 2))


def test_only_one_experiment_can_be_open_while_base_chains_continue():
    method, ctx, _ = make()
    first = get_experiment(method, ctx)
    for _ in range(4):
        items = run(method.ask(ctx, 2))
        assert not any(i.meta.get("molt2_experiment") for i in items)
        assert items, "an unresolved experiment must not block both protected base chains"
        resolve_base(method, ctx, items)
    assert first.batch_id in method.state_dict()["molt2"]["pending_experiments"]
    assert len(method.chains) == 2


def test_checkpoint_restores_completed_evidence_and_never_invents_outcome():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    resolve_pair(method, ctx, item)
    state = json.loads(json.dumps(method.state_dict()))
    restored = Molt2(num_chains=2, k_candidates=2, num_inspirations=2,
                     selector="rpucg", seed=0)
    restored.load_state_dict(state)
    restored.reconcile(ctx)
    assert restored.evidence == method.evidence
    assert restored.lineage(ctx.store)["mechanism_evidence"] == method.evidence
    assert len(restored.chains) == 2


def test_invalid_only_warmup_does_not_launch_an_experiment():
    method, ctx, _ = make(warmup=4)
    first = run(method.ask(ctx, 2))
    for item in first:
        for i in range(item.k):
            ind, m = measured(ctx, item,
                {"solution.py": f"# invalid-{item.batch_id}-{i}\ndef solve(x):\n    return x\n"},
                score=0., validity=0., artifacts={"invalid_reason": "shape mismatch"})
            run(method.on_measured(ctx, ind, m))
    assert not any(item.meta.get("molt2_experiment") for item in run(method.ask(ctx, 2)))


def test_pending_half_pair_is_interrupted_on_resume_not_fabricated():
    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    control, treatment, _ = toggle_pair({"solution.py": PY_TEMPLATE}, "solution.py")
    c, cm = measured(ctx, item, control, .6)
    run(method.on_measured(ctx, c, cm))
    assert not method.evidence
    state = json.loads(json.dumps(method.state_dict()))
    restored = Molt2(num_chains=2, k_candidates=2, num_inspirations=2,
                     selector="rpucg", seed=0)
    restored.load_state_dict(state)
    restored.reconcile(ctx)
    assert not restored.state_dict()["molt2"]["pending_experiments"]
    assert len(restored.evidence) == 1
    row = restored.evidence[0]
    assert row["delta"] is None and row.get("treatment_score") is None
    assert row["observed_arms"]["control"]["score"] == .6
    assert row["status"] not in {"supported_on_observed_pair", "negative_on_observed_pair"}
    # A late orphaned callback cannot turn the closed partial checkpoint into support.
    t, tm = measured(ctx, item, treatment, .8)
    run(restored.on_measured(ctx, t, tm))
    assert len(restored.evidence) == 1 and restored.evidence[0]["delta"] is None


def test_variator_samples_one_template_and_materializes_two_measured_arms(monkeypatch):
    from pantheon.evolution.core.work import Produced
    from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator

    method, ctx, _ = make()
    item = get_experiment(method, ctx)
    wire_k = []

    async def fake_provider(self, context, request):
        wire_k.append(request.k)
        return [Produced(genome=CodeGenome(files={"solution.py": PY_TEMPLATE}),
                         item_id=request.id, batch_id=request.batch_id,
                         parent_ids=list(request.parent_ids), meta={"summary": "a proposal"})]

    monkeypatch.setattr(UpstreamCompletionVariator, "create", fake_provider)
    variator = method.default_variator(model="fixture-no-network", target_file="solution.py")
    produced = run(variator.create(ctx, item))
    assert wire_k == [1]
    assert item.k == 2, "the real driver still accounts for both evaluations"
    assert len(produced) == 2
    assert [p.meta["molt2_arm"] for p in produced] == ["control", "treatment"]
    assert all(p.batch_id == item.batch_id for p in produced)
    assert produced[0].meta["molt2_pair_hash"] == produced[1].meta["molt2_pair_hash"]
    assert produced[0].parent_ids == produced[1].parent_ids == item.parent_ids
    off, on, _ = toggle_pair(produced[0].genome.files, "solution.py")
    assert off == produced[0].genome.files and on == produced[1].genome.files


def test_core_driver_materializes_pairs_with_real_dedup_and_budget(monkeypatch):
    """Exercise the actual async loop; only model and numerical solver are fixtures."""
    from pantheon.evolution.core.loop import evolve
    from pantheon.evolution.core.work import Produced
    from pantheon.evolution.methods.molt2 import switch_value
    from pantheon.evolution.methods.simpletes import UpstreamCompletionVariator

    provider_k = []
    eval_calls = []

    async def provider(self, ctx, item):
        provider_k.append((item.meta.get("molt2_experiment", False), item.k))
        # Always returning the same template intentionally exercises Store.add's
        # dedup reuse of the original seed and previously measured treatment.
        return [Produced(genome=CodeGenome(files={"solution.py": PY_TEMPLATE}),
                         item_id=item.id, batch_id=item.batch_id,
                         parent_ids=list(item.parent_ids), meta={"molt2_arm": "stale"})
                for _ in range(item.k)]

    class ToyEvaluator:
        kind = "code"

        async def measure(self, ctx, ind, fidelity="full"):
            assert fidelity == "full"
            assert ctx.objective == "TRAIN ONLY: fixed toy cases"
            arm = switch_value(ind.genome.files["solution.py"], "solution.py")
            eval_calls.append((ind.id, arm, fidelity))
            return Measurement(individual_id=ind.id, metrics={
                "combined_score": .6 + .1 * arm, "validity": 1.}, fidelity=fidelity)

    monkeypatch.setattr(UpstreamCompletionVariator, "create", provider)
    method = Molt2(num_chains=2, k_candidates=2, num_inspirations=2,
                    selector="rpucg", seed=0, warmup=2,
                    experiment_interval=1, experiment_fraction=.6)
    budget = Budget(max_items=4)
    variator = method.default_variator(model="unused-fixture", target_file="solution.py")
    result = run(evolve(method=method, variator=variator,
        evaluators={"code": ToyEvaluator()},
        seeds=[CodeGenome(files={"solution.py": PY_TEMPLATE})],
        objective="TRAIN ONLY: fixed toy cases", budget=budget, concurrency=1))
    assert budget.items_used == 4
    assert len(eval_calls) == 9, "one seed plus two evaluations for every dispatched batch"
    assert len(result.store) == 2, "actual store dedup should retain only OFF and ON programs"
    assert method.evidence and all(row["one_switch_source_contrast"] for row in method.evidence)
    assert all(row["delta"] == pytest.approx(.1) for row in method.evidence)
    assert all(k == 1 for experiment, k in provider_k if experiment), "one model template, not independent arms"
    assert len(method.chains) == 2


@pytest.mark.parametrize("code", [
    '"""Example only:\nMOLT2_TREATMENT = 0\n"""\ndef solve(x):\n    return x + MOLT2_TREATMENT\n',
    'MOLT2_TREATMENT = 0\nMOLT2_TREATMENT += 1\ndef solve(x):\n    return x + MOLT2_TREATMENT\n',
])
def test_binding_must_be_real_and_never_reassigned(code):
    with pytest.raises(ValueError):
        toggle_pair({"solution.py": code}, "solution.py")
