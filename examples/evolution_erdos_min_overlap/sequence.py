"""Erdős Minimum Overlap Problem — construct a step function h:[0,2]->[0,1].

This file is what evolution optimizes. ``run_construction()`` must return the K step
heights of h (equal-width steps tiling [0,2]) as a 1-D array. The evaluator scores

    Psi(h) = max_k  integral h(x)(1 - h(x+k)) dx        (LOWER is better)

subject to h in [0,1] and unit mass  <=>  sum(h) == K/2.

The naive seed below is the UNIFORM function h == 1/2, which scores the trivial
Psi = 0.5. The known-good shape is very non-uniform (near 0 at the edges, a plateau
near the center, mirror-symmetric) reaching Psi ~ 0.3809 — DISCOVER it: reason about
which translation k drives the max overlap and reshape h to push that worst case down,
while keeping h feasible (in [0,1], sum == K/2).

WARM START: if ``warm_start.json`` sits next to this file it holds the best step
function the PARENT produced; load it and refine from there (a short local optimization
of an already-good h beats re-deriving one), falling back to the seed when absent.
"""
import json
import os

import numpy as np

K = 95  # number of equal-width steps tiling [0,2]; unit mass <=> sum(h) == K/2
        # (K=95 matches AlphaEvolve's published solution; the score is ~K-independent.)


def normalize_mass(h):
    """Project h into the feasible set: clip to [0,1] and rescale so sum(h) == len(h)/2."""
    h = np.clip(np.asarray(h, dtype=float), 0.0, 1.0)
    target = len(h) / 2.0
    for _ in range(64):
        s = float(h.sum())
        if abs(s - target) < 1e-12:
            break
        if s > target:
            h = h * (target / s)                  # scale down: stays <= 1
        else:
            room = 1.0 - h
            denom = float(room.sum())
            if denom < 1e-15:
                break
            h = h + room * ((target - s) / denom)  # fill toward 1, never exceeds
        h = np.clip(h, 0.0, 1.0)
    return h


def _load_warm_start():
    """Load the parent's best step function from warm_start.json (cwd / next to this file), or None."""
    for path in ("warm_start.json", os.path.join(os.path.dirname(__file__), "warm_start.json")):
        try:
            with open(path) as f:
                d = json.load(f)
            h = np.asarray(d["sequence"], dtype=float)
            if h.ndim == 1 and len(h) >= 8 and np.all(np.isfinite(h)):
                return normalize_mass(h)
        except Exception:
            continue
    return None


def construct_sequence():
    """Return the K step heights of h. Naive seed = uniform 1/2 (Psi = 0.5)."""
    warm = _load_warm_start()
    if warm is not None:
        return warm
    return normalize_mass(np.full(K, 0.5))


def run_construction():
    """Entry point for the evaluator. Returns the 1-D array of step heights."""
    return np.asarray(construct_sequence(), dtype=float).reshape(-1)


if __name__ == "__main__":
    h = run_construction()
    conv = np.correlate(h, 1 - h, mode="full")
    psi = float(np.max(conv) / len(h) * 2)
    print(f"K={len(h)}  sum={h.sum():.4f} (need {len(h)/2})  Psi={psi:.6f}  (record ~0.380868)")
