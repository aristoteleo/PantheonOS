"""Two figures for the follow-up studies: baseline fidelity (our SimpleTES port vs the authors'
engine) and the LabNotebook ablations -- one marker per arm, grouped by variant, per task.

    uv run python plot_parts.py --config CONFIG.json --out FIG.png --title "..."

CONFIG: {"panels": [{"task": <name in plot_tasks.PANELS>, "groups": [[label, glob, color], ...]}]}
Files are summary.json / partial_summary.json; a finished arm reports its first-read best, a
live arm (hollow) the history max -- the same conventions as plot_tasks.load.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from plot_budget import INK, SUB, EDGE_GRID
from plot_tasks import PANELS


def load(pattern):
    rows = []
    for f in sorted(glob.glob(os.path.expanduser(pattern))):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        if not isinstance(r, dict) or r.get("task") is None and r.get("history") is None:
            continue
        h = [x for x in (r.get("history") or []) if x.get("score") is not None
             and (x.get("valid", 1) or 0) > 0]
        done = not r.get("partial")
        if not h and not done:
            continue
        best = r.get("best_combined_score") if done else None
        if best is None:
            best = max([x["score"] for x in h] + [r.get("seed_combined_score") or 0.0])
        rows.append({"best": best, "done": done, "seed": r.get("seed_combined_score")})
    return rows


def panel(ax, spec):
    title, disp, invert, refs, fmt = PANELS[spec["task"]]
    title = spec.get("title") or title   # a config may name a panel (two runs of one task)
    groups = [(g[0], load(g[1]), g[2]) for g in spec["groups"]]
    all_y = [disp(r["best"]) for _, rows, _ in groups for r in rows if np.isfinite(disp(r["best"]))]
    for i, (label, rows, color) in enumerate(groups):
        ys = [disp(r["best"]) for r in rows if np.isfinite(disp(r["best"]))]
        dn = [r["done"] for r in rows if np.isfinite(disp(r["best"]))]
        xs = i + np.linspace(-0.18, 0.18, len(ys)) if len(ys) > 1 else [i]
        for x, y, d in zip(xs, ys, dn):
            ax.scatter([x], [y], s=95, color=color if d else "white", edgecolors=color, linewidths=1.7, zorder=3)
        if ys:
            ax.plot([i - 0.3, i + 0.3], [np.mean(ys)] * 2, color=color, lw=2.4, zorder=2)
            if fmt:
                for y, d in zip(sorted(ys, reverse=not invert), dn):
                    pass
        if ys and fmt:
            span = (max(all_y) - min(all_y)) or 1.0
            order = sorted(range(len(ys)), key=lambda j: (ys[j] if invert else -ys[j]))
            last = None
            for j in order:
                yl = ys[j]
                if last is not None:
                    yl = max(yl, last + 0.05 * span) if invert else min(yl, last - 0.05 * span)
                last = yl
                ax.text(i + 0.33, yl, fmt.format(ys[j]) + ("" if dn[j] else " ·"), size=6.8, color=color,
                        ha="left", va="center", family="monospace")
    if refs:
        for v, lab in (refs if isinstance(refs, list) else [refs]):
            if all_y and (min(all_y) - 0.5 * (max(all_y) - min(all_y) or 1) <= v <= max(all_y) + 0.5 * (max(all_y) - min(all_y) or 1)):
                ax.axhline(v, color=INK, lw=0.9, ls=":", alpha=0.7)
                ax.text(-0.45, v, lab, ha="left", va="bottom", size=7.5, color=SUB)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[0] for g in groups], size=8.5, rotation=20, ha="right")
    ax.set_xlim(-0.5, len(groups) - 0.5 + 0.6)
    ax.set_title(title, size=10.5, loc="left", color=INK)
    if invert:
        ax.invert_yaxis()
    ax.grid(axis="y", color=EDGE_GRID, lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return sum(len(rows) for _, rows, _ in groups), sum(r["done"] for _, rows, _ in groups for r in rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    n = len(cfg["panels"])
    fig, axes = plt.subplots(1, n, figsize=(max(5.2 * n, 11.0), 5.6), dpi=150, squeeze=False)
    fig.suptitle(a.title, size=15, weight="bold", x=0.03, ha="left")
    tot = done = 0
    for ax, spec in zip(axes[0], cfg["panels"]):
        t, d = panel(ax, spec)
        tot += t; done += d
    fig.text(0.03, 0.905, f"filled = finished arm · hollow = still running (best so far, lower bound; · after a label) · "
             f"bar = group mean · dotted = published reference   ({done}/{tot} arms finished)", size=9, color=SUB)
    fig.tight_layout(rect=[0, 0, 1, 0.89])
    fig.savefig(a.out)
    print(os.path.basename(a.out), f"{done}/{tot} arms finished")
