"""Maximal determinant of a 29x29 (+1/-1) matrix.

Scoring is SimpleTES's, unchanged, so a number here is directly comparable with theirs:

    combined_score = |det(M)| / 1270698346568170340352

The denominator is Hadamard's bound for n=29. It is not attainable -- 29 is not divisible by 4 --
so the score has a real ceiling somewhere below 1 and the seed sits at 0.1433. That gap is the
reason this task is here: on Erdos every arm finished inside 0.0005 of every other, and circle
packing reached the record on its second iteration, so neither could separate two search policies.

The determinant is computed exactly, in integers. `numpy.linalg.det` on a 29x29 +/-1 matrix
returns a float whose magnitude is ~1e21, well past the 53 bits a double carries, so the last six
digits are noise -- and the ranking of two near-equal candidates would be noise with them. Bareiss
fraction-free elimination keeps it exact.
"""
import importlib.util
import os
import time
from typing import Any, Dict

import numpy as np

MATRIX_SIZE = 29
HADAMARD_BOUND = 1270698346568170340352      # n^(n/2) for n=29, as SimpleTES uses it


def _det_exact(m) -> int:
    """Bareiss fraction-free elimination: an exact integer determinant."""
    a = [[int(v) for v in row] for row in m]
    n = len(a)
    sign, prev = 1, 1
    for k in range(n - 1):
        if a[k][k] == 0:
            for i in range(k + 1, n):
                if a[i][k] != 0:
                    a[k], a[i] = a[i], a[k]
                    sign = -sign
                    break
            else:
                return 0
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                a[i][j] = (a[i][j] * a[k][k] - a[i][k] * a[k][j]) // prev
        prev = a[k][k]
    return sign * a[n - 1][n - 1]


def _load(workspace_path: str):
    path = os.path.join(workspace_path, "solution.py")
    spec = importlib.util.spec_from_file_location("solution", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _invalid(reason: str, t0: float) -> Dict[str, Any]:
    return {"abs_determinant": 0.0, "determinant_ratio": 0.0, "validity": 0.0,
            "combined_score": 0.0, "eval_time": time.time() - t0,
            "invalid_reason": reason,
            "fitness_weights": {"combined_score": 1.0}}


def evaluate(workspace_path: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        mod = _load(workspace_path)
        out = mod.run_code()
    except Exception as e:  # noqa: BLE001
        return _invalid(f"{type(e).__name__}: {e}", t0)

    m = out[0] if isinstance(out, (tuple, list)) and len(out) and not np.isscalar(out[0]) else out
    try:
        m = np.asarray(m)
    except Exception as e:  # noqa: BLE001
        return _invalid(f"output is not array-like: {e}", t0)

    if m.ndim != 2 or m.shape != (MATRIX_SIZE, MATRIX_SIZE):
        return _invalid(f"shape {m.shape}, expected ({MATRIX_SIZE}, {MATRIX_SIZE})", t0)
    if not np.all(np.isfinite(m)):
        return _invalid("non-finite entries", t0)
    if not np.all(np.isin(m, (-1, 1))):
        return _invalid("entries must be +1 or -1", t0)

    det = abs(_det_exact(m))
    ratio = det / HADAMARD_BOUND
    return {
        "abs_determinant": float(det),
        "determinant_ratio": float(ratio),
        "validity": 1.0,
        "combined_score": float(ratio),
        "eval_time": time.time() - t0,
        # Read by the loop to know what to maximise; the method never invents its own weights.
        "fitness_weights": {"combined_score": 1.0},
    }


if __name__ == "__main__" and "__file__" in globals():
    # Self-test on the seed beside this file. The `__file__` guard is load-bearing: the harness
    # exec()s this source with __name__ == "__main__" but no __file__, so an unguarded block runs
    # on every evaluation and fails -- silently, as an evaluation error that reads like the
    # program's fault rather than the evaluator's.
    import json
    print(json.dumps(evaluate(os.path.dirname(os.path.abspath(__file__))), indent=1))
