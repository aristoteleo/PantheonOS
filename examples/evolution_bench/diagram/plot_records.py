"""Two record figures -- circle packing and Erdos -- with their VERIFIED setups on the canvas.

Reference values cross-checked against the SimpleTES technical report (arXiv:2604.19341,
Table on p.13): Circle Packing n=26 is AlphaEvolve V2 2.635983 = SimpleTES 2.635983 (our
2.635983 ties both at reported precision); the report also lists TogetherAI 0.380871 as the
prior Erdos best, below AlphaEvolve's 0.380924.

    uv run python plot_records.py --out ~/Downloads

The setup boxes were checked against the primary sources, because memory had already drifted
once (circle packing is widely mis-remembered as an Opus run; its RESULTS.md says glm-5.2):

  * circle packing: `evolution_circle_packing/results/RESULTS.md` -- model
    `openrouter/z-ai/glm-5.2`, `single_agent_mutation=True`, 3 iterations, record at iter 2.
  * erdos: `results_solver_nowarm/config.yaml` + `metadata.json` -- mutator_model
    `openai/anthropic/claude-opus-4.8`, islands=2, complexity x diversity grid (10 bins),
    diff-based mutations, 12 iterations; ablation numbers from the RESULTS.md table.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Rectangle

from prob_data import ERDOS, PACKING

INK, SUB, MUTED = "#1f2328", "#57606a", "#8c959f"
RED, BLUE, GREEN, ORANGE, PURPLE = "#cf222e", "#0969da", "#1a7f37", "#bc4c00", "#8250df"

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial"],
    "text.color": INK, "axes.edgecolor": MUTED, "axes.labelcolor": SUB,
    "xtick.color": SUB, "ytick.color": SUB,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def setup_box(fig, x, y, lines, title="setup — verified against the run artifacts"):
    fig.text(x, y, title, size=12.5, weight="bold", color=INK)
    for i, (k, v) in enumerate(lines):
        fig.text(x, y - 0.045 - i * 0.038, k, size=11.5, color=SUB)
        fig.text(x + 0.115, y - 0.045 - i * 0.038, v, size=11.5, color=INK)


def fig_packing(out):
    C = np.asarray(PACKING["centers"])
    R = np.asarray(PACKING["radii"])
    fig = plt.figure(figsize=(16, 9), dpi=150)
    fig.text(0.055, 0.93, "Circle packing n = 26 — the record run", size=26, weight="bold")
    fig.text(0.055, 0.885, "sum of radii 2.635983 — a three-way tie with AlphaEvolve V2 and SimpleTES "
                           "(both report 2.635983) — found at iteration 2 of a 3-iteration run",
             size=14, color=SUB)

    ax = fig.add_axes([0.055, 0.09, 0.40, 0.72])
    ax.set_aspect("equal")
    ax.add_patch(Rectangle((0, 0), 1, 1, fill=False, edgecolor=INK, lw=2))
    for c, r in zip(C, R):
        ax.add_patch(Circle(c, r, facecolor=BLUE, alpha=0.22, edgecolor=BLUE, lw=1.3))
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.axis("off")

    # the score ladder
    axl = fig.add_axes([0.53, 0.52, 0.42, 0.3])
    vals = [("naive ring seed", 1.8045, MUTED), ("evolved (iter 2)", 2.635983, GREEN)]
    for i, (name, v, col) in enumerate(vals):
        axl.barh(i, v, 0.5, color=col, alpha=0.75)
        axl.text(v + 0.02, i, f"{v:.6f}".rstrip("0"), va="center", size=13, color=INK)
    axl.axvline(2.635983, color=ORANGE, lw=1.6, ls="--")
    axl.text(2.62, 1.62, "AlphaEvolve V2 = SimpleTES = 2.635983 (tie)", size=11.5, color=ORANGE, ha="right")
    axl.set_yticks(range(len(vals)))
    axl.set_yticklabels([v[0] for v in vals], size=12.5)
    axl.set_xlim(0, 3.0)
    axl.set_ylim(-0.5, 1.9)
    for s in ("top", "right", "left"):
        axl.spines[s].set_visible(False)

    setup_box(fig, 0.53, 0.40, [
        ("method", "MAP-Elites archive + ONE coding-agent mutation"),
        ("", "(single_agent_mutation=True; pre-refactor stack,"),
        ("", " today's AgentMapElites)"),
        ("model", "openrouter/z-ai/glm-5.2  — NOT Opus"),
        ("run", "3 iterations · workers 1 · record at iter 2, ~410 s"),
        ("how", "the agent ran SLSQP over all centers+radii with"),
        ("", " restarts inside its mutation, verified, submitted"),
        ("caveat", "n = 1 at this level; validity re-checked (1.0)"),
    ])
    fig.savefig(out / "record_circle_packing.png")
    plt.close(fig)


def fig_erdos(out):
    H = np.asarray(ERDOS["steps"])
    fig = plt.figure(figsize=(16, 9), dpi=150)
    fig.text(0.055, 0.93, "Erdős minimum overlap — the record run and what unlocked it",
             size=26, weight="bold")
    fig.text(0.055, 0.885, "Ψ = 0.380909, below AlphaEvolve's 0.380924 — reached only after "
                           "warm-start was turned OFF", size=14, color=SUB)

    # the construction
    ax = fig.add_axes([0.055, 0.55, 0.42, 0.26])
    x = np.linspace(0, 2, len(H))
    ax.fill_between(x, 0, H, color=RED, alpha=0.78, linewidth=0)
    ax.fill_between(x, H, 1, color=BLUE, alpha=0.55, linewidth=0)
    ax.set_xlim(0, 2)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(INK)
    ax.set_title("the K = 951 record construction", size=13, color=SUB, loc="left", pad=6)

    # the 2x2 ablation -- the finding
    axb = fig.add_axes([0.175, 0.10, 0.30, 0.35])
    runs = [("solver + warm-start ON", 0.381107, MUTED),
            ("solver + warm-start OFF", 0.380909, GREEN),
            ("no solver, no warm-start", 0.381401, MUTED),
            ("no solver (repeat)", 0.381484, MUTED)]
    for i, (name, v, col) in enumerate(runs):
        axb.barh(i, v - 0.3805, 0.55, left=0.3805, color=col, alpha=0.8)
        axb.text(v + 0.00003, i, f"{v:.6f}", va="center", size=11.5, color=INK)
    # The two reference lines sit 5.6e-5 apart; centred labels overlap into mush, so each
    # label leans away from the other, at the TOP of the (inverted) axis.
    axb.axvline(0.380924, color=ORANGE, lw=1.5, ls="--")
    axb.text(0.380934, -0.42, "AlphaEvolve 0.380924", size=10.5, color=ORANGE, ha="left")
    axb.axvline(0.380868, color=PURPLE, lw=1.5, ls="--")
    axb.text(0.380858, -0.42, "SimpleTES 0.380868\nTogetherAI 0.380871", size=10.5,
             color=PURPLE, ha="right", va="top")
    axb.set_yticks(range(len(runs)))
    axb.set_yticklabels([r[0] for r in runs], size=12)
    axb.invert_yaxis()
    axb.set_xlim(0.3805, 0.3818)
    axb.set_xticks([0.3809, 0.3812, 0.3815, 0.3818])
    axb.set_xlabel("final Ψ (lower is better)", size=12)
    for s in ("top", "right"):
        axb.spines[s].set_visible(False)
    axb.set_title("the warm-start ablation — OFF escapes the sticky-champion attractor",
                  size=13, color=SUB, loc="left", pad=6)

    setup_box(fig, 0.55, 0.78, [
        ("method", "MAP-Elites + coding-agent mutations"),
        ("", "(islands = 2, complexity × diversity grid,"),
        ("", " 10 bins, diff-based edits)"),
        ("model", "anthropic/claude-opus-4.8 via OpenRouter"),
        ("run", "12 iterations · workers 1 · improved 5/12"),
        ("solver", "external scipy allowed (softmax-subgradient"),
        ("", " hand-rolled version costs only ~0.0005)"),
        ("finding", "warm-start persists the champion as a data file"),
        ("", " the code reloads → monotone floor per lineage,"),
        ("", " code decays into a thin loader, exploration dies;"),
        ("", " OFF: 0.381107 → 0.380909"),
        ("caveat", "n = 1 per cell; margins are 1e-5-scale"),
    ])
    fig.savefig(out / "record_erdos.png")
    plt.close(fig)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    fig_packing(out)
    fig_erdos(out)
    for f in ("record_circle_packing.png", "record_erdos.png"):
        print(f, f"{(out / f).stat().st_size / 1e6:.2f} MB")
