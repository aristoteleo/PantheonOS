"""AHC039 -- purse seine fishing -- explained with a real case and the real seed net.

    manim -qm --format=mp4 manim_prob_ahc039.py AhcProblem

Everything drawn is real: case 0 of the 150 official inputs (a seeded 2,200-per-species sample
of its 10,000 fish, for rendering), and the 92-vertex net the 5th-place seed solution actually
outputs on that case. The inside-counts on screen were computed against all 10,000 fish.

Acts:

  1. the sea: 5,000 mackerel and 5,000 sardines, worth +1 and -1
  2. the net: one rectilinear polygon; the real seed's net draws itself around the mackerel
  3. the score: 4151 in, 617 regrets, mean over 150 cases -- and why this is the benchmark the
     ablation campaign runs on
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Create, Dot, FadeIn, FadeOut,
                   MovingCameraScene, Square, Transform, VGroup, VMobject, Write)

from manim_kit import BLUE, FULL_W, GREEN, INK, Kit, MUTED, RED, SUB, fit, para, txt
from prob_data import AHC

CAP_AT = np.array([0.0, -3.45, 0.0])
BOX = 5.9
AT = np.array([-2.35, 0.35, 0.0])
SCALE = AHC["coord_max"]


def to_screen(p):
    return AT + np.array([(p[0] / SCALE - 0.5) * BOX, (p[1] / SCALE - 0.5) * BOX, 0.0])


def fish_cloud(pts, color, r=0.016, opacity=0.55) -> VGroup:
    return VGroup(*[Dot(to_screen(p), radius=r, color=color).set_opacity(opacity)
                    for p in pts])


class AhcProblem(Kit, MovingCameraScene):

    def construct(self):
        title = txt("AHC039 — Purse Seine Fishing", 46, INK, weight="BOLD")
        sub = para("10,000 fish on a plane. Draw ONE net — an axis-aligned polygon —\n"
                   "that catches mackerel and leaves the sardines", 26, SUB)
        fine = txt("case 0 of 150, drawn from the real input; the net is the 5th-place seed's "
                   "actual output · 2,200 of each 5,000 shown", 15, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.2).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.6)
        self.play(FadeOut(card), run_time=0.8)
        self.caption = None

        # ---- act 1: the sea -----------------------------------------------------
        box = Square(side_length=BOX, color=MUTED, stroke_width=1.8).move_to(AT)
        mack = fish_cloud(AHC["mack_sample"], BLUE)
        sard = fish_cloud(AHC["sard_sample"], RED)
        self.say("mackerel are worth +1 each — and every sardine inside the net costs 1",
                 color=SUB)
        self.play(Create(box), FadeIn(mack, run_time=1.4), run_time=1.4)
        leg_m = VGroup(Dot(radius=0.05, color=BLUE), txt("mackerel  +1", 17, INK)).arrange(
            RIGHT, buff=0.16).move_to([3.1, 2.1, 0])
        self.play(FadeIn(leg_m), run_time=0.4)
        self.play(FadeIn(sard), run_time=1.2)
        leg_s = VGroup(Dot(radius=0.05, color=RED), txt("sardine   −1", 17, INK)).arrange(
            RIGHT, buff=0.16).next_to(leg_m, DOWN, buff=0.2, aligned_edge=LEFT)
        self.play(FadeIn(leg_s), run_time=0.4)
        self.say("the two schools mingle — there is no clean rectangle around the good fish",
                 hold=1.6)

        # ---- act 2: the net ------------------------------------------------------
        poly = np.asarray(AHC["poly"], float)
        net = VMobject(color=INK, stroke_width=2.6)
        net.set_points_as_corners([to_screen(p) for p in list(poly) + [poly[0]]])
        rules = para("axis-parallel edges only\n≤ 1,000 vertices\nperimeter ≤ 400,000\n"
                     "2 seconds per case", 16, SUB)
        rules.next_to(leg_s, DOWN, buff=0.5, aligned_edge=LEFT)
        self.say("the net is one rectilinear polygon. This one — 92 vertices — is what the\n"
                 "seed solution actually draws on this case", color=SUB)
        # The net draws alone: a FadeIn sharing its 3.2s window leaves the rules text sitting at
        # half opacity for three seconds, which reads as a rendering fault.
        self.play(Create(net), run_time=3.2)
        self.play(FadeIn(rules), run_time=0.5)
        self.wait(1.2)

        # ---- act 3: the score ----------------------------------------------------
        score = VGroup(
            txt(f"caught  {AHC['inside_mack']}", 20, BLUE),
            txt(f"regrets   −{AHC['inside_sard']}", 20, RED),
            txt(f"case score  {AHC['inside_mack'] - AHC['inside_sard'] + 1}", 22, GREEN,
                weight="BOLD"),
        ).arrange(DOWN, buff=0.22, aligned_edge=LEFT)
        score.next_to(rules, DOWN, buff=0.5, aligned_edge=LEFT)
        self.say("its score on this case: 4,151 mackerel in, 617 sardines it could not avoid",
                 color=SUB)
        self.play(FadeIn(score), run_time=0.8)
        self.wait(1.6)
        self.say("the contest score is the mean over 150 such cases — the seed averages ~3,700,\n"
                 "already 5th place. The search has to improve a strong incumbent", hold=2.6)
        self.say("that is why this is the campaign's benchmark: improvements stay additive\n"
                 "across cases, eval noise is 0.0018 against steps of 0.01–0.1", hold=3.2)

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
