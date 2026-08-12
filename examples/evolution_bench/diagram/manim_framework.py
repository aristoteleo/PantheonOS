"""Pantheon Evolve -- the framework itself, before any particular algorithm.

    manim -qm --format=mp4 manim_framework.py FrameworkRun

The other videos each show one method running. This one shows what they run IN: a fixed loop --
select, mutate, evaluate, record -- with named seams, and the method as a plug-in behind two of
the four stations. It is deliberately short and has no simulated run: the claim it makes is
structural, and the three method videos are its evidence.

Beats:

  1. the loop, assembled, with the runner making a lap -- "every method in this series is this
     machine"
  2. the seams, one at a time: Method (select + record: ask/on_measured/rank, and it names its
     own operator), Variator (mutate: the coding agent), Evaluator (evaluate: one per kind of
     genome), and underneath them the Store (everything ever made, with lineage) and the Budget
     (what stops the run)
  3. the swap: three method cards take the Method slot in turn -- AgentMapElites, SimpleTES,
     AnnealedIdeaCode -- while the rest of the machine does not move.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Circle, Create, DashedVMobject, Dot, FadeIn,
                   FadeOut, Indicate, LaggedStart, Line, MoveAlongPath, MovingCameraScene,
                   Rectangle, RoundedRectangle, Square, Transform, VGroup, VMobject, Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, PURPLE, RED,
                       SUB, arc, fit, para, score_color, spoke, txt)

# ---- the stage -------------------------------------------------------------
LOOP_AT = {"select": np.array([-2.6, 1.15, 0.0]), "mutate": np.array([0.0, 2.55, 0.0]),
           "evaluate": np.array([2.6, 1.15, 0.0]), "record": np.array([0.0, -0.25, 0.0])}
BOX_W, BOX_H = 2.9, 1.0
CAP_AT = np.array([0.0, -3.5, 0.0])
STORE_Y, CARD_Y = -1.55, -2.52


def loop_box(key: str, top: str, sub: str) -> VGroup:
    body = VGroup(txt(top, 22, INK), txt(sub, 14.5, SUB)).arrange(DOWN, buff=0.1)
    frame = RoundedRectangle(width=BOX_W, height=BOX_H, corner_radius=0.14, stroke_width=1.8,
                             color=EDGE, fill_color=PANEL, fill_opacity=1.0)
    return VGroup(frame, body).move_to(LOOP_AT[key])


def rect_anchor(box, other, pad=0.09) -> np.ndarray:
    c, o = box.get_center(), other.get_center()
    d = o - c
    u = d / max(float(np.linalg.norm(d)), 1e-6)
    t = min(BOX_W / 2 / abs(u[0]) if abs(u[0]) > 1e-6 else 1e9,
            BOX_H / 2 / abs(u[1]) if abs(u[1]) > 1e-6 else 1e9)
    return c + u * (t + pad)


def ring_arrow(a_box, b_box):
    return spoke(rect_anchor(a_box, b_box), rect_anchor(b_box, a_box), SUB, 2.8,
                 0.0, 0.0, tip=0.18)


def brace_around(mobs, color, pad=0.22) -> VMobject:
    """A dashed rounded frame around a group -- the visual for 'this is one seam'."""
    g = VGroup(*mobs)
    r = RoundedRectangle(width=g.width + 2 * pad, height=g.height + 2 * pad,
                         corner_radius=0.18, stroke_width=2.2, color=color,
                         fill_opacity=0.0).move_to(g.get_center())
    return DashedVMobject(r, num_dashes=48)


# ---- method-card thumbnails --------------------------------------------------
def thumb_grid() -> VGroup:
    g = VGroup()
    fills = {(0, 1): 0.75, (1, 2): 0.55, (2, 0): 0.62, (1, 0): 0.45}
    for cx in range(3):
        for cy in range(3):
            s = fills.get((cx, cy))
            g.add(Square(side_length=0.22, stroke_width=1.0, color=EDGE,
                         fill_color=PANEL if s is None else score_color(s, 0.3, 0.9),
                         fill_opacity=1.0).move_to([cx * 0.22, cy * 0.22, 0]))
    return g


def thumb_chains() -> VGroup:
    g = VGroup()
    for row, n in enumerate((4, 3, 4)):
        for i in range(n):
            g.add(Circle(radius=0.07, stroke_width=1.0, color=EDGE,
                         fill_color=score_color(0.4 + 0.12 * i, 0.3, 0.9),
                         fill_opacity=1.0).move_to([i * 0.24, -row * 0.24, 0]))
            if i:
                g.add(Line([i * 0.24 - 0.17, -row * 0.24, 0], [i * 0.24 - 0.07, -row * 0.24, 0],
                           color=EDGE, stroke_width=1.0))
    return g


def thumb_mix() -> VGroup:
    g = VGroup()
    x = 0.0
    for w, col in ((0.28, PURPLE), (0.14, BLUE), (0.38, GREEN)):
        g.add(Rectangle(width=w, height=0.2, stroke_width=0.8, color=EDGE, fill_color=col,
                        fill_opacity=0.6).move_to([x + w / 2, 0.18, 0]))
        x += w
    d = Square(side_length=0.16, stroke_width=1.4, color=PURPLE, fill_color="#ede7f8",
               fill_opacity=1.0).rotate(np.pi / 4).move_to([0.16, -0.18, 0])
    c = Circle(radius=0.07, stroke_width=1.0, color=EDGE,
               fill_color=score_color(0.7, 0.3, 0.9), fill_opacity=1.0).move_to([0.16, -0.44, 0])
    g.add(d, c, Line([0.16, -0.26, 0], [0.16, -0.37, 0], color=EDGE, stroke_width=1.0))
    return g


def method_card(name: str, line: str, thumb: VGroup) -> VGroup:
    label = VGroup(txt(name, 19, INK, weight="BOLD"), txt(line, 13.5, SUB)).arrange(
        DOWN, buff=0.08, aligned_edge=LEFT)
    thumb.scale_to_fit_height(0.62)
    body = VGroup(thumb, label).arrange(RIGHT, buff=0.3)
    frame = RoundedRectangle(width=body.width + 0.5, height=1.05, corner_radius=0.14,
                             stroke_width=2.0, color=ORANGE, fill_color=PANEL, fill_opacity=1.0)
    frame.move_to(body.get_center())
    return VGroup(frame, body)


class FrameworkRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("Pantheon Evolve", 54, INK, weight="BOLD")
        sub = para("one engine, many algorithms — the loop is fixed,\n"
                   "and everything interesting is a plug-in", 27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(1.8)
        self.play(FadeOut(card), run_time=0.9)

        self.caption = None
        self.act_loop()
        self.act_seams()
        self.act_swap()

    def say(self, text, size=23, color=INK, hold=0.0):
        new = para(text, size, color) if "\n" in text else txt(text, size, color)
        fit(new, FULL_W - 2.0).move_to(CAP_AT)
        if self.caption is None:
            self.caption = new
            self.play(FadeIn(new), run_time=0.5)
        else:
            self.play(Transform(self.caption, new), run_time=0.5)
        if hold:
            self.wait(hold)

    # ---- act 1: the loop ---------------------------------------------------
    def act_loop(self):
        boxes = {"select": loop_box("select", "select a parent", "from what the method keeps"),
                 "mutate": loop_box("mutate", "mutate it", "a coding agent"),
                 "evaluate": loop_box("evaluate", "evaluate", "a verifier scores it"),
                 "record": loop_box("record", "record", "the tree keeps everything")}
        self.boxes = boxes
        ring = VGroup(ring_arrow(boxes["select"][0], boxes["mutate"][0]),
                      ring_arrow(boxes["mutate"][0], boxes["evaluate"][0]),
                      ring_arrow(boxes["evaluate"][0], boxes["record"][0]),
                      ring_arrow(boxes["record"][0], boxes["select"][0]))
        self.ring = ring
        head = txt("evolve( )", 26, MUTED).move_to([0, 3.55, 0])
        self.play(FadeIn(head),
                  LaggedStart(*[FadeIn(boxes[k], scale=0.9) for k in
                                ("select", "mutate", "evaluate", "record")], lag_ratio=0.18),
                  LaggedStart(*[Create(a) for a in ring], lag_ratio=0.18), run_time=2.0)

        runner = Dot(radius=0.08, color=ORANGE)
        led = {k: boxes[k][0].get_corner(UP + LEFT) + np.array([0.2, -0.2, 0])
               for k in boxes}
        runner.move_to(led["select"])
        self.play(FadeIn(runner, scale=0.4), run_time=0.4)
        self.say("four stations, one cycle — every video in this series is this machine "
                 "running", hold=0.2)
        path = VMobject().set_points_as_corners(
            [led["select"], led["mutate"], led["evaluate"], led["record"], led["select"]])
        self.play(MoveAlongPath(runner, path), run_time=2.2)
        self.runner, self.head = runner, head
        self.wait(0.8)

    # ---- act 2: the seams ----------------------------------------------------
    def act_seams(self):
        b = self.boxes

        # Method: select + record together -- one object owns both decisions
        brace_m = brace_around([b["select"], b["record"]], ORANGE)
        tag_m = VGroup(txt("Method", 22, ORANGE, weight="BOLD"),
                       txt("ask() · on_measured() · rank()", 15, SUB)).arrange(RIGHT, buff=0.3)
        # BELOW the brace, not beside it: beside, the tag's left edge crosses x = -7.11 and the
        # first characters are simply not rendered
        tag_m.next_to(brace_m, DOWN, buff=0.16).align_to(brace_m, LEFT)
        self.play(Create(brace_m), FadeIn(tag_m), run_time=1.0)
        self.say("the ALGORITHM is one object behind two stations: which parent to hand out,\n"
                 "and what to do with the measured child. It also names its own operator.",
                 hold=2.8)

        # Variator
        brace_v = brace_around([b["mutate"]], GREEN)
        tag_v = VGroup(txt("Variator", 22, GREEN, weight="BOLD"),
                       txt("the agent: edit · run · check", 15, SUB)).arrange(DOWN, buff=0.08)
        tag_v.next_to(brace_v, RIGHT, buff=0.3)
        self.play(Create(brace_v), FadeIn(tag_v), run_time=1.0)
        self.say("the OPERATOR is a coding agent with a workspace — it verifies its own edit\n"
                 "before submitting. Swapping it changes the algorithm, so methods name theirs.",
                 hold=2.8)

        # Evaluator
        brace_e = brace_around([b["evaluate"]], BLUE)
        tag_e = VGroup(txt("Evaluator", 22, BLUE, weight="BOLD"),
                       txt("one per kind — code: a verifier · ideas: a judge", 15, SUB)
                       ).arrange(RIGHT, buff=0.3)
        tag_e.next_to(brace_e, DOWN, buff=0.16).align_to(brace_e, RIGHT)
        self.play(Create(brace_e), FadeIn(tag_e), run_time=1.0)
        self.say("scoring is per KIND of genome: programs meet a verifier; an idea, if a method\n"
                 "uses ideas, meets a judge that only predicts", hold=2.6)

        # Store + Budget, underneath everything
        store = RoundedRectangle(width=9.4, height=0.62, corner_radius=0.12, stroke_width=1.8,
                                 color=EDGE, fill_color=PANEL, fill_opacity=1.0)
        store.move_to([0, STORE_Y, 0])
        store_lab = VGroup(txt("Store", 18, INK, weight="BOLD"),
                           txt("every genome ever made, with both lineages", 14, SUB),
                           txt("·", 14, MUTED),
                           txt("Budget", 18, INK, weight="BOLD"),
                           txt("what stops the run", 14, SUB)
                           ).arrange(RIGHT, buff=0.3).move_to(store.get_center())
        self.play(FadeIn(store), FadeIn(store_lab), run_time=1.0)
        self.say("under all of it, the STORE: nothing is ever deleted, and every child knows\n"
                 "the code it edited and the idea it served", hold=2.6)

        self.braces = VGroup(brace_m, tag_m, brace_v, tag_v, brace_e, tag_e)
        self.store_g = VGroup(store, store_lab)

    # ---- act 3: the swap -------------------------------------------------------
    def act_swap(self):
        b = self.boxes
        cards = [
            method_card("AgentMapElites",
                        "a MAP-Elites grid picks parents — one vote per niche", thumb_grid()),
            method_card("SimpleTES",
                        "parallel chains — k candidates per prompt, best-of-k", thumb_chains()),
            method_card("AnnealedIdeaCode",
                        "ideas first — propose early, implement late", thumb_mix()),
        ]
        subs = ["from the MAP-Elites grid", "a chain's selector picks", "the annealed softmax"]
        rec_subs = ["tree grows · grid updates", "best-of-k joins its chain",
                    "tree grows · values update"]

        self.say("and the method is a PLUG-IN. Watch the select and record stations change\n"
                 "while the agent, the verifier and the store do not move", hold=1.2)
        # every tag goes, including Method's: the store bar arrives in the same band and the
        # half-covered caption reads worse than no caption. The orange brace stays -- it IS the
        # slot the cards are about to fill.
        self.play(FadeOut(self.braces[1:]), run_time=0.5)

        shown = None
        for i, card in enumerate(cards):
            card.move_to([0, CARD_Y, 0])
            new_sel = loop_box("select", "select a parent", subs[i])
            new_rec = loop_box("record", "record", rec_subs[i])
            anims = [Transform(b["select"], new_sel), Transform(b["record"], new_rec),
                     Indicate(self.braces[0], color=ORANGE, scale_factor=1.02)]
            if shown is None:
                anims.append(FadeIn(card, shift=UP * 0.2))
                shown = card
            else:
                anims.append(Transform(shown, card))
            self.play(*anims, run_time=0.9)
            self.wait(1.6)

        self.say("same loop, same agent, same verifier, same store — three different searches.\n"
                 "The method only ever decides one thing: where the tree grows next.", hold=3.4)
