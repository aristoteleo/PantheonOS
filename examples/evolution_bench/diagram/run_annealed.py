"""AnnealedIdeaCode, animated as a run.

Three things only a run shows: the budget sliding from proposing to implementing, the selection
distribution collapsing from near-uniform onto one idea, and the judge's prediction being checked
against a measurement it did not get to choose.
"""
from __future__ import annotations

import math
import random

from evo_anim import (BLUE, Canvas, EDGE, GREEN, GREY, INK, ORANGE, PANEL, PURPLE, RED, SUB, 
                      TITLE, score_fill)
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle

STEPS = 30
T0, T1, BETA0, GAMMA = 1.0, 0.05, 1.2, 2.0
W_NEW, W_REF, W_IMPL = 1.0, 0.42, 2.05
SEED = 0.42


def mix(t):
    u = [W_NEW * (1 - t) ** GAMMA, W_REF, W_IMPL]
    s = sum(u)
    return [v / s for v in u]


def temperature(t):
    return T0 * (T1 / T0) ** t


def simulate():
    rng = random.Random(3)
    ideas = []          # {parent, ceiling, base, pred, impls:[score]}
    events = []
    best = SEED
    for step in range(STEPS):
        t = step / STEPS
        p = mix(t)
        r = rng.random()
        act = "NEW" if r < p[0] else ("REFINE" if r < p[0] + p[1] else "IMPL")
        if not ideas:
            act = "NEW"

        # selection probabilities over the ideas that exist
        probs = []
        if ideas:
            vals = []
            for I in ideas:
                own = max(I["impls"]) if I["impls"] else None
                mu = own if own is not None else I["base"] + I["pred"]
                sig = 0.05 / math.sqrt(len(I["impls"])) if I["impls"] else 0.15
                vals.append(mu + BETA0 * (1 - t) * sig)
            lo, hi = min(vals), max(vals)
            norm = [(v - lo) / (hi - lo + 1e-9) for v in vals]
            T = max(1e-6, temperature(t))
            ex = [math.exp((v - max(norm)) / T) for v in norm]
            tot = sum(ex)
            probs = [v / tot for v in ex]

        chosen, note, pred, meas = None, "", None, None
        if act == "NEW":
            ceil = min(0.72, max(0.15, rng.gauss(0.55, 0.13)))
            base = SEED
            pred = round(min(0.30, max(-0.02, (ceil - base) + rng.gauss(0, 0.05))), 3)
            ideas.append({"parent": None, "ceiling": ceil, "base": base,
                          "pred": pred, "impls": []})
            chosen = len(ideas) - 1
            note = f"a new approach — the judge predicts a gain of {pred:+.3f}"
        else:
            u = rng.random()
            acc = 0.0
            chosen = len(ideas) - 1
            for j, pr in enumerate(probs):
                acc += pr
                if u <= acc:
                    chosen = j
                    break
            if act == "REFINE":
                par = ideas[chosen]
                inherited = max(par["impls"]) if par["impls"] else par["base"]
                ceil = min(0.75, par["ceiling"] + abs(rng.gauss(0.04, 0.03)))
                pred = round(min(0.25, max(-0.02, (ceil - inherited) + rng.gauss(0, 0.04))), 3)
                ideas.append({"parent": chosen, "ceiling": ceil, "base": inherited,
                              "pred": pred, "impls": []})
                note = (f"refine idea {chosen} — the child inherits its best program "
                        f"({inherited:.2f}) and is predicted {pred:+.3f}")
                chosen = len(ideas) - 1
            else:
                I = ideas[chosen]
                start = max(I["impls"]) if I["impls"] else I["base"]
                first = not I["impls"]
                got = start + (I["ceiling"] - start) * rng.betavariate(2.4, 1.6)
                got = max(0.05, min(0.98, got + rng.gauss(0, 0.01)))
                I["impls"].append(got)
                best = max(best, got)
                if first:
                    meas = round(got - I["base"], 3)
                    note = (f"first program for idea {chosen}: {got:.3f}  ·  predicted "
                            f"{I['pred']:+.3f}, measured {meas:+.3f}")
                else:
                    note = f"another program for idea {chosen}: {got:.3f}"

        events.append({
            "step": step, "t": t, "mix": p, "T": temperature(t), "act": act,
            "chosen": chosen, "note": note, "probs": probs, "best": best, "meas": meas,
            "ideas": [{"parent": I["parent"], "base": I["base"], "pred": I["pred"],
                       "impls": list(I["impls"])} for I in ideas],
        })
    return events


EVENTS = simulate()
FRAMES = len(EVENTS)
COLS = {"NEW": BLUE, "REFINE": PURPLE, "IMPL": GREEN}


def frame(i: int):
    e = EVENTS[i]
    c = Canvas("AnnealedIdeaCode  ·  a run",
               "broad early, narrow late — and a judge whose prediction gets checked")

    # ---------- populations ----------
    c.note(4, 49.0, "ideas", color=SUB, size=10.5)
    c.note(4, 38.0, "code", color=SUB, size=10.5)
    n = len(e["ideas"])
    span = min(6.0, 62.0 / max(1, n))
    for j, I in enumerate(e["ideas"]):
        x = 12 + j * span
        built = bool(I["impls"])
        act_now = (j == e["chosen"])
        c.ax.add_patch(Circle((x, 49.0), min(1.5, span * 0.32),
                              facecolor=score_fill(max(I["impls"]) if built else None),
                              edgecolor=COLS[e["act"]] if act_now else (EDGE if built else GREY),
                              linestyle="-" if built else (0, (2, 2)),
                              linewidth=2.2 if act_now else 1.1, zorder=3))
        if I["parent"] is not None:
            px = 12 + I["parent"] * span
            c.ax.add_patch(FancyArrowPatch((px, 51.0), (x, 51.0),
                                           connectionstyle="arc3,rad=-0.5",
                                           arrowstyle="-|>,head_width=2.0,head_length=3.6",
                                           color=PURPLE, lw=1.1, alpha=0.8, zorder=1))
        for k, sc in enumerate(sorted(I["impls"], reverse=True)[:4]):
            c.ax.add_patch(Rectangle((x - span * 0.22, 41.6 - k * 2.2), span * 0.44, 1.6,
                                     facecolor=score_fill(sc), edgecolor="none", zorder=3))
        if len(I["impls"]) > 4:
            c.ax.text(x, 32.6, f"+{len(I['impls']) - 4}", color=GREY, fontsize=7.5,
                      ha="center", va="center", zorder=3)
        if built:
            # the ANCHOR edge: this program implements that idea. Not descent -- a different
            # relationship, and the reason a lineage here has two kinds of edge at all.
            c.ax.plot([x, x], [47.4, 43.6], color=EDGE, lw=0.8, ls=":", zorder=1)

    # ---------- progress + schedule ----------
    # The lineage has TWO kinds of edge, and keeping them apart is what lets an idea be credited
    # for what it added rather than for the code it inherited.
    c.note(78, 50.5, "descent", color=PURPLE, size=9.5)
    c.note(78, 48.0, "one idea refining another", color=SUB, size=9)
    c.note(78, 44.5, "anchor", color=GREY, size=9.5)
    c.note(78, 42.0, "a program implementing an idea", color=SUB, size=9)

    c.note(4, 30.6, f"t = {e['t']:.2f}", color=SUB, size=10.5)
    c.ax.add_patch(Rectangle((12, 30.0), 62, 1.4, facecolor=PANEL, edgecolor=EDGE, zorder=2))
    c.ax.add_patch(Rectangle((12, 30.0), 62 * e["t"], 1.4, facecolor=BLUE,
                             edgecolor="none", zorder=3))

    c.note(4, 26.6, "budget mix", color=SUB, size=10)
    x = 12
    for lab, w in zip(("NEW", "REFINE", "IMPL"), e["mix"]):
        c.ax.add_patch(Rectangle((x, 25.8), 62 * w, 1.8, facecolor=COLS[lab],
                                 edgecolor="none", alpha=0.85, zorder=3))
        if w > 0.09:
            c.ax.text(x + 62 * w / 2, 26.7, lab, color=INK, fontsize=8,
                      ha="center", va="center", weight="bold", zorder=4)
        x += 62 * w

    c.note(4, 21.4, "P(pick)", color=SUB, size=10)
    c.note(78, 26.6, f"T = {e['T']:.3f}", color=SUB, size=10)
    if e["probs"]:
        w = 62 / max(1, len(e["probs"]))
        for j, pr in enumerate(e["probs"]):
            c.ax.add_patch(Rectangle((12 + j * w + w * 0.15, 19.2), w * 0.7, 4.4 * pr,
                                     facecolor=ORANGE if j == e["chosen"] else BLUE,
                                     edgecolor="none", zorder=3))
        c.ax.plot([12, 74], [19.2, 19.2], color=EDGE, lw=0.9, zorder=2)

    # ---------- what happened ----------
    c.ax.add_patch(Rectangle((4, 10.8), 90, 6.2, facecolor=PANEL, edgecolor=EDGE, zorder=2))
    c.note(6.5, 15.2, f"step {e['step'] + 1} / {FRAMES}", color=SUB, size=10)
    c.ax.add_patch(Circle((7.0, 12.6), 0.5, facecolor=COLS[e["act"]], edgecolor="none", zorder=4))
    c.note(9, 12.6, f"{e['act']}   {e['note']}", color=SUB, size=10.5)
    c.note(78, 15.2, f"best {e['best']:.3f}", color=SUB, size=10.5)

    # only IMPL steps produce a measurement; NEW and REFINE spend budget without one
    xs = [x["step"] + 1 for x in EVENTS[:i + 1] if x["act"] == "IMPL"]
    ys, seen = [], 0
    for x in EVENTS[:i + 1]:
        if x["act"] == "IMPL":
            flat = [v for I in x["ideas"] for v in I["impls"]]
            ys.append(flat[-1] if len(flat) > seen else (ys[-1] if ys else SEED))
            seen = len(flat)
    c.fitness([0.655, 0.055, 0.30, 0.155], xs, ys,
              [x["best"] for x in EVENTS[:i + 1]], FRAMES,
              title="fitness  ·  only IMPL steps measure anything", ylim=(0.35, 0.75),
              scatter_label="each program")

    c.note(4, 9.4,
           "Early the mix is heavy on NEW and P(pick) is nearly flat — the run is buying breadth. "
           "Late, NEW has decayed and T has fallen,",
           color=SUB, size=10.5)
    c.note(4, 6.9,
           "so the same distribution has collapsed onto one idea: refinement and implementation "
           "both land on the winner.",
           color=SUB, size=10.5)
    c.legend_model(y=3.4, extra="  ·  ideas, programs, and the judge — the schedule itself is arithmetic")
    return c.render()
