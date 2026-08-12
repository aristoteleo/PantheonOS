"""The Erdos minimum-overlap problem, explained with the record construction.

    manim -qm --format=mp4 manim_prob_erdos.py ErdosProblem

A PROBLEM explainer, not a method one: what the object is, how it is judged, and what the search
found. Everything drawn is real -- the step function is the K=951 record construction
(Psi = 0.380909) and both Psi(k) profiles come from the evaluator's own arithmetic.

Acts:

  1. the object: a step function h on [0,2] with unit mass, first shown as the naive uniform 1/2
  2. the judge: slide a translate across it; the score is the WORST translate, so a good h has no
     bad shift anywhere
  3. the answer: morph to the record shape and watch the profile flatten -- a minimax solution
     equalises its worst cases -- closing on 0.380909 vs the published numbers
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, UP, Axes, Create, FadeIn, FadeOut, MovingCameraScene,
                   Transform, VGroup, VMobject, Write)

from manim_kit import BLUE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, SUB, fit, para, txt
from prob_data import ERDOS

CAP_AT = np.array([0.0, -3.45, 0.0])
H = np.asarray(ERDOS["steps"], float)
K = len(H)
PROF = np.asarray(ERDOS["profile"], float)
PROF_U = np.asarray(ERDOS["profile_uniform"], float)


def step_curve(ax, h, color, width=2.4) -> VMobject:
    """The step function as one polyline; at K=951 individual steps read as a curve."""
    xs = np.linspace(0, 2, len(h))
    line = VMobject(color=color, stroke_width=width)
    line.set_points_as_corners([ax.c2p(x, y) for x, y in zip(xs, h)])
    return line


def profile_curve(ax, prof, color, width=2.2) -> VMobject:
    lags = np.linspace(-2, 2, len(prof))
    line = VMobject(color=color, stroke_width=width)
    line.set_points_as_corners([ax.c2p(k, v) for k, v in zip(lags, prof)])
    return line


class ErdosProblem(Kit, MovingCameraScene):

    def construct(self):
        title = txt("The Erdős Minimum-Overlap Problem", 46, INK, weight="BOLD")
        sub = para("shape one function so that no translate of it overlaps badly —\n"
                   "the score is the WORST case, and lower is better", 26, SUB)
        fine = txt("the construction shown is our record run's: K = 951, Ψ = 0.380909",
                   16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.2).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.6)
        self.play(FadeOut(card), run_time=0.8)
        self.caption = None

        # ---- act 1: the object --------------------------------------------------
        ax = Axes(x_range=[0, 2, 0.5], y_range=[0, 1.05, 0.5], x_length=8.4, y_length=3.1,
                  tips=False, axis_config={"color": MUTED, "stroke_width": 2})
        ax.move_to([0, 1.35, 0])
        ticks = VGroup(*[txt(s, 14, MUTED).next_to(ax.c2p(x, 0), DOWN, buff=0.14)
                         for s, x in (("0", 0), ("1", 1), ("2", 2))],
                       txt("1", 14, MUTED).next_to(ax.c2p(0, 1), LEFT, buff=0.12))
        self.play(Create(ax), FadeIn(ticks), run_time=0.9)

        uni = step_curve(ax, np.full(K, 0.5), BLUE)
        self.say("a step function h on [0,2], heights in [0,1], total mass fixed at 1.\n"
                 "The naive answer is flat: h = 1/2 everywhere", hold=0.4)
        self.play(Create(uni), run_time=1.2)
        self.wait(2.0)

        # ---- act 2: the judge ---------------------------------------------------
        pax = Axes(x_range=[-2, 2, 1], y_range=[0.0, 0.55, 0.25], x_length=8.4, y_length=1.7,
                   tips=False, axis_config={"color": MUTED, "stroke_width": 2})
        pax.move_to([0, -1.85, 0])
        # Bottom-right INSIDE the band: both profiles are low past |k| > 1, so this corner is
        # empty; at the top it collides with the worst-case annotation.
        plab = txt("overlap of each shift k — the MAX is the score", 15, MUTED)
        plab.move_to(pax.c2p(1.08, 0.44))
        pt = VGroup(*[txt(s, 14, MUTED).next_to(pax.c2p(x, 0), DOWN, buff=0.1)
                      for s, x in (("-2", -2), ("0", 0), ("+2", 2))])
        self.say("the judge slides a translate across it: for every shift k, how much does h\n"
                 "overlap 1 − h(·+k)? The score Ψ is the WORST shift", color=SUB)
        self.play(Create(pax), FadeIn(plab), FadeIn(pt), run_time=0.9)

        prof_u = profile_curve(pax, PROF_U, BLUE)
        self.play(Create(prof_u), run_time=1.6)
        worst_u = txt("Ψ = 0.500 — the flat answer's worst case", 17, ORANGE)
        worst_u.move_to(pax.c2p(-1.08, 0.44))
        self.play(FadeIn(worst_u), run_time=0.5)
        # Not "every shift is equally bad" -- the picture directly contradicts that. The flat
        # answer has ONE worst shift, the aligned one, and a single shift standing out is
        # exactly what act 3's flattened profile exists to remove.
        self.say("the flat answer's overlap peaks at the aligned shift: Ψ = 0.5. One shift\n"
                 "sticking out is precisely what a good construction must not allow", hold=2.0)

        # ---- act 3: the answer --------------------------------------------------
        self.say("evolution reshapes h — near zero at the edges, a plateau in the middle,\n"
                 "mirror-symmetric — trading mass away from wherever the worst shift lives",
                 hold=0.3)
        best = step_curve(ax, H, GREEN)
        prof_b = profile_curve(pax, PROF, GREEN)
        self.play(Transform(uni, best), run_time=2.2)
        self.wait(0.8)

        # The translate, made concrete at the one shift that matters: 1 - h(.+k) at the record's
        # WORST lag, drawn over the overlap window only (the evaluator's correlation does the
        # same). Without this the captions talk about sliding a translate no one ever sees.
        lag = int(ERDOS["worst_lag"])
        xs = np.linspace(0, 2, K)
        g = VMobject(color=ORANGE, stroke_width=2.2).set_stroke(opacity=0.85)
        pts = [(xs[i], 1 - H[i - lag]) for i in range(max(0, lag), min(K, K + lag))]
        g.set_points_as_corners([ax.c2p(x, y) for x, y in pts])
        glab = txt("1 − h(·+k) at the worst shift", 15, ORANGE)
        glab.next_to(ax.c2p(1.55, 0.97), UP, buff=0.08)
        self.say("here is its WORST translate, overlaid: even against this one, the pointwise\n"
                 "product of the two curves integrates to only 0.381", color=SUB)
        self.play(Create(g), FadeIn(glab), run_time=1.4)
        self.wait(1.8)
        self.play(FadeOut(g), FadeOut(glab), run_time=0.5)

        self.play(Transform(prof_u, prof_b), FadeOut(worst_u), run_time=1.6)
        self.say("and the profile flattens on top — a minimax solution has no single worst\n"
                 "case left, because any that stood out has been traded away", hold=2.4)

        lvl = txt("Ψ = 0.380909", 20, GREEN, weight="BOLD")
        lvl.next_to(pax.c2p(-1.25, 0.381), UP, buff=0.1)
        self.play(FadeIn(lvl), run_time=0.5)
        board = para("ours 0.380909   ·   AlphaEvolve 0.380924   ·   Haugland 0.380927\n"
                     "(SimpleTES's published construction is lower still: 0.380868)", 17, SUB)
        fit(board, FULL_W - 2.4).move_to(CAP_AT)
        self.play(Transform(self.caption, board), run_time=0.6)
        self.wait(4.0)

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
