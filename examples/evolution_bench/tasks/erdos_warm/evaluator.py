"""Erdos minimum overlap -- DeepMind's exact evaluator, bench-task edition.

Loads `solution.py` from the workspace, calls `run_construction()`, and scores
Psi(h) = max_k sum_i h_i (1-h)_{i-k} * 2/K (the discrete overlap integral). Lower Psi is
better; the framework maximises, so `combined_score = 1 - Psi`.

WARM-START VARIANT of `tasks/erdos`, for protocol B. The best construction of the run so far is
persisted beside the workspaces (`<workspace_base>/warm_start.json`) and copied INTO each
workspace as `warm_start.json` before the program runs, so a program may load it and refine it;
after scoring, a construction that beats the stored one replaces it (file-locked, atomic). This
is the channel the original example evaluator had and `tasks/erdos` removed because it caused
the sticky-champion collapse; it is restored here on purpose, to measure that under all four
methods. No module-scope `__file__`: CodeEvaluator exec()s this source.
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


WARM = "warm_start.json"


def _warm_shared(workspace_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(workspace_path)), WARM)


def _warm_copy_in(workspace_path: str) -> None:
    import shutil
    shared = _warm_shared(workspace_path)
    if os.path.exists(shared):
        try:
            shutil.copy(shared, os.path.join(workspace_path, WARM))
        except OSError:
            pass


def _warm_update(workspace_path: str, seq: np.ndarray, psi: float) -> None:
    """Replace the shared construction if this one is better. Locked, atomic; a lost race
    only costs one improvement, never the file."""
    import fcntl
    import json
    shared = _warm_shared(workspace_path)
    lock = shared + ".lock"
    try:
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            cur = None
            if os.path.exists(shared):
                try:
                    cur = float(json.load(open(shared)).get("psi", 1.0))
                except Exception:  # noqa: BLE001
                    cur = None
            if cur is None or psi < cur - 1e-12:
                tmp = shared + ".tmp"
                with open(tmp, "w") as f:
                    json.dump({"sequence": [float(x) for x in seq], "psi": psi,
                               "K": int(len(seq))}, f)
                os.replace(tmp, shared)
            fcntl.flock(lf, fcntl.LOCK_UN)
    except OSError:
        pass


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    path = os.path.join(workspace_path, "solution.py")
    _warm_copy_in(workspace_path)
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
    _warm_update(workspace_path, seq, psi)
    return {"overlap": psi, "K": len(seq), "validity": 1.0,
            "combined_score": 1.0 - psi, "eval_time": time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}}


if __name__ == "__main__" and "__file__" in globals():
    print(evaluate(os.path.dirname(os.path.abspath(__file__))))
