"""Evaluator for the Erdős Minimum Overlap Problem (continuous step-function relaxation).

Objective (Haugland / Swinnerton-Dyer form — the one AlphaEvolve / SimpleTES / CORAL optimize):

    Psi(h) = max_k  integral  h(x) * (1 - h(x+k)) dx ,     h:[0,2]->[0,1],  integral h = 1

MINIMIZE Psi. A lower Psi is a tighter upper bound on the minimum-overlap constant C_5.
Discretize h as K equal-width steps (width 2/K); unit mass  <=>  sum(h) = K/2.

The scorer below is DeepMind's ground-truth ``compute_upper_bound`` (from
alphaevolve_results/mathematical_results.ipynb) unmodified, so our number is directly
comparable to the published values. Reference: DeepMind reproduce 0.38092303510845.

Records to beat (ALL lower-is-better):
    Haugland 0.380927  ·  AlphaEvolve 0.380924  ·  SimpleTES 0.380868

Because the framework MAXIMIZES fitness, we expose combined_score = 1 - Psi (higher is
better <=> minimize Psi); ``overlap`` = Psi is the human-readable number to compare.
"""
import importlib.util
import os
import time
from typing import Any, Dict

import numpy as np

TOL = 1e-9


def _load_run(workspace_path: str):
    path = os.path.join(workspace_path, "sequence.py")
    spec = importlib.util.spec_from_file_location("sequence", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_construction


def compute_upper_bound(sequence) -> float:
    """DeepMind's exact evaluator: Psi(h) = max_k integral h(x)(1-h(x+k)) dx. Lower is better.

    np.correlate(h, 1-h, 'full')[lag] = sum_i h_i (1-h)_{i-lag} (discrete cross-correlation);
    max over lag = max over translations; * (2/K) turns the Riemann sum into the integral.
    """
    seq = np.asarray(sequence, dtype=float)
    conv = np.correlate(seq, 1 - seq, mode="full")
    return float(np.max(conv) / len(seq) * 2)


def _validate(seq: np.ndarray) -> str:
    """Return '' if h is a feasible step function, else a short reason."""
    if seq.ndim != 1 or len(seq) < 8:
        return f"sequence must be a 1-D array of length >= 8 (got shape {seq.shape})"
    if not np.all(np.isfinite(seq)):
        return "non-finite values"
    if np.any(seq < -TOL) or np.any(seq > 1 + TOL):
        return "step heights must lie in [0, 1]"
    mass = float(np.sum(seq))
    target = len(seq) / 2.0  # unit mass: integral h = (2/K) * sum = 1  <=>  sum = K/2
    if abs(mass - target) > 1e-4:
        return f"unit-mass violated: sum(h)={mass:.6f}, need K/2={target:.6f}"
    return ""


def evaluate(workspace_path: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        run = _load_run(workspace_path)
        seq = np.asarray(run(), dtype=float).reshape(-1)
    except Exception as e:  # noqa: BLE001
        return _fail(f"run_construction failed: {e}", time.time() - t0)

    reason = _validate(seq)
    eval_time = time.time() - t0
    if reason:
        return _fail(reason, eval_time)

    seq = np.clip(seq, 0.0, 1.0)
    psi = compute_upper_bound(seq)
    return {
        "overlap": psi,                       # Psi(h) — compare to 0.380924 / 0.380868 (LOWER better)
        "K": len(seq),
        "validity": 1.0,
        "eval_time": eval_time,
        "combined_score": 1.0 - psi,          # fitness (HIGHER better) == minimize Psi
        "fitness_weights": {"combined_score": 1.0},
        # Warm-start channel: hand back the produced step function so the framework persists it and
        # the next generation loads + polishes it instead of re-deriving from scratch.
        "_state": {"sequence": seq.tolist()},
    }


def _fail(reason: str, eval_time: float) -> Dict[str, Any]:
    return {
        "overlap": 1.0,
        "validity": 0.0,
        "eval_time": eval_time,
        "combined_score": 0.0,
        "invalid_reason": reason,
        "fitness_weights": {"combined_score": 1.0},
    }


if __name__ == "__main__" and "__file__" in globals():
    # Quick self-test on the seed in this directory (guarded so it doesn't run when embedded).
    print(evaluate(os.path.dirname(os.path.abspath(__file__))))
