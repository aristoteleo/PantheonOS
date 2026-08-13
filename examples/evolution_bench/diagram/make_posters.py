"""Three static posters, one per problem, from the same real artifacts as the videos.

    uv run python make_posters.py --out ~/Downloads

Same palette and the same honesty rules as the animations: every number on a poster is the
evaluator's, every shape is a run artifact. The AHC poster draws ALL 10,000 fish (statics can
afford what animations cannot); the case input is read straight from the task cache.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Polygon, Rectangle

from prob_data import AHC, ERDOS, PACKING

INK, SUB, MUTED = "#1f2328", "#57606a", "#8c959f"
RED, BLUE, GREEN, ORANGE = "#cf222e", "#0969da", "#1a7f37", "#bc4c00"
HERE = Path(__file__).parent

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial"],
    "text.color": INK, "axes.edgecolor": MUTED,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def _title(fig, title, sub, fine):
    fig.text(0.055, 0.935, title, size=30, weight="bold", color=INK)
    fig.text(0.055, 0.885, sub, size=15.5, color=SUB)
    fig.text(0.055, 0.048, fine, size=11, color=MUTED)


def poster_erdos(out):
    H = np.asarray(ERDOS["steps"])
    prof = np.asarray(ERDOS["profile"])
    fig = plt.figure(figsize=(16, 9), dpi=150)
    _title(fig, "The Erdős Minimum-Overlap Problem",
           "colour half a strip red, the rest blue — the score is the WORST slide of a copy "
           "across itself, and lower is better",
           "the construction is our record run's artifact (K = 951 cells); both numbers are the "
           "evaluator's own arithmetic · Pantheon Evolve")

    # the strip: red below the curve, blue above
    ax = fig.add_axes([0.055, 0.5, 0.62, 0.33])
    x = np.linspace(0, 2, len(H))
    ax.fill_between(x, 0, H, color=RED, alpha=0.78, linewidth=0)
    ax.fill_between(x, H, 1, color=BLUE, alpha=0.55, linewidth=0)
    ax.set_xlim(0, 2)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(INK)
    ax.set_title("the record colouring — exactly half red in total, pushed to the edges in "
                 "bursts", size=13, color=SUB, loc="left", pad=8)

    # the slide profile
    axp = fig.add_axes([0.055, 0.12, 0.62, 0.3])
    lags = np.linspace(-2, 2, len(prof))
    axp.plot(lags, prof, color=GREEN, lw=2.2)
    axp.fill_between(lags, 0, prof, color=GREEN, alpha=0.12, linewidth=0)
    axp.axhline(ERDOS["psi"], color=GREEN, lw=1.0, ls="--", alpha=0.7)
    axp.set_xlim(-2, 2)
    axp.set_ylim(0, 0.55)
    axp.set_xticks([])
    axp.set_yticks([])
    for nm, sp in axp.spines.items():
        sp.set_visible(nm == "bottom")
    axp.set_title("meetings at every slide — nearly flat on top: no slide left to blame",
                  size=13, color=SUB, loc="left", pad=8)
    axp.annotate(f"Ψ = {ERDOS['psi']:.6f}", (0.0, ERDOS["psi"]),
                 xytext=(0.25, 0.47), size=15, weight="bold", color=GREEN,
                 arrowprops=dict(arrowstyle="-", color=GREEN, lw=1.0))

    # scoreboard
    fig.text(0.72, 0.72, "Ψ = 0.380909", size=30, weight="bold", color=GREEN)
    rows = [("ours", "0.380909"), ("AlphaEvolve", "0.380924"),
            ("Haugland", "0.380927"), ("TogetherAI", "0.380871"),
            ("SimpleTES", "0.380868")]
    for i, (k, v) in enumerate(rows):
        y = 0.63 - i * 0.05
        fig.text(0.72, y, k, size=14, color=SUB)
        fig.text(0.86, y, v, size=14, color=INK, family="Menlo")
    fig.text(0.72, 0.40, "flat colouring: Ψ = 0.5\nthe naive floor this halves twice over",
             size=12, color=MUTED)
    fig.savefig(out / "poster_erdos.png", bbox_inches=None)
    plt.close(fig)


def poster_packing(out):
    C = np.asarray(PACKING["centers"])
    R = np.asarray(PACKING["radii"])
    fig = plt.figure(figsize=(16, 9), dpi=150)
    _title(fig, "Circle Packing, n = 26",
           "fit 26 circles in the unit square, no overlaps — the score is the sum of the radii",
           "the packing is our best artifact, verified by the evaluator (validity 1.0); "
           "2.635983 ties AlphaEvolve V2 and SimpleTES (arXiv:2604.19341) · Pantheon Evolve")

    ax = fig.add_axes([0.055, 0.1, 0.44, 0.74])
    ax.set_aspect("equal")
    ax.add_patch(Rectangle((0, 0), 1, 1, fill=False, edgecolor=INK, lw=2))
    for c, r in zip(C, R):
        ax.add_patch(Circle(c, r, facecolor=BLUE, alpha=0.20, edgecolor=BLUE, lw=1.4))
    # tangencies, from the data
    n_t = 0
    for i in range(26):
        for j in range(i + 1, 26):
            if abs(np.linalg.norm(C[i] - C[j]) - (R[i] + R[j])) < 1e-4:
                ax.plot(*zip(C[i], C[j]), color=ORANGE, lw=1.1, alpha=0.8)
                n_t += 1
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.axis("off")

    fig.text(0.56, 0.72, "Σr = 2.635983", size=30, weight="bold", color=GREEN)
    rows = [("AlphaEvolve V2 / SimpleTES", "2.635983"), ("naive ring seed", "1.8045"),
            ("tangencies", str(n_t)), ("radii", "0.069 – 0.137")]
    for i, (k, v) in enumerate(rows):
        y = 0.63 - i * 0.05
        fig.text(0.56, y, k, size=14, color=SUB)
        fig.text(0.76, y, v, size=14, color=INK, family="Menlo")
    fig.text(0.56, 0.36,
             "a three-way tie at reported precision. The orange web is the\n"
             "optimum's signature: everything touches, so growing any\n"
             "circle shrinks a neighbour",
             size=12.5, color=MUTED)
    fig.savefig(out / "poster_circle_packing.png", bbox_inches=None)
    plt.close(fig)


def poster_ahc(out):
    # ALL fish for the static: read the case input directly.
    case = (HERE.parent / "tasks/ahc039/cache/public_inputs_150/ahc039_inputs/"
            "ahc039_000000_input.txt").read_text().split()
    n = int(case[0])
    pts = np.asarray(case[1:], dtype=float).reshape(-1, 2)
    mack, sard = pts[:n], pts[n:]
    poly = np.asarray(AHC["poly"])

    fig = plt.figure(figsize=(16, 9), dpi=150)
    _title(fig, "AHC039 — Purse Seine Fishing",
           "10,000 fish on a plane: draw ONE axis-aligned net that catches mackerel (+1) and "
           "leaves the sardines (−1)",
           "case 0 of 150, all 10,000 fish drawn; the net is the 5th-place seed solution's real "
           "output on this case · Pantheon Evolve")

    ax = fig.add_axes([0.055, 0.1, 0.44, 0.74])
    ax.set_aspect("equal")
    ax.scatter(mack[:, 0], mack[:, 1], s=2.2, c=BLUE, alpha=0.5, linewidths=0)
    ax.scatter(sard[:, 0], sard[:, 1], s=2.2, c=RED, alpha=0.5, linewidths=0)
    ax.add_patch(Polygon(poly, closed=True, fill=False, edgecolor=INK, lw=2.0))
    m = AHC["coord_max"]
    ax.set_xlim(-0.01 * m, 1.01 * m)
    ax.set_ylim(-0.01 * m, 1.01 * m)
    ax.axis("off")

    fig.text(0.56, 0.74, f"case score  {AHC['inside_mack'] - AHC['inside_sard'] + 1}",
             size=30, weight="bold", color=GREEN)
    rows = [("mackerel caught", f"+{AHC['inside_mack']}"),
            ("sardines regretted", f"−{AHC['inside_sard']}"),
            ("net vertices", str(len(poly))),
            ("limits", "≤1,000 v · perim ≤4e5 · 2 s")]
    for i, (k, v) in enumerate(rows):
        y = 0.65 - i * 0.05
        fig.text(0.56, y, k, size=14, color=SUB)
        fig.text(0.78, y, v, size=14, color=INK, family="Menlo")
    fig.text(0.56, 0.38,
             "the contest score is the MEAN over 150 such cases; the seed\n"
             "averages ~3,700 — already 5th place. Improvements stay additive,\n"
             "eval noise 0.0018 vs steps 0.01–0.1: the benchmark that discriminates",
             size=12.5, color=MUTED)
    fig.savefig(out / "poster_ahc039.png", bbox_inches=None)
    plt.close(fig)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    poster_erdos(out)
    poster_packing(out)
    poster_ahc(out)
    for f in ("poster_erdos.png", "poster_circle_packing.png", "poster_ahc039.png"):
        print(f, f"{(out / f).stat().st_size / 1e6:.2f} MB")
