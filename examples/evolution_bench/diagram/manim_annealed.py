"""AnnealedIdeaCode, as a narrated run.

    manim -qm --format=mp4 manim_annealed.py AnnealedRun

**The tree is the protagonist, and it has two layers.** IDEAS are diamonds; PROGRAMS are circles
hanging under the idea they implement. Three kinds of edge, because the method issues three kinds
of parentage: refine (diamond to diamond), implementation (diamond to its circles), and BASE
inheritance (dashed, circle to circle) -- the code an implementation actually started from, which
is often the best program of a different idea. Every program knows two parents: the code it
edited and the idea it served.

Fixed stage, as in the sibling videos: loop top-left with a runner dot, the annealed MIX BAR and
the IDEA PANEL bottom-left (the method's two levers: which action, then which idea), tree right,
fitness curve bottom right. The idea panel shows value = mu + beta*sigma per idea -- lavender
while the value is only the judge's prediction, blue once an implementation has measured it --
with the selection probability beside each bar.

Acts:

  1. two slow laps -- one NEW (the judge PREDICTS; nothing runs, the curve gets no point) and one
     IMPLEMENT (softmax pick over the panel, the agent opens up, the verifier MEASURES, the
     idea's bar turns from prediction to fact). Then speed.
  2. the schedule: the mix slides from proposing to implementing while the temperature falls and
     the selection distribution collapses onto the leader.
  3. the judge, learning -- and the only act with REAL numbers in it. The pairs come from six
     actual runs on the Erdos problem (`judge_data`), replayed one step ahead: the identity while
     the evidence is thin, the first fit at five pairs, one bad extrapolation, and the observation
     that repairs it. It closes on what the calibration was actually worth, which is not much yet.
  4. the rest of the run at speed, closing on where the budget actually went.

The run is real: `sim_annealed` drives the actual method, schedule, selection and calibration
through the actual loop. Only the judge's model call is stubbed -- the title card says so.
"""
from __future__ import annotations

import numpy as np
from manim import (DL, DOWN, DR, LEFT, ORIGIN, RIGHT, UL, UP, UR, ArcBetweenPoints, Axes,
                   Circle, Create, DashedLine, DashedVMobject, Dot, FadeIn, FadeOut,
                   GrowFromPoint, Indicate, LaggedStart, Line, MoveAlongPath,
                   MovingCameraScene, Rectangle, RoundedRectangle, Square, Transform, VGroup,
                   VMobject, Write)

from judge_data import FEATURED_RHO, STEPS, SUMMARY, TALLY
from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, PURPLE, SUB,
                       arc, dim, fit, para, score_color, spoke, txt)
from sim_annealed import EVENTS, IMPLS, SEED_ID, SEED_SCORE

LO, HI = 0.35, 0.95                      # the score range every colour in the video spans

# ---- the fixed stage ------------------------------------------------------
LOOP_AT = {"action": np.array([-5.75, 1.75, 0.0]), "generate": np.array([-3.95, 3.05, 0.0]),
           "score": np.array([-2.15, 1.75, 0.0]), "record": np.array([-3.95, 0.45, 0.0])}
BOX_W, BOX_H = 2.55, 0.95
MIX_C, MIX_W, MIX_H = np.array([-4.05, -0.30, 0.0]), 3.2, 0.22
PANEL_TOP, ROW_H = -1.15, 0.27
PANEL_X0, BAR_X0, BAR_WMAX = -5.85, -5.05, 2.1
CURVE_C = np.array([3.35, -2.75, 0.0])
CAP_AT = np.array([-3.6, -3.42, 0.0])

IDEA_Y, STACK_TOP, STACK_DY, SUB_DX = 2.95, 2.3, 0.4, 0.24
IDEA_R, PROG_R = 0.19, 0.125
SEED_POS = np.array([0.55, 2.3, 0.0])

ACTIONS = [("NEW", PURPLE), ("REFINE", BLUE), ("IMPL", GREEN)]
LAVENDER = "#b9a7e6"
BY_ID = {e["id"]: e for e in EVENTS}


def tree_layout(events):
    """Ideas as columns, implementations stacked beneath their idea.

    Column positions are recomputed over whatever exists so far and re-centred, so the first
    diamond is born in the middle and the row spreads as ideas arrive. A stack deeper than eight
    wraps into a second sub-column -- the run's leading idea collects fifteen implementations,
    and fifteen circles at readable size do not fit in one column.
    """
    ideas = [e["id"] for e in events if e["kind"] == "idea"]
    sp = min(0.95, 5.9 / max(1, len(ideas) - 1))
    x0 = 3.7 - sp * (len(ideas) - 1) / 2
    pos = {SEED_ID: SEED_POS} if SEED_ID else {}
    for i, nid in enumerate(ideas):
        pos[nid] = np.array([x0 + i * sp, IDEA_Y, 0.0])
    counts: dict = {}
    total = {}
    for e in events:
        if e["kind"] == "code" and e["id"] != SEED_ID:
            total[e["anchor"]] = total.get(e["anchor"], 0) + 1
    for e in events:
        if e["kind"] != "code" or e["id"] == SEED_ID:
            continue
        k = counts.get(e["anchor"], 0)
        counts[e["anchor"]] = k + 1
        col, row = divmod(k, 8)
        base = pos.get(e["anchor"], SEED_POS)
        wraps = total.get(e["anchor"], 0) > 8
        dx = SUB_DX * (2 * col - 1) if wraps else 0.0
        pos[e["id"]] = np.array([base[0] + dx, STACK_TOP - row * STACK_DY, 0.0])
    return pos


def idea_mob(pos, lit=False) -> VGroup:
    d = Square(side_length=IDEA_R * 2, stroke_width=2.6 if lit else 2.0,
               color=GREEN if lit else PURPLE, fill_color="#ede7f8",
               fill_opacity=1.0).rotate(np.pi / 4).move_to(pos)
    return VGroup(d)


def prog_mob(ev, pos, lit=False) -> Circle:
    return Circle(radius=PROG_R, stroke_width=2.4 if lit else 1.8,
                  color=GREEN if lit else EDGE,
                  fill_color=score_color(ev["score"], LO, HI),
                  fill_opacity=1.0).move_to(pos)


def trimmed(a, b, ra, rb, **kw) -> Line:
    d = b - a
    u = d / max(float(np.linalg.norm(d)), 1e-6)
    return Line(a + u * (ra + 0.02), b - u * (rb + 0.02), **kw)


def mix_bar(mix, lit=None) -> VGroup:
    g = VGroup()
    x = MIX_C[0] - MIX_W / 2
    for i, (name, col) in enumerate(ACTIONS):
        w = max(0.001, mix[i] * MIX_W)
        r = Rectangle(width=w, height=MIX_H, stroke_width=1.8 if lit == i else 0.8,
                      color=INK if lit == i else EDGE, fill_color=col,
                      fill_opacity=0.85 if lit == i else 0.5)
        r.move_to([x + w / 2, MIX_C[1], 0])
        g.add(r)
        # a label wider than its segment spills into the neighbours and the bar becomes soup;
        # the segment's colour still says which action shrank
        label = txt(f"{name} {mix[i] * 100:.0f}%", 10.5, INK)
        if label.width < w - 0.1:
            g.add(label.move_to([x + w / 2, MIX_C[1], 0]))
        x += w
    return g


def loop_box(key: str, top: str, sub: str) -> VGroup:
    body = VGroup(txt(top, 20, INK), txt(sub, 13.5, SUB)).arrange(DOWN, buff=0.09)
    frame = RoundedRectangle(width=BOX_W, height=BOX_H, corner_radius=0.13, stroke_width=1.7,
                             color=EDGE, fill_color=PANEL, fill_opacity=1.0)
    return VGroup(frame, body).move_to(LOOP_AT[key])


def rect_anchor(box, other, pad=0.08) -> np.ndarray:
    c, o = box.get_center(), other.get_center()
    d = o - c
    u = d / max(float(np.linalg.norm(d)), 1e-6)
    t = min(BOX_W / 2 / abs(u[0]) if abs(u[0]) > 1e-6 else 1e9,
            BOX_H / 2 / abs(u[1]) if abs(u[1]) > 1e-6 else 1e9)
    return c + u * (t + pad)


def ring_arrow(a_box, b_box):
    return spoke(rect_anchor(a_box, b_box), rect_anchor(b_box, a_box), SUB, 2.6,
                 0.0, 0.0, tip=0.16)


class AnnealedRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("AnnealedIdeaCode", 52, INK, weight="BOLD")
        sub = para("search the IDEAS, not just the code — propose early, implement late,\n"
                   "and learn what an idea is worth before spending the budget on it", 27, SUB)
        fine = txt("simulated scores · real method decisions · the judge act is real data",
                   16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.0)
        self.play(FadeOut(card), run_time=0.9)

        self.idea_mobs: dict = {}
        self.prog_mobs: dict = {}
        self.edge_mobs: dict = {}              # (kind, a, b) -> mobject
        self.panel_rows: dict = {}             # idea id -> row mobject
        self.panel_order: list = []
        self.curve_dots, self.best_pts = VGroup(), []
        self.caption = None
        self.ptr = 0
        self.n_seen = 0
        self.refine_explained = False

        self.act_one()
        self.act_schedule()
        self.act_judge()
        self.act_rest()

    # ---- shared -----------------------------------------------------------
    def say(self, text, size=22, color=INK, hold=0.0):
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

    def led(self, key) -> np.ndarray:
        return self.boxes[key][0].get_corner(UL) + np.array([0.18, -0.18, 0.0])

    def lap_anim(self):
        path = VMobject().set_points_as_corners(
            [self.runner.get_center(), self.led("generate"), self.led("score"),
             self.led("record")])
        return MoveAlongPath(self.runner, path)

    def next_event(self):
        ev = EVENTS[self.ptr]
        self.ptr += 1
        return ev

    # ---- tree -------------------------------------------------------------
    def tree_anims(self, ev, lit=False):
        self.n_seen += 1
        pos = tree_layout(EVENTS[: self.n_seen])
        anims = []
        for nid, mob in {**self.idea_mobs, **self.prog_mobs}.items():
            if nid in pos and float(np.linalg.norm(mob.get_center() - pos[nid])) > 1e-3:
                anims.append(mob.animate.move_to(pos[nid]))
        for (kind, a, b), mob in self.edge_mobs.items():
            if kind == "spine":
                anims.append(Transform(mob, self.spine_edge(a, b, pos)))
            else:
                anims.append(Transform(mob, self.make_edge(kind, a, b, pos)))

        if ev["kind"] == "idea":
            node = idea_mob(pos[ev["id"]], lit=lit)
            self.idea_mobs[ev["id"]] = node
            if ev.get("parent") in self.idea_mobs:
                key = ("refine", ev["parent"], ev["id"])
                self.edge_mobs[key] = self.make_edge(*key, pos)
                anims.append(Create(self.edge_mobs[key]))
        else:
            node = prog_mob(ev, pos[ev["id"]], lit=lit)
            self.prog_mobs[ev["id"]] = node
            if ev.get("anchor") in self.idea_mobs:
                # ONE spine per sub-column, not an edge per implementation: fifteen circles
                # each wired to the same diamond braid into a rope. The spine is drawn once
                # and stretched as the stack grows; circles sit on top of it.
                col = round((pos[ev["id"]][0] - pos[ev["anchor"]][0]) / SUB_DX) if SUB_DX else 0
                key = ("spine", ev["anchor"], col)
                if key in self.edge_mobs:
                    anims.append(Transform(self.edge_mobs[key],
                                           self.spine_edge(ev["anchor"], col, pos)))
                else:
                    self.edge_mobs[key] = self.spine_edge(ev["anchor"], col, pos)
                    anims.append(Create(self.edge_mobs[key]))
            if ev.get("parent") in self.prog_mobs:
                key = ("base", ev["parent"], ev["id"])
                self.edge_mobs[key] = self.make_edge(*key, pos)
                anims.append(Create(self.edge_mobs[key]))
        anims.append(FadeIn(node, scale=0.5))
        return anims

    def spine_edge(self, idea_id, col, pos):
        """Diamond to the bottom of one of its sub-columns."""
        xs = [pos[nid][0] for nid, e in BY_ID.items()
              if e.get("kind") == "code" and e.get("anchor") == idea_id and nid in pos
              and round((pos[nid][0] - pos[idea_id][0]) / SUB_DX) == col]
        ys = [pos[nid][1] for nid, e in BY_ID.items()
              if e.get("kind") == "code" and e.get("anchor") == idea_id and nid in pos
              and round((pos[nid][0] - pos[idea_id][0]) / SUB_DX) == col]
        x = xs[0] if xs else pos[idea_id][0]
        y_end = min(ys) if ys else STACK_TOP
        return Line(pos[idea_id] + DOWN * (IDEA_R + 0.02), [x, y_end, 0],
                    color=EDGE, stroke_width=1.5).set_stroke(opacity=0.7)

    def make_edge(self, kind, a, b, pos):
        ra = IDEA_R if BY_ID.get(a, {}).get("kind") == "idea" else PROG_R
        rb = IDEA_R if BY_ID.get(b, {}).get("kind") == "idea" else PROG_R
        if kind == "refine":
            return arc(pos[a], pos[b], PURPLE, 1.8, angle=-0.5,
                       buff_a=ra + 0.04, buff_b=rb + 0.04, tip=0.1)
        if kind == "impl":
            return trimmed(pos[a], pos[b], ra, rb, color=EDGE,
                           stroke_width=1.5).set_stroke(opacity=0.8)
        # An arc, not a line. Five base edges leave the seed for circles at the SAME height,
        # and straight dashed lines lie collinear on that row -- one fence through every circle.
        # Bowed under the row, each span gets its own curve.
        d = pos[b] - pos[a]
        u = d / max(float(np.linalg.norm(d)), 1e-6)
        a2, b2 = pos[a] + u * (ra + 0.04), pos[b] - u * (rb + 0.04)
        curve = ArcBetweenPoints(a2, b2, angle=-0.45, color=MUTED, stroke_width=1.3)
        return DashedVMobject(curve, num_dashes=max(6, int(np.linalg.norm(d) * 7))
                              ).set_stroke(opacity=0.55)

    # ---- the idea panel ----------------------------------------------------
    def panel_row_mob(self, entry, y) -> VGroup:
        mu, sig, beta = entry["mu"], entry["sigma"], entry["beta"]
        measured = entry["measured"]
        w_mu = max(0.02, min((mu - 0.3) / 0.7, 1.0) * BAR_WMAX)
        w_bo = max(0.0, min(beta * sig / 0.7 * BAR_WMAX, BAR_WMAX * 0.5))
        icon = Square(side_length=0.13, stroke_width=1.6, color=PURPLE,
                      fill_color=PURPLE if measured else "#ede7f8",
                      fill_opacity=1.0).rotate(np.pi / 4).move_to([PANEL_X0 + 0.1, y, 0])
        bar = Rectangle(width=w_mu, height=0.13, stroke_width=0,
                        fill_color=BLUE if measured else LAVENDER,
                        fill_opacity=0.95).move_to([BAR_X0 + w_mu / 2, y, 0])
        bonus = Rectangle(width=max(w_bo, 0.001), height=0.13, stroke_width=0, fill_color=ORANGE,
                          fill_opacity=0.85).move_to([BAR_X0 + w_mu + w_bo / 2, y, 0])
        ptxt = txt(f"{entry['prob'] * 100:.0f}%", 12, SUB)
        ptxt.move_to([BAR_X0 + BAR_WMAX + 0.85, y, 0])
        return VGroup(icon, bar, bonus, ptxt)

    def panel_anims(self, table):
        anims = []
        for entry in table:
            if entry["id"] not in self.panel_order:
                self.panel_order.append(entry["id"])
            y = PANEL_TOP - self.panel_order.index(entry["id"]) * ROW_H
            new = self.panel_row_mob(entry, y)
            old = self.panel_rows.get(entry["id"])
            if old is None:
                self.panel_rows[entry["id"]] = new
                anims.append(FadeIn(new, scale=0.7))
            else:
                anims.append(Transform(old, new))
        return anims

    def flash_idea(self, idea_id):
        out = []
        if idea_id in self.panel_rows:
            out.append(Indicate(self.panel_rows[idea_id], color=ORANGE, scale_factor=1.1))
        if idea_id in self.idea_mobs:
            out.append(Indicate(self.idea_mobs[idea_id], color=ORANGE, scale_factor=1.35))
        return out

    # ---- curve (programs only: prediction is free, measurement is not) ----
    def curve_anims(self, ev):
        if ev["kind"] != "code":
            return []
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

    # ---- one step ----------------------------------------------------------
    def step(self, ev, run_time=0.55):
        act_i = ["NEW", "REFINE", "IMPL"].index(ev["action"]) if ev.get("action") else None
        pre = []
        if act_i is not None:
            pre.append(Transform(self.mix, mix_bar(ev["mix"], lit=act_i)))
        if ev["kind"] == "code" and ev.get("anchor"):
            pre += self.flash_idea(ev["anchor"])
        elif ev["kind"] == "idea" and ev.get("parent"):
            pre += self.flash_idea(ev["parent"])
        if pre:
            self.play(*pre, self.runner.animate.move_to(self.led("action")),
                      run_time=run_time * 0.6)
        self.play(*self.tree_anims(ev), *self.panel_anims(ev["table"]),
                  *self.curve_anims(ev), self.lap_anim(), run_time=run_time)
        if ev["kind"] == "idea" and ev["action"] == "REFINE" and not self.refine_explained:
            self.refine_explained = True
            self.say("refine — a variation of the leading idea. It starts from that idea's\n"
                     "best code, so the intellectual lineage carries the code lineage with it",
                     hold=2.4)

    # ---- act 1 -------------------------------------------------------------
    def act_one(self):
        boxes = {"action": loop_box("action", "draw an action", "from the annealed mix"),
                 "generate": loop_box("generate", "generate", "idea: one call · code: an agent"),
                 "score": loop_box("score", "score", "judge predicts · verifier measures"),
                 "record": loop_box("record", "record", "tree grows · values update")}
        self.boxes = boxes
        ring = VGroup(ring_arrow(boxes["action"][0], boxes["generate"][0]),
                      ring_arrow(boxes["generate"][0], boxes["score"][0]),
                      ring_arrow(boxes["score"][0], boxes["record"][0]),
                      ring_arrow(boxes["record"][0], boxes["action"][0]))
        self.ring = ring

        first = EVENTS[0]
        self.mix = mix_bar(first["mix"])
        mix_lab = txt("the annealed mix", 13, SUB).next_to(self.mix, DOWN, buff=0.1)
        panel_lab = fit(txt("the ideas — value μ + bonus β·σ · chance of being drawn", 13, SUB),
                        5.3).move_to([-4.05, -0.85, 0])

        self.ax = Axes(x_range=[0, len(IMPLS) + 2, 5], y_range=[0.4, 1.0, 0.3],
                       x_length=6.6, y_length=2.1, tips=False,
                       axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        self.ax.move_to(CURVE_C)
        ticks = VGroup(*[txt(f"{v:.1f}", 13, MUTED).next_to(self.ax.c2p(0, v), LEFT, buff=0.1)
                         for v in (0.4, 0.7, 1.0)],
                       *[txt(str(v), 13, MUTED).next_to(self.ax.c2p(v, 0.4), DOWN, buff=0.1)
                         for v in (10, 20)],
                       txt("programs", 13, MUTED).next_to(self.ax.c2p(len(IMPLS) + 2, 0.4),
                                                          DOWN, buff=0.1))
        curve_lab = txt("fitness · every program measured, and the best so far", 15, SUB)
        curve_lab.next_to(self.ax, UP, buff=0.1).align_to(self.ax, LEFT)
        self.best_line = VGroup()
        self.add(self.best_line, self.curve_dots)

        self.tree_lab = txt("the tree — ideas above, their implementations below", 17, SUB)
        tree_lab = self.tree_lab.move_to([3.6, 3.55, 0])
        self.runner = Dot(radius=0.07, color=ORANGE)

        seed_dot = Circle(radius=PROG_R, stroke_width=1.8, color=EDGE,
                          fill_color=score_color(SEED_SCORE, LO, HI),
                          fill_opacity=1.0).move_to(SEED_POS)
        seed_tag = txt("seed", 12, MUTED).next_to(seed_dot, UP, buff=0.1)
        self.seed_tag = seed_tag                # act 3 clears the tree, label included
        self.prog_mobs[SEED_ID] = seed_dot
        self.best_pts.append(self.ax.c2p(1, SEED_SCORE))
        seed_pt = Dot(self.ax.c2p(1, SEED_SCORE), radius=0.045, color=BLUE).set_opacity(0.65)
        self.curve_dots.add(seed_pt)

        self.play(LaggedStart(*[FadeIn(boxes[k], scale=0.9) for k in
                                ("action", "generate", "score", "record")], lag_ratio=0.15),
                  LaggedStart(*[Create(a) for a in ring], lag_ratio=0.15),
                  FadeIn(self.mix), FadeIn(mix_lab), FadeIn(panel_lab),
                  Create(self.ax), FadeIn(ticks), FadeIn(curve_lab), FadeIn(tree_lab),
                  FadeIn(seed_dot), FadeIn(seed_tag), FadeIn(seed_pt),
                  run_time=2.0)
        self.runner.move_to(self.led("action"))
        self.play(FadeIn(self.runner, scale=0.4), run_time=0.4)
        self.say("one seed program, already measured — and no ideas yet", hold=1.4)

        # ---- slow lap A: a NEW idea ----------------------------------------
        ev = self.next_event()
        self.play(Indicate(boxes["action"][1][0], color=ORANGE, scale_factor=1.15),
                  Transform(self.mix, mix_bar(ev["mix"], lit=0)),
                  self.runner.animate.move_to(self.led("action")), run_time=0.9)
        self.say("draw an action — early in the run the mix leans towards PROPOSING\n"
                 "new ideas; that lean is exactly what the schedule will anneal away", hold=2.2)

        self.play(Indicate(boxes["generate"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("generate")), run_time=0.7)
        self.say("generate — an idea is one model call: a paragraph describing an approach,\n"
                 "not a program. Nothing is executed.", hold=2.0)

        self.play(Indicate(boxes["score"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("score")), run_time=0.7)
        self.say("score — the JUDGE predicts what a competent implementation would reach.\n"
                 "A prediction is free: notice the fitness curve gets no point", hold=2.2)

        self.play(Indicate(boxes["record"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("record")),
                  *self.tree_anims(ev, lit=True), *self.panel_anims(ev["table"]), run_time=1.0)
        self.say("record — a diamond on the tree, and a row in the panel: the judge's value\n"
                 "in lavender, because it is a claim, not a measurement", hold=2.4)
        self.play(self.idea_mobs[ev["id"]][0].animate.set_stroke(PURPLE, width=2.0),
                  run_time=0.4)

        # a second NEW, in one breath
        self.step(self.next_event(), run_time=0.5)

        # ---- slow lap B: an IMPLEMENT ---------------------------------------
        ev = self.next_event()
        self.play(Indicate(boxes["action"][1][0], color=ORANGE, scale_factor=1.15),
                  Transform(self.mix, mix_bar(ev["mix"], lit=2)),
                  self.runner.animate.move_to(self.led("action")), run_time=0.9)
        self.say("this time: IMPLEMENT. Which idea? — a softmax over value + β·σ,\n"
                 "still wide open while the temperature is high", hold=0.4)
        self.play(*self.flash_idea(ev["anchor"]), run_time=0.9)
        self.wait(1.4)

        agent = self.agent_inset()
        gen = self.boxes["generate"][0]
        callout = VGroup(
            DashedLine(gen.get_corner(DL), agent[0].get_corner(UL), color=ORANGE,
                       stroke_width=1.6, dash_length=0.08),
            DashedLine(gen.get_corner(DR), agent[0].get_corner(UR), color=ORANGE,
                       stroke_width=1.6, dash_length=0.08))
        self.play(Indicate(self.boxes["generate"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("generate")),
                  GrowFromPoint(agent, gen.get_center()), run_time=0.9)
        self.play(Create(callout), run_time=0.4)
        self.say("implementing is a coding agent. It starts from the idea's best code so far —\n"
                 "here, the seed — edits, runs the evaluator itself, submits what passes")
        for _ in range(2):
            for j in range(3):
                self.play(Indicate(agent[1][j][1], color=ORANGE, scale_factor=1.25),
                          run_time=0.32)
        self.wait(0.6)

        self.play(FadeOut(agent), FadeOut(callout),
                  Indicate(self.boxes["score"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("score")),
                  *self.curve_anims(ev), run_time=0.9)
        self.say(f"score — the VERIFIER measures it: {ev['score']:.2f}. Measurements are what "
                 "the budget\nbuys; predictions were the discount version", hold=2.0)

        self.play(Indicate(self.boxes["record"][1][0], color=ORANGE, scale_factor=1.15),
                  self.runner.animate.move_to(self.led("record")),
                  *self.tree_anims(ev, lit=True), *self.panel_anims(ev["table"]), run_time=1.0)
        self.say("record — a circle under its diamond, a dashed line back to the code it "
                 "edited,\nand the idea's bar turns BLUE: its value is now a fact, not a claim",
                 hold=2.6)
        self.play(self.prog_mobs[ev["id"]].animate.set_stroke(EDGE, width=1.8), run_time=0.4)

        # ---- speed ----------------------------------------------------------
        self.say("the same turn, over and over — ideas appear, implementations pile up",
                 color=SUB)
        while self.n_seen < 9:
            self.step(self.next_event())
        self.wait(0.8)
        self.drop_caption()

    def agent_inset(self) -> VGroup:
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
        rail_y = steps.get_bottom()[1] - 0.42
        down = Line(steps[2][0].get_bottom() + DOWN * 0.04,
                    [steps[2][0].get_bottom()[0], rail_y, 0], color=MUTED, stroke_width=1.6)
        across = Line([steps[2][0].get_bottom()[0], rail_y, 0],
                      [steps[0][0].get_bottom()[0], rail_y, 0], color=MUTED, stroke_width=1.6)
        up = spoke([steps[0][0].get_bottom()[0], rail_y, 0],
                   steps[0][0].get_bottom() + DOWN * 0.04, MUTED, 1.6, 0.0, 0.02, tip=0.12)
        retry = txt("fails? edit again", 13, MUTED).next_to(across, DOWN, buff=0.08)
        head = txt("implement, opened up", 15, ORANGE)
        head.next_to(steps, UP, buff=0.22)
        content = VGroup(head, steps, fwd, down, across, up, retry)
        frame = RoundedRectangle(width=content.width + 0.55, height=content.height + 0.4,
                                 corner_radius=0.15, stroke_width=1.8, color=ORANGE,
                                 fill_color=PANEL, fill_opacity=1.0)
        frame.move_to(content.get_center())
        return VGroup(frame, steps, fwd, VGroup(down, across, up), retry, head).move_to(
            [-3.95, 1.32, 0])

    # ---- act 2: the schedule ------------------------------------------------
    def act_schedule(self):
        self.play(*[self.boxes[k][0].animate.set_stroke(opacity=0.35) for k in self.boxes],
                  *[self.boxes[k][1].animate.set_opacity(0.35) for k in self.boxes],
                  *dim([(self.ring, "stroke")], 0.22), run_time=0.8)

        ax = Axes(x_range=[0, 1.02, 0.5], y_range=[0, 1.05, 0.5], x_length=3.1, y_length=1.6,
                  tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([-3.95, 1.65, 0])
        ts = [e["t"] for e in EVENTS]
        curves = VGroup(
            ax.plot_line_graph(ts, [e["mix"][0] for e in EVENTS], line_color=PURPLE,
                               add_vertex_dots=False, stroke_width=3.4),
            ax.plot_line_graph(ts, [e["mix"][2] for e in EVENTS], line_color=GREEN,
                               add_vertex_dots=False, stroke_width=3.4),
            ax.plot_line_graph(ts, [e["temperature"] for e in EVENTS], line_color=ORANGE,
                               add_vertex_dots=False, stroke_width=3.0))
        key = VGroup(txt("propose", 14, PURPLE), txt("implement", 14, GREEN),
                     txt("temperature", 14, ORANGE)).arrange(RIGHT, buff=0.35)
        key.next_to(ax, UP, buff=0.12)
        tick = VGroup(txt("t = 0", 12, MUTED).next_to(ax.c2p(0, 0), DOWN, buff=0.1),
                      txt("t = 1", 12, MUTED).next_to(ax.c2p(1, 0), DOWN, buff=0.1))
        self.say("the schedule, drawn from this very run: proposing decays, implementing\n"
                 "takes what it gives up, and the temperature falls alongside", hold=0.2)
        self.play(Create(ax), FadeIn(key), FadeIn(tick), run_time=0.8)
        self.play(Create(curves), run_time=2.0)
        self.wait(1.6)
        self.say("a falling temperature narrows the softmax over the panel: early, every idea\n"
                 "gets a real chance — late, the leader takes nearly every implementation",
                 hold=2.6)
        self.play(FadeOut(VGroup(ax, curves, key, tick)), run_time=0.6)

    # ---- act 3: the judge ----------------------------------------------------
    def act_judge(self):
        """The one act with real numbers in it.

        Everything else in this video is the stubbed run. Here the pairs come out of six actual
        `AnnealedIdeaCode` runs on the Erdos problem, replayed one step ahead through the real
        calibration -- so the act can show the update happening AND what it was worth, which on
        this evidence is not what the design hoped for. The caption says so rather than cutting
        the awkward half.
        """
        X_LO, X_HI = -0.01, 0.20
        Y_LO, Y_HI = -0.12, 0.15

        # The tree goes all the way out, not down to a ghost. This chart's subject is a field of
        # blue dots, and a faded tree is a field of blue circles behind it -- at 12% the two read
        # as one scatter. The stage's identity is carried by the loop and the panel, which stay.
        tree = VGroup(*self.idea_mobs.values(),
                      *[m for i, m in self.prog_mobs.items()],
                      *self.edge_mobs.values())
        self.play(tree.animate.set_opacity(0.0), FadeOut(self.tree_lab),
                  FadeOut(self.seed_tag), run_time=0.7)

        ax = Axes(x_range=[X_LO, X_HI, 0.05], y_range=[Y_LO, Y_HI, 0.05],
                  x_length=3.3, y_length=2.7, tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([3.5, 1.5, 0])
        xlab = txt("what the judge said", 15, MUTED).next_to(ax, DOWN, buff=0.14)
        ylab = txt("what it gained", 15, MUTED).rotate(np.pi / 2).next_to(ax, LEFT, buff=0.12)
        head = txt("the judge, learning", 21, INK).next_to(ax, UP, buff=0.52)
        src = txt("a real run — Erdős minimum overlap, 10 labelled ideas", 12, MUTED)
        src.next_to(head, DOWN, buff=0.1)
        self.say("the judge is checked against the verifier every time an idea gets built:\n"
                 "what it SAID, against what the program actually GAINED", color=SUB)
        self.play(Create(ax), FadeIn(xlab), FadeIn(ylab), FadeIn(head), FadeIn(src), run_time=0.9)

        def pt(x, y):
            return ax.c2p(min(max(x, X_LO), X_HI), min(max(y, Y_LO), Y_HI))

        def curve(knots):
            """What the calibration returns across the axis: the knots, held flat past both ends
            (`_interp` clamps), which is exactly the behaviour that misfires below."""
            kx, ky = knots
            if not kx:
                # A true diagonal, so it has to stop where the shorter axis does -- drawn to the
                # x-axis end it leaves the plot and reads as an arbitrary slope.
                d = min(X_HI, Y_HI)
                return Line(pt(X_LO, X_LO), pt(d, d), color=MUTED, stroke_width=2.2)
            xs = [X_LO] + list(kx) + [X_HI]
            ys = [ky[0]] + list(ky) + [ky[-1]]
            line = VMobject(color=PURPLE, stroke_width=2.6)
            line.set_points_as_corners([pt(x, y) for x, y in zip(xs, ys)])
            return line

        state = txt("0 pairs · the identity", 14, MUTED)
        state.next_to(ax, DOWN, buff=0.42)
        # Gains go negative on this problem, so the sign has to be readable: without a marked
        # zero, "gained nothing" and "lost ground" are the same picture. It goes at the RIGHT end
        # of the zero line -- to the left is where the axis label already is, and the origin
        # itself has data sitting on it.
        zero = txt("0", 12, MUTED).next_to(ax.c2p(X_HI, 0), RIGHT, buff=0.07)
        fitline = curve(([], []))
        fitlab = txt("said = gained", 12, MUTED).rotate(np.pi / 4.4).move_to(pt(0.125, 0.135))
        self.play(Create(fitline), FadeIn(fitlab), FadeIn(state), FadeIn(zero), run_time=0.7)
        self.say("below five pairs the calibration IS the identity — it will not fit a curve\n"
                 "the evidence cannot hold up", hold=1.2)

        dots = []
        for i, s in enumerate(STEPS):
            d = Dot(pt(s["said"], s["gained"]), radius=0.05, color=BLUE).set_opacity(0.8)
            dots.append(d)
            anims = [GrowFromPoint(d, pt(s["said"], s["gained"]))]
            n_after = s["n"] + 1
            after = txt(f"{n_after} pair{'' if n_after == 1 else 's'} · "
                        + ("fitted" if s["fitted_after"] else "the identity"), 14,
                        MUTED if not s["fitted_after"] else INK).move_to(state)
            anims.append(Transform(state, after))
            if s["fitted_after"] and (s["knots_after"] != s["knots_before"]):
                anims.append(Transform(fitline, curve(s["knots_after"])))
            if i == 4:                                    # the fit fires for the first time
                # The pair lands, THEN the caption, THEN the curve it describes. Played as one
                # block the new fit sits under the old caption ("below five pairs...") for the
                # better part of a second and flatly contradicts it.
                self.play(GrowFromPoint(d, pt(s["said"], s["gained"])),
                          Transform(state, after), FadeOut(fitlab), run_time=0.5)
                # Not "the fifth pair, and..." -- a caption pinned to a count goes stale while it
                # is still on screen, and two steps later it is contradicting the counter above it.
                self.say("five pairs in, the fit takes over from the identity: a monotone curve\n"
                         "through what the judge said and what those ideas turned out to be worth")
                self.play(Transform(fitline, curve(s["knots_after"])), run_time=0.7)
                self.wait(1.4)
                continue
            if i == 6:                                    # the clamp, in the act of misfiring
                # The curve is held at what it was, deliberately. The orange bar measures the
                # point against the fit that was ON SCREEN when the prediction was made; update
                # the curve first and the bar floats in space pointing at nothing, while the
                # repair plays out under a caption still saying the thing is broken.
                self.play(GrowFromPoint(d, pt(s["said"], s["gained"])),
                          Transform(state, after), run_time=0.5)
                gap = Line(pt(s["said"], s["gained"]), pt(s["said"], s["before"]),
                           color=ORANGE, stroke_width=3)
                gap_lab = txt("+0.084", 13, ORANGE).next_to(gap, RIGHT, buff=0.08)
                self.play(Create(gap), FadeIn(gap_lab), run_time=0.5)
                self.say("the run has hit the plateau and the judge knows it — it says this idea\n"
                         "is worth +0.001. The fit has never seen a prediction that low, so it\n"
                         "clamps to the lowest it knows, +0.084, and overrides the judge",
                         color=ORANGE, hold=2.8)
                self.say("then the outcome goes in, and the curve learns a left-hand end",
                         color=SUB)
                self.play(Transform(fitline, curve(s["knots_after"])),
                          FadeOut(gap), FadeOut(gap_lab), run_time=0.9)
                self.wait(1.4)
                continue
            if i == 7:                                    # the repaired fit, tested
                self.play(*anims, run_time=0.5)
                self.say("the very next prediction is the same +0.001 — and this time it\n"
                         "calibrates to +0.001 and lands on the point", hold=2.2)
                continue
            self.play(*anims, run_time=0.42)

        self.say(f"and it does rank them: Spearman {FEATURED_RHO:+.2f} on this run, "
                 f"{min(r for _, _, r in SUMMARY):+.2f} to {max(r for _, _, r in SUMMARY):+.2f}\n"
                 f"across all six — against −0.15 for the judge this one replaced", hold=2.4)

        # The disclosure, and it goes in the caption slot rather than under the chart: squeezed
        # into the gap above the fitness panel it renders as a cramped grey block, and this is
        # the one thing in the act nobody should have to squint at.
        self.say(f"but the calibration ON TOP of that order has not paid for itself: over six "
                 f"runs it\nchanged {TALLY['changed']} of {TALLY['n']} predictions and was closer "
                 f"on {TALLY['better']} of them. Ten pairs is not a training set", 18, MUTED,
                 hold=3.4)

        self.play(FadeOut(VGroup(ax, xlab, ylab, head, src, state, zero, fitline, *dots)),
                  tree.animate.set_opacity(1.0), FadeIn(self.tree_lab),
                  FadeIn(self.seed_tag), run_time=0.8)

    # ---- act 4: the rest ------------------------------------------------------
    def act_rest(self):
        self.say("the rest of the budget — watch where it goes", color=SUB)
        while self.ptr < len(EVENTS):
            self.step(self.next_event(), run_time=0.4)

        lead = max({e["anchor"] for e in IMPLS if e.get("anchor")},
                   key=lambda a: sum(1 for e in IMPLS if e["anchor"] == a))
        n_lead = sum(1 for e in IMPLS if e["anchor"] == lead)
        self.play(*self.flash_idea(lead), run_time=0.9)
        self.say(f"{len(IMPLS)} implementations, {n_lead} of them on this one idea — the mix "
                 "slid from proposing\nto implementing, and the cooling softmax picked its "
                 "winner", hold=3.0)
        self.say("every program here has two parents: the code it edited (dashed) and the\n"
                 "idea it served — the search keeps both lineages, and budgets by the second",
                 hold=3.2)
