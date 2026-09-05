"""One figure: every method on every task, one marker per arm.

    uv run python plot_tasks.py --ahc 'w6/*.json' --erdos 'ep/erdos-wave1_*.json' \\
        --packing 'ep/packing-wave1_*.json' --out ~/Downloads --tag all
    uv run python plot_tasks.py --panel circle_packing_32='ae/p32_*.json' \\
        --panel autocorr_first='ae/c1_*.json' --tag ae   # any tasks known to PANELS

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
def _inv(b):
    return 1.0 / b if b else float("nan")


# task -> (title, display transform of the harness score, invert the axis, reference lines, label fmt)
# Reference lines name the system that holds each published value, record first.
PANELS = {
    "ahc039": ("AHC039 (official points / case)", lambda b: b * 1500.0, False, None, None),
    "circle_packing": ("circle packing n=26 (sum of radii)", lambda b: b, False,
                       [(2.635983, "record 2.635983 · TTT-Discover, ours")], "{:.6f}"),
    "erdos": ("Erdős  Ψ  (lower is better, axis inverted)", lambda b: 1 - b, True,
              [(0.380909, "our record 0.380909 (warm-off) · AlphaEvolve 0.380924")], "{:.6f}"),
    # AlphaEvolve-suite (SimpleTES contract): the harness score is converted back to the paper's unit
    "circle_packing_32": ("circle packing n=32 (sum of radii)", lambda b: b, False,
                          [(2.939572, "record 2.939572 · AlphaEvolve V2, TTT-Discover, SimpleTES"),
                           (2.937944, "AlphaEvolve v1 2.937944")], "{:.6f}"),
    "autocorr_first": ("C1 upper bound (lower is better, axis inverted)", _inv, True,
                       [(1.50287, "record 1.50287 · TTT-Discover"),
                        (1.50314, "ThetaEvolve 1.50314 · AlphaEvolve V2 1.50317"),
                        (1.5053, "AlphaEvolve v1 1.5053"),
                        (1.50973, "best human 1.50973")], "{:.5f}"),
    "autocorr_second": ("C2 lower bound", lambda b: b, False,
                        [(0.9627, "record 0.9627 · SimpleTES"),
                         (0.9610, "AlphaEvolve V2 0.9610"),
                         (0.9591, "TTT-Discover 0.9591"),
                         (0.9469, "ThetaEvolve 0.9469"),
                         (0.9015, "best human 0.9015"),
                         (0.8962, "AlphaEvolve v1 0.8962")], "{:.4f}"),
    "autocorr_third": ("C3 upper bound (lower is better, axis inverted)",
                       lambda b: 1.4556427953745406 / b if b else float("nan"), True,   # the evaluator's reference constant
                       [(1.453675, "record 1.45368 · SimpleTES"), (1.454555, "TTT-Discover 1.45456"),
                        (1.4556, "AlphaEvolve 1.4556")], "{:.5f}"),
    "sums_diffs": ("sums vs differences  C(A)", lambda b: b, False, [(1.1449, "SimpleTES 1.1449")], "{:.4f}"),
    "hadamard29": ("Hadamard order 29  |det| / reference", lambda b: b, False, None, "{:.4f}"),
    # AlphaEvolve-suite (CodeEvolve instances): score = benchmark ratio, 1.0 = AlphaEvolve
    **{t: (f"{lab}  (ratio to AlphaEvolve)", (lambda b: b), False, [(1.0, "AlphaEvolve = 1.0")], "{:.4f}")
       for t, lab in [("kissing11", "kissing number, d=11"), ("heilbronn_tri11", "Heilbronn triangle, n=11"),
                      ("heilbronn_conv13", "Heilbronn convex, n=13"), ("minmax2d16", "min/max distance ratio, n=16"),
                      ("packing_rect21", "circles in a rectangle, n=21"), ("hexagon11", "hexagons in a hexagon, n=11")]},
}


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
        # a summary.json is a finished arm even when items_run is 0 -- an arm resumed after a
        # preemption may end at once on its restored budget; only partials are still running
        done = not r.get("partial")
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


def panel(ax, rows, disp, invert, ref, ylabel, label_fmt=None):
    by_m = defaultdict(list)
    for r in rows:
        if r["method"] in COLORS and np.isfinite(disp(r["best"])):
            by_m[r["method"]].append(r)
    ms = [m for m in ORDER if m in by_m]
    flip = invert
    all_y = [disp(r["best"]) for m in ms for r in by_m[m]]
    span = (max(all_y) - min(all_y)) or 1.0
    for i, m in enumerate(ms):
        ys = [disp(r["best"]) for r in by_m[m]]
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
        refs = ref if isinstance(ref, list) else [ref]
        for v, _ in refs:
            ax.axhline(v, color=INK, lw=1.0, ls=":", alpha=0.7)
        # one label per line, named for its holder; labels are pushed apart where lines sit
        # within a few percent of the panel's span (the 2026 systems cluster tightly)
        vals = all_y + [v for v, _ in refs]
        step = 0.04 * ((max(vals) - min(vals)) or 1.0)
        last = None
        for v, lab in sorted(refs, key=lambda t: t[0], reverse=not flip):   # top of the panel first
            y = v if last is None else (max(v, last + step) if flip else min(v, last - step))
            last = y
            ax.text(-0.45, y, lab, ha="left", va="bottom", size=8.3, color=SUB)   # just above its line
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
    ap.add_argument("--ahc", help="summaries glob for AHC039")
    ap.add_argument("--packing", help="summaries glob for circle packing n=26")
    ap.add_argument("--erdos", help="summaries glob for Erdős")
    ap.add_argument("--panel", action="append", default=[], metavar="TASK=GLOB",
                    help="any task known to PANELS; repeatable, in panel order")
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    ap.add_argument("--tag", default="all")
    ap.add_argument("--title", default="Four methods × three tasks — best per arm, and what it cost")
    a = ap.parse_args()
    panels = [(t, g) for t, g in (("ahc039", a.ahc), ("circle_packing", a.packing), ("erdos", a.erdos)) if g]
    for spec in a.panel:
        task, pat = spec.split("=", 1)
        if task not in PANELS:
            raise SystemExit(f"unknown task {task!r}; known: {', '.join(sorted(PANELS))}")
        panels.append((task, pat))
    if not panels:
        raise SystemExit("nothing to draw: pass --ahc/--packing/--erdos or --panel TASK=GLOB")
    data = {task: load(pat) for task, pat in panels}

    fig, axes = plt.subplots(2, len(panels), figsize=(max(5.6 * len(panels), 11.0), 8.2), dpi=150, squeeze=False,
                             gridspec_kw={"height_ratios": [3, 1.15]})
    fig.suptitle(a.title, size=17, weight="bold", x=0.04, ha="left")
    for j, (task, _) in enumerate(panels):
        title, disp, invert, ref, fmt = PANELS[task]
        ms, by_m = panel(axes[0][j], data[task], disp, invert, ref, title, label_fmt=fmt)
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
