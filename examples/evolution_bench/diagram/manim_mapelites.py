"""MAP-Elites, as a narrated run.

    manim -qm --format=mp4 manim_mapelites.py MapElitesRun

Three acts:

  1. what one step IS -- take a program off the grid, mutate it, measure it, and put it in the cell
     its DESCRIPTORS point at. Twice: once into an empty cell, once against a sitting tenant.
  2. the grid filling, with the count of occupied cells climbing. This is where the idea lives: a
     program worse than anything found so far still earns a slot, provided its slot was empty.
  3. the second island, and migration between them.

The run is real. `sim_mapelites` drives the actual `MapElitesIslands` through the actual loop and
records what it decided; only the landscape and the mutation are invented.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Create, FadeIn, FadeOut, Flash, Indicate,
                   LaggedStart, Line, MovingCameraScene, Rectangle, Square, Transform, VGroup,
                   Write)

from manim_kit import (BLUE, EDGE, FULL_H, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, RED, SUB,
                       fit, ink_on, node_mob, para, score_color, txt)
from sim_mapelites import BINS, EVENTS, ISLANDS, PLACES, quality

CELL = 0.52
GRID_X = [-4.62, 0.78]          # left edge of each island's grid
GRID_Y = -0.22                  # bottom edge
LO, HI = 0.25, 0.75             # the score range the cell colours span


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
        sub = para("keep the best program in each NICHE, not the best program overall —\n"
                   "so a mediocre result still earns a place, provided nothing else is like it",
                   27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(1.6)
        self.play(FadeOut(card), run_time=0.9)

        self.cells = {}                      # (island, cell) -> the Square sitting there
        self.grids = VGroup(*[blank_grid(i) for i in range(ISLANDS)])
        self.axes_labels = VGroup()
        for i in range(ISLANDS):
            c = np.array([GRID_X[i] + BINS * CELL / 2, GRID_Y, 0.0])
            self.axes_labels.add(txt("complexity →", 17, MUTED).next_to(c, DOWN, buff=0.22))
            v = txt("diversity →", 17, MUTED).rotate(np.pi / 2)
            v.next_to(np.array([GRID_X[i], GRID_Y + BINS * CELL / 2, 0.0]), LEFT, buff=0.22)
            self.axes_labels.add(v)

        self.camera.frame.move_to([GRID_X[0] + BINS * CELL / 2, 0.55, 0]).set(width=7.6)
        self.play(FadeIn(self.grids[0]), FadeIn(self.axes_labels[:2]), run_time=1.0)
        note = self.cap("one cell per combination of descriptors — an empty grid, and one seed",
                        24, SUB)
        note.move_to([GRID_X[0] + BINS * CELL / 2, -1.15, 0])
        self.play(FadeIn(note), run_time=0.6)

        seed_sq = self.place(PLACES[0], lit=True)
        self.wait(1.4)
        # Reset the ring: green means "just committed", and a seed that keeps it reads as the
        # newest cell for the rest of the video.
        self.play(seed_sq.animate.set_stroke(EDGE, width=1.4), FadeOut(note), run_time=0.5)

        self.act_one()
        self.act_two()
        self.act_three()

    # ---- drawing ---------------------------------------------------------
    def place(self, ev, lit=False):
        """Put the child in its cell, replacing whoever was there."""
        key = (ev["island"], ev["cell"])
        sq = occupant(ev["score"], ev["island"], ev["cell"], lit=lit)
        old = self.cells.get(key)
        if old is not None:
            self.play(Transform(old, sq), run_time=0.4)
        else:
            self.cells[key] = sq
            self.play(FadeIn(sq, scale=0.6), run_time=0.4)
        return self.cells[key]

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
        """The rest of island 0, at speed, with the occupied count climbing."""
        counter = None
        note = self.cap("Most of these are worse than the best already found. They are kept "
                        "anyway,\nbecause the grid asks a different question: is anything else "
                        "like this?", 23, INK)
        note.move_to([GRID_X[0] + BINS * CELL / 2, -1.3, 0])
        self.play(FadeIn(note), run_time=0.6)

        shown = 0
        for i, ev in enumerate(PLACES):
            if i == 0 or ev["island"] != 0:
                continue
            if not ev["admitted"]:
                continue
            self.place(ev)
            shown += 1
            new = self.cap(f"{len(self.cells)} of {BINS * BINS} cells filled", 24, ORANGE)
            new.move_to([GRID_X[0] + BINS * CELL / 2, BINS * CELL + 0.35, 0])
            if counter is None:
                counter = new
                self.play(FadeIn(counter), run_time=0.3)
            else:
                self.play(Transform(counter, new), run_time=0.22)
        self.wait(1.8)
        self.play(FadeOut(note), FadeOut(counter), run_time=0.6)

    # ---- act 3 -----------------------------------------------------------
    def act_three(self):
        """The second island, migration, and what the run bought."""
        self.play(self.camera.frame.animate.move_to([0, 0.35, 0]).set(width=FULL_W), run_time=1.4)
        head = txt("islands: several grids, occasionally trading programs", 28, INK)
        head.move_to([0, 3.5, 0])
        tags = VGroup(*[txt(f"island {i + 1}", 20, SUB)
                        .move_to([GRID_X[i] + BINS * CELL / 2, BINS * CELL + 0.05, 0])
                        for i in range(ISLANDS)])
        self.play(FadeIn(head), FadeIn(self.grids[1]), FadeIn(self.axes_labels[2:]),
                  FadeIn(tags), run_time=1.2)

        rest = [e for e in EVENTS if e["kind"] == "migrate"
                or (e["kind"] == "place" and e["island"] != 0 and e["admitted"])]
        for ev in rest:
            if ev["kind"] == "place":
                self.place(ev)
            else:
                self.show_migration(ev)

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
