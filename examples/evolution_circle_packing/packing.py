"""Circle packing in the unit square.

Place N = 26 non-overlapping circles inside the unit square [0, 1] x [0, 1] to
MAXIMIZE the sum of their radii. This is the classic AlphaEvolve / OpenEvolve
benchmark; the state of the art for N = 26 is a sum of radii of about 2.635.

This file is what evolution optimizes. Improve ``construct_packing()`` — the naive
concentric-ring layout below leaves a lot on the table. ``run_packing()`` is the
entry point the evaluator calls; keep its return signature.

WARM START: if a ``warm_start.json`` sits next to this file, it holds the best
layout the PARENT generation produced. The framework manages that file; load it and
POLISH from it (cheap, reliably climbs) instead of re-deriving a good layout from
scratch, so numerical refinement accumulates across generations.
"""
import json
import os

import numpy as np

N = 26


def construct_packing():
    """Return (centers, radii) for N circles in the unit square.

    Naive concentric-ring layout: one circle in the middle, a ring of 8, then a
    ring of 17. Centers are fixed here and radii are grown to the largest
    non-overlapping values. Better center placement + radius optimization (e.g.
    move the centers then recompute, or run a real optimizer) pushes the sum of
    radii toward — and past — 2.635.
    """
    centers = [[0.5, 0.5]]                                  # 1 center circle
    for k in range(8):                                     # inner ring of 8
        a = 2 * np.pi * k / 8
        centers.append([0.5 + 0.28 * np.cos(a), 0.5 + 0.28 * np.sin(a)])
    for k in range(N - 9):                                 # outer ring of 17
        a = 2 * np.pi * k / (N - 9)
        centers.append([0.5 + 0.45 * np.cos(a), 0.5 + 0.45 * np.sin(a)])
    centers = np.clip(np.asarray(centers, dtype=float), 0.01, 0.99)
    radii = compute_max_radii(centers)
    return centers, radii


def compute_max_radii(centers):
    """Largest non-overlapping radii for fixed centers.

    Start each radius at the distance to the nearest square border, then repeatedly
    shrink any overlapping pair proportionally until the packing is valid.
    """
    n = len(centers)
    radii = np.array([min(x, 1 - x, y, 1 - y) for x, y in centers], dtype=float)
    for _ in range(200):
        changed = False
        for i in range(n):
            for j in range(i + 1, n):
                d = float(np.linalg.norm(centers[i] - centers[j]))
                if radii[i] + radii[j] > d + 1e-12:
                    s = d / (radii[i] + radii[j])
                    radii[i] *= s
                    radii[j] *= s
                    changed = True
        if not changed:
            break
    return radii


def _load_warm_start():
    """Load the parent's best layout from warm_start.json (next to this file / cwd), or None."""
    for path in ("warm_start.json", os.path.join(os.path.dirname(__file__), "warm_start.json")):
        try:
            with open(path) as f:
                d = json.load(f)
            c = np.asarray(d["centers"], dtype=float)
            if c.shape == (N, 2) and np.all(np.isfinite(c)):
                return np.clip(c, 0.001, 0.999)
        except Exception:
            continue
    return None


def polish(centers, rounds=60):
    """Deterministic multi-scale coordinate descent: nudge each circle along the axes
    at shrinking step sizes, keeping any move that raises the (feasible) sum of radii."""
    c = np.asarray(centers, dtype=float).copy()
    best = float(np.sum(compute_max_radii(c)))
    for _ in range(rounds):
        improved = False
        for i in range(len(c)):
            for step in (0.02, 0.008, 0.003, 0.001):
                for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step)):
                    cand = c.copy()
                    cand[i, 0] = min(0.999, max(0.001, cand[i, 0] + dx))
                    cand[i, 1] = min(0.999, max(0.001, cand[i, 1] + dy))
                    s = float(np.sum(compute_max_radii(cand)))
                    if s > best + 1e-12:
                        best, c, improved = s, cand, True
                        break
                else:
                    continue
                break
        if not improved:
            break
    return c


def run_packing():
    """Entry point for the evaluator. Returns (centers, radii, sum_of_radii).

    Take the best of: a from-scratch construction and (if a warm start exists) a polish
    of the parent's best layout. The warm-started branch is what lets refinement compound
    generation over generation.
    """
    centers, radii = construct_packing()
    best_c, best_s = centers, float(np.sum(radii))

    warm = _load_warm_start()
    if warm is not None:
        wc = polish(warm)
        ws = float(np.sum(compute_max_radii(wc)))
        if ws > best_s:
            best_c, best_s = wc, ws

    radii = compute_max_radii(best_c)
    return best_c, radii, float(np.sum(radii))


if __name__ == "__main__":
    c, r, s = run_packing()
    print(f"N={len(r)} circles, sum_of_radii={s:.4f} (AlphaEvolve record ~2.635)")
