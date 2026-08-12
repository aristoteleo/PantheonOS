"""Circle packing n=26, explained with the best packing we produced.

    manim -qm --format=mp4 manim_prob_packing.py PackingProblem

Everything drawn is the real artifact: the 26 centres and radii `results/packing_best.py`
returns, whose sum of radii is 2.635983 against the published 2.635.

Acts:

  1. the object: 26 circles grow into the unit square, largest first, the sum ticking up
  2. the rules: inside the square, no two circles overlapping -- and at the optimum almost
     everything KISSES; the tangencies are drawn from the data (gap < 1e-4)
  3. the score: 2.635983, a hair above the published record, from a 1.8045 naive seed
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, UP, Circle, Create, FadeIn, FadeOut, LaggedStart, Line,
                   MovingCameraScene, Square, Transform, VGroup, Write)

from manim_kit import BLUE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, SUB, fit, para, txt
from prob_data import PACKING

CAP_AT = np.array([0.0, -3.45, 0.0])
C = np.asarray(PACKING["centers"], float)
R = np.asarray(PACKING["radii"], float)
BOX = 5.6                                    # screen size of the unit square
AT = np.array([-2.2, 0.35, 0.0])             # its centre


def to_screen(p):
    return AT + np.array([(p[0] - 0.5) * BOX, (p[1] - 0.5) * BOX, 0.0])


class PackingProblem(Kit, MovingCameraScene):

    def construct(self):
        title = txt("Circle Packing, n = 26", 48, INK, weight="BOLD")
        sub = para("fit 26 circles into the unit square — grow their radii as far as\n"
                   "geometry allows, and the score is the SUM of all 26", 26, SUB)
        fine = txt("the packing shown is our best artifact: Σr = 2.635983 (published record 2.635)",
                   16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.2).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.2)
        self.play(FadeOut(card), run_time=0.8)
        self.caption = None

        # ---- act 1: the object --------------------------------------------------
        box = Square(side_length=BOX, color=INK, stroke_width=2.4).move_to(AT)
        boxlab = txt("the unit square", 15, MUTED).next_to(box, UP, buff=0.14)
        self.play(Create(box), FadeIn(boxlab), run_time=0.8)

        order = np.argsort(-R)
        circles = {}
        for i in order:
            circles[i] = Circle(radius=R[i] * BOX, stroke_width=1.6, color=BLUE,
                                fill_color=BLUE, fill_opacity=0.18).move_to(to_screen(C[i]))

        total = txt("Σr = 0.000000", 26, INK).move_to([3.35, 1.6, 0])
        self.say("twenty-six circles, placed and sized freely. Every bit of radius anywhere\n"
                 "adds to one number", color=SUB)
        self.play(FadeIn(total), run_time=0.4)
        run_sum, batches = 0.0, []
        idx = list(order)
        for k in range(0, 26, 5):
            chunk = idx[k:k + 5]
            run_sum += float(R[chunk].sum())
            batches.append((chunk, run_sum))
        for chunk, s in batches:
            # The counter swap is its OWN short play. Transform scrambles digit glyphs, and a
            # crossfade sharing the batch's 0.75s window shows both readings at half opacity
            # for most of it -- sub-animation run_times do not survive being grouped.
            self.play(LaggedStart(*[FadeIn(circles[i], scale=0.6) for i in chunk],
                                  lag_ratio=0.12), run_time=0.6)
            new_total = txt(f"Σr = {s:.6f}", 26, INK).move_to(total)
            self.play(FadeOut(total), FadeIn(new_total), run_time=0.15)
            total = new_total
        self.wait(1.0)

        # ---- act 2: the rules ---------------------------------------------------
        self.say("two rules: stay inside the square, and never overlap another circle.\n"
                 "At the optimum the slack is gone — almost everything touches", hold=0.3)
        touches = VGroup()
        for i in range(26):
            for j in range(i + 1, 26):
                gap = np.linalg.norm(C[i] - C[j]) - (R[i] + R[j])
                if abs(gap) < 1e-4:
                    touches.add(Line(to_screen(C[i]), to_screen(C[j]),
                                     color=ORANGE, stroke_width=1.8).set_opacity(0.85))
        wall = VGroup(*[Circle(radius=R[i] * BOX, stroke_width=2.6, color=ORANGE)
                        .move_to(to_screen(C[i]))
                        for i in range(26)
                        if min(C[i][0] - R[i], 1 - C[i][0] - R[i],
                               C[i][1] - R[i], 1 - C[i][1] - R[i]) < 1e-4])
        self.play(LaggedStart(*[Create(t) for t in touches], lag_ratio=0.03), run_time=1.8)
        tlab = txt(f"{len(touches)} tangencies", 17, ORANGE).move_to([3.35, 0.7, 0])
        self.play(FadeIn(tlab), run_time=0.4)
        self.say("every orange edge is a pair at distance exactly r₁+r₂ — a rigid web.\n"
                 "Growing any one circle means shrinking its neighbours", hold=1.8)
        self.play(LaggedStart(*[Create(w) for w in wall], lag_ratio=0.1), run_time=1.0)
        wlab = txt(f"{len(wall)} press the wall", 17, ORANGE).next_to(tlab, DOWN,
                                                                     buff=0.18, aligned_edge=LEFT)
        self.play(FadeIn(wlab), run_time=0.4)
        self.wait(1.2)

        # ---- act 3: the score ---------------------------------------------------
        # `total` leaves too -- the board restates the same number in green, and a stale black
        # copy floating above it reads as two different scores.
        self.play(FadeOut(touches), FadeOut(wall), FadeOut(tlab), FadeOut(wlab),
                  FadeOut(total), run_time=0.6)
        board = VGroup(
            txt("Σr = 2.635983", 30, GREEN, weight="BOLD"),
            para("published record (AlphaEvolve, n=26)  2.635\n"
                 "naive seed — rings of equal circles   1.8045", 18, SUB),
        ).arrange(DOWN, buff=0.3, aligned_edge=LEFT).move_to([3.3, 1.1, 0])
        fit(board, 4.4)
        self.play(FadeIn(board), run_time=0.7)
        self.say("the evolved packing reaches 2.635983 — a hair above the published record —\n"
                 "having started from a naive ring layout worth 1.80", hold=3.2)

    def say(self, text, size=22, color=INK, hold=0.0):
        new = para(text, size, color) if "\n" in text else txt(text, size, color)
        fit(new, FULL_W - 2.0).move_to(CAP_AT)
        if self.caption is None:
            self.caption = new
            self.play(FadeIn(new), run_time=0.5)
        else:
            self.play(Transform(self.caption, new), run_time=0.5)
        if hold:
            self.wait(hold)
