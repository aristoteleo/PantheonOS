"""Second autocorrelation inequality (C2) -- SimpleTES's exact verifier, bench-task edition.

Loads `solution.py` from the workspace, calls `run_code()`, recomputes the objective with the
same formula SimpleTES's evaluator uses (datasets/autocorrelation/autocorrelation_second/evaluator.py), and reports
`combined_score` exactly as SimpleTES does so numbers stay comparable. A self-reported value is
ignored. No module-scope `__file__`: CodeEvaluator exec()s this source.
"""
import importlib.util
import os
import time
from typing import Any, Dict

import numpy as np


def _fail(reason: str, t0: float, extra: Dict[str, Any]) -> Dict[str, Any]:
    return {"validity": 0.0, "combined_score": 0.0, "invalid_reason": reason,
            "eval_time": time.time() - t0, "fitness_weights": {"combined_score": 1.0}, **extra}


def _load(workspace_path: str):
    spec = importlib.util.spec_from_file_location("solution", os.path.join(workspace_path, "solution.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _first(out):
    """SimpleTES programs return (construction, self-reported value) or the construction alone."""
    if isinstance(out, tuple) and len(out) >= 1:
        return out[0]
    return out

def c2_of(seq) -> float:
    """SimpleTES's evaluate_sequence_ac2, unchanged: R(f) = ||f*f||_2^2 / (||f*f||_1 ||f*f||_inf)
    with its piecewise-linear L2 integral and its specific L1 normalisation."""
    seq = [float(x) for x in np.asarray(seq, dtype=float).reshape(-1)]
    if not seq or not all(np.isfinite(seq)):
        raise ValueError("invalid sequence")
    seq = [max(0.0, x) for x in seq]
    if np.sum(seq) < 0.01:
        raise ValueError("sum too close to zero")
    seq = [min(1000.0, x) for x in seq]
    conv = np.convolve(seq, seq)
    m = len(conv)
    xs = np.linspace(-0.5, 0.5, m + 2)
    h = np.diff(xs)
    ys = np.concatenate(([0.0], conv, [0.0]))
    l2sq = 0.0
    for i in range(m + 1):
        y1, y2 = ys[i], ys[i + 1]
        l2sq += (h[i] / 3.0) * (y1 * y1 + y1 * y2 + y2 * y2)
    n1 = float(np.sum(np.abs(conv)) / (m + 1))
    ninf = float(np.max(np.abs(conv)))
    return float(l2sq / (n1 * ninf))


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    try:
        seq = _first(_load(workspace_path).run_code())
        c2 = c2_of(seq)
    except Exception as e:  # noqa: BLE001
        return _fail(f"run_code failed: {e}", t0, {"c2": 0.0})
    if not np.isfinite(c2) or c2 <= 0:
        return _fail("invalid sequence", t0, {"c2": 0.0})
    return {"c2": c2, "n": int(len(np.asarray(seq).reshape(-1))), "validity": 1.0,
            "combined_score": c2, "eval_time": time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}}
