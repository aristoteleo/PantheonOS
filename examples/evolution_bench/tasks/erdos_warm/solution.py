"""Erdos Minimum Overlap -- construct a step function h: [0,2] -> [0,1].

This file is what evolution optimizes. ``run_construction()`` must return the K step heights
of h (equal-width steps tiling [0,2]) as a 1-D array. The evaluator scores

    Psi(h) = max_k  integral h(x)(1 - h(x+k)) dx        (LOWER is better)

subject to h in [0,1] and unit mass  <=>  sum(h) == K/2.

The naive seed below is the UNIFORM function h == 1/2, which scores the trivial Psi = 0.5.
Known-good constructions are very non-uniform -- near 0 at the edges, a plateau near the
centre, mirror-symmetric -- reaching Psi ~ 0.3809. DISCOVER such a shape: reason about which
translation k drives the max overlap and reshape h to push that worst case down, while keeping
h feasible (in [0,1], sum == K/2). K is yours to choose; finer K can represent better shapes.
"""
import numpy as np

K = 100


def run_construction():
    return np.full(K, 0.5)


if __name__ == "__main__":
    h = np.asarray(run_construction(), dtype=float)
    conv = np.correlate(h, 1 - h, mode="full")
    print(f"K={len(h)}  Psi={float(conv.max()) / len(h) * 2:.6f}")
