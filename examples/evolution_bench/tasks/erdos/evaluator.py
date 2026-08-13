"""Erdos minimum overlap -- DeepMind's exact evaluator, bench-task edition.

Loads `solution.py` from the workspace, calls `run_construction()`, and scores
Psi(h) = max_k sum_i h_i (1-h)_{i-k} * 2/K (the discrete overlap integral). Lower Psi is
better; the framework maximises, so `combined_score = 1 - Psi`.

Differences from the original example's evaluator, both deliberate:
  * no warm-start channel (`_state`): persisting the champion construction gave every lineage
    a monotone floor and killed exploration -- the sticky-champion attractor the Erdos ablation
    isolated. Constructions here live in code alone.
  * no module-scope `__file__`: CodeEvaluator exec()s this source, where `__file__` is absent.
"""
import importlib.util
import os
import time
from typing import Any, Dict

import numpy as np

TOL = 1e-9


def compute_upper_bound(seq) -> float:
    seq = np.asarray(seq, dtype=float)
    conv = np.correlate(seq, 1 - seq, mode="full")
    return float(np.max(conv) / len(seq) * 2)


def _validate(seq: np.ndarray) -> str:
    if seq.ndim != 1 or len(seq) < 8:
        return f"sequence must be a 1-D array of length >= 8 (got shape {seq.shape})"
    if not np.all(np.isfinite(seq)):
        return "non-finite values"
    if np.any(seq < -TOL) or np.any(seq > 1 + TOL):
        return "step heights must lie in [0, 1]"
    mass = float(np.sum(seq))
    target = len(seq) / 2.0
    if abs(mass - target) > 1e-4:
        return f"unit-mass violated: sum(h)={mass:.6f}, need K/2={target:.6f}"
    return ""


def _fail(reason: str, eval_time: float) -> Dict[str, Any]:
    return {"overlap": 1.0, "validity": 0.0, "combined_score": 0.0,
            "invalid_reason": reason, "eval_time": eval_time,
            "fitness_weights": {"combined_score": 1.0}}


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    path = os.path.join(workspace_path, "solution.py")
    try:
        spec = importlib.util.spec_from_file_location("solution", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        seq = np.asarray(mod.run_construction(), dtype=float).reshape(-1)
    except Exception as e:  # noqa: BLE001
        return _fail(f"run_construction failed: {e}", time.time() - t0)

    reason = _validate(seq)
    if reason:
        return _fail(reason, time.time() - t0)
    seq = np.clip(seq, 0.0, 1.0)
    psi = compute_upper_bound(seq)
    return {"overlap": psi, "K": len(seq), "validity": 1.0,
            "combined_score": 1.0 - psi, "eval_time": time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}}


if __name__ == "__main__" and "__file__" in globals():
    print(evaluate(os.path.dirname(os.path.abspath(__file__))))
