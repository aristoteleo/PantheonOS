"""Method-comparison figures: best-so-far curves + final-best table, per task.

    uv run python plot_compare.py --collected w4.json [w2.json w3b.json ...] --out ~/Downloads

One figure per task: LEFT the best-so-far evolution curve (x = measured programs, thin lines =
seeds, thick = per-method mean of best-so-far), RIGHT the final bests as dots. Curves come from
each run's `history` (every measured program in order); the seed's own score anchors the curve
at x=0 where the store recorded it.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

INK, SUB, MUTED = "#1f2328", "#57606a", "#8c959f"
COLORS = {"hypothesis_bandit": "#8250df", "agent_map_elites": "#0969da", "simpletes": "#bc4c00"}
LABELS = {"hypothesis_bandit": "HypothesisBandit", "agent_map_elites": "AgentMapElites",
          "simpletes": "SimpleTES"}

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial"],
    "text.color": INK, "axes.edgecolor": MUTED, "axes.labelcolor": SUB,
    "xtick.color": SUB, "ytick.color": SUB,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def method_of(row):
    m = (row.get("search") or {}).get("method") or row.get("method")
    if m:
        return "hypothesis_bandit" if m == "pantheon_evo" else m
    arm = row["arm"]
    for key in ("hypothesis_bandit", "pantheon_evo", "mapelites", "agent_map_elites",
                "simpletes"):
        if key in arm:
            return {"mapelites": "agent_map_elites", "pantheon_evo": "hypothesis_bandit"}.get(key, key)
    return "?"


def task_of(row):
    arm = row["arm"]
    for t in ("erdos", "packing", "circle_packing", "ahc039"):
        if t in arm:
            return "circle_packing" if t == "packing" else t
    return row.get("task", "?")


def best_so_far(row):
    hist = row.get("history") or []
    ys, best = [], None
    seed = row.get("seed_own")
    if seed is not None:
        best = seed
        ys.append(best)
    for h in hist:
        s = h.get("score")
        v = h.get("valid", 1)
        if s is None or (v is not None and v <= 0):
            continue
        best = s if best is None else max(best, s)
        ys.append(best)
    return ys


def main(paths, out):
    rows = []
    for p in paths:
        rows += json.load(open(p))
    by_task = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("best") is None:
            continue
        by_task[task_of(r)][method_of(r)].append(r)

    for task, methods in sorted(by_task.items()):
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.2), dpi=150,
                                     gridspec_kw={"width_ratios": [2.1, 1]})
        fig.suptitle(f"{task} — best-so-far under one harness, same model, matched budget",
                     size=19, weight="bold", x=0.055, ha="left")
        for m, rs in sorted(methods.items()):
            col = COLORS.get(m, MUTED)
            curves = [best_so_far(r) for r in rs]
            curves = [c for c in curves if len(c) >= 2]
            for c in curves:
                a1.plot(range(len(c)), c, color=col, lw=1.0, alpha=0.35)
            if curves:
                L = max(len(c) for c in curves)
                padded = np.array([c + [c[-1]] * (L - len(c)) for c in curves])
                a1.plot(range(L), padded.mean(axis=0), color=col, lw=2.6,
                        label=f"{LABELS.get(m, m)} (n={len(rs)})")
            finals = [r["best"] for r in rs]
            x = list(COLORS).index(m) if m in COLORS else 3
            a2.scatter([x] * len(finals), finals, s=80, color=col, alpha=0.8, zorder=3)
            a2.hlines(np.mean(finals), x - 0.22, x + 0.22, color=INK, lw=2.2, zorder=4)
        a1.set_xlabel("measured programs (in order)")
        a1.set_ylabel("best combined_score so far")
        a1.legend(frameon=False, fontsize=11.5, loc="lower right")
        a2.set_xticks(range(len(COLORS)))
        a2.set_xticklabels([LABELS[m] for m in COLORS], size=10.5, rotation=12)
        a2.set_title("final best per run", size=12, color=SUB, loc="left")
        for ax in (a1, a2):
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
        fig.tight_layout(rect=[0, 0, 1, 0.92])
        fig.savefig(out / f"compare_{task}.png")
        plt.close(fig)
        print(f"compare_{task}.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--collected", nargs="+", required=True)
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    a = ap.parse_args()
    outdir = Path(a.out).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    main(a.collected, outdir)
