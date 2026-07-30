"""Evaluator for the circle-packing problem (Pantheon Evolution).

Objective: pack N = 26 non-overlapping circles inside the unit square [0,1]x[0,1]
to maximize the sum of their radii. Follows the AlphaEvolve / OpenEvolve setup.

The evolved program must expose ``run_packing()`` returning ``(centers, radii)`` or
``(centers, radii, sum)`` where ``centers`` has shape (N, 2) and ``radii`` has shape (N,).

A packing is INVALID (score 0) if any circle leaves the unit square or two circles
overlap (numerical tolerance 1e-6). For a valid packing the fitness is simply
``combined_score = sum_radii`` (the raw objective); ``target_ratio = sum_radii / 2.635``
is reported alongside as an informational "fraction of the AlphaEvolve record".
"""
import importlib.util
import os
import time
from typing import Any, Dict

import numpy as np

N = 26
TARGET_VALUE = 2.635  # AlphaEvolve record for N=26 (sum of radii)
TOL = 1e-6


def _load_run_packing(workspace_path: str):
    path = os.path.join(workspace_path, "packing.py")
    spec = importlib.util.spec_from_file_location("packing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_packing


def _validate(centers: np.ndarray, radii: np.ndarray) -> str:
    """Return '' if valid, else a short reason."""
    if centers.shape != (N, 2) or radii.shape != (N,):
        return f"bad shape centers={centers.shape} radii={radii.shape} (expected ({N},2),({N},))"
    if not (np.all(np.isfinite(centers)) and np.all(np.isfinite(radii))):
        return "non-finite values"
    if np.any(radii <= 0):
        return "non-positive radius"
    # inside the unit square
    x, y = centers[:, 0], centers[:, 1]
    if np.any(x - radii < -TOL) or np.any(x + radii > 1 + TOL) or \
       np.any(y - radii < -TOL) or np.any(y + radii > 1 + TOL):
        return "circle outside the unit square"
    # pairwise non-overlap
    for i in range(N):
        for j in range(i + 1, N):
            dist = float(np.linalg.norm(centers[i] - centers[j]))
            if dist < radii[i] + radii[j] - TOL:
                return f"overlap between circles {i} and {j}"
    return ""


def evaluate(workspace_path: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        run_packing = _load_run_packing(workspace_path)
        result = run_packing()
    except Exception as e:
        return _fail(f"run_packing failed: {e}", time.time() - t0)

    try:
        if len(result) == 3:
            centers, radii, _reported = result
        elif len(result) == 2:
            centers, radii = result
        else:
            return _fail(f"run_packing returned {len(result)} values (expected 2 or 3)", time.time() - t0)
        centers = np.asarray(centers, dtype=float)
        radii = np.asarray(radii, dtype=float).reshape(-1)
    except Exception as e:
        return _fail(f"bad run_packing output: {e}", time.time() - t0)

    reason = _validate(centers, radii)
    eval_time = time.time() - t0
    if reason:
        return _fail(reason, eval_time)

    sum_radii = float(np.sum(radii))
    target_ratio = sum_radii / TARGET_VALUE
    return {
        "sum_radii": sum_radii,
        "min_radius": float(np.min(radii)),
        "max_radius": float(np.max(radii)),
        "target_ratio": target_ratio,           # informational: fraction of the 2.635 record
        "validity": 1.0,
        "eval_time": eval_time,
        "combined_score": sum_radii,             # fitness IS the raw sum of radii (not normalized)
        "fitness_weights": {"combined_score": 1.0},
        # Warm-start channel: hand the produced layout back so the framework can persist it and the
        # next generation can load + polish it instead of re-running the whole search from scratch.
        "_state": {"centers": centers.tolist(), "radii": radii.tolist(), "sum_radii": sum_radii},
    }


def _fail(reason: str, eval_time: float) -> Dict[str, Any]:
    return {
        "sum_radii": 0.0,
        "target_ratio": 0.0,
        "validity": 0.0,
        "eval_time": eval_time,
        "combined_score": 0.0,
        "invalid_reason": reason,
        "fitness_weights": {"combined_score": 1.0},
    }


if __name__ == "__main__" and "__file__" in globals():
    # Quick self-test on the seed packing in this directory. Guarded on __file__ so this
    # block does NOT run when the evaluator source is embedded and exec'd inside the
    # framework's `python -c` subprocess (there __name__ == "__main__" but __file__ is undefined).
    print(evaluate(os.path.dirname(os.path.abspath(__file__))))
