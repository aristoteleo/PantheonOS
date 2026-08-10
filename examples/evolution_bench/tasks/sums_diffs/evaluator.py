"""Sumset versus difference set: how large can C(A) be?

For a finite set of integers A,

    C(A) = log(|A+A| / |A|) / log(|A-A| / |A|)

and the task is to maximise it -- a lower bound on the smallest constant C for which
|A+A|/|A| <= (|A-A|/|A|)^C holds for every finite A. Scoring is SimpleTES's, unchanged:
`combined_score = C(A)`, seed 1.0598.

Constraints, also theirs: 2 <= |A| <= 512 after deduplication, every element in
[-1_000_000, 1_000_000], integers only.

Unlike Hadamard this one is scored on the program's OUTPUT rather than on a construction with a
known bound, so there is no ceiling to normalise against -- the interesting quantity is simply how
far above the seed a search can get, and whether different families of construction land in
different places.
"""
import importlib.util
import math
import os
import time
from typing import Any, Dict

MIN_SET_SIZE = 2
MAX_SET_SIZE = 512
MIN_INT = -1_000_000
MAX_INT = 1_000_000


def _load(workspace_path: str):
    path = os.path.join(workspace_path, "solution.py")
    spec = importlib.util.spec_from_file_location("solution", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _invalid(reason: str, t0: float) -> Dict[str, Any]:
    return {"c_value": 0.0, "set_size": 0, "validity": 0.0, "combined_score": 0.0,
            "eval_time": time.time() - t0, "invalid_reason": reason,
            "fitness_weights": {"combined_score": 1.0}}


def _as_set(values):
    """A sorted list of distinct ints, or a reason it is not one.

    Deliberately strict where SimpleTES's own seed program is forgiving: it silently clamps
    out-of-range values and pads a too-small set to [0, 1]. Clamping quietly turns an illegal
    answer into a legal one, which is the same class of confusion as reading an infeasible
    program's score as a zero -- the search should be told it broke a constraint, not handed a
    repaired answer.
    """
    try:
        raw = list(values)
    except TypeError as e:
        return None, f"output is not iterable: {e}"
    out = []
    for x in raw:
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return None, f"non-numeric element {x!r}"
        if not math.isfinite(xf):
            return None, "non-finite element"
        if abs(xf - round(xf)) > 1e-9:
            return None, f"element {xf} is not an integer"
        xi = int(round(xf))
        if not (MIN_INT <= xi <= MAX_INT):
            return None, f"element {xi} outside [{MIN_INT}, {MAX_INT}]"
        out.append(xi)
    vals = sorted(set(out))
    if not (MIN_SET_SIZE <= len(vals) <= MAX_SET_SIZE):
        return None, f"|A| = {len(vals)}, must be in [{MIN_SET_SIZE}, {MAX_SET_SIZE}]"
    return vals, None


def _c_value(vals):
    n = len(vals)
    sums = {a + b for a in vals for b in vals}
    diffs = {a - b for a in vals for b in vals}
    sr, dr = len(sums) / n, len(diffs) / n
    if sr <= 1.0 or dr <= 1.0:
        return 0.0, sr, dr
    return float(math.log(sr) / math.log(dr)), sr, dr


def evaluate(workspace_path: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        mod = _load(workspace_path)
        out = mod.run_code()
    except Exception as e:  # noqa: BLE001
        return _invalid(f"{type(e).__name__}: {e}", t0)

    # run_code() may return A, or (A, claimed_c). A claim is ignored -- the score is recomputed.
    values = out[0] if isinstance(out, tuple) else out
    vals, why = _as_set(values)
    if vals is None:
        return _invalid(why, t0)

    c, sr, dr = _c_value(vals)
    return {
        "c_value": float(c),
        "set_size": len(vals),
        "sum_ratio": float(sr),
        "diff_ratio": float(dr),
        "validity": 1.0,
        "combined_score": float(c),
        "eval_time": time.time() - t0,
        "fitness_weights": {"combined_score": 1.0},
    }


if __name__ == "__main__" and "__file__" in globals():
    # See the note in hadamard29/evaluator.py: the `__file__` guard is what keeps this block from
    # running inside the harness, which exec()s the source with __name__ == "__main__".
    import json
    print(json.dumps(evaluate(os.path.dirname(os.path.abspath(__file__))), indent=1))
