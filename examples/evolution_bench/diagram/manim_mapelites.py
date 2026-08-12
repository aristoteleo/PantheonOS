"""MAP-Elites, as a narrated run.

    manim -qm --format=mp4 manim_mapelites.py MapElitesRun

**The loop is the algorithm.** Select a parent, mutate it, evaluate it, decide what to keep, repeat.
Every method in this series runs that cycle; MAP-Elites is one answer to the last box, and the
answer happens to change the first box too -- keeping an archive rather than a champion changes
what there is to select from.

An earlier cut opened on the grid, which is opening on the answer: it made the archive look like
the algorithm and left the loop happening offscreen. The loop goes first now, and the grid arrives
as the archive box opened up.

There are then two structures, and the difference between them is the rest of the mechanism:

  the GRID keeps one program per niche, and forgets whoever it replaced
  the TREE keeps everything ever made, including the children that were thrown away

Four acts:

  1. the loop, with one real step walked around it
  2. what the archive does with a child -- into an empty cell, against a tenant it beats, against
     a tenant it does not
  3. both structures filling together. A program worse than anything found so far still earns a
     slot if its slot was empty, and the tree shows those same mediocre programs going on to have
     children -- which is what the grid buys and what the grid alone cannot show.
  4. the second island, and migration between them.

The run is real. `sim_mapelites` drives the actual `MapElitesIslands` through the actual loop and
records what it decided; only the landscape and the mutation are invented.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Circle, Create, FadeIn, FadeOut, Indicate,
                   LaggedStart, Line, MovingCameraScene, Square, Transform, VGroup, Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, RED, SUB,
                       arc, fit, node_mob, para, score_color, txt)
from sim_mapelites import BINS, EVENTS, ISLANDS, PLACES, quality

CELL = 0.52
GRID_X = [-4.62, 0.78]          # left edge of each island's grid
GRID_Y = -0.22                  # bottom edge
LO, HI = 0.25, 0.75             # the score range the cell colours span

TREE_X0, TREE_DX = -0.45, 0.78
TREE_TOP, TREE_DY = 2.25, 0.38
TREE_R = 0.14
"""Laid out in layers by DEPTH rather than as a tidy tree with a row per node: this run is 8
generations deep and at most 10 wide, so columns fit the frame and one-row-per-node does not.
DY is set from that 10 -- the deepest column has to clear the caption underneath it."""


def tree_layout(events):
    """`id -> (position, parent id, admitted, score)` for one island's lineage.

    Depth comes from the parent chain, not from the order things were placed: a child of an early
    program made late in the run belongs next to its parent, not next to its timestamp.
    """
    depth, out, per_layer = {}, {}, {}
    for e in events:
        pid = e["parent"]
        d = 0 if pid not in depth else depth[pid] + 1
        depth[e["id"]] = d
        row = per_layer.get(d, 0)
        per_layer[d] = row + 1
        out[e["id"]] = (np.array([TREE_X0 + d * TREE_DX, TREE_TOP - row * TREE_DY, 0.0]),
                        pid, e["admitted"], e["score"])
    return out


def cell_pos(island: int, cell) -> np.ndarray:
    """Descriptor space, laid out as it is binned: complexity across, diversity up."""
    return np.array([GRID_X[island] + (cell[0] + 0.5) * CELL,
                     GRID_Y + (cell[1] + 0.5) * CELL, 0.0])


def blank_grid(island: int) -> VGroup:
    g = VGroup()
    for cx in range(BINS):
        for cy in range(BINS):
            g.add(Square(side_length=CELL, stroke_width=1.4, color=EDGE,
                         fill_color=PANEL, fill_opacity=1.0).move_to(cell_pos(island, (cx, cy))))
    return g


def occupant(score: float, island: int, cell, lit=False) -> Square:
    return Square(side_length=CELL, stroke_width=3.0 if lit else 1.4,
                  color=GREEN if lit else EDGE,
                  fill_color=score_color(score, LO, HI),
                  fill_opacity=1.0).move_to(cell_pos(island, cell))


def first_where(pred, start=0):
    for i in range(start, len(PLACES)):
        if pred(PLACES[i]):
            return i
    return None


class MapElitesRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("MAP-Elites", 54, INK, weight="BOLD")
        sub = para("a parent, a mutation, a measurement, and a decision about what to keep.\n"
                   "MAP-Elites is one answer to the last of those — and it changes the first.",
                   27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(2.0)
        self.play(FadeOut(card), run_time=0.9)

        self.act_the_loop()

        self.cells = {}                      # (island, cell) -> the Square sitting there
        self.grids = VGroup(*[blank_grid(i) for i in range(ISLANDS)])
        self.axes_labels = VGroup()
        for i in range(ISLANDS):
            c = np.array([GRID_X[i] + BINS * CELL / 2, GRID_Y, 0.0])
            self.axes_labels.add(txt("complexity →", 17, MUTED).next_to(c, DOWN, buff=0.22))
            v = txt("diversity →", 17, MUTED).rotate(np.pi / 2)
            v.next_to(np.array([GRID_X[i], GRID_Y + BINS * CELL / 2, 0.0]), LEFT, buff=0.22)
            self.axes_labels.add(v)

        # Wide enough that the frame is TALL enough: the counter sits above the grid and the
        # captions below it, and a frame's height follows from its width.
        self.camera.frame.move_to([GRID_X[0] + BINS * CELL / 2, 0.55, 0]).set(width=8.4)
        self.play(FadeIn(self.grids[0]), FadeIn(self.axes_labels[:2]), run_time=1.0)
        note = self.cap("the archive, opened up: one cell per combination of descriptors.\n"
                        "Two axes chosen for the problem — here, how complex and how varied.",
                        24, SUB)
        note.move_to([GRID_X[0] + BINS * CELL / 2, -1.25, 0])
        self.play(FadeIn(note), run_time=0.6)

        seed_sq = self.place(PLACES[0], lit=True)
        self.wait(1.4)
        # Reset the ring: green means "just committed", and a seed that keeps it reads as the
        # newest cell for the rest of the video.
        self.play(seed_sq.animate.set_stroke(EDGE, width=1.4), FadeOut(note), run_time=0.5)

        self.act_one()
        self.act_two()
        self.act_three()

    # ---- the loop --------------------------------------------------------
    def act_the_loop(self):
        """The search is the loop. MAP-Elites is a component inside it.

        This act exists because the first cut of this video opened on the grid, which is opening
        on the answer: it made the archive look like the algorithm and left the loop -- select,
        mutate, evaluate, decide -- as something happening offscreen. Every method in this series
        runs this same cycle; what they differ in is one box.
        """
        head = txt("the search is a loop", 31, INK).move_to([0, 3.35, 0])
        self.play(FadeIn(head), run_time=0.6)

        spots = {"select": np.array([-3.7, 0.55, 0.0]),
                 "mutate": np.array([0.0, 2.35, 0.0]),
                 "evaluate": np.array([3.7, 0.55, 0.0]),
                 "archive": np.array([0.0, -1.25, 0.0])}
        labels = {"select": ("select a parent", "from what the search has kept"),
                  "mutate": ("mutate it", "ask a model for a variation"),
                  "evaluate": ("evaluate", "run it, and get a score"),
                  "archive": ("decide what to keep", "the only box that differs")}
        order = ["select", "mutate", "evaluate", "archive"]

        boxes = {}
        for key in order:
            top, bottom = labels[key]
            body = VGroup(txt(top, 24, INK), txt(bottom, 18, SUB)).arrange(DOWN, buff=0.12)
            frame = Square(side_length=1.0, stroke_width=2.0,
                           color=ORANGE if key == "archive" else EDGE,
                           fill_color=PANEL, fill_opacity=1.0)
            frame.stretch_to_fit_width(body.width + 0.55).stretch_to_fit_height(body.height + 0.5)
            boxes[key] = VGroup(frame, body).move_to(spots[key])
        self.play(LaggedStart(*[FadeIn(boxes[k], scale=0.85) for k in order], lag_ratio=0.25),
                  run_time=1.8)

        arrows = VGroup()
        for i, key in enumerate(order):
            nxt = order[(i + 1) % len(order)]
            a, b = spots[key], spots[nxt]
            arrows.add(arc(a, b, MUTED, 2.4, angle=-0.42,
                           buff_a=boxes[key].width * 0.42, buff_b=boxes[nxt].width * 0.42))
        self.play(LaggedStart(*[Create(x) for x in arrows], lag_ratio=0.2), run_time=1.6)
        self.wait(1.2)

        # one real step around it, with the numbers the recorded run actually produced
        ev = PLACES[1]
        beats = [("select", f"a program the archive is holding — {ev['best']:.2f} is the best so "
                            "far, but it is not the only one available"),
                 ("mutate", "a variation on it"),
                 ("evaluate", f"the verifier scores it: {ev['score']:.2f}"),
                 ("archive", "and now the decision — this is where the methods part company")]
        cap = None
        for key, text in beats:
            new = para(text, 22, INK) if "\n" in text else txt(text, 22, INK)
            fit(new, FULL_W - 2.6).move_to([0, -2.95, 0])
            anims = [Indicate(boxes[key], color=ORANGE, scale_factor=1.08)]
            anims.append(FadeIn(new) if cap is None else Transform(cap, new))
            self.play(*anims, run_time=0.9)
            if cap is None:
                cap = new
            self.wait(1.5)

        punch = para("Keep only the best program and the loop collapses onto one line of attack.\n"
                     "MAP-Elites keeps an ARCHIVE instead — which changes what there is to select "
                     "from.", 23, INK)
        fit(punch, FULL_W - 2.0).move_to([0, -2.95, 0])
        self.play(Transform(cap, punch),
                  boxes["archive"][0].animate.set_stroke(ORANGE, width=3.4), run_time=0.9)
        self.wait(3.2)
        self.play(FadeOut(head), FadeOut(arrows), FadeOut(cap),
                  *[FadeOut(boxes[k]) for k in order if k != "archive"], run_time=0.9)
        # the archive box opens into the grid
        self.play(FadeOut(boxes["archive"]), run_time=0.7)

    # ---- drawing ---------------------------------------------------------
    def place_anim(self, ev, lit=False):
        """The animation for putting a child in its cell, replacing whoever was there.

        Returned rather than played, so a run of them can go into one `play` call. One call per
        placement is a third of a second each, and there are fifty-odd of them.
        """
        key = (ev["island"], ev["cell"])
        sq = occupant(ev["score"], ev["island"], ev["cell"], lit=lit)
        old = self.cells.get(key)
        if old is not None:
            return Transform(old, sq), old
        self.cells[key] = sq
        return FadeIn(sq, scale=0.6), sq

    def place(self, ev, lit=False):
        anim, mob = self.place_anim(ev, lit)
        self.play(anim, run_time=0.4)
        return mob

    def child_token(self, ev, at) -> VGroup:
        return node_mob(ev["score"], at, r=0.24, ring=ORANGE, width=3.0, lo=LO, hi=HI)

    # ---- act 1 -----------------------------------------------------------
    def act_one(self):
        """One step into an empty cell, one step against a tenant."""
        empty = first_where(lambda e: e["admitted"] and not e["displaced"]
                            and e["score"] < e["best"] - 1e-9, 1)
        beaten = first_where(lambda e: e["displaced"], 1)
        rejected = first_where(lambda e: not e["admitted"], 1)

        self.step_closeup(
            empty,
            "a parent comes off the grid, and its child is measured",
            "its descriptors point at an EMPTY cell, so it is kept —\n"
            f"even though {PLACES[empty]['score']:.2f} is below the best so far "
            f"({PLACES[empty]['best']:.2f})")

        if beaten is not None:
            self.step_closeup(
                beaten, "this one lands where something already sits",
                "it scores higher than the tenant, so it takes the cell.\n"
                "The program it replaced is gone from the grid.")

        if rejected is not None:
            self.step_closeup(
                rejected, "and this one lands on a cell it cannot win",
                "the tenant scores higher, so the child is measured, recorded — and dropped.")

    def step_closeup(self, idx, before: str, after: str):
        ev = PLACES[idx]
        cx, _, _ = self.camera.frame.get_center()
        start = np.array([GRID_X[0] + BINS * CELL + 1.15, GRID_Y + BINS * CELL * 0.62, 0.0])
        tok = self.child_token(ev, start)
        c1 = self.cap(before, 24, INK)
        c1.move_to([GRID_X[0] + BINS * CELL / 2, -1.15, 0])
        self.play(FadeIn(tok, scale=0.5), FadeIn(c1), run_time=0.8)
        self.wait(1.1)

        target = cell_pos(ev["island"], ev["cell"])
        trail = Line(start, target, color=ORANGE, stroke_width=2).set_stroke(opacity=0.45)
        self.play(Create(trail), tok.animate.move_to(target), run_time=1.0)

        c2 = self.cap(after, 24, INK)
        c2.move_to([GRID_X[0] + BINS * CELL / 2, -1.35, 0])
        self.play(FadeOut(c1), FadeIn(c2), run_time=0.6)
        if ev["admitted"]:
            self.play(FadeOut(tok), FadeOut(trail), run_time=0.3)
            sq = self.place(ev, lit=True)
            self.play(Indicate(sq, color=GREEN, scale_factor=1.12), run_time=0.6)
            self.play(sq.animate.set_stroke(EDGE, width=1.4), run_time=0.3)
        else:
            self.play(tok.animate.set_stroke(RED, width=3.0), run_time=0.4)
            self.play(FadeOut(tok, scale=0.4), FadeOut(trail), run_time=0.6)
        self.wait(2.2)
        self.play(FadeOut(c2), run_time=0.4)

    # ---- act 2 -----------------------------------------------------------
    def act_two(self):
        """Both structures, growing from the same events."""
        island0 = [e for e in PLACES if e["island"] == 0]
        self.tree = tree_layout(island0)
        self.tree_mobs = {}
        self.tree_edges = VGroup()
        """Edges are held, not just drawn. Act 3 clears the tree to make room for island 2, and
        anything not in a group here stays on screen as a stray line across the second grid."""

        self.play(self.camera.frame.animate.move_to([-0.6, 0.3, 0]).set(width=FULL_W),
                  run_time=1.3)
        heads = VGroup(
            txt("the grid — one per niche", 22, INK)
            .move_to([GRID_X[0] + BINS * CELL / 2, GRID_Y + BINS * CELL + 0.34, 0]),
            txt("the tree — everything ever made", 22, INK)
            .move_to([TREE_X0 + 3.4 * TREE_DX, TREE_TOP + 0.62, 0]))
        self.play(FadeIn(heads), run_time=0.8)

        # whatever act 1 already put on the grid also belongs in the tree
        seen = 0
        for e in island0[:3]:
            self.add_tree_node(e, animate=False)
            seen += 1

        note = para("The grid forgets whoever it replaced. The tree does not — every attempt is "
                    "still here,\nincluding the ones that were measured and thrown away.",
                    22, INK)
        fit(note, FULL_W - 2.0).move_to([-0.6, -2.55, 0])
        self.play(FadeIn(note), run_time=0.7)
        self.wait(2.0)

        def tally():
            t = txt(f"{len(self.cells)} of {BINS * BINS} cells filled", 22, ORANGE)
            return t.move_to([GRID_X[0] + BINS * CELL / 2, GRID_Y - 0.72, 0])

        counter = tally()
        self.play(FadeIn(counter), run_time=0.3)

        todo = island0[seen:]
        for start in range(0, len(todo), 3):
            anims = []
            for e in todo[start:start + 3]:
                if e["admitted"]:
                    anims.append(self.place_anim(e)[0])
                anims.extend(self.add_tree_node(e, animate=True))
            if not anims:
                continue
            self.play(LaggedStart(*anims, lag_ratio=0.3), Transform(counter, tally()),
                      run_time=0.9)
        self.wait(1.6)

        closing = para("Hollow dots are children that were measured and dropped — they cost the "
                       "same as the rest.\nEvery filled dot with something below it is a mediocre "
                       "program that went on to be a parent.", 22, INK)
        fit(closing, FULL_W - 2.0).move_to([-0.6, -2.55, 0])
        self.play(FadeOut(note), FadeIn(closing), run_time=0.8)
        self.wait(3.4)
        self.play(FadeOut(closing), FadeOut(counter), FadeOut(heads), run_time=0.6)

    def add_tree_node(self, ev, animate=True):
        """A dot, and the edge from its parent. Filled if it took a cell, hollow if it was
        dropped -- the tree keeps both, which is the whole reason it is on screen."""
        pos, pid, admitted, score = self.tree[ev["id"]]
        dot = Circle(radius=TREE_R if admitted else TREE_R * 0.6,
                     stroke_width=2.0 if admitted else 1.6,
                     color=EDGE if admitted else MUTED,
                     fill_color=score_color(score, LO, HI),
                     fill_opacity=1.0 if admitted else 0.0).move_to(pos)
        self.tree_mobs[ev["id"]] = dot
        anims = []
        if pid in self.tree:
            a = self.tree[pid][0]
            edge = Line(a + RIGHT * TREE_R, pos - RIGHT * TREE_R,
                        color=EDGE, stroke_width=1.6).set_stroke(opacity=0.75)
            self.tree_edges.add(edge)
            if animate:
                anims.append(Create(edge))
            else:
                self.add(edge)
        if animate:
            anims.append(FadeIn(dot, scale=0.5))
        else:
            self.add(dot)
        return anims

    # ---- act 3 -----------------------------------------------------------
    def act_three(self):
        """The second island, migration, and what the run bought."""
        # The tree has said what it had to say, and island 2's grid needs its half of the frame.
        self.play(*[FadeOut(m) for m in self.tree_mobs.values()], FadeOut(self.tree_edges),
                  self.camera.frame.animate.move_to([0, 0.35, 0]).set(width=FULL_W), run_time=1.4)
        head = txt("islands: several grids, occasionally trading programs", 28, INK)
        head.move_to([0, 3.5, 0])
        tags = VGroup(*[txt(f"island {i + 1}", 20, SUB)
                        .move_to([GRID_X[i] + BINS * CELL / 2, BINS * CELL + 0.05, 0])
                        for i in range(ISLANDS)])
        self.play(FadeIn(head), FadeIn(self.grids[1]), FadeIn(self.axes_labels[2:]),
                  FadeIn(tags), run_time=1.2)

        # Island 2 only ever fills through migration and through the descendants of what migrates:
        # every seed starts on island 1 and a child inherits its parent's island, so nothing is
        # born there until something arrives.
        rest = [e for e in EVENTS if e["kind"] == "migrate"
                or (e["kind"] == "place" and e["island"] != 0 and e["admitted"])]
        batch = []
        for ev in rest:
            if ev["kind"] == "place":
                batch.append(self.place_anim(ev)[0])
                continue
            if batch:
                self.play(LaggedStart(*batch, lag_ratio=0.4), run_time=0.9)
                batch = []
            self.show_migration(ev)
        if batch:
            self.play(LaggedStart(*batch, lag_ratio=0.4), run_time=0.9)

        self.wait(1.0)
        self.play(FadeOut(head), run_time=0.5)
        self.curves()

    def show_migration(self, ev):
        moved = txt(f"migration — {len(ev['moved'])} programs change island", 24, ORANGE)
        moved.move_to([0, -1.35, 0])
        arrow = Line([GRID_X[0] + BINS * CELL + 0.25, GRID_Y + BINS * CELL / 2, 0],
                     [GRID_X[1] - 0.25, GRID_Y + BINS * CELL / 2, 0],
                     color=ORANGE, stroke_width=4)
        self.play(FadeIn(moved), Create(arrow), run_time=0.7)
        self.wait(1.4)
        self.play(FadeOut(moved), FadeOut(arrow), run_time=0.4)

    def curves(self):
        ax = Axes(x_range=[0, len(PLACES) + 1, 5], y_range=[0, 1.0, 0.25],
                  x_length=9.0, y_length=2.15, tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([0, -2.18, 0])
        ticks = VGroup(txt("0", 16, MUTED).next_to(ax.c2p(0, 0), LEFT, buff=0.16),
                       txt("1", 16, MUTED).next_to(ax.c2p(0, 1.0), LEFT, buff=0.16),
                       txt("step", 16, MUTED).next_to(ax.c2p(len(PLACES) + 1, 0), DOWN, buff=0.18))
        best = ax.plot_line_graph([i + 1 for i in range(len(PLACES))],
                                  [e["best"] for e in PLACES], line_color=ORANGE,
                                  add_vertex_dots=False, stroke_width=4)
        cov = ax.plot_line_graph([i + 1 for i in range(len(PLACES))],
                                 [e["coverage"] for e in PLACES], line_color=BLUE,
                                 add_vertex_dots=False, stroke_width=4)
        key = VGroup(txt("best score so far", 19, ORANGE), txt("cells filled", 19, BLUE))
        key.arrange(RIGHT, buff=0.6).next_to(ax, UP, buff=0.18)

        self.play(Create(ax), FadeIn(ticks), FadeIn(key), run_time=1.0)
        self.play(Create(best), Create(cov), run_time=2.0)
        tag = para("The best score stalls for long stretches. Coverage does not — and every cell "
                   "filled\nin one of those stretches is a parent the search would not otherwise "
                   "have had.", 21, SUB)
        fit(tag, FULL_W - 2.0).move_to([0, 3.3, 0])
        self.play(FadeIn(tag), run_time=0.8)
        self.wait(3.0)
