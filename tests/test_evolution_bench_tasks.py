"""The benchmark task packages: do they score their own seed, and do they survive the harness?

Two things are checked, and the second is the one that bit.

`CodeEvaluator` does not import an evaluator, it `exec()`s the source -- with `__name__` set to
`"__main__"` but no `__file__`. An unguarded `if __name__ == "__main__":` self-test therefore runs
on *every* evaluation and raises `NameError`, which the harness reports as an evaluation error. It
reads like the evolved program's fault. In a smoke run it took every score to zero, including the
seed's, and the run reported `failures 0` throughout.

The seed scores are pinned against SimpleTES's published values so a number produced here stays
comparable with theirs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

TASKS = Path(__file__).resolve().parents[1] / "examples" / "evolution_bench" / "tasks"

# task -> the score SimpleTES's own evaluator gives its seed
SEED_SCORES = {
    "hadamard29": 0.14327485380116958,
    "sums_diffs": 1.0597930945472454,
}


def load(task: str):
    """Run the evaluator the way the harness does: exec the source, no __file__."""
    src = (TASKS / task / "evaluator.py").read_text()
    ns = {"__name__": "__main__"}          # deliberately no __file__
    exec(compile(src, "<evaluator>", "exec"), ns)
    return ns["evaluate"]


ALL_TASKS = sorted(p.name for p in TASKS.iterdir() if (p / "evaluator.py").exists())


@pytest.mark.parametrize("task", ALL_TASKS)
def test_evaluator_survives_being_exec_d_without_a_file(task):
    """Every task, including the ones whose scoring needs Docker.

    This caught the same mistake twice. First an unguarded `if __name__ == "__main__":` self-test,
    then `HERE = Path(__file__).parent` at module scope -- which fails even earlier, before
    `evaluate` is defined, and is reported as the evolved program failing to evaluate. Anything an
    evaluator needs from beside itself has to arrive by environment.
    """
    assert callable(load(task)), f"{task}: evaluator did not define evaluate()"


def test_a_task_that_evolves_a_non_python_file_declares_it():
    """`run_bench` seeds the genome with the file named in task.json. Without one it assumes
    `solution.py`, which for a C++ task would seed the run with nothing."""
    for task in ALL_TASKS:
        cfg_path = TASKS / task / "task.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        evolved = cfg.get("evolve", "solution.py")
        assert (TASKS / task / evolved).exists(), (
            f"{task}: task.json names {evolved!r} but that file is not there")


@pytest.mark.parametrize("task,expected", sorted(SEED_SCORES.items()))
def test_seed_scores_match_the_published_value(task, expected):
    evaluate = load(task)
    out = evaluate(str(TASKS / task))
    assert out["validity"] == 1.0, f"{task}: seed is not feasible -- {out.get('invalid_reason')}"
    assert out["combined_score"] == pytest.approx(expected, rel=1e-6), (
        f"{task}: seed scores {out['combined_score']}, SimpleTES reports {expected}; "
        "a number produced here is no longer comparable with theirs")


@pytest.mark.parametrize("task", sorted(SEED_SCORES))
def test_every_task_declares_what_to_maximise(task):
    out = load(task)(str(TASKS / task))
    assert out.get("fitness_weights") == {"combined_score": 1.0}, (
        "the evaluator supplies the weights; a method that had to invent them would be "
        "optimising something the problem never asked for")


@pytest.mark.parametrize("task", sorted(SEED_SCORES))
def test_a_broken_program_is_infeasible_not_a_zero_score(task, tmp_path):
    (tmp_path / "solution.py").write_text("def run_code():\n    raise RuntimeError('boom')\n")
    out = load(task)(str(tmp_path))
    assert out["validity"] == 0.0
    assert out["combined_score"] == 0.0
    assert out.get("invalid_reason"), "the reason has to reach whoever reads the measurement"


def test_sums_diffs_rejects_an_out_of_range_set_instead_of_clamping(tmp_path):
    """SimpleTES's own seed clamps out-of-range values back into the legal window. Quietly
    repairing an illegal answer is the same class of mistake as reading an infeasible program's
    zero as a score: the search is told it succeeded when it broke a constraint."""
    (tmp_path / "solution.py").write_text(
        "def run_code():\n    return [0, 1, 2, 10_000_000]\n")
    out = load("sums_diffs")(str(tmp_path))
    assert out["validity"] == 0.0
    assert "outside" in out["invalid_reason"]


def test_hadamard_rejects_entries_that_are_not_plus_or_minus_one(tmp_path):
    (tmp_path / "solution.py").write_text(
        "import numpy as np\n"
        "def run_code():\n    return (np.zeros((29, 29)),)\n")
    out = load("hadamard29")(str(tmp_path))
    assert out["validity"] == 0.0
    assert "+1 or -1" in out["invalid_reason"]


def test_hadamard_determinant_is_exact_where_a_float_would_not_be():
    """Near the top of the range a 29x29 +/-1 determinant runs to ~1e20-1e21, well past the 53 bits
    a float64 carries, so `numpy.linalg.det` loses the low digits -- and with them the ordering of
    two candidates that differ by less than a part in 1e16. The seed already sits there."""
    import importlib.util

    import numpy as np

    src = (TASKS / "hadamard29" / "evaluator.py").read_text()
    ns = {"__name__": "x"}
    exec(compile(src, "<e>", "exec"), ns)

    spec = importlib.util.spec_from_file_location(
        "seed_sol", TASKS / "hadamard29" / "solution.py")
    sol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sol)
    m = np.asarray(sol.run_code()[0])

    exact = ns["_det_exact"](m)
    assert isinstance(exact, int), "an integer determinant must come back as an int"
    assert abs(exact) > 2 ** 53, "the seed should already be past float64's integer range"
    assert exact == pytest.approx(float(np.linalg.det(m.astype(float))), rel=1e-6)
    # The float route cannot even represent the answer, which is the whole point.
    assert int(float(exact)) != exact or abs(exact) % 2 == 0
