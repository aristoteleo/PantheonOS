"""Pantheon Evolve -- the framework itself, before any particular algorithm.

    manim -qm --format=mp4 manim_framework.py FrameworkRun

The other videos each show one method running. This one shows what they run IN: a fixed loop --
select, mutate, evaluate, record -- and a method that reaches ALL FOUR stations. It is deliberately
short and has no simulated run: the claim it makes is structural, and the three method videos are
its evidence.

The point worth getting right, because an earlier cut of this video got it wrong: a method is not
just a parent-selection policy. It decides at two stations (`ask`, `on_measured`) and NAMES the
component at the other two (`default_variator`, `default_evaluators`). SimpleTES mutates with a
single completion and AgentMapElites with a coding agent -- that difference is part of what those
algorithms ARE, not a deployment choice. Anything a method does not name falls back to a default.

The one boundary that is not the method's: what a program is worth. `code` is measured by the
problem's verifier, supplied by the caller, and a search method that could redefine the objective
could make its own results.

Beats:

  1. the loop, assembled, with the runner making a lap -- "every method in this series is this
     machine"
  2. the four seams, with the API name at each station, then the split inside `evaluate`, then
     what the LOOP keeps: concurrency, budget, checkpoints
  3. the swap: three method cards in turn -- AgentMapElites, SimpleTES, AnnealedIdeaCode -- each
     rewriting all four stations, while the store and the loop's own machinery do not move.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Circle, Create, Dot, FadeIn, FadeOut, Indicate,
                   LaggedStart, Line, MoveAlongPath, MovingCameraScene, Rectangle,
                   RoundedRectangle, Square, Transform, VGroup, VMobject, Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, PURPLE, SUB,
                       fit, para, score_color, spoke, txt)

# ---- the stage -------------------------------------------------------------
LOOP_AT = {"select": np.array([-2.6, 1.15, 0.0]), "mutate": np.array([0.0, 2.55, 0.0]),
           "evaluate": np.array([2.6, 1.15, 0.0]), "record": np.array([0.0, -0.25, 0.0])}
BOX_W, BOX_H = 2.9, 1.0
CAP_AT = np.array([0.0, -3.5, 0.0])
STORE_Y, CARD_Y = -1.55, -2.52
TITLES = {"select": "select a parent", "mutate": "mutate it",
          "evaluate": "evaluate", "record": "record"}


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
        boxes = {k: loop_box(k, TITLES[k], sub) for k, sub in (
            ("select", "from what the method keeps"), ("mutate", "an operator writes a child"),
            ("evaluate", "something scores it"), ("record", "the tree keeps everything"))}
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

        # The method reaches every station. Shown as the actual API at each one rather than as a
        # brace around a subset: an earlier cut braced select+record and said "the method decides
        # where the tree grows next", which is the parent-selection half of an algorithm and left
        # the operator and the judge looking like deployment choices.
        api = {
            "select": txt("ask()", 15, ORANGE).next_to(b["select"], LEFT, buff=0.22),
            "record": txt("on_measured()", 15, ORANGE).next_to(b["record"], DOWN, buff=0.2),
            "mutate": txt("default_variator()", 15, ORANGE).next_to(b["mutate"], RIGHT, buff=0.22),
            "evaluate": txt("default_evaluators()", 15, ORANGE).next_to(b["evaluate"], RIGHT,
                                                                       buff=0.22),
        }
        tag_all = VGroup(txt("Method", 22, ORANGE, weight="BOLD"),
                         txt("the algorithm", 15, SUB)).arrange(DOWN, buff=0.06)
        tag_all.move_to([-5.35, 3.25, 0])
        self.play(FadeIn(tag_all),
                  LaggedStart(*[FadeIn(api[k]) for k in
                                ("select", "record", "mutate", "evaluate")], lag_ratio=0.2),
                  run_time=1.6)
        self.say("a method reaches all FOUR stations. At two it decides, item by item: which\n"
                 "parent goes out, and what the measured child means", hold=2.4)
        self.play(Indicate(VGroup(b["select"], b["record"], api["select"], api["record"]),
                           color=ORANGE, scale_factor=1.03), run_time=1.0)
        self.say("at the other two it does not decide — it NAMES the component. SimpleTES\n"
                 "mutates with one blind completion, AgentMapElites with a coding agent, and\n"
                 "that difference is part of what those algorithms are", hold=3.0)
        self.play(Indicate(VGroup(b["mutate"], b["evaluate"], api["mutate"], api["evaluate"]),
                           color=ORANGE, scale_factor=1.03), run_time=1.0)

        # The one boundary that is not the method's.
        # Kept short and then width-capped: this column starts at x ~ 4.2 and the frame ends at
        # 7.11, so a line that reads well in the source runs straight off the right edge.
        split = VGroup(txt("idea → the method's judge", 13.5, ORANGE),
                       txt("code → the problem's verifier", 13.5, BLUE)
                       ).arrange(DOWN, buff=0.1, aligned_edge=LEFT)
        fit(split, 2.75)
        split.next_to(api["evaluate"], DOWN, buff=0.18).align_to(api["evaluate"], LEFT)
        self.play(FadeIn(split), run_time=0.7)
        self.say("with one line it may not cross: what a PROGRAM is worth is the problem's\n"
                 "question. A method that could answer it could make its own results", hold=3.0)

        # Store + Budget + what the loop keeps for itself
        store = RoundedRectangle(width=10.6, height=0.62, corner_radius=0.12, stroke_width=1.8,
                                 color=EDGE, fill_color=PANEL, fill_opacity=1.0)
        store.move_to([0, STORE_Y, 0])
        store_lab = VGroup(txt("Store", 17, INK, weight="BOLD"),
                           txt("every genome, with both lineages", 13.5, SUB),
                           txt("·", 13.5, MUTED),
                           txt("the loop keeps", 17, INK, weight="BOLD"),
                           txt("concurrency · budget · checkpoints", 13.5, SUB)
                           ).arrange(RIGHT, buff=0.26).move_to(store.get_center())
        self.play(FadeIn(store), FadeIn(store_lab), run_time=1.0)
        self.say("and what the loop keeps is what every algorithm needs and none should have to\n"
                 "write. If adding a method meant editing the loop, the seam would be wrong",
                 hold=2.8)

        self.tags = VGroup(tag_all, split, *api.values())
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
        # All four stations, per method. The mutate row is the one that makes the point: these
        # are not the same algorithm running three selection policies.
        rows = [
            {"select": "from the MAP-Elites grid", "mutate": "a coding agent",
             "evaluate": "the problem's verifier", "record": "tree grows · grid updates"},
            {"select": "a chain's selector picks", "mutate": "one completion, no tools",
             "evaluate": "the problem's verifier", "record": "best-of-k joins its chain"},
            {"select": "the annealed softmax", "mutate": "prose, then a coding agent",
             "evaluate": "the verifier · and its own judge", "record": "tree · judge recalibrates"},
        ]

        self.say("so a method is a plug-in at every station. Watch all four rewrite themselves —\n"
                 "and watch what does not move", hold=1.2)
        self.play(FadeOut(self.tags), run_time=0.5)

        shown = None
        for i, card in enumerate(cards):
            card.move_to([0, CARD_Y, 0])
            # The station titles are re-stated rather than read back off the mobject: `Transform`
            # moves points, not attributes, so after the first swap the old text is still hanging
            # on the object and reading it back is only accidentally right.
            fresh = {k: loop_box(k, TITLES[k], rows[i][k]) for k in rows[i]}
            anims = [Transform(b[k], fresh[k]) for k in fresh]
            if shown is None:
                anims.append(FadeIn(card, shift=UP * 0.2))
                shown = card
            else:
                anims.append(Transform(shown, card))
            self.play(*anims, run_time=0.9)
            self.wait(1.8)

        self.say("three different searches on one engine — and the store, the budget and the\n"
                 "loop never learned any of their names. What a method does not name, it "
                 "inherits.", hold=3.4)
