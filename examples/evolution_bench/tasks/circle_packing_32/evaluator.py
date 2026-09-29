"""Circle packing, n=32 -- SimpleTES's exact verifier, bench-task edition.

Loads `solution.py` from the workspace, calls `construct_circles()`, recomputes the objective with the
same formula SimpleTES's evaluator uses (datasets/circle_packing/circle_packing_32/evaluator.py), and reports
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

N = 32
TOL = 1e-12   # SimpleTES's tolerance for this task; AlphaEvolve used zero


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    try:
        c = np.asarray(_first(_load(workspace_path).construct_circles()), dtype=float)
    except Exception as e:  # noqa: BLE001
        return _fail(f"construct_circles failed: {e}", t0, {"sum_radii": 0.0})
    if c.shape != (N, 3) or not np.all(np.isfinite(c)):
        return _fail(f"bad shape/values {getattr(c, 'shape', None)}", t0, {"sum_radii": 0.0})
    x, y, r = c[:, 0], c[:, 1], c[:, 2]
    if np.any(r <= 0):
        return _fail("non-positive radius", t0, {"sum_radii": 0.0})
    if np.any(x - r < -TOL) or np.any(x + r > 1 + TOL) or np.any(y - r < -TOL) or np.any(y + r > 1 + TOL):
        return _fail("circle outside the unit square", t0, {"sum_radii": 0.0})
    for i in range(N):
        for j in range(i + 1, N):
            if float(np.hypot(x[i] - x[j], y[i] - y[j])) < r[i] + r[j] - TOL:
                return _fail(f"overlap between circles {i} and {j}", t0, {"sum_radii": 0.0})
    s = float(r.sum())
    return {"sum_radii": s, "min_radius": float(r.min()), "max_radius": float(r.max()), "validity": 1.0,
            "combined_score": s, "eval_time": time.time() - t0, "fitness_weights": {"combined_score": 1.0}}
