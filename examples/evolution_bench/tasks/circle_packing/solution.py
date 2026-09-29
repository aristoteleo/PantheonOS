"""Circle packing in the unit square, n = 26.

This file is what evolution optimizes. ``run_packing()`` must return ``(centers, radii)`` with
``centers`` of shape (26, 2) and ``radii`` of shape (26,): 26 non-overlapping circles inside
[0,1] x [0,1], maximising the SUM of the radii.

The naive seed below fixes a concentric-ring layout and grows radii to the largest
non-overlapping values; it scores ~1.80. The published record is 2.635983 — better centre
placement plus real numerical optimisation (jointly move centres AND radii) closes the gap.
Compute inside ``run_packing()`` is allowed; keep the runtime under the evaluator's timeout.
"""
import numpy as np

N = 26


def compute_max_radii(centers):
    """Largest non-overlapping radii for FIXED centers: start at the border distance, then
    shrink overlapping pairs proportionally until valid."""
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


def run_packing():
    centers = [[0.5, 0.5]]
    for k in range(8):
        a = 2 * np.pi * k / 8
        centers.append([0.5 + 0.28 * np.cos(a), 0.5 + 0.28 * np.sin(a)])
    for k in range(N - 9):
        a = 2 * np.pi * k / (N - 9)
        centers.append([0.5 + 0.45 * np.cos(a), 0.5 + 0.45 * np.sin(a)])
    centers = np.clip(np.asarray(centers, dtype=float), 0.01, 0.99)
    return centers, compute_max_radii(centers)


if __name__ == "__main__":
    c, r = run_packing()
    print(f"sum_radii = {float(np.sum(r)):.6f}")
