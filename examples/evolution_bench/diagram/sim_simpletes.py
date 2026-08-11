"""A SimpleTES run, simulated -- shared by every renderer.

The selection is done by the real `Selector`, imported rather than paraphrased, so an animation
cannot drift from the implementation the way the first one did. That one drew a chain-level bandit:
three chains competing on `u = score + exploration bonus`, attention swinging to whichever chain
was behind. No such thing exists. Chains are independent searches with equal shares of the budget,
served round-robin; the selector runs INSIDE one chain, over its nodes.

Only the scores are invented. Everything about who gets picked, how many, and what commits comes
from the method.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List

from pantheon.evolution.methods.simpletes import Selector

CHAINS = 3
K = 3
N_PARENTS = 4          # num_inspirations
STEPS = 21
KEY = "combined_score"
CEILINGS = [0.75, 0.66, 0.70]      # each line of attack has its own limit
SEED_SCORE = 0.40


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

    def copy(self) -> "Node":
        return Node(self.id, self.score, self.order, list(self.parents), list(self.cands),
                    self.win)


def tiers(size: int):
    """The bands `Selector.pick` draws from, so each pick can be labelled by where it landed.

    A label for the node's stratum, not for the roll that produced it -- which is the part worth
    seeing anyway: whether this prompt is built entirely out of the leaders or reaches further down.
    """
    return max(1, int(size * 0.2)), max(1, int(size * 0.1)), max(2, int(size * 0.6))


def simulate(seed: int = 7):
    rng = random.Random(seed)
    sel = Selector()                                   # the default: balance
    chains = [[Node(id=f"c{c}n0", score=SEED_SCORE, order=0)] for c in range(CHAINS)]
    base_share, extra = divmod(STEPS, CHAINS)
    budget = {c: base_share + (1 if c < extra else 0) for c in range(CHAINS)}
    spent = {c: 0 for c in range(CHAINS)}

    events, best, nxt = [], SEED_SCORE, 0
    for step in range(STEPS):
        c = nxt
        nxt = (nxt + 1) % CHAINS
        spent[c] += 1

        ranked = sorted(chains[c], key=lambda n: -n.score)      # `_chain_nodes`: best first
        picked = sel.pick(ranked, min(N_PARENTS, len(ranked)), rng, KEY)

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
        head = CEILINGS[c]
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
            "chains": [[n.copy() for n in ch] for ch in chains],
        })
    return events


def drew_phrase(labels) -> str:
    """"the incumbent, 2 from the middle, 1 from the tail"."""
    tally: dict = {}
    for lab in labels:
        tally[lab] = tally.get(lab, 0) + 1
    return ", ".join("the incumbent" if k == "incumbent" else f"{v} from the {k}"
                     for k, v in tally.items())


EVENTS = simulate()
FRAMES = len(EVENTS)
