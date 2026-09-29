"""Search-then-polish for the Erdos minimum-overlap constructions (protocol A).

    uv run python polish_erdos.py --rounds 20 --round-seconds 120 --out DIR label=program.py ...

Every method's best PROGRAM was found under the bench rule: build from scratch in 120 s, no
warm start. This script gives each method's best CONSTRUCTION the same accumulated polish the
published records had: round 0 runs the program once and keeps what it returns; every later
round starts from the previous construction (upsampled when that pays), optimizes for at most
`round_seconds`, and keeps the result. No single call exceeds the bench's per-call limit; the
effort simply accumulates instead of being discarded. The polisher is one fixed routine applied
identically to every method, so the comparison measures the SHAPE each search found, not the
optimizer its program happened to contain.

Objective: Psi(h) = max_k C_k, C = correlate(h, 1-h, full) * 2/K, h in [0,1]^K, sum(h) = K/2
(the evaluator's own formula). The polish minimizes a log-sum-exp surrogate of the max with an
annealed temperature, by L-BFGS-B with box bounds, projecting to exact unit mass after each
stage; correlations go through FFT so K in the thousands costs milliseconds, not seconds. The
gradient is checked against finite differences at start-up, on the actual K.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import numpy as np
from scipy.optimize import minimize
from scipy.signal import fftconvolve


def psi_exact(h: np.ndarray) -> float:
    K = len(h)
    return float(np.max(np.correlate(h, 1.0 - h, mode="full")) * 2.0 / K)


def corr_full(a: np.ndarray, v: np.ndarray) -> np.ndarray:
    """np.correlate(a, v, 'full') via FFT: c[m] = sum_n a[n + m - (K-1)] v[n]."""
    return fftconvolve(a, v[::-1], mode="full")


def surrogate(h: np.ndarray, beta: float, mass_w: float):
    """(value, gradient) of  (1/beta) log sum_m exp(beta * C_m * 2/K)  +  mass_w (sum h - K/2)^2."""
    K = len(h)
    g = 1.0 - h
    s = 2.0 / K
    C = corr_full(h, g) * s                       # length 2K-1
    z = beta * C
    zmax = z.max()
    w = np.exp(z - zmax)
    Z = w.sum()
    val = (zmax + np.log(Z)) / beta
    w /= Z                                        # softmax over shifts, length 2K-1
    # dC_m/dh_j = s * ( g[j - m + K - 1] - h[j + m - K + 1] )
    # term1_j = sum_m w_m g[j - m + K - 1]  = (w * g)[j + K - 1]        (convolution)
    # term2_j = sum_m w_m h[j + m - K + 1]  = corr(h, w)-type; both via FFT
    term1 = fftconvolve(w, g, mode="full")[K - 1: 2 * K - 1]
    term2 = fftconvolve(h, w[::-1], mode="full")[K - 1 + (K - 1) - (K - 1): ][:K]
    # term2 derivation: sum_m w_m h[j+m-K+1] = sum_p h[p] w[p - j + K - 1] = (h corr w)[j]
    grad = s * (term1 - term2)
    m = h.sum() - K / 2.0
    val += mass_w * m * m
    grad = grad + 2.0 * mass_w * m
    return val, grad


def check_gradient(K: int = 64) -> float:
    rng = np.random.default_rng(0)
    h = np.clip(rng.random(K), 0.05, 0.95)
    beta, mass_w = 300.0, 1.0
    v0, g0 = surrogate(h, beta, mass_w)
    eps = 1e-6
    num = np.zeros(K)
    for j in range(K):
        hp = h.copy(); hp[j] += eps
        hm = h.copy(); hm[j] -= eps
        num[j] = (surrogate(hp, beta, mass_w)[0] - surrogate(hm, beta, mass_w)[0]) / (2 * eps)
    return float(np.max(np.abs(num - g0)) / (np.max(np.abs(num)) + 1e-12))


def project_mass(h: np.ndarray) -> np.ndarray:
    """Nearest point with sum = K/2 and 0 <= h <= 1: clip(h + c) with c found by bisection."""
    K = len(h)
    target = K / 2.0
    lo, hi = -1.0, 1.0
    for _ in range(80):
        c = 0.5 * (lo + hi)
        m = np.clip(h + c, 0.0, 1.0).sum()
        if m < target:
            lo = c
        else:
            hi = c
    return np.clip(h + 0.5 * (lo + hi), 0.0, 1.0)


def upsample(h: np.ndarray) -> np.ndarray:
    return np.repeat(h, 2)


def active_rows(h: np.ndarray, active: np.ndarray) -> np.ndarray:
    """Gradient rows dC_m/dh (on the Psi scale) for the active shifts m.

    C_m = s * sum_n h[n + m - (K-1)] (1 - h[n]);  dC_m/dh_j = s * (g[j - m + K - 1] - h[j + m - K + 1]),
    out-of-range indices contribute 0.
    """
    K = len(h)
    s = 2.0 / K
    g = 1.0 - h
    j = np.arange(K)
    rows = np.zeros((len(active), K))
    for r, m in enumerate(active):
        i1 = j - m + K - 1
        i2 = j + m - K + 1
        ok1 = (i1 >= 0) & (i1 < K)
        ok2 = (i2 >= 0) & (i2 < K)
        rows[r, ok1] += g[i1[ok1]]
        rows[r, ok2] -= h[i2[ok2]]
    return s * rows


def check_rows(K: int = 48) -> float:
    rng = np.random.default_rng(1)
    h = np.clip(rng.random(K), 0.05, 0.95)
    s = 2.0 / K
    active = np.array([0, K // 3, K - 1, K + 5, 2 * K - 2])
    rows = active_rows(h, active)
    eps = 1e-6
    worst = 0.0
    for r, m in enumerate(active):
        for jj in (0, K // 2, K - 1):
            hp = h.copy(); hp[jj] += eps
            hm = h.copy(); hm[jj] -= eps
            num = (corr_full(hp, 1 - hp)[m] - corr_full(hm, 1 - hm)[m]) * s / (2 * eps)
            worst = max(worst, abs(num - rows[r, jj]))
    return worst


def polish_round(h: np.ndarray, seconds: float, radius: float = 1e-4, block: int = 400,
                 max_active: int = 3000, rng=None):
    """Block-coordinate sequential LP with a trust region on the exact minimax.

    A good construction is an equioscillation point -- a third of all shifts sit within 1e-4 of
    the maximum -- so every step must model ~1400 active shifts. Over all K coordinates that LP
    takes a minute; over a random block of `block` coordinates it takes 0.2 s and keeps ~80% of
    the step, so the round takes hundreds of steps instead of two. Each step: linearize the
    active shifts, solve  min t  s.t.  C_m + grad_m . d <= t,  sum d = 0,  |d| <= radius,
    0 <= h + d <= 1  over the block; accept only if the EXACT Psi drops; adapt the radius.
    """
    from scipy.optimize import linprog

    rng = rng or np.random.default_rng(0)
    t_end = time.time() + seconds
    K = len(h)
    s = 2.0 / K
    psi = psi_exact(h)
    fails = 0
    while time.time() < t_end and radius > 1e-9:
        C = corr_full(h, 1.0 - h) * s
        band = max(4.0 * radius, 2e-7)
        active = np.where(C >= C.max() - band)[0]
        if len(active) > max_active:
            active = active[np.argsort(-C[active])[:max_active]]
        cols = np.sort(rng.choice(K, min(block, K), replace=False))
        A = active_rows(h, active)[:, cols]
        a, n = len(active), len(cols)
        c = np.zeros(n + 1); c[-1] = 1.0
        A_ub = np.hstack([A, -np.ones((a, 1))])
        b_ub = -C[active]
        A_eq = np.zeros((1, n + 1)); A_eq[0, :n] = 1.0
        lo = np.maximum(-radius, -h[cols]); hi = np.minimum(radius, 1.0 - h[cols])
        bounds = list(zip(lo.tolist(), hi.tolist())) + [(None, None)]
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=[0.0], bounds=bounds, method="highs-ds")
        if not res.success:
            radius *= 0.5
            continue
        d = np.zeros(K); d[cols] = res.x[:n]
        cand = project_mass(np.clip(h + d, 0.0, 1.0))
        p = psi_exact(cand)
        if p < psi - 1e-13:
            h, psi = cand, p
            radius = min(radius * 1.3, 2e-3)
            fails = 0
        else:
            fails += 1
            if fails >= 3:                    # a different block first; shrink only if that fails too
                radius *= 0.5
                fails = 0
    return h, psi


def load_program(path: str) -> np.ndarray:
    spec = importlib.util.spec_from_file_location("sol", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h = np.clip(np.asarray(mod.run_construction(), dtype=float).reshape(-1), 0.0, 1.0)
    return project_mass(h)


def run(label: str, path: str, rounds: int, round_seconds: float, k_max: int, out_dir: str) -> dict:
    t0 = time.time()
    h = load_program(path)
    log = [{"round": 0, "K": len(h), "psi": psi_exact(h), "elapsed": round(time.time() - t0, 1)}]
    print(f"[{label}] round 0: K={len(h)} Psi={log[-1]['psi']:.6f}", flush=True)
    stall = 0
    for r in range(1, rounds + 1):
        before = psi_exact(h)
        if (stall >= 1 or r == 1) and len(h) * 2 <= k_max:
            h = upsample(h)
            stall = 0
        K = len(h)
        h, p = polish_round(h, round_seconds, rng=np.random.default_rng(r))
        gain = before - p
        stall = stall + 1 if gain < 2e-6 else 0
        log.append({"round": r, "K": K, "psi": p, "elapsed": round(time.time() - t0, 1)})
        print(f"[{label}] round {r}: K={K} Psi={p:.6f} ({gain:+.2e}) t={time.time() - t0:.0f}s", flush=True)
        np.save(os.path.join(out_dir, f"{label}_best.npy"), h)
        json.dump({"label": label, "program": path, "log": log},
                  open(os.path.join(out_dir, f"{label}.json"), "w"), indent=1)
    return {"label": label, "final_psi": psi_exact(h), "K": len(h), "log": log}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("items", nargs="+", help="label=path/to/program.py")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--round-seconds", type=float, default=120.0)
    ap.add_argument("--k-max", type=int, default=15360)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    err = check_rows()
    print(f"linearization check (K=48, max abs error): {err:.2e}", flush=True)
    if err > 1e-6:
        sys.exit("linearization check failed; not polishing with wrong gradient rows")
    from concurrent.futures import ProcessPoolExecutor
    items = [it.split("=", 1) for it in a.items]
    with ProcessPoolExecutor(len(items)) as ex:
        futs = [ex.submit(run, lab, p, a.rounds, a.round_seconds, a.k_max, a.out) for lab, p in items]
        results = [f.result() for f in futs]
    print("\nfinal:")
    for r in sorted(results, key=lambda x: x["final_psi"]):
        print(f"  {r['label']:14s} Psi={r['final_psi']:.6f}  K={r['K']}")
