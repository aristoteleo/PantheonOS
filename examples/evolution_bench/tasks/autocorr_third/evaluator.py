"""Third autocorrelation inequality (C3) -- SimpleTES's exact verifier, bench-task edition.

Loads `solution.py` from the workspace, calls `construct_function()`, recomputes the objective with the
same formula SimpleTES's evaluator uses (datasets/autocorrelation/autocorrelation_third/evaluator.py), and reports
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

BENCHMARK = 1.4556427953745406   # AlphaEvolve's C3; SimpleTES scores BENCHMARK / C3
MAX_ABS = 1e10


def c3_of(h) -> float:
    """SimpleTES's compute_score, unchanged: C3 = max|f*f| dx / (sum(f) dx)^2 on [-1/4, 1/4]."""
    h = np.asarray(h, dtype=float)
    if h.ndim != 1 or h.size == 0 or not np.all(np.isfinite(h)):
        raise ValueError("invalid heights")
    if float(np.max(np.abs(h))) > MAX_ABS:
        raise ValueError("extreme values")
    dx = 0.5 / int(h.size)
    integral_sq = (float(np.sum(h)) * dx) ** 2
    if integral_sq < 1e-9:
        raise ValueError("integral close to zero")
    conv = np.convolve(h, h, mode="full") * dx
    return float(np.max(np.abs(conv)) / integral_sq)


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    try:
        h = _first(_load(workspace_path).construct_function())
        c3 = c3_of(h)
    except Exception as e:  # noqa: BLE001
        return _fail(f"construct_function failed: {e}", t0, {"c3": float("inf")})
    if not np.isfinite(c3) or c3 <= 0:
        return _fail("invalid construction", t0, {"c3": float("inf")})
    return {"c3": c3, "n": int(np.asarray(h).size), "validity": 1.0,
            "combined_score": BENCHMARK / c3, "eval_time": time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}}
