"""Budget-axis figures: what a score COST, not just when it arrived.

    uv run python plot_budget.py --summaries w5_*.json --out ~/Downloads --tag wave5

Three figures from raw per-arm `summary.json` files:

  1. budget_llm_<tag>.png    best-so-far vs cumulative LLM calls
  2. budget_eval_<tag>.png   best-so-far vs cumulative evaluator calls
  3. budget_box_<tag>.png    final best per run under MATCHED caps -- every run truncated at
                             the same LLM-call AND evaluator-call budget (the minimum any run
                             consumed), so no method is paid for spending more

Budget positions come from `usage_timeline` when the summary has one (exact, one row per
booking). Older summaries (wave5 itself) carry only run totals; there the cumulative spend at a
measurement is estimated by time-share -- cum(t) = total * t/T -- and the axis label says so.
Scores are shown in official per-case points (harness fitness x 1500, AHC039's convention).
"""
from __future__ import annotations

import argparse
import glob
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
K = 1500.0

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial"],
    "text.color": INK, "axes.edgecolor": MUTED, "axes.labelcolor": SUB,
    "xtick.color": SUB, "ytick.color": SUB,
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def method_of(r):
    m = r.get("method") or r.get("_method_hint", "?")
    return "hypothesis_bandit" if m == "pantheon_evo" else m


def infer_method(path: str):
    base = os.path.basename(path)
    for pat, m in (("hypbandit", "hypothesis_bandit"), ("mapelites", "agent_map_elites"),
                   ("simpletes", "simpletes")):
        if pat in base:
            return m
    return None


def spend_at(r, t, key):
    """Cumulative SEARCH spend of `key` ('llm_calls' | 'eval_calls') at run-relative second t.

    Harness overhead (warm-up read, drift probes) is subtracted where the timeline tags it --
    those measurements exist to audit the machine, not to advance the search, and charging
    them to the method would skew the eval axis for the longest-running arms most."""
    tl = r.get("usage_timeline") or []
    if tl:
        t0 = r.get("t0_epoch") or tl[0]["t"]
        best = 0
        for row in tl:
            if row["t"] - t0 <= t:
                if key == "tokens":
                    best = row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)
                elif key == "eval_calls":
                    best = row[key] - row.get("eval_calls_harness", 0)
                else:
                    best = row[key]
            else:
                break
        return best
    if key == "llm_calls":
        total = r.get("llm_usage", {}).get("calls", 0)
    elif key == "tokens":
        lu = r.get("llm_usage", {})
        total = lu.get("prompt_tokens", 0) + lu.get("completion_tokens", 0)
    else:
        eu = r.get("eval_usage", {})
        total = eu.get("calls", 0) - eu.get("calls_harness", 0)
    T = max(float(r.get("seconds") or 1.0), 1e-6)
    return total * min(t, T) / T


def run_points(r, key):
    """[(budget, best_so_far_score)] for one run, anchored at the seed."""
    seed = r.get("seed_combined_score")
    pts, best = [], None
    if seed:
        best = seed
        pts.append((0.0, best))
    for h in r.get("history") or []:
        s, t = h.get("score"), h.get("t")
        if s is None or (h.get("valid", 1) or 0) <= 0:
            continue
        if h.get("fidelity", "full") != "full":
            continue          # cheap screens are decisions, not scores
        best = s if best is None else max(best, s)
        pts.append((spend_at(r, float(t or 0.0), key), best))
    return pts


def curve_fig(rows, key, xlabel, exact, out_png, tag):
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=150)
    fig.suptitle(f"ahc039 ({tag}) — best-so-far vs {xlabel}", size=17, weight="bold",
                 x=0.06, ha="left")
    by_m = defaultdict(list)
    for r in rows:
        by_m[method_of(r)].append(r)
    for m, rs in sorted(by_m.items()):
        if m not in COLORS:
            continue
        col = COLORS[m]
        done = [run_points(r, key) for r in rs if not r.get("partial")]
        part = [run_points(r, key) for r in rs if r.get("partial")]
        done = [c for c in done if len(c) >= 2]
        part = [c for c in part if len(c) >= 2]
        for c in done:
            xs, ys = zip(*c)
            ax.step(xs, [y * K for y in ys], where="post", color=col, lw=1.1, alpha=0.4)
        for c in part:
            # still-running arms: dashed, thinner, excluded from the mean
            xs, ys = zip(*c)
            ax.step(xs, [y * K for y in ys], where="post", color=col, lw=1.0, alpha=0.35,
                    linestyle="--")
        every = done + part
        if every:
            # Mean over EVERY arm, finished or not -- excluding the running ones left a method
            # with no mean line at all and nothing to compare. Past an arm's last measurement
            # its best-so-far is held flat (monotone, so this is a lower bound), and the mean
            # is drawn SOLID only while every arm still contributes; beyond the shortest arm
            # the sample thins and the line fades to say so.
            ends = [max(x for x, _ in c) for c in every]
            grid = np.linspace(0, max(ends), 240)
            vals = []
            for c in every:
                xs, ys = zip(*c)
                vals.append(np.interp(grid, xs, [y * K for y in ys]))
            mean = np.mean(vals, axis=0)
            full = grid <= min(ends)
            ax.plot(grid[full], mean[full], color=col, lw=2.6,
                    label=f"{LABELS[m]} (n={len(done)} done"
                          + (f" + {len(part)} running)" if part else ")"))
            if (~full).any():
                thin = grid >= min(ends)
                ax.plot(grid[thin], mean[thin], color=col, lw=2.0, alpha=0.45)
    ax.set_xlabel(xlabel + ("" if exact else "   (estimated by time-share: total × t/T)"))
    if any(r.get("partial") for rs in by_m.values() for r in rs):
        ax.set_title("thin lines = individual arms (dashed = still running); bold = method mean, "
                     "faded where fewer arms have reached that budget",
                     size=10, color=SUB, loc="left")
    ax.set_ylabel("best official score per case")
    # clamp to the decision-relevant band: a wrecked first child on a partial (no seed anchor)
    # otherwise stretches the axis 2000 points down and flattens every real difference
    finals = [c[-1][1] * K for rs in by_m.values() for r in rs
              for c in [run_points(r, key)] if len(c) >= 2]
    if finals:
        top = max(finals)
        ax.set_ylim(top - 160, top + 18)
    ax.legend(frameon=False, fontsize=11.5, loc="lower right")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(out_png)
    plt.close(fig)
    print(os.path.basename(out_png))


def box_fig(rows, out_png, tag):
    cap_llm = min(r.get("llm_usage", {}).get("calls", 0) or 1 for r in rows)
    cap_eval = min((r.get("eval_usage", {}).get("calls", 0)
                    - r.get("eval_usage", {}).get("calls_harness", 0)) or 1 for r in rows)
    by_m = defaultdict(list)
    for r in rows:
        pts_l = run_points(r, "llm_calls")
        pts_e = run_points(r, "eval_calls")
        best = None
        for (bl, s), (be, _) in zip(pts_l, pts_e):
            if bl <= cap_llm and be <= cap_eval:
                best = s if best is None else max(best, s)
        if best is not None:
            by_m[method_of(r)].append(best * K)

    fig, ax = plt.subplots(figsize=(8.5, 6.2), dpi=150)
    fig.suptitle(f"ahc039 ({tag}) — final best under matched budgets", size=17,
                 weight="bold", x=0.07, ha="left")
    ax.set_title(f"every run truncated at ≤{cap_llm} LLM calls AND ≤{cap_eval} evaluator calls"
                 " — the minimum any run consumed", size=10.5, color=SUB, loc="left")
    ms = [m for m in COLORS if m in by_m]
    data = [by_m[m] for m in ms]
    bp = ax.boxplot(data, positions=range(len(ms)), widths=0.45, showfliers=False,
                    medianprops={"color": INK, "lw": 2},
                    boxprops={"color": MUTED}, whiskerprops={"color": MUTED},
                    capprops={"color": MUTED})
    for i, m in enumerate(ms):
        ys = by_m[m]
        ax.scatter([i + 0.02] * len(ys), ys, s=90, color=COLORS[m], alpha=0.85, zorder=3)
    ax.set_xticks(range(len(ms)))
    ax.set_xticklabels([LABELS[m] for m in ms], size=11)
    ax.set_ylabel("best official score per case within budget")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout(rect=[0, 0, 1, 0.90])
    fig.savefig(out_png)
    plt.close(fig)
    print(os.path.basename(out_png), f"(caps: {cap_llm} llm / {cap_eval} eval)")
    return bp


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--summaries", nargs="+", required=True)
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    ap.add_argument("--tag", default="wave5")
    a = ap.parse_args()
    files = [f for pat in a.summaries for f in glob.glob(os.path.expanduser(pat))]
    rows = []
    for f in files:
        try:
            r = json.load(open(f))
        except Exception:
            continue
        if r.get("method") is None:
            hint = infer_method(f)
            if hint:
                r["_method_hint"] = hint
        if (r.get("items_run", 0) > 0 or r.get("partial")) and (r.get("history") or []):
            rows.append(r)
    if not rows:
        raise SystemExit("no completed summaries among inputs")
    print(f"{len(rows)} completed runs")
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    exact = all(r.get("usage_timeline") for r in rows)
    curve_fig(rows, "llm_calls", "cumulative LLM calls", exact,
              str(out / f"budget_llm_{a.tag}.png"), a.tag)
    curve_fig(rows, "eval_calls", "cumulative evaluator calls (search only)", exact,
              str(out / f"budget_eval_{a.tag}.png"), a.tag)
    curve_fig(rows, "tokens", "cumulative LLM tokens (prompt + completion)", exact,
              str(out / f"budget_tokens_{a.tag}.png"), a.tag)
    complete = [r for r in rows if not r.get("partial")]
    if complete:
        box_fig(complete, str(out / f"budget_box_{a.tag}.png"), a.tag)
