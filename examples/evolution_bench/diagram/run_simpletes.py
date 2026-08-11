"""SimpleTES, animated as a run.

The selection is done by the real `Selector` -- imported, not paraphrased -- so the picture cannot
drift from the code the way the first version of this did. That one animated a chain-level bandit:
three chains competing on `u = score + exploration bonus`, attention swinging to whichever chain was
behind. No such thing exists in the implementation. Chains are independent searches with equal
shares of the budget and they are served round-robin; the selector runs INSIDE one chain, over its
nodes.

Each chain is a trunk running left to right. At every step the selected nodes feed one prompt, the
prompt fans out into k candidate squares, and one of them continues the trunk. The k are drawn as
peers and the one that commits is only a colour apart, because that is the honest relationship --
it won a comparison between siblings, it is not a different kind of object. An earlier version drew
the losers as small hollow rings hanging off the winner, which reads as empty slots rather than as
programs that were written, run and scored.

What a run shows that a diagram cannot:

  - a chain is a DAG, not a line. Every selected node is a parent, so the arcs into a prompt come
    from several earlier points on the trunk.
  - the draw is stratified: the incumbent is always in, most of the rest come from the elite head,
    and some reach into the middle or the tail.
  - best-of-k commits even when it is worse than the best thing it was built from.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List

from evo_anim import (BLUE, Canvas, EDGE, GREEN, GREY, ORANGE, PANEL, SUB, TITLE, score_fill,
                      score_ink)
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

from pantheon.evolution.methods.simpletes import Selector

CHAINS = 3
K = 3
N_PARENTS = 4          # num_inspirations
STEPS = 21
KEY = "combined_score"


@dataclass
class Node:
    """Enough of an `Individual` for the real selector to sort and pick."""

    id: str
    score: float
    order: int
    parents: List[int] = field(default_factory=list)
    cands: List[float] = field(default_factory=list)
    win: int = 0
    """Which of the k candidates commits. The other k-1 are measured and kept -- they cost money
    and a later analysis may want them -- but no later prompt sees them."""

    def measured(self):
        return SimpleNamespace(metrics={KEY: self.score})


def tiers(size: int):
    """The bands `Selector.pick` draws from, so each pick can be labelled by where it landed.

    A label for the node's stratum, not for the roll that produced it -- which is the part worth
    seeing anyway: whether this prompt is built entirely out of the leaders or reaches further down.
    """
    return max(1, int(size * 0.2)), max(1, int(size * 0.1)), max(2, int(size * 0.6))


def simulate():
    rng = random.Random(7)
    sel = Selector()                                   # the default: balance
    chains = [[Node(id=f"c{c}n0", score=0.40, order=0)] for c in range(CHAINS)]
    ceilings = [0.66, 0.75, 0.70]                      # each line of attack has its own limit
    base_share, extra = divmod(STEPS, CHAINS)
    budget = {c: base_share + (1 if c < extra else 0) for c in range(CHAINS)}
    spent = {c: 0 for c in range(CHAINS)}

    events, best, nxt = [], 0.40, 0
    for step in range(STEPS):
        c = nxt
        nxt = (nxt + 1) % CHAINS
        spent[c] += 1

        ranked = sorted(chains[c], key=lambda n: -n.score)      # `_chain_nodes`: best first
        n_pick = min(N_PARENTS, len(ranked))
        picked = sel.pick(ranked, n_pick, rng, KEY)

        rank_of = {n.id: i for i, n in enumerate(ranked)}
        elite_end, mid_start, mid_end = tiers(len(ranked))
        labels = []
        for j, p in enumerate(picked):
            r = rank_of[p.id]
            if j == 0:
                labels.append("incumbent")
            elif r < elite_end:
                labels.append("elite")
            elif mid_start <= r < mid_end:
                labels.append("middle")
            else:
                labels.append("tail")

        # One prompt, k candidates, synthesised from every parent. The best reference sets the
        # level; gains shrink as a chain approaches its own ceiling, and the noise is what makes
        # most candidates worse than what they were built from.
        head = ceilings[c]
        anchor = max(p.score for p in picked)
        cands = [max(0.05, min(0.99, anchor + (head - anchor) * rng.betavariate(1.5, 3.2)
                               + rng.gauss(0, 0.035)))
                 for _ in range(K)]
        win = max(range(K), key=lambda j: cands[j])

        node = Node(id=f"c{c}n{len(chains[c])}", score=cands[win], order=len(chains[c]),
                    parents=[p.order for p in picked], cands=list(cands), win=win)
        chains[c].append(node)
        best = max(best, node.score)

        events.append({
            "step": step, "chain": c, "picked": [(p.order, p.score) for p in picked],
            "labels": labels, "anchor": anchor, "cands": cands, "win": win,
            "winner": node.score, "improved": node.score > anchor, "best": best,
            "spent": dict(spent), "budget": dict(budget),
            "chains": [[Node(n.id, n.score, n.order, list(n.parents), list(n.cands), n.win)
                        for n in ch] for ch in chains],
        })
    return events


EVENTS = simulate()
FRAMES = len(EVENTS)

TOP = 72.0
BAND_Y = [56.0, 43.0, 30.0]
X0, XSTEP = 15.0, 11.0
R = 1.5                       # trunk node radius
FAN_DX, SQ_W, SQ_H, FAN_DY = 6.5, 2.9, 2.3, 3.3


def trunk_x(order: int) -> float:
    return X0 + order * XSTEP


def cand_xy(order: int, j: int, band: float):
    """The k candidates of the prompt that produced node `order`, fanned around the trunk."""
    return trunk_x(order - 1) + FAN_DX, band + (1 - j) * FAN_DY


def frame(i: int):
    e = EVENTS[i]
    c = Canvas("SimpleTES  ·  a run",
               "every selected node is a parent — one prompt fans out into k candidates, "
               "and one of them continues the chain",
               height=11.0, top=TOP)

    parent_orders = {o for o, _ in e["picked"]}

    for ci, ch in enumerate(e["chains"]):
        band = BAND_Y[ci]
        active = ci == e["chain"]
        c.note(4.0, band + 1.4, f"chain {ci + 1}", size=11, color=TITLE if active else SUB,
               weight="bold" if active else "normal")
        c.note(4.0, band - 1.4, f"{e['spent'][ci]} / {e['budget'][ci]} prompts",
               color=ORANGE if active else GREY, size=8.5)

        for n in ch:
            if not n.cands:
                continue
            newest = active and n is ch[-1]
            px = trunk_x(n.order - 1)

            # the arcs into this prompt, one per selected parent -- this is the DAG
            gate = px + FAN_DX - SQ_W / 2 - 1.4
            for po in n.parents:
                back = po < n.order - 1
                span = gate - trunk_x(po)
                # An arc's height is rad * span / 2, so a fixed rad sends a long reach-back over
                # the band above and the picture turns into a hairball. Hold the height instead.
                rad = -min(0.34, 5.0 / span) if back and span > 0 else 0.0
                c.ax.add_patch(FancyArrowPatch(
                    (trunk_x(po), band + (R if back else 0)), (gate, band),
                    connectionstyle=f"arc3,rad={rad}",
                    arrowstyle="-|>,head_width=2.0,head_length=3.6",
                    color=ORANGE if newest else GREY,
                    lw=1.6 if newest else 0.8, alpha=0.95 if newest else 0.16, zorder=1))

            # one prompt -> k candidates, drawn as peers
            for j, s in enumerate(n.cands):
                x, y = cand_xy(n.order, j, band)
                won = j == n.win
                c.ax.add_patch(FancyArrowPatch(
                    (px + FAN_DX - SQ_W / 2 - 1.4, band), (x - SQ_W / 2, y),
                    connectionstyle=f"arc3,rad={0.30 * (1 - j)}",
                    arrowstyle="-|>,head_width=1.8,head_length=3.2",
                    color=BLUE if newest else GREY, lw=1.3 if newest else 0.7,
                    alpha=0.9 if newest else 0.28, zorder=1))
                # Past steps keep the k peers but drop the emphasis: a green ring on every winner
                # in the run repeats an answer the trunk already gives, and twenty of them crowd
                # out the one step the frame is actually about.
                c.ax.add_patch(FancyBboxPatch(
                    (x - SQ_W / 2, y - SQ_H / 2), SQ_W, SQ_H,
                    boxstyle="round,pad=0.12,rounding_size=0.4",
                    facecolor=score_fill(s) if won else PANEL,
                    edgecolor=(GREEN if newest else EDGE) if won else EDGE,
                    linewidth=1.9 if (won and newest) else 1.0,
                    alpha=1.0 if newest else 0.45, zorder=3))
                if newest:
                    c.ax.text(x, y, f"{s:.2f}", color=score_ink(s) if won else SUB, fontsize=7.5,
                              ha="center", va="center", zorder=4)

            # the winner rejoins the trunk
            wx, wy = cand_xy(n.order, n.win, band)
            c.ax.add_patch(FancyArrowPatch(
                (wx + SQ_W / 2, wy), (trunk_x(n.order) - R, band),
                connectionstyle=f"arc3,rad={0.30 * (n.win - 1)}",
                arrowstyle="-|>,head_width=2.0,head_length=3.6",
                color=GREEN if newest else GREY, lw=1.7 if newest else 0.9,
                alpha=1.0 if newest else 0.45, zorder=1))

        for n in ch:
            x = trunk_x(n.order)
            is_new = active and n is ch[-1]
            is_parent = active and n.order in parent_orders
            c.ax.add_patch(Circle(
                (x, band), R, facecolor=score_fill(n.score),
                edgecolor=GREEN if is_new else (ORANGE if is_parent else EDGE),
                linewidth=2.3 if (is_new or is_parent) else 1.0, zorder=3))
            c.ax.text(x, band, f"{n.score:.2f}", color=score_ink(n.score), fontsize=7.5,
                      ha="center", va="center", zorder=4)

    # ---- what just happened ---------------------------------------------
    tally = {}
    for lab in e["labels"]:
        tally[lab] = tally.get(lab, 0) + 1
    drew = ", ".join(f"{v} from the {k}" if k != "incumbent" else "the incumbent"
                     for k, v in tally.items())
    c.note(4.0, 22.0, f"step {e['step'] + 1} / {FRAMES}   ·   chain {e['chain'] + 1}'s turn",
           color=TITLE, size=12)
    c.note(4.0, 18.9, f"the selector drew {len(e['picked'])} parents — {drew}", color=SUB,
           size=10.5)
    c.note(4.0, 16.1,
           f"best of k = {e['winner']:.2f} continues chain {e['chain'] + 1}"
           + ("" if e["improved"] else f", below its best parent ({e['anchor']:.2f}) — "
                                       "a chain records what was tried, it is not a ratchet"),
           color=SUB, size=10.5)

    c.note(4.0, 12.6, "Chains do not compete. Each gets an equal share of the budget and they are "
                      "served in turn; the", color=SUB, size=10.5)
    c.note(4.0, 10.0, "selection happens inside one chain, over its own nodes — which is why an "
                      "arc can reach back several steps.", color=SUB, size=10.5)

    # One row, so the key stays clear of the fitness plot on the right.
    key = [(5.6, "circle", ORANGE, "a parent for this prompt"),
           (25.0, "square", EDGE, f"a candidate — one call makes all {K}"),
           (49.0, "square", GREEN, "the best of them continues the chain")]
    for x, shape, col, text in key:
        if shape == "circle":
            c.ax.add_patch(Circle((x, 5.6), 1.2, facecolor=score_fill(0.62), edgecolor=col,
                                  linewidth=2.2, zorder=3))
        else:
            c.ax.add_patch(FancyBboxPatch(
                (x - SQ_W / 2, 5.6 - SQ_H / 2), SQ_W, SQ_H,
                boxstyle="round,pad=0.12,rounding_size=0.4",
                facecolor=score_fill(0.62) if col is GREEN else PANEL,
                edgecolor=col, linewidth=1.9 if col is GREEN else 1.0, zorder=3))
        c.note(x + 2.8, 5.6, text, color=SUB, size=9.5)

    xs = [x["step"] + 1 for x in EVENTS[:i + 1] for _ in x["cands"]]
    ys = [v for x in EVENTS[:i + 1] for v in x["cands"]]
    c.fitness([0.755, 0.055, 0.225, 0.145], xs, ys,
              [x["best"] for x in EVENTS[:i + 1]], FRAMES,
              title=f"fitness  ·  all {K} candidates per step", ylim=(0.3, 0.85),
              scatter_label="every candidate")
    return c.render()
