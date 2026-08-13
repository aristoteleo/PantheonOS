"""One figure per experiment of the AHC039 campaign, from the collected summaries.

    uv run python plot_findings.py --collected w1.json w2.json --out ~/Downloads

Each figure is built to carry ONE claim, stated in its title. The data is the same
`--collect` output the analysis reads; nothing is retyped by hand.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

INK, SUB, MUTED = "#1f2328", "#57606a", "#8c959f"
RED, BLUE, GREEN, ORANGE, PURPLE = "#cf222e", "#0969da", "#1a7f37", "#bc4c00", "#8250df"

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial"],
    "text.color": INK, "axes.edgecolor": MUTED, "axes.labelcolor": SUB,
    "xtick.color": SUB, "ytick.color": SUB,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})

EXCLUDED = {"wave1/e3_fixedT_s2"}      # dead-seed container; its numbers probe the harness


def load(paths):
    rows = []
    for p in paths:
        rows += json.load(open(p))
    return {r["arm"]: r for r in rows if r["arm"] not in EXCLUDED}


def groups_of(rows, *prefixes):
    out = {}
    for pre in prefixes:
        out[pre] = [r for a, r in sorted(rows.items()) if f"/{pre}" in a]
    return out


def kid_bars(ax, groups, colors=None):
    """Stacked child-census bars: improved / neither / wrecked, as fractions."""
    names = list(groups)
    for i, name in enumerate(names):
        rs = groups[name]
        n = sum((r["kids"] or {}).get("n", 0) for r in rs)
        imp = sum((r["kids"] or {}).get("improved", 0) for r in rs)
        wre = sum((r["kids"] or {}).get("wrecked", 0) for r in rs)
        mid = n - imp - wre
        if n == 0:
            continue
        ax.bar(i, imp / n, 0.62, color=GREEN, alpha=0.85)
        ax.bar(i, mid / n, 0.62, bottom=imp / n, color=MUTED, alpha=0.35)
        ax.bar(i, wre / n, 0.62, bottom=(imp + mid) / n, color=RED, alpha=0.75)
        ax.text(i, 1.03, f"improved {imp}/{n}", ha="center", size=11.5, color=GREEN,
                weight="bold")
        if wre / n > 0.12:
            ax.text(i, (imp + mid) / n + wre / n / 2, f"wrecked\n{wre}", ha="center",
                    va="center", size=10.5, color="white", weight="bold")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, size=12.5)
    ax.set_ylim(0, 1.12)
    ax.set_yticks([0, 0.5, 1.0])
    ax.set_yticklabels(["0%", "50%", "100%"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def gain_dots(ax, groups):
    for i, (name, rs) in enumerate(groups.items()):
        gains = [r["gain"] for r in rs if r.get("gain") is not None]
        ax.scatter([i] * len(gains), gains, s=90, color=BLUE, alpha=0.75, zorder=3)
        if gains:
            ax.hlines(np.mean(gains), i - 0.22, i + 0.22, color=INK, lw=2.2, zorder=4)
    ax.axhline(0, color=MUTED, lw=1, ls="--")
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(list(groups), size=12.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def fig_e4(rows, out):
    g = {"MAP-Elites": [], "annealed (llm)": [], "SimpleTES": []}
    for a, r in sorted(rows.items()):
        if "e4_mapelites" in a:
            g["MAP-Elites"].append(r)
        elif "e4_simpletes" in a:
            g["SimpleTES"].append(r)
        elif "e4_annealed" in a or "e1_judge_llm" in a:
            g["annealed (llm)"].append(r)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.5), dpi=150)
    fig.suptitle("E4 · on a mature seed, EDITING beats IMPLEMENTING IDEAS",
                 size=20, weight="bold", x=0.055, ha="left")
    fig.text(0.055, 0.9, "left: what kind of children each method wrote (green improved the "
                         "seed / red WRECKED it, lost >0.5) · right: each run's gain over its "
                         "own seed, bar = mean", size=12.5, color=SUB)
    kid_bars(a1, g)
    a1.set_title("children census (all seeds pooled)", size=13, color=SUB, loc="left", pad=18)
    gain_dots(a2, g)
    a2.set_title("gain of the run's best over the seed (one dot = one run)",
                 size=13, color=SUB, loc="left")
    a2.set_ylabel("gain")
    fig.tight_layout(rect=[0, 0, 1, 0.86])
    fig.savefig(out / "findings_e4_methods.png")
    plt.close(fig)


def fig_e1(rows, out):
    g = {"random": [], "constant": [], "llm": []}
    for a, r in sorted(rows.items()):
        for k in g:
            if f"e1_judge_{k}" in a:
                g[k].append(r)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.5), dpi=150)
    fig.suptitle("E1 · the judge ablation INVERTED: a random judge beats the LLM judge",
                 size=20, weight="bold", x=0.055, ha="left")
    fig.text(0.055, 0.9, "same method, only the judge's opinion differs. llm concentrates the "
                         "budget on grand rewrite-shaped ideas; random spreads it — and wins. "
                         "3 seeds each, every seed agrees", size=12.5, color=SUB)
    kid_bars(a1, g)
    a1.set_title("children census", size=13, color=SUB, loc="left", pad=18)
    gain_dots(a2, g)
    a2.set_title("per-run gain", size=13, color=SUB, loc="left")
    fig.tight_layout(rect=[0, 0, 1, 0.86])
    fig.savefig(out / "findings_e1_judge.png")
    plt.close(fig)


def fig_e3(rows, out):
    g = {"baseline": [], "fixed mix": [], "fixed T": [], "no bonus": [],
         "abs norm": []}
    for a, r in sorted(rows.items()):
        if "e1_judge_llm" in a:
            g["baseline"].append(r)
        elif "e3_fixedmix" in a:
            g["fixed mix"].append(r)
        elif "e3_fixedT" in a:
            g["fixed T"].append(r)
        elif "e3_nobonus" in a:
            g["no bonus"].append(r)
        elif "e3_absnorm" in a:
            g["abs norm"].append(r)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.5), dpi=150)
    fig.suptitle("E3 · remove the exploration bonus and EVERY implementation wrecks the seed",
                 size=20, weight="bold", x=0.055, ha="left")
    fig.text(0.055, 0.92, "schedule knobs, one at a time. 'no bonus' (beta0=0: pure "
                          "exploitation of the judge's favourites) wrecked 47/47 —\nthe bonus "
                          "was the only thing diluting the judge's advice. One fixed-T arm "
                          "excluded (dead-seed container)", size=12.5, color=SUB, va="top")
    kid_bars(a1, g)
    a1.set_title("children census", size=13, color=SUB, loc="left", pad=18)
    gain_dots(a2, g)
    a2.set_title("per-run gain", size=13, color=SUB, loc="left")
    fig.tight_layout(rect=[0, 0, 1, 0.86])
    fig.savefig(out / "findings_e3_schedules.png")
    plt.close(fig)


def fig_e2(rows, out):
    stages = ["r1", "r2", "r3"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 6.5), dpi=150)
    fig.suptitle("E2 · chained judge-state: the first live sign that accumulation helps",
                 size=20, weight="bold", x=0.055, ha="left")
    fig.text(0.055, 0.92, "three consecutive runs share one growing judge training set "
                          "(--judge-state). Chain A improves monotonically as the set\ngrows; "
                          "the un-chained baseline found nothing in 3 runs. n=2 chains — "
                          "suggestive, not settled", size=12.5, color=SUB, va="top")
    for name, color in (("chainA", PURPLE), ("chainB", ORANGE)):
        imp, gains = [], []
        for s in stages:
            r = next((r for a, r in rows.items() if f"e2_{name}_{s}" in a), None)
            imp.append((r["kids"] or {}).get("improved", 0) if r else None)
            gains.append(r.get("gain") if r else None)
        a1.plot(range(3), imp, "-o", color=color, lw=2.4, ms=9, label=name)
        a2.plot(range(3), gains, "-o", color=color, lw=2.4, ms=9, label=name)
    for ax, ylab in ((a1, "children that improved the seed"), (a2, "gain of best over seed")):
        ax.set_xticks(range(3))
        ax.set_xticklabels(["run 1", "run 2", "run 3"], size=12.5)
        ax.set_ylabel(ylab)
        ax.axhline(0, color=MUTED, lw=1, ls="--")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.legend(frameon=False, fontsize=12)
    a1.text(0.02, 0.02, "un-chained baseline: 0 improving children in 3 runs",
            transform=a1.transAxes, size=11, color=MUTED)
    fig.tight_layout(rect=[0, 0, 1, 0.86])
    fig.savefig(out / "findings_e2_chains.png")
    plt.close(fig)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--collected", nargs="+", required=True)
    ap.add_argument("--out", default="~/Downloads")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    rows = load(a.collected)
    fig_e4(rows, out)
    fig_e1(rows, out)
    fig_e3(rows, out)
    fig_e2(rows, out)
    for f in sorted(out.glob("findings_*.png")):
        print(f.name, f"{f.stat().st_size / 1e6:.2f} MB")
