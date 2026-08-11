"""MAP-Elites, animated as a run, with the lineage tree beside the grid.

Two structures, and the difference between them is the mechanism:

  the GRID keeps one program per niche, and forgets whoever it replaced
  the TREE keeps everything ever made, including the children that were discarded

A child that is worse than the run's best still takes an empty bin, and later becomes a parent.
Watching the tree grow out of those nodes is the only way to see why the grid is worth having.
"""
from __future__ import annotations

import random

from evo_anim import (BLUE, Canvas, EDGE, GREEN, GREY, ORANGE, PANEL, RED, SUB, TITLE, 
                      score_fill, score_ink)
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle

ROWS, COLS = 4, 5
ISLANDS = 2
STEPS = 24
MIGRATE_EVERY = 9


def simulate():
    rng = random.Random(11)
    grids = [{} for _ in range(ISLANDS)]              # (r,c) -> node id
    nodes = [{"id": 0, "parent": None, "score": 0.42, "admitted": True,
              "gen": 0, "island": 0, "cell": (2, 1)}]
    grids[0][(2, 1)] = 0
    events, best = [], 0.42
    for step in range(STEPS):
        isl = step % ISLANDS
        grid = grids[isl]
        pid = rng.choice(list(grid.values())) if grid else 0
        parent = nodes[pid]
        r = max(0, min(ROWS - 1, parent["cell"][0] + rng.choice([-1, 0, 0, 1])))
        cc = max(0, min(COLS - 1, parent["cell"][1] + rng.choice([-1, 0, 1, 1])))
        score = max(0.05, min(0.98, parent["score"] + rng.gauss(0.02, 0.09)))
        held = grid.get((r, cc))
        incumbent = nodes[held]["score"] if held is not None else None
        admitted = incumbent is None or score > incumbent
        nid = len(nodes)
        nodes.append({"id": nid, "parent": pid, "score": score, "admitted": admitted,
                      "gen": parent["gen"] + 1, "island": isl, "cell": (r, cc)})
        why = ("its niche was empty — kept although it is WORSE than the run's best"
               if incumbent is None and score < best else
               "it beats the elite that held this bin" if admitted else
               "the bin's elite is better, so it is stored but holds no slot")
        if admitted:
            grid[(r, cc)] = nid
            best = max(best, score)
        migrated = None
        if step and step % MIGRATE_EVERY == 0 and grids[0] and grids[1]:
            src = step // MIGRATE_EVERY % 2
            dst = 1 - src
            cell = max(grids[src], key=lambda k: nodes[grids[src][k]]["score"])
            cur = grids[dst].get(cell)
            if cur is None or nodes[cur]["score"] < nodes[grids[src][cell]]["score"]:
                grids[dst][cell] = grids[src][cell]
                migrated = (src, dst)
        events.append({
            "step": step, "island": isl, "parent": pid, "child": nid, "cell": (r, cc),
            "score": score, "incumbent": incumbent, "admitted": admitted, "why": why,
            "best": best, "migrated": migrated,
            "grids": [dict(g) for g in grids], "nodes": [dict(n) for n in nodes],
        })
    return events


EVENTS = simulate()
FRAMES = len(EVENTS)


def _grid(c, x0, y0, cell, grid, nodes, *, parent=None, child=None, admitted=True, alpha=1.0):
    for r in range(ROWS):
        for col in range(COLS):
            x, y = x0 + col * cell, y0 + (ROWS - 1 - r) * cell
            nid = grid.get((r, col))
            s = nodes[nid]["score"] if nid is not None else None
            fill = score_fill(s)
            edge, lw = EDGE, 1.0
            if child is not None and nodes[child]["cell"] == (r, col):
                edge, lw = (GREEN if admitted else RED), 2.2
            elif parent is not None and nodes[parent]["cell"] == (r, col):
                edge, lw = ORANGE, 2.0
            c.ax.add_patch(Rectangle((x, y), cell * 0.9, cell * 0.9, facecolor=fill,
                                     edgecolor=edge, linewidth=lw, alpha=alpha, zorder=2))
            if s is not None:
                c.ax.text(x + cell * 0.45, y + cell * 0.45, f"{s:.2f}",
                          color=score_ink(s), fontsize=7, ha="center", va="center",
                          alpha=alpha, zorder=3)


def _tree(c, nodes, e, x0, y0, w, h):
    gens = {}
    for n in nodes:
        gens.setdefault(n["gen"], []).append(n)
    depth = max(gens) or 1
    dy = h / max(1, depth)
    pos = {}
    for g, row in sorted(gens.items()):
        step_x = w / (len(row) + 1)
        for k, n in enumerate(sorted(row, key=lambda z: z["id"])):
            pos[n["id"]] = (x0 + step_x * (k + 1), y0 + h - g * dy)
    for n in nodes:
        if n["parent"] is None:
            continue
        p0, p1 = pos[n["parent"]], pos[n["id"]]
        fresh = n["id"] == e["child"]
        c.ax.add_patch(FancyArrowPatch(
            p0, p1, connectionstyle="arc3,rad=0.08",
            arrowstyle="-|>,head_width=1.8,head_length=3.2",
            color=(GREEN if e["admitted"] else RED) if fresh else EDGE,
            lw=1.6 if fresh else 0.9, zorder=1))
    for n in nodes:
        x, y = pos[n["id"]]
        fresh = n["id"] == e["child"]
        held = n["admitted"]
        c.ax.add_patch(Circle((x, y), 0.95,
                              facecolor=score_fill(n["score"]) if held else PANEL,
                              edgecolor=(GREEN if e["admitted"] else RED) if fresh
                              else (EDGE if held else GREY),
                              linewidth=2.0 if fresh else 1.0, zorder=3))
        if n["id"] == e["parent"]:
            c.ax.add_patch(Circle((x, y), 1.6, facecolor="none", edgecolor=ORANGE,
                                  linewidth=1.6, zorder=4))
    return pos


def frame(i: int):
    e = EVENTS[i]
    nodes = e["nodes"]
    c = Canvas("MAP-Elites over islands  ·  a run",
               "the grid keeps one program per niche  ·  the tree keeps every program ever made")

    for isl in range(ISLANDS):
        x0 = 5 + isl * 22
        active = isl == e["island"]
        c.note(x0, 50.6, f"island {isl + 1}" + ("  ←" if active else ""),
               color=TITLE if active else SUB, size=10,
               weight="bold" if active else "normal")
        _grid(c, x0, 32, 3.9, e["grids"][isl], nodes,
              parent=e["parent"] if active else None,
              child=e["child"] if active else None,
              admitted=e["admitted"], alpha=1.0 if active else 0.4)
    c.note(5, 29.6, "GRID — only the current elite of each niche", color=SUB, size=9.5)

    c.note(52, 50.6, "LINEAGE TREE — everything, admitted or not", color=SUB, size=10)
    _tree(c, nodes, e, 50, 30, 44, 18)

    c.note(52, 27.6, "filled node = holds a bin", color=SUB, size=9)
    c.note(72, 27.6, "hollow = stored, holds nothing", color=GREY, size=9)

    c.ax.add_patch(Rectangle((5, 12.0), 57, 12.0, facecolor=PANEL, edgecolor=EDGE, zorder=2))
    c.note(7.5, 21.6, f"step {e['step'] + 1} / {FRAMES}", color=SUB, size=10)
    c.ax.add_patch(Circle((8.0, 18.6), 0.5, facecolor=ORANGE, edgecolor="none", zorder=4))
    c.note(10, 18.6, f"parent: node {e['parent']} at {nodes[e['parent']]['score']:.2f}"
                     f"   ·   a child is written and measured: {e['score']:.2f}",
           color=SUB, size=10.5)
    c.ax.add_patch(Circle((8.0, 15.6), 0.5,
                          facecolor=GREEN if e["admitted"] else RED, edgecolor="none", zorder=4))
    inc = "empty" if e["incumbent"] is None else f"{e['incumbent']:.2f}"
    c.note(10, 15.6, f"its niche {e['cell']} held {inc}  ->  "
                     + ("ADMITTED, and the tree gains a node that can be a parent"
                        if e["admitted"] else
                        "not admitted — the tree still keeps it, the grid does not"),
           color=SUB, size=10.5)
    if e["migrated"]:
        s_, d_ = e["migrated"]
        c.note(10, 13.0, f"migration: island {s_ + 1}'s best is copied into island {d_ + 1}",
               color=BLUE, size=10.5)
    c.note(34, 21.6, f"nodes {len(nodes)}   ·   holding a bin "
                     f"{sum(1 for n in nodes if n['admitted'])}", color=SUB, size=10)
    c.fitness([0.655, 0.20, 0.29, 0.16],
              [x["step"] + 1 for x in EVENTS[:i + 1]],
              [x["score"] for x in EVENTS[:i + 1]],
              [x["best"] for x in EVENTS[:i + 1]], FRAMES,
              title="fitness", ylim=(0.0, 1.0))

    c.note(5, 8.2,
           "Selection pressure applies WITHIN a niche, not across the run. A weak child takes an "
           "empty bin, joins the tree, and becomes a parent —",
           color=SUB, size=10.5)
    c.note(5, 5.6,
           "which is how the grid keeps stepping stones that a search comparing everything "
           "against the best would have thrown away.",
           color=SUB, size=10.5)
    c.legend_model(y=2.0, extra="  ·  writing the child. Placement and admission are arithmetic.")
    return c.render()
