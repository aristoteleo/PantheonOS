"""The Erdos minimum-overlap problem, told entirely in cells.

    manim -qm --format=mp4 manim_prob_erdos.py ErdosProblem

Third cut. The first opened on measure theory; the second still switched notation midway (cells,
then suddenly axes, h, and 1-h(.+k)) and still never said WHY anything slides. This one keeps a
single visual language -- a strip of red/blue cells and a bar chart of slides -- and puts the
missing bridge on screen: a red and a blue cell six apart LINE UP when the copy slides by six,
so the slide-by-6 count IS the number of red-blue pairs at distance 6.

  1. the pair: one red, one blue, 6 apart; slide the copy 6 and they align. All distance-6 pairs
     align at once: count 6. The judge tries every slide and keeps only the WORST bar.
  2. spreading out: half-red cells make every meeting worth 1/4 -- the worst bar halves
     (Psi 1.0 -> 0.5).
  3. the record: the same strip with 951 cells (drawn as a red/blue area -- same object, finer),
     whose bar chart is nearly FLAT on top at Psi = 0.380909. No slide left to blame.

All numbers are the evaluator's own; the 951-cell construction is the record run's artifact.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, UP, Create, FadeIn, FadeOut, LaggedStart, Line,
                   MovingCameraScene, Polygon, Rectangle, Square, Transform, VGroup, VMobject,
                   Write)

from manim_kit import BLUE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, RED, SUB, fit, para, txt
from prob_data import ERDOS

CAP_AT = np.array([0.0, -3.45, 0.0])
H = np.asarray(ERDOS["steps"], float)
PROF = np.asarray(ERDOS["profile"], float)

N, CELL = 12, 0.5
STRIP_Y, COPY_Y = 2.35, 1.55
X0 = -N * CELL / 2

# The bar panel: every possible slide, scored as Psi (meetings x 2/N -- length-normalised, so a
# 12-cell toy and the 951-cell record live on one scale).
PANEL_Y, PANEL_H, PANEL_W = -2.25, 1.55, 7.6


def psi_profile(reds):
    r = np.asarray(reds, float)
    return np.correlate(r, 1 - r, mode="full") * 2 / len(r)


def strip(reds, y, fill=1.0) -> VGroup:
    g = VGroup()
    for i, r in enumerate(reds):
        cell = Square(side_length=CELL, stroke_width=1.6, color=INK)
        cell.move_to([X0 + (i + 0.5) * CELL, y, 0])
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


def bars(prof, color, opacity=0.75) -> VGroup:
    """One thin bar per slide, x centred, height = Psi."""
    g = VGroup()
    n = len(prof)
    w = PANEL_W / n
    for i, v in enumerate(prof):
        if v <= 1e-9:
            continue
        b = Rectangle(width=w * 0.72, height=max(PANEL_H * v, 0.012), stroke_width=0,
                      fill_color=color, fill_opacity=opacity)
        b.move_to([-PANEL_W / 2 + (i + 0.5) * w, PANEL_Y + PANEL_H * v / 2, 0])
        g.add(b)
    return g


class ErdosProblem(Kit, MovingCameraScene):

    def construct(self):
        title = txt("The Erdős Minimum-Overlap Problem", 46, INK, weight="BOLD")
        sub = para("colour half a strip red, the rest blue. Slide a copy under it:\n"
                   "every slide gets a score, and YOUR score is your worst slide", 26, SUB)
        fine = txt("the 951-cell construction at the end is our record run's: Ψ = 0.380909",
                   16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.2).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.8)
        self.play(FadeOut(card), run_time=0.8)
        self.caption = None

        block = [1.0] * 6 + [0.0] * 6

        # ---- act 1: why slides ------------------------------------------------
        top = strip(block, STRIP_Y)
        copy = strip(block, COPY_Y, fill=0.8)
        copylab = txt("its copy", 15, SUB).next_to(copy, LEFT, buff=0.3)
        self.say("the obvious colouring: red left, blue right — and a copy of the same strip\n"
                 "sitting underneath", color=SUB)
        self.play(FadeIn(top), FadeIn(copy), FadeIn(copylab), run_time=1.0)

        # one pair, its distance made explicit
        i_red, i_blue = 2, 8
        arc = VMobject(color=ORANGE, stroke_width=2.6)
        a = [X0 + (i_red + 0.5) * CELL, STRIP_Y + CELL / 2, 0]
        b = [X0 + (i_blue + 0.5) * CELL, STRIP_Y + CELL / 2, 0]
        arc.set_points_smoothly([a, [(a[0] + b[0]) / 2, STRIP_Y + 1.0, 0], b])
        dlab = txt("6 apart", 16, ORANGE).move_to([(a[0] + b[0]) / 2, STRIP_Y + 1.18, 0])
        self.say("take one red cell and one blue cell, six positions apart", hold=0.2)
        self.play(Create(arc), FadeIn(dlab), run_time=0.9)
        self.wait(0.8)

        self.say("slide the copy six to the left — and that pair LINES UP: the red sits\n"
                 "directly over the blue. One meeting", color=SUB)
        self.play(copy.animate.shift(LEFT * 6 * CELL),
                  copylab.animate.shift(LEFT * 6 * CELL), run_time=1.6)
        pair = Line([a[0], STRIP_Y - CELL / 2, 0], [a[0], COPY_Y + CELL / 2, 0],
                    color=ORANGE, stroke_width=5)
        self.play(Create(pair), run_time=0.5)
        self.wait(1.2)

        self.say("but EVERY red-blue pair six apart lines up at this same slide — all six of\n"
                 "them. This slide scores 6 meetings, the worst possible", hold=0.2)
        pairs = VGroup(*[Line([X0 + (i + 0.5) * CELL, STRIP_Y - CELL / 2, 0],
                              [X0 + (i + 0.5) * CELL, COPY_Y + CELL / 2, 0],
                              color=ORANGE, stroke_width=5) for i in range(6) if i != i_red])
        cnt = txt("meetings: 6", 20, ORANGE, weight="BOLD").move_to([4.75, 1.95, 0])
        self.play(LaggedStart(*[Create(p) for p in pairs], lag_ratio=0.12),
                  FadeIn(cnt), FadeOut(arc), FadeOut(dlab), run_time=1.3)
        self.wait(1.6)

        # the judge: every slide, worst bar
        prof_block = psi_profile(block)
        panel = VGroup(
            Line([-PANEL_W / 2, PANEL_Y, 0], [PANEL_W / 2, PANEL_Y, 0],
                 color=MUTED, stroke_width=2),
            txt("← slide left        every possible slide        slide right →", 13, MUTED)
            .move_to([0, PANEL_Y - 0.28, 0]),
        )
        pb = bars(prof_block, ORANGE)
        peak = txt("worst slide: Ψ = 1.0", 17, ORANGE, weight="BOLD")
        peak.move_to([-PANEL_W / 2 + 1.1, PANEL_Y + PANEL_H + 0.24, 0])
        self.say("the judge tries every slide and scores each one — six meetings out of a\n"
                 "twelve-cell strip is Ψ = 1.0. Only the TALLEST bar counts", color=SUB)
        self.play(FadeIn(panel), LaggedStart(*[FadeIn(x) for x in pb], lag_ratio=0.04),
                  run_time=1.4)
        self.play(FadeIn(peak), run_time=0.4)
        self.say("the block colouring is perfect at some slides and catastrophic at its worst.\n"
                 "The score is the worst: Ψ = 1.0", hold=2.2)

        # ---- act 2: spreading out ---------------------------------------------
        self.say("Erdős allows partial cells. Make every cell HALF red, and slide the copy\n"
                 "back into line", color=SUB)
        half = [0.5] * N
        top2, copy2 = strip(half, STRIP_Y), strip(half, COPY_Y, fill=0.8)
        self.play(Transform(top, top2), Transform(copy, copy2),
                  copylab.animate.shift(6 * CELL * np.array([1.0, 0, 0])),
                  FadeOut(cnt), FadeOut(pair), FadeOut(pairs), run_time=1.4)
        thin = VGroup(*[Line([X0 + (i + 0.5) * CELL, STRIP_Y - CELL / 2, 0],
                             [X0 + (i + 0.5) * CELL, COPY_Y + CELL / 2, 0],
                             color=ORANGE, stroke_width=2.2).set_opacity(0.7)
                        for i in range(N)])
        cnt2 = txt("meetings: 3", 20, INK).move_to(cnt)
        self.play(LaggedStart(*[Create(t) for t in thin], lag_ratio=0.05),
                  FadeIn(cnt2), run_time=1.0)
        self.say("every column now meets — but each only at ½ × ½ = ¼. Twelve columns make 3,\n"
                 "and no other slide is worse", hold=1.6)
        prof_half = psi_profile(half)
        ph = bars(prof_half, BLUE)
        peak2 = txt("worst slide: Ψ = 0.5", 17, BLUE, weight="BOLD").move_to(peak)
        # Swap, not morph: orange-to-blue bar morphs read as brown mush for the whole window.
        self.play(FadeOut(pb), FadeIn(ph), FadeOut(peak), FadeIn(peak2), run_time=0.7)
        pb = ph
        self.say("the worst bar HALVED: Ψ = 0.5. Spreading out is the whole game —\n"
                 "how much further can it go?", hold=2.4)

        # ---- act 3: the record -------------------------------------------------
        self.say("our record run plays the same game with 951 cells. Same strip, drawn finer:\n"
                 "red below the curve, blue above — still exactly half red in total", color=SUB)
        xs = np.linspace(-PANEL_W / 2, PANEL_W / 2, len(H))
        y0, y1 = 1.35, 2.9                     # the strip band, redrawn wider
        red_area = Polygon(*([[x, y0, 0] for x in xs]
                             + [[xs[i], y0 + (y1 - y0) * H[i], 0]
                                for i in range(len(H) - 1, -1, -1)]),
                           stroke_width=0, fill_color=RED, fill_opacity=0.75)
        blue_area = Polygon(*([[xs[i], y0 + (y1 - y0) * H[i], 0] for i in range(len(H))]
                              + [[x, y1, 0] for x in reversed(xs)]),
                            stroke_width=0, fill_color=BLUE, fill_opacity=0.55)
        frame = Rectangle(width=PANEL_W, height=y1 - y0, stroke_width=1.8, color=INK)
        frame.move_to([0, (y0 + y1) / 2, 0])
        fine_strip = VGroup(red_area, blue_area, frame)
        self.play(FadeOut(VGroup(copy, copylab, cnt2, *thin)),
                  Transform(top, fine_strip), run_time=2.0)
        self.wait(1.4)

        prof_scaled = PROF.copy()
        line = VMobject(color=GREEN, stroke_width=2.6)
        lx = np.linspace(-PANEL_W / 2, PANEL_W / 2, len(prof_scaled))
        line.set_points_as_corners([[x, PANEL_Y + PANEL_H * v, 0]
                                    for x, v in zip(lx, prof_scaled)])
        peak3 = txt("worst slide: Ψ = 0.380909", 17, GREEN, weight="BOLD").move_to(peak)
        self.say("bursts at the edges, a plateau in the middle, mirror symmetry — and look at\n"
                 "its slide chart: nearly FLAT on top. No slide left to blame", color=SUB)
        self.play(FadeOut(pb), Create(line), FadeOut(peak2), FadeIn(peak3), run_time=1.8)
        self.wait(2.2)

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
