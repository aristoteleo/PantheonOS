"""Figures for the Erdos search-then-polish study (protocol A) and the resolution/time test.

    uv run python plot_polish.py --polish-dir DIR --out FILE.png

Three panels:
  1. Psi vs accumulated polish time per method (from polish_erdos.py's per-round logs), with
     the resolution K marked where a round upsampled -- the trajectory of the fixed polisher.
  2. search result -> polished result per method, against the published references.
  3. the program-side test: each method's best PROGRAM re-run at its own K and at 2.5x K under a
     500 s cap (values recorded from bench_variants.py); markers hollow where the run would have
     been rejected by the bench's 120 s limit.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_budget import COLORS, LABELS, INK, SUB, EDGE_GRID

REFS = [(0.380927, "Haugland 2016"), (0.380924, "AlphaEvolve"), (0.380909, "ours (earlier, warm-off)"),
        (0.380868, "SimpleTES paper")]
ORDER = ["lab_notebook", "simpletes", "agent_map_elites", "hypothesis_bandit"]
KEY = {"labnotebook": "lab_notebook", "simpletes": "simpletes", "mapelites": "agent_map_elites",
       "hypbandit": "hypothesis_bandit"}

# bench_variants.py, 2026-09-02: (label, K, Psi, seconds) at x1 and x2.5 resolution
SCALING = {
    "lab_notebook":      [("s0", 960, 0.381032, 14), ("s0", 2400, 0.380942, 336)],
    "simpletes":         [("s0", 200, 0.381331, 4), ("s0", 500, 0.381689, 25),
                          ("s2", 160, 0.381293, 11), ("s2", 380, 0.381745, 8)],
    "agent_map_elites":  [("s0", 1024, 0.383916, 21), ("s0", 2560, 0.384050, 92),
                          ("s2", 2048, 0.381880, 5), ("s2", 5120, 0.381928, 11)],
    "hypothesis_bandit": [("s0", 128, 0.387500, 5), ("s0", 320, 0.385159, 6),
                          ("s1", 256, 0.382044, 8), ("s1", 640, 0.382076, 71),
                          ("s2", 200, 0.383121, 1), ("s2", 500, 0.383368, 2)],
}
LAB_NOTE_START = 0.381032   # LabNotebook's construction: no polish round completed (degenerate LPs)


def load_logs(d):
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        r = json.load(open(f))
        if "log" in r:
            out[KEY.get(r["label"], r["label"])] = r["log"]
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--polish-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    logs = load_logs(a.polish_dir)

    fig, axes = plt.subplots(1, 3, figsize=(17, 6), dpi=150, gridspec_kw={"width_ratios": [1.5, 1, 1.2]})
    fig.suptitle("Erdős minimum overlap — search-then-polish (A) and the resolution/time test",
                 size=16, weight="bold", x=0.03, ha="left")

    # ---- 1. polish trajectories ----------------------------------------------------------
    ax = axes[0]
    for m, log in logs.items():
        t = [x["elapsed"] / 60 for x in log]
        p = [x["psi"] for x in log]
        ax.plot(t, p, color=COLORS[m], lw=2.2, marker="o", ms=3.5,
                label=f"{LABELS[m]}  (K {log[0]['K']} → {log[-1]['K']})")
        ax.annotate(f"K={log[-1]['K']}", (t[-1], p[-1]), textcoords="offset points",
                    xytext=(5, -3), size=8, color=COLORS[m], va="center")
    ax.axhline(LAB_NOTE_START, color=COLORS["lab_notebook"], lw=2.2, ls="--",
               label="LabNotebook  (K 960; no round completed)")
    lo_ref, hi_ref = min(v for v, _ in REFS), max(v for v, _ in REFS)
    ax.axhspan(lo_ref, hi_ref, color=INK, alpha=0.08, lw=0)
    ax.text(0.01, hi_ref, f" published records {lo_ref:.6f}–{hi_ref:.6f} (SimpleTES paper … Haugland)",
            transform=ax.get_yaxis_transform(), va="top", size=8, color=SUB)
    ax.set_xlabel("accumulated polish time (min); rounds of ≤120 s each")
    ax.set_ylabel("Ψ  (lower is better; axis inverted)")
    ax.set_ylim(0.3822, 0.38080)
    ax.set_title("polish trajectories (one fixed polisher, all methods)", size=11, loc="left")
    ax.legend(frameon=False, fontsize=9, loc="lower right")

    # ---- 2. search -> polished ------------------------------------------------------------
    ax = axes[1]
    rows = []
    for m in ORDER:
        if m in logs:
            s, e = logs[m][0]["psi"], min(x["psi"] for x in logs[m])
        else:
            s, e = LAB_NOTE_START, LAB_NOTE_START
        rows.append((m, s, e))
    for i, (m, s, e) in enumerate(rows):
        ax.plot([s, e], [i, i], color=COLORS[m], lw=2.5)
        ax.scatter([s], [i], s=70, color="white", edgecolors=COLORS[m], linewidths=2, zorder=3)
        ax.scatter([e], [i], s=80, color=COLORS[m], zorder=3)
        ax.text(min(s, e) - 0.00008, i - 0.28, f"{s - e:+.1e}" if s != e else "no change",
                size=8.5, color=SUB, ha="right", va="center")
    ax.axvspan(lo_ref, hi_ref, color=INK, alpha=0.08, lw=0)
    ax.text(hi_ref, len(rows) - 0.55, " published\n records", size=8, color=SUB, ha="left", va="top")
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.invert_yaxis()
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([LABELS[m] for m, _, _ in rows], size=9.5)
    ax.set_xlim(0.38225, 0.38075)
    ax.set_xlabel("Ψ  (hollow = search result under the 120 s rule; filled = after equal polish)")
    ax.set_title("what equal polish changes", size=11, loc="left")

    # ---- 3. program-side scaling test -------------------------------------------------------
    ax = axes[2]
    for m in ORDER:
        pts = SCALING[m]
        by_seed = {}
        for seed, K, psi, sec in pts:
            by_seed.setdefault(seed, []).append((K, psi, sec))
        for seed, lst in by_seed.items():
            ks = [k for k, _, _ in lst]; ps = [p for _, p, _ in lst]
            ax.plot(ks, ps, color=COLORS[m], lw=1.4, alpha=0.8)
            for k, p, sec in lst:
                ax.scatter([k], [p], s=55, color=COLORS[m] if sec <= 120 else "white",
                           edgecolors=COLORS[m], linewidths=1.6, zorder=3)
    ax.axhspan(lo_ref, hi_ref, color=INK, alpha=0.08, lw=0)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("K (cells);  each program at its own K and at 2.5×K")
    ax.set_ylabel("Ψ (axis inverted)")
    ax.set_ylim(0.3880, 0.38080)
    ax.set_title("same program, more resolution (hollow = over 120 s)", size=11, loc="left")

    for ax in axes:
        ax.grid(color=EDGE_GRID, lw=0.6, alpha=0.6)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(a.out)
    print(a.out)
