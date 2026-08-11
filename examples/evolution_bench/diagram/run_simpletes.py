"""SimpleTES, animated as a run.

The selection is done by the real `Selector` -- imported, not paraphrased -- so the picture cannot
drift from the code the way the first version of this did. That one animated a chain-level bandit:
three chains competing on `u = score + exploration bonus`, attention swinging to whichever chain was
behind. No such thing exists in the implementation. Chains are independent searches with equal
shares of the budget and they are served round-robin; the selector runs INSIDE one chain, over its
nodes.

What a run shows that a diagram cannot:

  - a chain is a DAG, not a line. Every selected node is a parent, so k candidates are a synthesis
    of several earlier programs and the edges converge.
  - the draw is stratified. The incumbent is always in; most of the rest come from the elite head,
    some from the middle, a few from anywhere. Nodes are placed by score here, so a parent set that
    reaches down the ranking is visible as an edge coming from the left.
  - best-of-k commits even when it is worse than what it was built from.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List

from evo_anim import (BLUE, Canvas, EDGE, GREEN, GREY, ORANGE, PANEL, SUB, TITLE, score_fill,
                      score_ink)
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle

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
    lost: List[float] = field(default_factory=list)
    """The k-1 candidates from the same prompt that did not commit. They are measured and kept in
    the store -- they cost money and a later analysis may want them -- but no later prompt sees
    them, which is the whole of what best-of-k means."""

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
        cands = sorted(
            max(0.05, min(0.99, anchor + (head - anchor) * rng.betavariate(1.5, 3.2)
                          + rng.gauss(0, 0.035)))
            for _ in range(K))
        winner = cands[-1]

        node = Node(id=f"c{c}n{len(chains[c])}", score=winner, order=len(chains[c]),
                    parents=[p.order for p in picked], lost=list(cands[:-1]))
        chains[c].append(node)
        best = max(best, winner)

        events.append({
            "step": step, "chain": c, "picked": [(p.order, p.score) for p in picked],
            "labels": labels, "anchor": anchor, "cands": cands, "winner": winner,
            "improved": winner > anchor, "best": best,
            "spent": dict(spent), "budget": dict(budget),
            "chains": [[Node(n.id, n.score, n.order, list(n.parents), list(n.lost)) for n in ch]
                       for ch in chains],
        })
    return events


EVENTS = simulate()
FRAMES = len(EVENTS)
_ALL = [n.score for e in EVENTS for ch in e["chains"] for n in ch]
LO, HI = min(_ALL), max(_ALL)

COL_X = [11.5, 27.0, 42.5]
TOP_Y, DY, HALF = 49.0, 3.5, 5.0
TIER_COLOR = {"incumbent": ORANGE, "elite": BLUE, "middle": BLUE, "tail": GREY}
"""Only the LABEL takes the tier colour. The ring stays orange on all of them, matching the tree,
because the point of the row is that all four are parents -- ringing them in four different colours
says the opposite, and breaks the correspondence with the orange rings on the left."""


def node_xy(chain: int, n: Node):
    """Down the column is creation order; across it is score.

    Placing nodes by score is what makes the stratified draw readable -- an edge arriving from the
    left is a parent that came from the middle or the tail of the ranking, which a column of evenly
    spaced dots would hide.
    """
    frac = (n.score - LO) / (HI - LO) if HI > LO else 0.5
    return COL_X[chain] + (frac - 0.5) * 2 * HALF, TOP_Y - n.order * DY


def frame(i: int):
    e = EVENTS[i]
    c = Canvas("SimpleTES  ·  a run",
               "every selected node is a parent — k candidates from one prompt, best of k commits")

    fresh = e["chains"][e["chain"]][-1]
    parent_orders = {o for o, _ in e["picked"]}

    for ci, ch in enumerate(e["chains"]):
        active = ci == e["chain"]
        c.note(COL_X[ci], 53.6, f"chain {ci + 1}", size=10.5, ha="center",
               color=TITLE if active else SUB, weight="bold" if active else "normal")
        c.note(COL_X[ci], 51.4, f"{e['spent'][ci]} / {e['budget'][ci]} prompts",
               color=ORANGE if active else GREY, size=8.5, ha="center")

        for n in ch:
            x, y = node_xy(ci, n)
            for po in n.parents:
                p = ch[po]
                px, py = node_xy(ci, p)
                new_edge = active and n is ch[-1]
                c.ax.add_patch(FancyArrowPatch(
                    (px, py - 1.3), (x, y + 1.3),
                    connectionstyle=f"arc3,rad={0.22 if px > x else -0.22}",
                    arrowstyle="-|>,head_width=2.0,head_length=3.5",
                    color=GREEN if new_edge else GREY,
                    lw=1.7 if new_edge else 0.9, alpha=1.0 if new_edge else 0.45, zorder=1))

        for n in ch:
            x, y = node_xy(ci, n)
            is_new = active and n is ch[-1]
            is_parent = active and n.order in parent_orders and not is_new
            # the k-1 that were measured and did not commit, parked beside the one that did.
            # Tinted by their score rather than left hollow: they are measured programs, and an
            # empty ring reads as an empty slot -- something the search has not filled yet.
            for j, s in enumerate(n.lost):
                c.ax.add_patch(Circle((x + 2.2 + j * 1.2, y), 0.5, facecolor=score_fill(s),
                                      edgecolor=GREY, linewidth=1.0,
                                      alpha=0.85 if is_new else 0.45, zorder=2))
            c.ax.add_patch(Circle(
                (x, y), 1.35, facecolor=score_fill(n.score),
                edgecolor=GREEN if is_new else (ORANGE if is_parent else EDGE),
                linewidth=2.2 if (is_new or is_parent) else 1.0, zorder=3))
            c.ax.text(x, y, f"{n.score:.2f}", color=score_ink(n.score), fontsize=7,
                      ha="center", va="center", zorder=4)

    # A key with the marks drawn, not described. The first version named them in a line of small
    # text at the bottom and left the orange ring -- the one that carries the whole selection story
    # -- with no entry at all, so the picture had to be guessed at.
    key = [(ORANGE, 1.35, "selected as a parent for this prompt  (the four on the right)"),
           (GREEN, 1.35, "the child that just committed — best of its k"),
           (GREY, 0.5, "a candidate that lost: measured and kept, but no later prompt sees it")]
    for j, (col, r, text) in enumerate(key):
        y = 20.2 - j * 2.9
        c.ax.add_patch(Circle((7.4, y), r, facecolor=score_fill(0.62) if r > 1 else score_fill(0.5),
                              edgecolor=col, linewidth=2.2 if r > 1 else 1.0, zorder=3))
        c.note(9.6, y, text, color=SUB, size=9.5)

    c.note(6.0, 10.2, "down each column is creation order  ·  across it is score, better to the "
                      "right —", color=GREY, size=9.5)
    c.note(6.0, 7.7, "so an arrow from the left is a parent drawn from the middle or the tail of "
                     "the ranking", color=GREY, size=9.5)
    c.note(6.0, 4.4, "Chains do not compete. Each gets an equal share of the budget and they are "
                     "served in turn;", color=SUB, size=10.5)
    c.note(6.0, 1.9, "the selection happens inside one chain, over its own nodes.",
           color=SUB, size=10.5)

    # ---- the step, on the right -----------------------------------------
    c.note(55, 53.6, f"step {e['step'] + 1} / {FRAMES}", color=SUB, size=11)
    c.note(55, 50.9, f"chain {e['chain'] + 1}'s turn  ·  the selector draws "
                     f"{len(e['picked'])} nodes from its ranking", color=TITLE, size=10.5)

    for j, (order, s) in enumerate(e["picked"]):
        x = 58 + j * 9.5
        lab = e["labels"][j]
        c.ax.add_patch(Circle((x, 45.6), 1.5, facecolor=score_fill(s),
                              edgecolor=ORANGE, linewidth=2.2, zorder=3))
        c.ax.text(x, 45.6, f"{s:.2f}", color=score_ink(s), fontsize=8,
                  ha="center", va="center", zorder=4)
        c.note(x, 42.6, lab, color=TIER_COLOR[lab], size=8.5, ha="center")
    c.note(55, 39.6, "all of them are parents — the prompt asks for a new program, not an edit",
           color=SUB, size=9.5)

    c.ax.add_patch(Circle((56, 36.2), 0.5, facecolor=ORANGE, edgecolor="none", zorder=4))
    c.note(58, 36.2, f"one prompt  ->  {K} candidates", color=SUB, size=10.5)

    for j, s in enumerate(e["cands"]):
        win = s == e["winner"]
        x = 58 + j * 11.5
        c.ax.add_patch(Rectangle((x, 30.0), 8.4, 4.2,
                                 facecolor=score_fill(s) if win else PANEL,
                                 edgecolor=GREEN if win else EDGE,
                                 linewidth=1.8 if win else 1.0, zorder=3))
        c.ax.text(x + 4.2, 32.1, f"{s:.2f}", color=score_ink(s) if win else TITLE, fontsize=10,
                  ha="center", va="center", zorder=4)

    c.ax.add_patch(Circle((56, 27.0), 0.5,
                          facecolor=GREEN if e["improved"] else GREY, edgecolor="none", zorder=4))
    c.note(58, 27.0,
           f"best of k = {e['winner']:.2f} joins chain {e['chain'] + 1}"
           + ("" if e["improved"] else f"  — below its best parent ({e['anchor']:.2f}), "
                                       "and it still commits"),
           color=SUB, size=10.5)
    c.note(58, 24.4,
           "the other k-1 were measured and are kept, but no later prompt sees them"
           if e["improved"] else "a chain records what was tried; it is not a ratchet",
           color=GREY, size=9.5)

    xs = [x["step"] + 1 for x in EVENTS[:i + 1] for _ in x["cands"]]
    ys = [v for x in EVENTS[:i + 1] for v in x["cands"]]
    c.fitness([0.62, 0.115, 0.33, 0.155], xs, ys,
              [x["best"] for x in EVENTS[:i + 1]], FRAMES,
              title=f"fitness  ·  all {K} candidates per step", ylim=(0.3, 0.85),
              scatter_label="every candidate")

    c.legend_model(x=56, y=2.0,
                   extra=f"  ·  ONE call produces all {K} candidates — that is the cost profile")
    return c.render()
