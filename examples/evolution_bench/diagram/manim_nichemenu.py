"""NicheMenu, as a narrated run.

    manim -qm --format=mp4 manim_nichemenu.py NicheMenuRun

**The tree is the protagonist.** Evolution is a tree growing; the loop is the machine that grows
it; the MAP-Elites grid is the rule for where it grows next. (NicheMenu is the METHOD's name --
the structure on screen is a MAP-Elites grid and is labelled as one.) Every structure holds one
fixed position for the whole video: loop top-left, grid bottom-left, tree right, curve bottom.

Three acts:

  1. one full turn of the loop, slowly, all four structures updating in sync -- the parent is
     drawn from the grid and the grid points back into the tree; the mutation is an AGENT
     (edit -> run -> check, submit only what passes); the verifier's score lands on the curve;
     the child is recorded onto the tree and into the grid. Then turns at speed, and EVERY turn
     shows its selection: the drawn cell flashes, the parent node lights, the child appears.
  2. the grid's bookkeeping, in place: metric axes, a tree node projecting into its cell, the
     run's real admission cases, and what selection means over the cells now filled.
  3. the rest of the run at speed, closing on best-vs-coverage.

The run is real: `sim_nichemenu` drives the actual `NicheMenu` method through the actual loop and
records its decisions. Only the landscape and the mutation are invented.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Circle, Create, Dot, FadeIn, FadeOut,
                   Indicate, LaggedStart, Line, MovingCameraScene, RoundedRectangle, Square,
                   Transform, VGroup, Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, RED, SUB, arc,
                       fit, para, score_color, spoke, txt)
from sim_nichemenu import BINS, PLACES

LO, HI = 0.25, 0.75                      # the score range every colour in the video spans

# ---- the fixed stage ------------------------------------------------------
LOOP_AT = {"select": np.array([-5.55, 1.75, 0.0]), "mutate": np.array([-3.95, 3.0, 0.0]),
           "evaluate": np.array([-2.35, 1.75, 0.0]), "record": np.array([-3.95, 0.5, 0.0])}
BOX_W, BOX_H = 2.55, 0.95
"""Equal boxes. Four different widths read as four different kinds of thing, and they are four
stations of one loop."""
GRID_C, GRID_CELL = np.array([-4.05, -1.5, 0.0]), 0.46
CURVE_C = np.array([3.35, -2.75, 0.0])
CAP_AT = np.array([-3.6, -3.42, 0.0])
TREE_TOP, TREE_DY = 3.0, 0.5


def tidy_tree(events):
    """A real tree layout: each subtree owns a contiguous span of leaf slots, and every parent
    sits centred over its children.

    The first version layered nodes by depth and stacked them by ARRIVAL order, which scatters
    siblings and crosses edges -- it drew the data, not the tree.
    """
    children = defaultdict(list)
    for e in events:
        children[e.get("parent")].append(e["id"])
    root = events[0]["id"]

    xs, depths, next_slot = {}, {}, [0]

    def place(nid, depth):
        depths[nid] = depth
        kids = children.get(nid, [])
        if not kids:
            xs[nid] = float(next_slot[0])
            next_slot[0] += 1
            return
        for k in kids:
            place(k, depth + 1)
        xs[nid] = sum(xs[k] for k in kids) / len(kids)

    place(root, 0)
    n_leaves = next_slot[0]
    dx = min(0.52, 6.4 / max(1, n_leaves - 1))
    x0 = 3.45 - dx * (n_leaves - 1) / 2

    by_id = {e["id"]: e for e in events}
    return {nid: (np.array([x0 + xs[nid] * dx, TREE_TOP - depths[nid] * TREE_DY, 0.0]),
                  by_id[nid].get("parent"), by_id[nid]["admitted"], by_id[nid]["score"])
            for nid in xs}


TREE = tidy_tree(PLACES)
TREE_R = 0.155


def tree_dot(ev, lit=False) -> Circle:
    """Filled if it holds a cell in the grid, hollow if it was measured and is not a
    representative -- the tree keeps both, which is the point of drawing it."""
    pos, _, admitted, score = TREE[ev["id"]]
    return Circle(radius=TREE_R if admitted else TREE_R * 0.7,
                  stroke_width=2.6 if lit else (2.0 if admitted else 1.6),
                  color=GREEN if lit else (EDGE if admitted else MUTED),
                  fill_color=score_color(score, LO, HI),
                  fill_opacity=1.0 if admitted else 0.0).move_to(pos)


def cell_center(cell) -> np.ndarray:
    return GRID_C + np.array([(cell[0] - (BINS - 1) / 2) * GRID_CELL,
                              (cell[1] - (BINS - 1) / 2) * GRID_CELL, 0.0])


def grid_square(cell, score=None, lit=False) -> Square:
    return Square(side_length=GRID_CELL, stroke_width=2.6 if lit else 1.2,
                  color=GREEN if lit else EDGE,
                  fill_color=PANEL if score is None else score_color(score, LO, HI),
                  fill_opacity=1.0).move_to(cell_center(cell))


def loop_box(key: str, top: str, sub: str) -> VGroup:
    body = VGroup(txt(top, 20, INK), txt(sub, 13.5, SUB)).arrange(DOWN, buff=0.09)
    frame = RoundedRectangle(width=BOX_W, height=BOX_H, corner_radius=0.13, stroke_width=1.7,
                             color=EDGE, fill_color=PANEL, fill_opacity=1.0)
    return VGroup(frame, body).move_to(LOOP_AT[key])


def ring_arrow(a, b, angle=-0.55):
    """Between box ANCHORS, with a head sized to be seen. The first version ran centre-to-centre
    with the kit's default tip, which left stubby arcs and arrowheads the size of commas."""
    return arc(a, b, SUB, 2.6, angle=angle, buff_a=0.07, buff_b=0.09, tip=0.17)


class NicheMenuRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("NicheMenu", 54, INK, weight="BOLD")
        sub = para("evolution is a tree, growing —\na MAP-Elites grid decides where it grows next",
                   27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(1.8)
        self.play(FadeOut(card), run_time=0.9)

        self.tree_mobs, self.tree_edges = {}, VGroup()
        self.cells: dict = {}
        self.curve_dots, self.best_pts = VGroup(), []
        self.caption = None
        self.next_idx = 1                      # PLACES[0] is the seed

        self.act_one()
        self.act_two()
        self.act_three()

    # ---- shared drawing ---------------------------------------------------
    def say(self, text: str, size=22, color=INK, hold=0.0):
        new = para(text, size, color) if "\n" in text else txt(text, size, color)
        fit(new, 6.6).move_to(CAP_AT)
        if self.caption is None:
            self.caption = new
            self.play(FadeIn(new), run_time=0.5)
        else:
            self.play(Transform(self.caption, new), run_time=0.5)
        if hold:
            self.wait(hold)

    def drop_caption(self):
        if self.caption is not None:
            self.play(FadeOut(self.caption), run_time=0.4)
            self.caption = None

    def tree_anims(self, ev, lit=False):
        dot = tree_dot(ev, lit=lit)
        self.tree_mobs[ev["id"]] = dot
        anims = []
        pid = TREE[ev["id"]][1]
        if pid in self.tree_mobs:
            a, b = TREE[pid][0], TREE[ev["id"]][0]
            edge = Line(a, b, color=EDGE, stroke_width=1.5,
                        buff=TREE_R + 0.02).set_stroke(opacity=0.8)
            self.tree_edges.add(edge)
            anims.append(Create(edge))
        anims.append(FadeIn(dot, scale=0.5))
        return anims

    def grid_anims(self, ev, lit=False):
        """Update the child's cell from the recorded snapshot -- the snapshot already knows
        whether the representative changed."""
        cell = ev["cell"]
        held = ev["grid"].get(cell)
        if held is None:
            return []
        sq = grid_square(cell, held[1], lit=lit)
        old = self.cells.get(cell)
        if old is not None:
            return [Transform(old, sq)]
        self.cells[cell] = sq
        return [FadeIn(sq, scale=0.6)]

    def curve_anims(self, ev):
        i = len(self.best_pts)
        p = self.ax.c2p(i + 1, ev["score"])
        d = Dot(p, radius=0.045, color=BLUE).set_opacity(0.65)
        self.curve_dots.add(d)
        anims = [FadeIn(d, scale=0.4)]
        bp = self.ax.c2p(i + 1, ev["best"])
        if self.best_pts:
            seg = Line(self.best_pts[-1], bp, color=ORANGE, stroke_width=3.6)
            self.best_line.add(seg)
            anims.append(Create(seg))
        self.best_pts.append(bp)
        return anims

    def select_flashes(self, ev):
        """The selection, made visible on every single step: the drawn cell flashes and the
        parent node lights up BEFORE the child exists. Without this the grid sits there being
        updated and its entire purpose -- handing out parents -- happens off screen."""
        anims = []
        pcell = ev.get("parent_cell")
        if ev.get("from_menu") and pcell is not None and pcell in self.cells:
            anims.append(Indicate(self.cells[pcell], color=ORANGE, scale_factor=1.18))
        if ev.get("parent") in self.tree_mobs:
            anims.append(Indicate(self.tree_mobs[ev["parent"]], color=ORANGE, scale_factor=1.3))
        return anims

    def step(self, ev, run_time=0.55):
        flashes = self.select_flashes(ev)
        if flashes:
            self.play(*flashes, run_time=run_time * 0.55)
        self.play(*self.tree_anims(ev), *self.grid_anims(ev), *self.curve_anims(ev),
                  run_time=run_time)

    # ---- act 1: the loop, the tree, the grid, the curve --------------------
    def act_one(self):
        boxes = {"select": loop_box("select", "select a parent", "from the MAP-Elites grid"),
                 "mutate": loop_box("mutate", "mutate it", "a coding agent"),
                 "evaluate": loop_box("evaluate", "evaluate", "the verifier scores it"),
                 "record": loop_box("record", "record", "tree grows · grid updates")}
        self.boxes = boxes
        b = boxes
        ring = VGroup(
            ring_arrow(b["select"].get_top() + RIGHT * 0.5, b["mutate"].get_left(), -0.45),
            ring_arrow(b["mutate"].get_right(), b["evaluate"].get_top() + LEFT * 0.5, -0.45),
            ring_arrow(b["evaluate"].get_bottom() + LEFT * 0.5, b["record"].get_right(), -0.45),
            ring_arrow(b["record"].get_left(), b["select"].get_bottom() + RIGHT * 0.5, -0.45))
        self.ring = ring

        grid_frame = VGroup(*[grid_square((cx, cy)) for cx in range(BINS) for cy in range(BINS)])
        grid_lab = txt("the MAP-Elites grid", 17, SUB).next_to(grid_frame, UP, buff=0.14)
        feeds = arc(GRID_C + UP * (BINS * GRID_CELL / 2) + LEFT * 0.95,
                    b["select"].get_bottom() + LEFT * 0.55, MUTED, 2.2, angle=0.4,
                    buff_a=0.34, buff_b=0.08, tip=0.15)
        self.grid_frame, self.grid_lab, self.feeds = grid_frame, grid_lab, feeds

        self.ax = Axes(x_range=[0, len(PLACES) + 1, 5], y_range=[0, 0.8, 0.2],
                       x_length=6.6, y_length=2.1, tips=False,
                       axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        self.ax.move_to(CURVE_C)
        ticks = VGroup(*[txt(f"{v:.1f}", 13, MUTED).next_to(self.ax.c2p(0, v), LEFT, buff=0.1)
                         for v in (0.0, 0.4, 0.8)],
                       *[txt(str(v), 13, MUTED).next_to(self.ax.c2p(v, 0), DOWN, buff=0.1)
                         for v in (10, 20)],
                       txt("step", 13, MUTED).next_to(self.ax.c2p(len(PLACES) + 1, 0), DOWN,
                                                      buff=0.1))
        curve_lab = txt("fitness · every child, and the best so far", 15, SUB)
        curve_lab.next_to(self.ax, UP, buff=0.1).align_to(self.ax, LEFT)
        self.best_line = VGroup()
        self.add(self.best_line, self.curve_dots)

        tree_lab = txt("the tree — everything ever made", 17, SUB).move_to([3.45, 3.55, 0])

        seed = PLACES[0]
        self.play(LaggedStart(*[FadeIn(boxes[k], scale=0.9) for k in
                                ("select", "mutate", "evaluate", "record")], lag_ratio=0.15),
                  LaggedStart(*[Create(a) for a in ring], lag_ratio=0.15),
                  FadeIn(grid_frame), FadeIn(grid_lab), Create(feeds),
                  Create(self.ax), FadeIn(ticks), FadeIn(curve_lab), FadeIn(tree_lab),
                  run_time=2.0)
        self.play(*self.tree_anims(seed), *self.grid_anims(seed), *self.curve_anims(seed),
                  run_time=0.8)
        self.say("the seed: one node on the tree, one cell in the grid, one point on the curve",
                 hold=1.6)

        # ---- one slow lap, on the run's real first child -------------------
        ev = PLACES[self.next_idx]
        self.next_idx += 1

        # SELECT
        pcell = ev.get("parent_cell")
        sel_marks = VGroup()
        if pcell is not None and pcell in self.cells:
            sel_marks.add(self.cells[pcell].copy().set_stroke(ORANGE, width=3.2)
                          .set_fill(opacity=0))
        ppos = TREE[ev["parent"]][0]
        sel_marks.add(Circle(radius=TREE_R + 0.08, color=ORANGE, stroke_width=3.0).move_to(ppos))
        sel_marks.add(arc(cell_center(pcell) if pcell else GRID_C, ppos, ORANGE, 2.2,
                          angle=0.3, buff_a=GRID_CELL * 0.7, buff_b=TREE_R + 0.12, tip=0.15))
        self.play(Indicate(boxes["select"][1][0], color=ORANGE, scale_factor=1.15),
                  FadeIn(sel_marks), run_time=0.9)
        self.say("select — a uniform draw over the grid's filled cells, one vote each.\n"
                 "The cell holds a pointer; the parent itself is a node of the tree.", hold=2.2)

        # MUTATE: the agent, opened up
        agent = self.agent_inset()
        self.play(Indicate(boxes["mutate"][1][0], color=ORANGE, scale_factor=1.15),
                  FadeIn(agent, scale=0.85), run_time=0.8)
        self.say("mutate — the operator is a coding agent: it edits, runs the evaluator itself,\n"
                 "and submits only an edit that passes. Not one model call — a loop of its own.")
        for _ in range(2):
            for j in range(3):
                self.play(Indicate(agent[1][j][1], color=ORANGE, scale_factor=1.25),
                          run_time=0.32)
        self.wait(0.7)

        # EVALUATE
        self.play(FadeOut(agent),
                  Indicate(boxes["evaluate"][1][0], color=ORANGE, scale_factor=1.15),
                  *self.curve_anims(ev), run_time=0.9)
        self.say(f"evaluate — the verifier reports the metrics; this child scores "
                 f"{ev['score']:.2f}", hold=1.6)

        # RECORD
        self.play(Indicate(boxes["record"][1][0], color=ORANGE, scale_factor=1.15),
                  *self.tree_anims(ev, lit=True), *self.grid_anims(ev, lit=True), run_time=1.0)
        self.say("record — the tree keeps everything, always. The grid updates one cell's\n"
                 "representative — the rules for that are the next scene.", hold=2.2)
        # Green means "this just happened"; a ring that survives the beat reads as a legend
        # entry nobody defined.
        self.play(FadeOut(sel_marks),
                  self.tree_mobs[ev["id"]].animate.set_stroke(EDGE, width=2.0),
                  self.cells[ev["cell"]].animate.set_stroke(EDGE, width=1.2),
                  run_time=0.5)

        # ---- more laps, at speed, each with its selection visible ----------
        self.say("the same turn, again and again — watch the grid hand out each parent",
                 color=SUB)
        while self.next_idx < 10:
            e = PLACES[self.next_idx]
            self.next_idx += 1
            self.step(e)
        self.wait(1.0)
        self.drop_caption()

    def agent_inset(self) -> VGroup:
        """The mutate box, opened up. Sized to its contents.

        The first version fixed the frame at 2.0 tall and parked the steps in its upper half:
        half the panel was dead space, the retry arc cut straight through the "run" box, and a
        translucent fill let the loop boxes underneath ghost through the text.
        """
        steps = VGroup(*[VGroup(RoundedRectangle(width=1.0, height=0.5, corner_radius=0.08,
                                                 stroke_width=1.4, color=EDGE,
                                                 fill_color=PANEL, fill_opacity=1.0),
                                txt(w, 15, INK))
                         for w in ("edit", "run", "check")])
        for box in steps:
            box[1].move_to(box[0])
        steps.arrange(RIGHT, buff=0.5)
        fwd = VGroup(spoke(steps[0].get_right(), steps[1].get_left(), MUTED, 1.6,
                           0.04, 0.04, tip=0.12),
                     spoke(steps[1].get_right(), steps[2].get_left(), MUTED, 1.6,
                           0.04, 0.04, tip=0.12))
        # the retry path goes UNDER the row -- it is a return edge, not a strike-through
        retry_arc = arc(steps[2].get_bottom() + DOWN * 0.05, steps[0].get_bottom() + DOWN * 0.05,
                        MUTED, 1.5, angle=0.55, buff_a=0.04, buff_b=0.04, tip=0.12)
        retry = txt("fails? edit again", 13, MUTED).next_to(retry_arc, DOWN, buff=0.07)
        head = txt("the agent", 15, ORANGE)

        content = VGroup(head, steps, fwd, retry_arc, retry)
        head.next_to(steps, UP, buff=0.22)
        frame = RoundedRectangle(width=content.width + 0.55, height=content.height + 0.4,
                                 corner_radius=0.15, stroke_width=1.8, color=ORANGE,
                                 fill_color=PANEL, fill_opacity=1.0)
        frame.move_to(content.get_center())
        return VGroup(frame, steps, fwd, retry_arc, retry, head).move_to([-3.95, 1.7, 0])

    # ---- act 2: the grid's bookkeeping --------------------------------------
    def act_two(self):
        # the loop has been taught; attention moves to the grid, which stays where it is
        self.play(*[self.boxes[k][0].animate.set_stroke(opacity=0.35) for k in self.boxes],
                  *[self.boxes[k][1].animate.set_opacity(0.35) for k in self.boxes],
                  self.ring.animate.set_stroke(opacity=0.2),
                  run_time=0.8)

        xlab = txt("metric 1 →", 15, MUTED).next_to(
            GRID_C + DOWN * (BINS * GRID_CELL / 2), DOWN, buff=0.12)
        ylab = txt("metric 2 →", 15, MUTED).rotate(np.pi / 2).next_to(
            GRID_C + LEFT * (BINS * GRID_CELL / 2), LEFT, buff=0.12)
        self.play(FadeIn(xlab), FadeIn(ylab), run_time=0.7)
        self.say("the grid's axes are two metrics the evaluator reports — whatever the problem\n"
                 "measures. Each cell is one niche; a filled cell holds its best program so far.",
                 hold=2.4)

        # projection: a tree node flies into its cell
        pe = PLACES[3]
        ghost = tree_dot(pe).copy()
        self.say("every tree node has coordinates in this space — the grid files each one by "
                 "its cell", color=SUB)
        self.play(ghost.animate.move_to(cell_center(pe["cell"])).scale(1.35), run_time=1.2)
        self.play(FadeOut(ghost), run_time=0.4)

        # the three admission cases, in stream order -- the run supplies them back to back
        while self.next_idx < 11:
            e = PLACES[self.next_idx]
            self.next_idx += 1
            self.step(e, run_time=0.45)

        cases = [
            ("its cell is EMPTY, so it becomes the representative —\n"
             "even at {score:.2f}, below the best so far ({best:.2f})", True),
            ("it lands on a held cell and SCORES HIGHER —\nit takes over as the representative",
             True),
            ("it lands on a held cell and loses. It is not the representative —\n"
             "but it stays on the tree, and it can still be drawn as a parent", False),
        ]
        for text, admitted in cases:
            ev = PLACES[self.next_idx]
            self.next_idx += 1
            assert ev["admitted"] == admitted, "the recorded run no longer matches the script"
            flashes = self.select_flashes(ev)
            if flashes:
                self.play(*flashes, run_time=0.4)
            tok = tree_dot(ev).copy().scale(1.25)
            self.play(FadeIn(tok, scale=0.5), *self.tree_anims(ev), *self.curve_anims(ev),
                      run_time=0.7)
            self.say(text.format(score=ev["score"], best=ev["best"]))
            self.play(tok.animate.move_to(cell_center(ev["cell"])), run_time=0.9)
            if admitted:
                self.play(FadeOut(tok), *self.grid_anims(ev, lit=True), run_time=0.7)
                self.wait(1.4)
                self.play(self.cells[ev["cell"]].animate.set_stroke(EDGE, width=1.2),
                          self.tree_mobs[ev["id"]].animate.set_stroke(EDGE, width=2.0),
                          run_time=0.4)
            else:
                self.play(tok.animate.set_stroke(RED, width=3.0), run_time=0.4)
                self.play(tok.animate.move_to(TREE[ev["id"]][0]).scale(1 / 1.25), run_time=0.8)
                self.play(FadeOut(tok), run_time=0.3)
                self.wait(1.4)

        # the selection rule, over everything the grid now holds
        votes = VGroup(*[sq.copy().set_stroke(ORANGE, width=2.8).set_fill(opacity=0)
                         for sq in self.cells.values()])
        self.say(f"and this is what selection means now: {len(self.cells)} filled cells,\n"
                 "one vote each — however crowded a niche is, it gets exactly one", hold=0.4)
        self.play(LaggedStart(*[FadeIn(v) for v in votes], lag_ratio=0.06), run_time=1.2)
        self.wait(1.8)
        self.play(FadeOut(votes), run_time=0.5)

        note = para("the grid holds no programs, only pointers into the tree. Its one job in "
                    "this method:\ndecide where the tree grows next.", 21, SUB)
        fit(note, 6.6).move_to(CAP_AT)
        self.play(Transform(self.caption, note), run_time=0.6)
        self.wait(2.8)

    # ---- act 3: the rest of the run ----------------------------------------
    def act_three(self):
        self.say("the rest of the budget, at speed", color=SUB)
        while self.next_idx < len(PLACES):
            e = PLACES[self.next_idx]
            self.next_idx += 1
            self.step(e, run_time=0.4)

        # coverage joins the curve for the closing point
        cov = VGroup()
        for i in range(1, len(PLACES)):
            cov.add(Line(self.ax.c2p(i, PLACES[i - 1]["coverage"]),
                         self.ax.c2p(i + 1, PLACES[i]["coverage"]),
                         color=BLUE, stroke_width=3.2))
        cov_lab = txt("cells filled", 14, BLUE).next_to(
            self.ax.c2p(len(PLACES) - 3, PLACES[-1]["coverage"]), UP, buff=0.12)
        self.play(Create(cov), FadeIn(cov_lab), run_time=1.6)
        self.say("the best score stalls for long stretches; coverage does not — and every cell\n"
                 "filled during a stall is a parent the search would not otherwise have had",
                 hold=3.0)
        self.say("(in practice several grids run in parallel as islands, occasionally trading\n"
                 "representatives — a deployment detail, not the mechanism)", color=SUB, hold=2.6)
