"""First autocorrelation inequality (C1) -- SimpleTES's exact verifier, bench-task edition.

Loads `solution.py` from the workspace, calls `run_code()`, recomputes the objective with the
same formula SimpleTES's evaluator uses (datasets/autocorrelation/autocorrelation_first/evaluator.py), and reports
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

def c1_of(seq) -> float:
    """SimpleTES's evaluate_sequence_c1, unchanged: clip to [0, 1000], C1 = 2n max(f*f) / (sum f)^2."""
    seq = [float(x) for x in np.asarray(seq, dtype=float).reshape(-1)]
    if not seq or not all(np.isfinite(seq)):
        return float("inf")
    seq = [min(1000.0, max(0.0, x)) for x in seq]
    n = len(seq)
    conv = np.convolve(seq, seq)
    s = float(np.sum(seq))
    if s < 0.01:
        return float("inf")
    return float(2 * n * float(np.max(conv)) / (s ** 2))


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    try:
        seq = _first(_load(workspace_path).run_code())
        c1 = c1_of(seq)
    except Exception as e:  # noqa: BLE001
        return _fail(f"run_code failed: {e}", t0, {"c1": float("inf")})
    if not np.isfinite(c1):
        return _fail("invalid sequence", t0, {"c1": float("inf")})
    return {"c1": c1, "n": int(len(np.asarray(seq).reshape(-1))), "validity": 1.0,
            "combined_score": 1.0 / (1e-8 + c1), "eval_time": time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}}
