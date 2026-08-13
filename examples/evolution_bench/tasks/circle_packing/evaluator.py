"""Circle packing n=26 -- AlphaEvolve/OpenEvolve rules, bench-task edition.

Loads `solution.py` from the workspace, calls `run_packing()` -> (centers, radii[, sum]),
validates (inside the unit square, no overlaps, tolerance 1e-6) and scores
`combined_score = sum of radii` (0 if invalid). The published record for n=26 is 2.635983
(AlphaEvolve V2 = SimpleTES).

No warm-start channel, deliberately: persisting the champion layout is the sticky-champion
attractor the Erdos ablation isolated, and the packing arm that used it (B3) finished BELOW its
warm-start-free twin. Layouts live in code alone.
"""
import importlib.util
import os
import time
from typing import Any, Dict

import numpy as np

N = 26
TOL = 1e-6


def _fail(reason: str, eval_time: float) -> Dict[str, Any]:
    return {"sum_radii": 0.0, "validity": 0.0, "combined_score": 0.0,
            "invalid_reason": reason, "eval_time": eval_time,
            "fitness_weights": {"combined_score": 1.0}}


def _validate(centers: np.ndarray, radii: np.ndarray) -> str:
    if centers.shape != (N, 2) or radii.shape != (N,):
        return f"bad shape centers={centers.shape} radii={radii.shape}"
    if not (np.all(np.isfinite(centers)) and np.all(np.isfinite(radii))):
        return "non-finite values"
    if np.any(radii <= 0):
        return "non-positive radius"
    x, y = centers[:, 0], centers[:, 1]
    if np.any(x - radii < -TOL) or np.any(x + radii > 1 + TOL) or \
       np.any(y - radii < -TOL) or np.any(y + radii > 1 + TOL):
        return "circle outside the unit square"
    for i in range(N):
        for j in range(i + 1, N):
            if float(np.linalg.norm(centers[i] - centers[j])) < radii[i] + radii[j] - TOL:
                return f"overlap between circles {i} and {j}"
    return ""


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    path = os.path.join(workspace_path, "solution.py")
    try:
        spec = importlib.util.spec_from_file_location("solution", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.run_packing()
    except Exception as e:  # noqa: BLE001
        return _fail(f"run_packing failed: {e}", time.time() - t0)

    try:
        centers, radii = result[0], result[1]
        centers = np.asarray(centers, dtype=float)
        radii = np.asarray(radii, dtype=float).reshape(-1)
    except Exception as e:  # noqa: BLE001
        return _fail(f"bad run_packing output: {e}", time.time() - t0)

    reason = _validate(centers, radii)
    if reason:
        return _fail(reason, time.time() - t0)
    s = float(np.sum(radii))
    return {"sum_radii": s, "min_radius": float(np.min(radii)),
            "max_radius": float(np.max(radii)), "validity": 1.0,
            "combined_score": s, "eval_time": time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}}


if __name__ == "__main__" and "__file__" in globals():
    print(evaluate(os.path.dirname(os.path.abspath(__file__))))
