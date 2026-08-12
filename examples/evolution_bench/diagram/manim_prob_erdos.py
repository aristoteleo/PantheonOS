"""The Erdos minimum-overlap problem, from the discrete game up to the record construction.

    manim -qm --format=mp4 manim_prob_erdos.py ErdosProblem

The earlier cut of this video opened on "a step function with unit mass" and was rightly called
unintelligible. The problem has a concrete story and the video now tells it in order:

  1. the GAME: colour half the cells of a strip red, the rest blue. Slide a copy of the strip
     across itself; wherever a red cell meets a blue cell, that is one overlap. Erdos asks for
     the colouring whose WORST slide has the fewest meetings. A block split fails maximally: at
     one slide, every red cell finds a blue partner.
  2. fractional colouring: a cell may be partly red -- h(x) is the red fraction, always summing
     to half the strip. Half-red everywhere halves the worst slide. The bars ARE the step
     function; nothing else changed.
  3. the judge, live: the translate slides across, the overlap window shrinks, and a dot traces
     overlap-vs-shift below. The peak of that trace is the score Psi. For flat h: Psi = 0.5.
  4. the answer: morph to the real K=951 record (Psi = 0.380909), overlay its actual worst
     translate, and watch the profile flatten -- a minimax solution has traded away every
     stand-out shift. Close on the published numbers.

Everything numeric is real: the construction, both profiles, and the worst lag come from the
evaluator's own arithmetic (see `prob_data`).
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Create, FadeIn, FadeOut, LaggedStart,
                   MovingCameraScene, Rectangle, Square, Transform, ValueTracker, VGroup,
                   VMobject, Write, always_redraw)

from manim_kit import BLUE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, RED, SUB, fit, para, txt
from prob_data import ERDOS

CAP_AT = np.array([0.0, -3.45, 0.0])
H = np.asarray(ERDOS["steps"], float)
K = len(H)
PROF = np.asarray(ERDOS["profile"], float)
PROF_U = np.asarray(ERDOS["profile_uniform"], float)

N_CELLS, CELL = 12, 0.5


def strip(reds, y, fill=1.0) -> VGroup:
    """A strip of cells; `reds[i]` is how red cell i is (the rest of the cell is blue)."""
    g = VGroup()
    x0 = -N_CELLS * CELL / 2
    for i, r in enumerate(reds):
        cell = Square(side_length=CELL, stroke_width=1.6, color=INK)
        cell.move_to([x0 + (i + 0.5) * CELL, y, 0])
        if r > 0:
            red = Rectangle(width=CELL, height=CELL * r, stroke_width=0, fill_color=RED,
                            fill_opacity=0.75 * fill)
            red.move_to(cell.get_bottom() + np.array([0, CELL * r / 2, 0]))
            g.add(red)
        if r < 1:
            blue = Rectangle(width=CELL, height=CELL * (1 - r), stroke_width=0, fill_color=BLUE,
                             fill_opacity=0.55 * fill)
            blue.move_to(cell.get_top() - np.array([0, CELL * (1 - r) / 2, 0]))
            g.add(blue)
        g.add(cell)
    return g


class ErdosProblem(Kit, MovingCameraScene):

    def construct(self):
        title = txt("The Erdős Minimum-Overlap Problem", 46, INK, weight="BOLD")
        sub = para("colour half of a strip red and the rest blue, so that however far\n"
                   "you slide a copy, red meets blue as rarely as possible", 26, SUB)
        fine = txt("the construction at the end is our record run's: K = 951, Ψ = 0.380909",
                   16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.2).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.8)
        self.play(FadeOut(card), run_time=0.8)
        self.caption = None

        self.act_game()
        self.act_fractional()
        self.act_judge()

    # ---- act 1: the game ------------------------------------------------------
    def act_game(self):
        block = [1.0] * 6 + [0.0] * 6
        top = strip(block, 1.9)
        toplab = txt("the strip — 6 red cells, 6 blue", 16, SUB).next_to(top, UP, buff=0.18)
        self.say("start with the obvious colouring: red on the left, blue on the right",
                 color=SUB)
        self.play(FadeIn(top), FadeIn(toplab), run_time=0.9)

        copy = strip(block, 0.35, fill=0.85)
        copylab = txt("an identical copy, sliding underneath", 16, SUB)
        copylab.next_to(copy, DOWN, buff=0.18)
        self.play(FadeIn(copy), FadeIn(copylab), run_time=0.8)
        self.say("slide the copy. Count every place where a RED cell sits above a BLUE cell —\n"
                 "one meeting each. The colouring is judged by its WORST slide", hold=1.2)

        counter = txt("meetings: 0", 20, INK).move_to([4.75, 1.9, 0])
        self.play(FadeIn(counter), run_time=0.4)

        # slide by 6: every red cell of the top strip lands over a blue cell of the copy
        self.play(copy.animate.shift(LEFT * 6 * CELL),
                  copylab.animate.shift(LEFT * 6 * CELL), run_time=1.6)
        marks = VGroup(*[Square(side_length=CELL, stroke_width=3.2, color=ORANGE).move_to(
            [-N_CELLS * CELL / 2 + (i + 0.5) * CELL, 1.9, 0]) for i in range(6)])
        newc = txt("meetings: 6", 20, ORANGE, weight="BOLD").move_to(counter)
        self.play(LaggedStart(*[Create(m) for m in marks], lag_ratio=0.1),
                  Transform(counter, newc), run_time=1.2)
        self.say("slid by six, the red block sits exactly on the blue block: 6 meetings out of\n"
                 "6 possible. The block colouring fails as badly as anything can", hold=2.4)

        self.game = VGroup(top, toplab, copy, copylab, counter, marks)

    # ---- act 2: fractional colouring -----------------------------------------
    def act_fractional(self):
        self.say("Erdős allows a subtler move: a cell may be PARTLY red — say half — as long\n"
                 "as the total red over the strip stays exactly half", color=SUB)
        half = strip([0.5] * N_CELLS, 1.9)
        top, toplab = self.game[0], self.game[1]
        self.play(Transform(top, half),
                  FadeOut(self.game[2:]),
                  Transform(toplab, txt("every cell half red, half blue", 16, SUB)
                            .next_to(half, UP, buff=0.18)),
                  run_time=1.4)
        self.wait(1.0)
        self.say("now no slide is special: every aligned pair meets with strength ½ × ½ = ¼ —\n"
                 "the worst slide just fell from 6 to 3. Spreading out helps", hold=2.6)
        self.say("read the red HEIGHTS as a curve and this colouring is a function h on the\n"
                 "strip: h(x) = how red the strip is at x. That curve is the whole game",
                 hold=2.4)
        self.play(FadeOut(top), FadeOut(toplab), run_time=0.7)

    # ---- acts 3 + 4: the judge, live, then the record -------------------------
    def act_judge(self):
        ax = Axes(x_range=[0, 2, 0.5], y_range=[0, 1.05, 0.5], x_length=8.4, y_length=2.6,
                  tips=False, axis_config={"color": MUTED, "stroke_width": 2})
        ax.move_to([0, 1.75, 0])
        ticks = VGroup(*[txt(s, 14, MUTED).next_to(ax.c2p(x, 0), DOWN, buff=0.14)
                         for s, x in (("0", 0), ("1", 1), ("2", 2))],
                       txt("1", 14, MUTED).next_to(ax.c2p(0, 1), LEFT, buff=0.12),
                       txt("h — how red", 15, SUB).next_to(ax.c2p(0.02, 1.0), RIGHT, buff=0.1))
        uni = VMobject(color=RED, stroke_width=2.6)
        uni.set_points_as_corners([ax.c2p(0, 0.5), ax.c2p(2, 0.5)])
        self.play(Create(ax), FadeIn(ticks), Create(uni), run_time=1.0)

        pax = Axes(x_range=[-2, 2, 1], y_range=[0.0, 0.55, 0.25], x_length=8.4, y_length=1.55,
                   tips=False, axis_config={"color": MUTED, "stroke_width": 2})
        pax.move_to([0, -1.7, 0])
        pt = VGroup(*[txt(s, 14, MUTED).next_to(pax.c2p(x, 0), DOWN, buff=0.1)
                      for s, x in (("-2", -2), ("0", 0), ("+2", 2))],
                    txt("meetings at each slide", 15, MUTED).move_to(pax.c2p(-1.3, 0.47)))
        self.play(Create(pax), FadeIn(pt), run_time=0.8)

        # The live judge: the sliding copy is the window where the strips still overlap; the
        # meeting rate is 1/4 on that window, and a dot traces total-meetings against the shift.
        k = ValueTracker(-2.0)

        def window():
            lo, hi = max(0.0, -k.get_value()), min(2.0, 2.0 - k.get_value())
            if hi <= lo:
                return VGroup()
            r = Rectangle(width=ax.c2p(hi, 0)[0] - ax.c2p(lo, 0)[0],
                          height=ax.c2p(0, 0.25)[1] - ax.c2p(0, 0)[1],
                          stroke_width=0, fill_color=ORANGE, fill_opacity=0.35)
            r.move_to(ax.c2p((lo + hi) / 2, 0.125))
            return r

        def tracer():
            kk = k.get_value()
            idx = int(round((kk + 2) / 4 * (len(PROF_U) - 1)))
            keep = PROF_U[: idx + 1]
            line = VMobject(color=BLUE, stroke_width=2.4)
            if len(keep) >= 2:
                lags = np.linspace(-2, 2, len(PROF_U))[: idx + 1]
                line.set_points_as_corners([pax.c2p(x, v) for x, v in zip(lags, keep)])
            return line

        win = always_redraw(window)
        trace = always_redraw(tracer)
        self.add(win, trace)
        self.say("the judge slides the whole strip across itself. Orange: where the two copies\n"
                 "still overlap, red meeting blue at rate ¼. Below: total meetings, per slide",
                 color=SUB)
        self.play(k.animate.set_value(2.0), run_time=4.0, rate_func=lambda t: t)
        self.remove(win, trace)
        prof_u = VMobject(color=BLUE, stroke_width=2.4)
        prof_u.set_points_as_corners([pax.c2p(x, v) for x, v in
                                      zip(np.linspace(-2, 2, len(PROF_U)), PROF_U)])
        self.add(prof_u)
        worst_u = txt("Ψ = 0.500 — the worst slide, dead centre", 17, ORANGE)
        worst_u.move_to(pax.c2p(1.15, 0.44))
        self.play(FadeIn(worst_u), run_time=0.5)
        self.say("the score is the PEAK of that curve: Ψ = 0.5 for the flat colouring. One\n"
                 "slide sticking out is exactly what a good h must not allow", hold=2.6)

        # ---- the record ----------------------------------------------------------
        self.say("this is what evolution found instead — still exactly half red in total,\n"
                 "but pushed to the edges in bursts", hold=0.3)
        xs = np.linspace(0, 2, K)
        best = VMobject(color=GREEN, stroke_width=2.2)
        best.set_points_as_corners([ax.c2p(x, y) for x, y in zip(xs, H)])
        self.play(Transform(uni, best), run_time=2.2)
        self.wait(0.8)

        lag = int(ERDOS["worst_lag"])
        g = VMobject(color=ORANGE, stroke_width=2.2).set_stroke(opacity=0.85)
        pts = [(xs[i], 1 - H[i - lag]) for i in range(max(0, lag), min(K, K + lag))]
        g.set_points_as_corners([ax.c2p(x, y) for x, y in pts])
        glab = txt("the blue of the copy, at the worst slide", 15, ORANGE)
        glab.next_to(ax.c2p(1.42, 1.0), UP, buff=0.08)
        self.say("its worst slide, overlaid in orange: wherever this curve is high, the red\n"
                 "curve below it is low — the two dodge each other", color=SUB)
        self.play(Create(g), FadeIn(glab), run_time=1.4)
        self.wait(2.0)
        self.play(FadeOut(g), FadeOut(glab), run_time=0.5)

        prof_b = VMobject(color=GREEN, stroke_width=2.4)
        prof_b.set_points_as_corners([pax.c2p(x, v) for x, v in
                                      zip(np.linspace(-2, 2, len(PROF)), PROF)])
        self.play(Transform(prof_u, prof_b), FadeOut(worst_u), run_time=1.6)
        lvl = txt("Ψ = 0.380909", 20, GREEN, weight="BOLD")
        lvl.move_to(pax.c2p(1.25, 0.47))
        self.play(FadeIn(lvl), run_time=0.5)
        self.say("its meetings curve is nearly FLAT on top: no slide stands out any more,\n"
                 "because any that did has been traded away. That is what minimax looks like",
                 hold=2.8)

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
