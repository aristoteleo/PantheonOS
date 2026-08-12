"""NicheMenu, as a narrated run.

    manim -qm --format=mp4 manim_nichemenu.py NicheMenuRun

**The tree is the protagonist.** Evolution is a tree growing; the loop is the machine that grows
it; the menu is the rule for where it grows next. The tree is on screen from the first working
frame to the last, and everything else -- the loop diagram, the menu grid, the fitness curve --
is arranged around it and stays in one place for the whole video.

Three acts:

  1. one full turn of the loop, slowly, all four structures updating in sync: a parent comes off
     the MENU, the mutation is an AGENT (edit -> run -> check, submit only what passes), the
     verifier scores it and the CURVE gains a point, and the child is RECORDED -- onto the tree,
     which keeps everything, and into the menu, which updates one representative. Then several
     turns at speed.
  2. the menu's bookkeeping, enlarged: metric axes, a tree node projecting into its cell, and the
     three admission cases from the real run -- into an empty cell (below the best so far, kept
     anyway), beating a representative, losing to one (still on the tree, still selectable).
  3. the rest of the run at speed, closing on best-vs-coverage.

The run is real: `sim_nichemenu` drives the actual `NicheMenu` through the actual loop and records
its decisions. Only the landscape and the mutation are invented.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Circle, Create, Dot, FadeIn, FadeOut,
                   Indicate, LaggedStart, Line, MovingCameraScene, RoundedRectangle, Square,
                   Transform, VGroup, Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, RED, SUB, arc,
                       fit, para, score_color, spoke, txt)
from sim_nichemenu import BINS, PLACES

LO, HI = 0.25, 0.75                      # the score range every colour in the video spans

# ---- the fixed stage ------------------------------------------------------
TREE_X0, TREE_DX, TREE_TOP, TREE_DY, TREE_R = -0.85, 0.74, 3.0, 0.52, 0.16
LOOP_AT = {"select": np.array([-5.55, 1.0, 0.0]), "mutate": np.array([-4.15, 2.35, 0.0]),
           "evaluate": np.array([-2.75, 1.0, 0.0]), "record": np.array([-4.15, -0.35, 0.0])}
"""The select box's LEFT edge is the binding constraint: anything past x = -7.11 is not rendered,
and the first layout put "select a parent" half off screen."""
THUMB_C, THUMB_CELL = np.array([-4.15, -2.3, 0.0]), 0.24
GRID_C, GRID_CELL = np.array([-4.3, 0.62, 0.0]), 0.5      # the menu, enlarged (act 2 onward)
CURVE_C = np.array([2.7, -2.9, 0.0])
CAP_AT = np.array([-3.85, -3.28, 0.0])                    # captions live left of the curve


def tree_layout(events):
    """`id -> (position, parent id, admitted, score)`, layered by DEPTH.

    Depth comes from the parent chain, not placement order: a child of an early node made late in
    the run belongs next to its parent, not next to its timestamp.
    """
    depth, out, per_layer = {}, {}, {}
    for e in events:
        pid = e.get("parent")
        d = 0 if pid not in depth else depth[pid] + 1
        depth[e["id"]] = d
        row = per_layer.get(d, 0)
        per_layer[d] = row + 1
        out[e["id"]] = (np.array([TREE_X0 + d * TREE_DX, TREE_TOP - row * TREE_DY, 0.0]),
                        pid, e["admitted"], e["score"])
    return out


TREE = tree_layout(PLACES)


def tree_dot(ev, lit=False) -> Circle:
    """Filled if it holds a cell in the menu, hollow if it was measured and is not a
    representative -- the tree keeps both, which is the point of drawing it."""
    pos, _, admitted, score = TREE[ev["id"]]
    return Circle(radius=TREE_R if admitted else TREE_R * 0.7,
                  stroke_width=2.6 if lit else (2.0 if admitted else 1.6),
                  color=GREEN if lit else (EDGE if admitted else MUTED),
                  fill_color=score_color(score, LO, HI),
                  fill_opacity=1.0 if admitted else 0.0).move_to(pos)


def cell_center(cell, center, size) -> np.ndarray:
    return center + np.array([(cell[0] - (BINS - 1) / 2) * size,
                              (cell[1] - (BINS - 1) / 2) * size, 0.0])


def grid_square(cell, center, size, score=None, lit=False) -> Square:
    return Square(side_length=size, stroke_width=2.6 if lit else 1.2,
                  color=GREEN if lit else EDGE,
                  fill_color=PANEL if score is None else score_color(score, LO, HI),
                  fill_opacity=1.0).move_to(cell_center(cell, center, size))


def loop_box(key: str, top: str, sub: str, accent=False) -> VGroup:
    body = VGroup(txt(top, 21, INK), txt(sub, 14, SUB)).arrange(DOWN, buff=0.1)
    frame = RoundedRectangle(width=body.width + 0.5, height=body.height + 0.42,
                             corner_radius=0.12, stroke_width=2.4 if accent else 1.6,
                             color=ORANGE if accent else EDGE,
                             fill_color=PANEL, fill_opacity=1.0)
    return VGroup(frame, body).move_to(LOOP_AT[key])


class NicheMenuRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("NicheMenu", 54, INK, weight="BOLD")
        sub = para("evolution is a tree, growing — a menu of niche representatives\n"
                   "decides where it grows next", 27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(1.8)
        self.play(FadeOut(card), run_time=0.9)

        self.tree_mobs, self.tree_edges = {}, VGroup()
        self.menu_center, self.menu_size = THUMB_C, THUMB_CELL
        self.menu_cells: dict = {}
        self.curve_dots, self.best_pts = VGroup(), []
        self.caption = None
        self.next_idx = 1                      # PLACES[0] is the seed

        self.act_one()
        self.act_two()
        self.act_three()

    # ---- shared drawing ---------------------------------------------------
    def say(self, text: str, size=22, color=INK, hold=0.0):
        new = para(text, size, color) if "\n" in text else txt(text, size, color)
        fit(new, 6.1).move_to(CAP_AT)
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
                        buff=TREE_R + 0.02).set_stroke(opacity=0.75)
            self.tree_edges.add(edge)
            anims.append(Create(edge))
        anims.append(FadeIn(dot, scale=0.5))
        return anims

    def menu_anims(self, ev, lit=False):
        """Update the child's cell from the recorded snapshot -- the snapshot already knows
        whether the representative changed."""
        cell = ev["cell"]
        held = ev["grid"].get(cell)
        if held is None:
            return []
        sq = grid_square(cell, self.menu_center, self.menu_size, held[1], lit=lit)
        old = self.menu_cells.get(cell)
        if old is not None:
            return [Transform(old, sq)]
        self.menu_cells[cell] = sq
        return [FadeIn(sq, scale=0.6)]

    def curve_anims(self, ev):
        i = len(self.best_pts)
        p = self.ax.c2p(i + 1, ev["score"])
        d = Dot(p, radius=0.035, color=BLUE).set_opacity(0.6)
        self.curve_dots.add(d)
        anims = [FadeIn(d, scale=0.4)]
        bp = self.ax.c2p(i + 1, ev["best"])
        if self.best_pts:
            seg = Line(self.best_pts[-1], bp, color=ORANGE, stroke_width=3.2)
            self.best_line.add(seg)
            anims.append(Create(seg))
        self.best_pts.append(bp)
        return anims

    def step_anims(self, ev, lit=False):
        return self.tree_anims(ev, lit) + self.menu_anims(ev, lit) + self.curve_anims(ev)

    # ---- act 1: the loop, the tree, the menu, the curve --------------------
    def act_one(self):
        boxes = {"select": loop_box("select", "select a parent", "one vote per niche"),
                 "mutate": loop_box("mutate", "mutate it", "a coding agent"),
                 "evaluate": loop_box("evaluate", "evaluate", "the verifier scores it"),
                 "record": loop_box("record", "record", "tree grows · menu updates")}
        self.boxes = boxes
        ring = VGroup()
        order = ["select", "mutate", "evaluate", "record"]
        for i, k in enumerate(order):
            a, b = LOOP_AT[k], LOOP_AT[order[(i + 1) % 4]]
            ring.add(arc(a, b, MUTED, 2.2, angle=-0.45,
                         buff_a=boxes[k].width * 0.38, buff_b=boxes[order[(i + 1) % 4]].width * 0.38))
        self.ring = ring

        thumb_frame = VGroup(*[grid_square((cx, cy), THUMB_C, THUMB_CELL)
                               for cx in range(BINS) for cy in range(BINS)])
        self.thumb_frame = thumb_frame
        menu_lab = txt("the menu", 16, SUB).next_to(thumb_frame, UP, buff=0.12)
        tether = Line(LOOP_AT["select"] + DOWN * 0.55, THUMB_C + UP * (BINS * THUMB_CELL / 2 + 0.4),
                      color=MUTED, stroke_width=1.6).set_stroke(opacity=0.6)
        self.menu_lab, self.tether = menu_lab, tether

        # curve strip
        self.ax = Axes(x_range=[0, len(PLACES) + 1, 5], y_range=[0, 1.0, 0.5],
                       x_length=6.8, y_length=1.5, tips=False,
                       axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        self.ax.move_to(CURVE_C)
        ticks = VGroup(txt("0", 13, MUTED).next_to(self.ax.c2p(0, 0), LEFT, buff=0.12),
                       txt("1", 13, MUTED).next_to(self.ax.c2p(0, 1.0), LEFT, buff=0.12),
                       txt("step", 13, MUTED).next_to(self.ax.c2p(len(PLACES) + 1, 0), DOWN,
                                                      buff=0.12))
        curve_lab = txt("fitness  ·  every child, and the best so far", 15, SUB)
        curve_lab.next_to(self.ax, UP, buff=0.1).align_to(self.ax, LEFT)
        self.best_line = VGroup()
        self.add(self.best_line, self.curve_dots)

        tree_lab = txt("the tree — everything ever made", 17, SUB)
        tree_lab.move_to([TREE_X0 + 2.9, TREE_TOP + 0.55, 0])

        seed = PLACES[0]
        self.play(LaggedStart(*[FadeIn(boxes[k], scale=0.9) for k in order], lag_ratio=0.15),
                  LaggedStart(*[Create(a) for a in ring], lag_ratio=0.15),
                  FadeIn(thumb_frame), FadeIn(menu_lab), FadeIn(tether),
                  Create(self.ax), FadeIn(ticks), FadeIn(curve_lab), FadeIn(tree_lab),
                  run_time=2.0)
        self.play(*self.step_anims(seed), run_time=0.8)
        self.say("the seed: one node on the tree, one representative in the menu, one point on "
                 "the curve", hold=1.6)

        # ---- one slow lap, on the run's real first child -------------------
        ev = PLACES[self.next_idx]
        self.next_idx += 1

        # SELECT
        pcell = ev.get("parent_cell")
        sel_marks = VGroup()
        if pcell is not None and pcell in self.menu_cells:
            sel_marks.add(self.menu_cells[pcell].copy().set_stroke(ORANGE, width=3.0)
                          .set_fill(opacity=0))
        ppos = TREE[ev["parent"]][0]
        sel_marks.add(Circle(radius=TREE_R + 0.07, color=ORANGE, stroke_width=3.0).move_to(ppos))
        sel_marks.add(arc(THUMB_C + RIGHT * (BINS * THUMB_CELL / 2), ppos, ORANGE, 2.0,
                          angle=0.35, buff_a=0.1, buff_b=TREE_R + 0.1))
        self.play(Indicate(boxes["select"], color=ORANGE, scale_factor=1.06),
                  FadeIn(sel_marks), run_time=0.9)
        self.say("select — the menu holds one representative per filled niche, one vote each.\n"
                 "The menu points back into the tree: the parent is a tree node.", hold=2.2)

        # MUTATE: the agent, opened up
        agent = self.agent_inset()
        self.play(Indicate(boxes["mutate"], color=ORANGE, scale_factor=1.06),
                  FadeIn(agent, scale=0.85), run_time=0.8)
        self.say("mutate — the operator is a coding agent: it edits, runs the evaluator itself,\n"
                 "and submits only an edit that passes. Not one model call — a loop of its own.")
        for _ in range(2):
            for j in range(3):
                # the TEXT, not the box: Indicate recolours fill as well, and an indicated
                # panel-filled rectangle flashes as a solid orange block
                self.play(Indicate(agent[1][j][1], color=ORANGE, scale_factor=1.25),
                          run_time=0.35)
        self.wait(0.8)

        # EVALUATE
        self.play(FadeOut(agent), Indicate(boxes["evaluate"], color=ORANGE, scale_factor=1.06),
                  *self.curve_anims(ev), run_time=0.9)
        self.say(f"evaluate — the verifier reports the metrics; this child scores "
                 f"{ev['score']:.2f}", hold=1.6)

        # RECORD
        self.play(Indicate(boxes["record"], color=ORANGE, scale_factor=1.06),
                  *self.tree_anims(ev, lit=True), *self.menu_anims(ev, lit=True), run_time=1.0)
        self.say("record — the tree keeps everything, always. The menu updates one cell's\n"
                 "representative — how, exactly, is the next scene.", hold=2.2)
        # Green means "this just happened"; a ring that survives the beat reads as a legend
        # entry nobody defined.
        self.play(FadeOut(sel_marks),
                  self.tree_mobs[ev["id"]].animate.set_stroke(EDGE, width=2.0),
                  self.menu_cells[ev["cell"]].animate.set_stroke(EDGE, width=1.2),
                  run_time=0.5)

        # ---- more laps, at speed -------------------------------------------
        self.say("the same turn, again and again — tree, menu and curve move together",
                 color=SUB)
        while self.next_idx < 10:
            e = PLACES[self.next_idx]
            self.next_idx += 1
            self.play(*self.step_anims(e), run_time=0.5)
        self.wait(1.0)
        self.drop_caption()

    def agent_inset(self) -> VGroup:
        steps = VGroup(*[VGroup(RoundedRectangle(width=1.0, height=0.5, corner_radius=0.08,
                                                 stroke_width=1.4, color=EDGE,
                                                 fill_color=PANEL, fill_opacity=1.0),
                                txt(w, 15, INK))
                         for w in ("edit", "run", "check")])
        for box in steps:
            box[1].move_to(box[0])
        steps.arrange(RIGHT, buff=0.45)
        arrows = VGroup(spoke(steps[0].get_center() + RIGHT * 0.5,
                              steps[1].get_center() + LEFT * 0.5, MUTED, 1.6, 0.05, 0.05),
                        spoke(steps[1].get_center() + RIGHT * 0.5,
                              steps[2].get_center() + LEFT * 0.5, MUTED, 1.6, 0.05, 0.05),
                        arc(steps[2].get_center() + DOWN * 0.28,
                            steps[0].get_center() + DOWN * 0.28, MUTED, 1.4, angle=0.7,
                            buff_a=0.1, buff_b=0.1))
        retry = txt("fails? edit again", 13, MUTED).next_to(arrows[2], DOWN, buff=0.06)
        frame = RoundedRectangle(width=steps.width + 0.7, height=2.0, corner_radius=0.15,
                                 stroke_width=1.8, color=ORANGE, fill_color=PANEL,
                                 fill_opacity=0.97)
        head = txt("the agent", 15, ORANGE)
        g = VGroup(frame, steps, arrows, retry, head)
        steps.move_to(frame.get_center() + UP * 0.18)
        arrows[0].become(spoke(steps[0].get_center() + RIGHT * 0.5,
                               steps[1].get_center() + LEFT * 0.5, MUTED, 1.6, 0.05, 0.05))
        arrows[1].become(spoke(steps[1].get_center() + RIGHT * 0.5,
                               steps[2].get_center() + LEFT * 0.5, MUTED, 1.6, 0.05, 0.05))
        arrows[2].become(arc(steps[2].get_center() + DOWN * 0.3,
                             steps[0].get_center() + DOWN * 0.3, MUTED, 1.4, angle=0.7,
                             buff_a=0.12, buff_b=0.12))
        retry.next_to(arrows[2], DOWN, buff=0.05)
        head.next_to(frame.get_top(), DOWN, buff=0.1)
        return g.move_to([-4.35, 1.0, 0])

    # ---- act 2: the menu's bookkeeping -------------------------------------
    def act_two(self):
        # the loop has been taught; the menu takes its place, enlarged
        grown = {}
        anims = [FadeOut(self.boxes[k]) for k in self.boxes] + [FadeOut(self.ring),
                                                                FadeOut(self.tether),
                                                                FadeOut(self.menu_lab)]
        frame_new = VGroup(*[grid_square((cx, cy), GRID_C, GRID_CELL)
                             for cx in range(BINS) for cy in range(BINS)])
        anims.append(Transform(self.thumb_frame, frame_new))
        for cell, sq in self.menu_cells.items():
            held = None
            for e in PLACES[: self.next_idx]:
                held = e["grid"].get(cell, held)
            big = grid_square(cell, GRID_C, GRID_CELL, held[1] if held else None)
            grown[cell] = big
            anims.append(Transform(sq, big))
        self.menu_center, self.menu_size = GRID_C, GRID_CELL
        self.play(*anims, run_time=1.4)

        xlab = txt("metric 1 →", 16, MUTED).next_to(
            GRID_C + DOWN * (BINS * GRID_CELL / 2), DOWN, buff=0.15)
        ylab = txt("metric 2 →", 16, MUTED).rotate(np.pi / 2).next_to(
            GRID_C + LEFT * (BINS * GRID_CELL / 2), LEFT, buff=0.15)
        head = txt("the menu, up close", 24, INK).next_to(
            GRID_C + UP * (BINS * GRID_CELL / 2), UP, buff=0.28)
        self.play(FadeIn(xlab), FadeIn(ylab), FadeIn(head), run_time=0.8)
        self.say("the axes are two metrics the evaluator reports — whatever the problem\n"
                 "measures. Each cell is one niche: one combination of the two.", hold=2.4)

        # projection: a tree node flies into its cell
        pe = PLACES[3]
        ghost = tree_dot(pe).copy()
        target = cell_center(pe["cell"], GRID_C, GRID_CELL)
        self.say("every tree node has coordinates in this space — the menu files each one\n"
                 "by its cell, and remembers only the best per cell", color=SUB)
        self.play(ghost.animate.move_to(target).scale(1.4), run_time=1.2)
        self.play(FadeOut(ghost), run_time=0.4)

        # the three admission cases, in stream order -- the run happens to supply them back to
        # back at indices 11, 12, 13; index 10 just plays through quietly first
        while self.next_idx < 11:
            e = PLACES[self.next_idx]
            self.next_idx += 1
            self.play(*self.step_anims(e), run_time=0.5)

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
            tok = tree_dot(ev).copy().scale(1.3)
            self.play(FadeIn(tok, scale=0.5), *self.tree_anims(ev), *self.curve_anims(ev),
                      run_time=0.7)
            self.say(text.format(score=ev["score"], best=ev["best"]))
            tgt = cell_center(ev["cell"], GRID_C, GRID_CELL)
            self.play(tok.animate.move_to(tgt), run_time=0.9)
            if admitted:
                self.play(FadeOut(tok), *self.menu_anims(ev, lit=True), run_time=0.7)
                self.wait(1.4)
                self.play(self.menu_cells[ev["cell"]].animate.set_stroke(EDGE, width=1.2),
                          self.tree_mobs[ev["id"]].animate.set_stroke(EDGE, width=2.0),
                          run_time=0.4)
            else:
                back = TREE[ev["id"]][0]
                self.play(tok.animate.set_stroke(RED, width=3.0), run_time=0.4)
                self.play(tok.animate.move_to(back).scale(1 / 1.3), run_time=0.8)
                self.play(FadeOut(tok), run_time=0.3)
                self.wait(1.4)

        note = para("the menu is a derived index — it holds no programs, only pointers into the "
                    "tree.\nIts one job: decide where the tree grows next. (The grid is "
                    "MAP-Elites'; here it only serves selection.)", 20, SUB)
        fit(note, 6.2).move_to(CAP_AT)
        self.play(Transform(self.caption, note), run_time=0.6)
        self.wait(3.2)
        self.play(FadeOut(head), run_time=0.4)

    # ---- act 3: the rest of the run ----------------------------------------
    def act_three(self):
        self.say("the rest of the budget, at speed", color=SUB)
        while self.next_idx < len(PLACES):
            e = PLACES[self.next_idx]
            self.next_idx += 1
            self.play(*self.step_anims(e), run_time=0.42)

        # coverage joins the curve for the closing point
        cov = VGroup()
        for i in range(1, len(PLACES)):
            cov.add(Line(self.ax.c2p(i, PLACES[i - 1]["coverage"]),
                         self.ax.c2p(i + 1, PLACES[i]["coverage"]),
                         color=BLUE, stroke_width=3.2))
        cov_lab = txt("cells filled", 14, BLUE).next_to(self.ax.c2p(len(PLACES),
                      PLACES[-1]["coverage"]), UP + LEFT, buff=0.16)
        self.play(Create(cov), FadeIn(cov_lab), run_time=1.6)
        self.say("the best score stalls for long stretches; coverage does not — and every cell\n"
                 "filled during a stall is a parent the search would not otherwise have had",
                 hold=3.0)
        self.say("(in practice several menus run in parallel as islands, occasionally trading\n"
                 "representatives — a deployment detail, not the mechanism)", color=SUB, hold=2.6)
