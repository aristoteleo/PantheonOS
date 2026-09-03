"""One figure: every method on every task, one marker per arm.

    uv run python plot_tasks.py --ahc 'w6/*.json' --erdos 'ep/erdos-wave1_*.json' \\
        --packing 'ep/packing-wave1_*.json' --out ~/Downloads --tag all

Three panels, one per task, x = method, y = the task's own display unit (official points per
case; sum of radii; Psi with the axis inverted so up is better everywhere). Filled markers are
finished arms, hollow ones are still running (their best so far, a lower bound). A short bar
marks the method mean over its arms; a dotted line marks the task's record. A second row shows
what each arm SPENT in tokens, so the score panels can be read against their price.

Inputs are `summary.json` / `partial_summary.json` files; for a running arm both may exist and
the partial (fresher) wins. Method is read from the summary, or inferred from the filename.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from plot_budget import COLORS, LABELS, INK, SUB, EDGE_GRID, infer_method, method_of

ORDER = ["hypothesis_bandit", "agent_map_elites", "simpletes", "lab_notebook"]
PANELS = [
    # (task, title, scale, flip, record, per-replicate label format or None)
    ("ahc039", "AHC039 (official points / case)", 1500.0, False, None, None),
    ("circle_packing", "circle packing (sum of radii)", 1.0, False, (2.635983, "record"), "{:.6f}"),
    ("erdos", "Erdős  Ψ  (lower is better, axis inverted)", 1.0, True,
     (0.380909, "our record"), "{:.6f}"),
]


def load(pattern: str):
    """Rows keyed by arm; a `_p` partial replaces a summary with no items."""
    rows = {}
    for f in sorted(glob.glob(os.path.expanduser(pattern))):
        base = os.path.basename(f)
        if base.endswith("_store.json") or "collected" in base:
            continue
        try:
            r = json.load(open(f))
        except Exception:
            continue
        if not isinstance(r, dict):
            continue
        arm = base.replace("_p.json", "").replace(".json", "")
        h = [x for x in (r.get("history") or [])
             if x.get("score") is not None and x.get("fidelity", "full") == "full"
             and (x.get("valid", 1) or 0) > 0]
        if not h:
            continue
        m = r.get("method")
        if m is None:
            m = infer_method(base)
        else:
            m = method_of(r)
        done = bool(r.get("items_run", 0) > 0) and not r.get("partial")
        # finished arms report the store-derived (first-read) best; live ones the history max
        best = r.get("best_combined_score") if done else None
        if best is None:
            # the seed is the incumbent: a live arm whose only children so far are worse
            # still holds the seed, and its best-so-far is the seed's score
            best = max([x["score"] for x in h] + [r.get("seed_combined_score") or 0.0])
        lu = r.get("llm_usage") or {}
        rec = {"method": m, "best": best, "done": done,
               "tokens": (lu.get("prompt_tokens", 0) + lu.get("completion_tokens", 0)) / 1e6,
               "calls": lu.get("calls", 0)}
        prev = rows.get(arm)
        if prev is None or (not prev["done"] and len(h) >= 0):
            if prev is None or not prev["done"]:
                rows[arm] = rec
    return list(rows.values())


def panel(ax, rows, scale, flip, ref, ylabel, label_fmt=None):
    by_m = defaultdict(list)
    for r in rows:
        if r["method"] in COLORS:
            by_m[r["method"]].append(r)
    ms = [m for m in ORDER if m in by_m]
    all_y = [((1 - r["best"]) if flip else r["best"] * scale) for m in ms for r in by_m[m]]
    span = (max(all_y) - min(all_y)) or 1.0
    for i, m in enumerate(ms):
        ys = [(1 - r["best"]) if flip else r["best"] * scale for r in by_m[m]]
        dn = [r["done"] for r in by_m[m]]
        xs = i + np.linspace(-0.16, 0.16, len(ys)) if len(ys) > 1 else [i]
        for x, y, d in zip(xs, ys, dn):
            ax.scatter([x], [y], s=110, color=COLORS[m] if d else "white",
                       edgecolors=COLORS[m], linewidths=1.8, zorder=3)
        ax.plot([i - 0.28, i + 0.28], [np.mean(ys)] * 2, color=COLORS[m], lw=2.6, zorder=2)
        if label_fmt:
            # one value per replicate, listed to the right of the cluster; when values sit
            # within a few percent of the axis span the labels are pushed apart so tight
            # clusters (the record-matching arms) stay legible
            step = 0.045 * span
            order = sorted(range(len(ys)), key=lambda j: -ys[j])       # top first (better on top)
            if flip:
                order = sorted(range(len(ys)), key=lambda j: ys[j])     # inverted axis: smaller Psi is higher
            last = None
            for j in order:
                y_lab = ys[j]
                if last is not None:
                    if flip:
                        y_lab = max(y_lab, last + step)
                    else:
                        y_lab = min(y_lab, last - step)
                last = y_lab
                ax.text(i + 0.31, y_lab, label_fmt.format(ys[j]) + ("" if dn[j] else " ·"),
                        size=7.2, color=COLORS[m], ha="left", va="center", family="monospace")
    if ref:
        ax.axhline(ref[0], color=INK, lw=1.0, ls=":", alpha=0.7)
        ax.text(-0.45, ref[0], ref[1], ha="left", size=9, color=SUB,
                va="top" if flip else "bottom")
    ax.set_xticks(range(len(ms)))
    ax.set_xticklabels([LABELS[m].replace("Hypothesis", "Hypothesis\n").replace(
        "AgentMap", "AgentMap\n") for m in ms], size=9.5)
    if label_fmt:
        ax.set_xlim(-0.5, len(ms) - 0.5 + 0.45)
    ax.set_title(ylabel, size=11, loc="left", color=INK)
    if flip:
        ax.invert_yaxis()
    ax.grid(axis="y", color=EDGE_GRID, lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    return ms, by_m


def cost_panel(ax, ms, by_m):
    for i, m in enumerate(ms):
        ts = [r["tokens"] for r in by_m[m]]
        dn = [r["done"] for r in by_m[m]]
        xs = i + np.linspace(-0.16, 0.16, len(ts)) if len(ts) > 1 else [i]
        for x, t, d in zip(xs, ts, dn):
            ax.scatter([x], [t], s=70, color=COLORS[m] if d else "white",
                       edgecolors=COLORS[m], linewidths=1.5, zorder=3)
        ax.plot([i - 0.28, i + 0.28], [np.mean(ts)] * 2, color=COLORS[m], lw=2.2, zorder=2)
    ax.set_xticks(range(len(ms)))
    ax.set_xticklabels([""] * len(ms))
    ax.set_ylabel("LLM tokens spent (M)", size=9.5, color=SUB)
    ax.grid(axis="y", color=EDGE_GRID, lw=0.6, alpha=0.6)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ahc", required=True)
    ap.add_argument("--packing", required=True)
    ap.add_argument("--erdos", required=True)
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    ap.add_argument("--tag", default="all")
    ap.add_argument("--title", default="Four methods × three tasks — best per arm, and what it cost")
    a = ap.parse_args()
    data = {"ahc039": load(a.ahc), "circle_packing": load(a.packing), "erdos": load(a.erdos)}

    fig, axes = plt.subplots(2, 3, figsize=(15, 8.2), dpi=150,
                             gridspec_kw={"height_ratios": [3, 1.15]})
    fig.suptitle(a.title, size=17, weight="bold", x=0.04, ha="left")
    for j, (task, ylabel, scale, flip, ref, fmt) in enumerate(PANELS):
        ms, by_m = panel(axes[0][j], data[task], scale, flip, ref, ylabel, label_fmt=fmt)
        cost_panel(axes[1][j], ms, by_m)
    n_live = sum(1 for rs in data.values() for r in rs if not r["done"])
    fig.text(0.04, 0.925,
             "filled = finished arm · hollow = still running (best so far, a lower bound; · after a label) · "
             "bar = method mean · dotted = record"
             + (f"   ({n_live} arms still running)" if n_live else ""),
             size=10, color=SUB)
    fig.tight_layout(rect=[0, 0, 1, 0.91])
    out = os.path.join(os.path.expanduser(a.out), f"methods_tasks_{a.tag}.png")
    fig.savefig(out)
    print(os.path.basename(out))
    for task, rs in data.items():
        print(f"  {task}: {len(rs)} arms ({sum(r['done'] for r in rs)} done)")
