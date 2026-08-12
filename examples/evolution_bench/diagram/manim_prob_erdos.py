"""The Erdos minimum-overlap problem, taught the way it finally landed in conversation.

    manim -qm --format=mp4 manim_prob_erdos.py ErdosProblem

Fourth cut. The first three explained the CONTINUOUS object (step functions, sliding copies) and
each time the viewer bounced off. What worked in the end was a worked example small enough to
check by eye -- four numbers, every cross-pair enumerated, the tally of differences built pair by
pair -- and only then the jump to "with 2n numbers, a split is a red-fraction curve and the tally
becomes this profile". This cut follows that order exactly:

  1. the problem: split {1,2,3,4} into two groups of two
  2. the obvious split A={1,2}: enumerate all four cross-pairs, tally their differences --
     difference -2 appears twice, so the score (the most crowded difference) is 2
  3. the smarter split A={1,4}: same enumeration, all four differences distinct, score 1. Same
     numbers, half the worst crowding -- that is the whole game, and Erdos asks how low the
     worst crowding can go as n grows
  4. scaling up: a split of 2n numbers is "how much of each position is A's" -- the tally
     becomes a curve of crowding per difference. The record construction (K=951, ours) has a
     nearly FLAT profile at Psi = 0.380909: no difference is crowded any more
  5. the scoreboard against the published constructions

All record-side numbers are the evaluator's own; see `prob_data`.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, UP, Create, CubicBezier, FadeIn, FadeOut, Line,
                   MovingCameraScene, Polygon, Rectangle, Square, Transform, VGroup, VMobject,
                   Write)

from manim_kit import BLUE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, RED, SUB, fit, para, txt
from prob_data import ERDOS

CAP_AT = np.array([0.0, -3.45, 0.0])
H = np.asarray(ERDOS["steps"], float)
PROF = np.asarray(ERDOS["profile"], float)

TILE = 0.92
TILE_Y = 2.5
TALLY_Y, TALLY_H, TALLY_W = -2.3, 1.5, 7.4    # the differences panel, reused by every act


def tiles(groups, y=TILE_Y) -> VGroup:
    """The numbers 1..4 as tiles; groups[i] is 'A' (red) or 'B' (blue)."""
    g = VGroup()
    n = len(groups)
    x0 = -(n * (TILE + 0.25) - 0.25) / 2
    for i, grp in enumerate(groups):
        col = RED if grp == "A" else BLUE
        box = Square(side_length=TILE, stroke_width=2.2, color=INK,
                     fill_color=col, fill_opacity=0.28)
        box.move_to([x0 + i * (TILE + 0.25) + TILE / 2, y, 0])
        num = txt(str(i + 1), 30, INK, weight="BOLD").move_to(box.get_center())
        tag = txt(grp, 15, col).move_to(box.get_corner(UP + LEFT) + np.array([0.16, -0.16, 0]))
        g.add(VGroup(box, num, tag))
    return g


def tally_axis() -> VGroup:
    base = Line([-TALLY_W / 2, TALLY_Y, 0], [TALLY_W / 2, TALLY_Y, 0],
                color=MUTED, stroke_width=2)
    g = VGroup(base)
    for k in range(-3, 4):
        x = k * TALLY_W / 7
        g.add(Line([x, TALLY_Y, 0], [x, TALLY_Y - 0.07, 0], color=MUTED, stroke_width=2),
              txt(f"{k:+d}" if k else "0", 14, MUTED).move_to([x, TALLY_Y - 0.28, 0]))
    g.add(txt("difference a − b", 14, MUTED).move_to([TALLY_W / 2 - 1.0, TALLY_Y - 0.62, 0]))
    return g


def tally_block(k: int, level: int, color) -> Rectangle:
    """One counted pair: a block stacked at difference k, `level` blocks already below it."""
    unit = TALLY_H / 2.4
    b = Rectangle(width=TALLY_W / 7 * 0.5, height=unit * 0.88, stroke_width=1.4,
                  color=INK, fill_color=color, fill_opacity=0.55)
    b.move_to([k * TALLY_W / 7, TALLY_Y + unit * (level + 0.5), 0])
    return b


class ErdosProblem(Kit, MovingCameraScene):

    def construct(self):
        title = txt("The Erdős Minimum-Overlap Problem", 46, INK, weight="BOLD")
        sub = para("split the numbers 1…2n into two equal groups, so that no difference\n"
                   "between the groups is crowded — the score is the WORST difference", 26, SUB)
        fine = txt("the construction at the end is our record run's: K = 951, Ψ = 0.380909",
                   16, MUTED)
        card = VGroup(title, sub, fine).arrange(DOWN, buff=0.42)
        fit(card, FULL_W - 2.2).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), FadeIn(fine), run_time=1.4)
        self.wait(2.8)
        self.play(FadeOut(card), run_time=0.8)
        self.caption = None

        # ---- act 1+2: the obvious split, every pair counted ---------------------
        tl = tiles(["A", "A", "B", "B"])
        self.say("four numbers, two groups of two: the obvious split puts 1 and 2 in A,\n"
                 "3 and 4 in B", color=SUB)
        self.play(FadeIn(tl), run_time=0.9)
        self.wait(0.8)

        ax = tally_axis()
        self.say("now take EVERY pair with one number from each group, and tally the\n"
                 "difference a − b", color=SUB)
        self.play(FadeIn(ax), run_time=0.7)

        pairs_1 = [(0, 2, -2), (0, 3, -3), (1, 2, -1), (1, 3, -2)]
        levels: dict = {}
        blocks = VGroup()
        for a, b, k in pairs_1:
            pa, pb = tl[a][0].get_bottom(), tl[b][0].get_bottom()
            arc = CubicBezier(pa, pa + np.array([0, -0.85, 0]), pb + np.array([0, -0.85, 0]), pb)
            arc.set_stroke(ORANGE, 2.4)
            lab = txt(f"{a + 1} − {b + 1} = {k:+d}", 17, ORANGE)
            lab.move_to([(pa[0] + pb[0]) / 2, TILE_Y - 1.5, 0])
            lvl = levels.get(k, 0)
            levels[k] = lvl + 1
            blk = tally_block(k, lvl, ORANGE)
            blocks.add(blk)
            self.play(Create(arc), FadeIn(lab), run_time=0.45)
            self.play(FadeIn(blk, shift=UP * 0.15), FadeOut(arc), FadeOut(lab), run_time=0.45)

        worst = txt("difference −2 appears TWICE — score: 2", 18, ORANGE, weight="BOLD")
        worst.move_to([0, TALLY_Y + TALLY_H + 0.4, 0])
        ring = Rectangle(width=TALLY_W / 7 * 0.66, height=TALLY_H / 2.4 * 1.85,
                         stroke_width=3, color=ORANGE)
        ring.move_to([-2 * TALLY_W / 7, TALLY_Y + TALLY_H / 2.4 * 1.0, 0])
        self.play(FadeIn(worst), Create(ring), run_time=0.7)
        self.say("the score is the height of the TALLEST stack — the most crowded\n"
                 "difference. The obvious split scores 2", hold=2.2)

        # ---- act 3: the smarter split ------------------------------------------
        tl2 = tiles(["A", "B", "B", "A"])
        self.say("same four numbers, smarter split: A gets 1 and 4, B gets 2 and 3", color=SUB)
        self.play(Transform(tl, tl2), FadeOut(blocks), FadeOut(worst), FadeOut(ring),
                  run_time=1.0)

        pairs_2 = [(0, 1, -1), (0, 2, -2), (3, 1, 2), (3, 2, 1)]
        levels = {}
        blocks2 = VGroup()
        for a, b, k in pairs_2:
            pa, pb = tl[a][0].get_bottom(), tl[b][0].get_bottom()
            arc = CubicBezier(pa, pa + np.array([0, -0.85, 0]), pb + np.array([0, -0.85, 0]), pb)
            arc.set_stroke(BLUE, 2.4)
            lab = txt(f"{a + 1} − {b + 1} = {k:+d}", 17, BLUE)
            lab.move_to([(pa[0] + pb[0]) / 2, TILE_Y - 1.5, 0])
            lvl = levels.get(k, 0)
            levels[k] = lvl + 1
            blk = tally_block(k, lvl, BLUE)
            blocks2.add(blk)
            self.play(Create(arc), FadeIn(lab), run_time=0.4)
            self.play(FadeIn(blk, shift=UP * 0.15), FadeOut(arc), FadeOut(lab), run_time=0.4)

        even = txt("four different differences — score: 1", 18, BLUE, weight="BOLD")
        even.move_to([0, TALLY_Y + TALLY_H + 0.4, 0])
        self.play(FadeIn(even), run_time=0.6)
        self.say("every difference occurs once: the worst crowding fell from 2 to 1.\n"
                 "THAT is the whole game — spread the differences out evenly", hold=2.6)
        self.say("Erdős asked: as n grows, how low can the worst crowding go, relative\n"
                 "to n? Nobody knows exactly — the bounds have narrowed for 70 years", hold=2.6)

        # ---- act 4: scaling up, one rung at a time ------------------------------
        # An earlier cut jumped from four tiles straight to the 951-position curve and lost the
        # viewer mid-morph. The ladder now adds ONE idea per rung: more numbers -> thin columns
        # -> fractional membership -> the record. One helper draws every rung.
        y0, y1 = 1.55, 3.05

        def columns(shares, sep=True) -> VGroup:
            g = VGroup()
            n = len(shares)
            w = TALLY_W / n
            for i, r in enumerate(shares):
                x = -TALLY_W / 2 + i * w
                if r > 0:
                    g.add(Rectangle(width=w, height=(y1 - y0) * r, stroke_width=0,
                                    fill_color=RED, fill_opacity=0.75)
                          .move_to([x + w / 2, y0 + (y1 - y0) * r / 2, 0]))
                if r < 1:
                    g.add(Rectangle(width=w, height=(y1 - y0) * (1 - r), stroke_width=0,
                                    fill_color=BLUE, fill_opacity=0.55)
                          .move_to([x + w / 2, y1 - (y1 - y0) * (1 - r) / 2, 0]))
                if sep:
                    g.add(Line([x, y0, 0], [x, y1, 0], color=INK, stroke_width=1.0))
            g.add(Rectangle(width=TALLY_W, height=y1 - y0, stroke_width=1.8, color=INK)
                  .move_to([0, (y0 + y1) / 2, 0]))
            return g

        # rung 1: twelve numbers, whole membership -- the same game, a wider tally
        twelve = [1, 1, 0, 0] * 3
        col12 = columns(twelve)
        lab12 = txt("more numbers, same game — each number still wholly red or wholly blue",
                    14, SUB).next_to([0, y1, 0], UP, buff=0.12)
        nums = VGroup(*[txt(str(i + 1), 13, MUTED)
                        .move_to([-TALLY_W / 2 + (i + 0.5) * TALLY_W / 12, y0 - 0.22, 0])
                        for i in range(12)])
        self.say("now twelve numbers instead of four. Nothing else changed: pick a group\n"
                 "for each number, tally every cross-group difference", color=SUB)
        self.play(FadeOut(tl), FadeOut(blocks2), FadeOut(even), FadeIn(col12), FadeIn(lab12),
                  FadeIn(nums), run_time=1.2)
        self.wait(1.8)

        # rung 2: hundreds of numbers -- a barcode, still all-or-nothing
        rng = np.random.default_rng(4)
        barcode = (rng.permutation(np.repeat([1.0, 0.0], 24))).tolist()
        col48 = columns(barcode, sep=False)
        lab48 = txt("hundreds of numbers: the strip becomes a barcode — red column, blue column",
                    14, SUB).move_to(lab12)
        self.say("keep going: with hundreds of numbers the tiles thin into a barcode.\n"
                 "Still the same game", color=SUB)
        self.play(FadeOut(col12), FadeOut(nums), FadeIn(col48),
                  FadeOut(lab12), FadeIn(lab48), run_time=1.0)
        self.wait(1.6)

        # rung 3: fractional membership -- the height IS the share
        coarse = [float(np.mean(H[int(i * len(H) / 24):int((i + 1) * len(H) / 24)]))
                  for i in range(24)]
        col24 = columns(coarse)
        lab24 = txt("A's share of each position, as a HEIGHT — 60% red means 60% in A",
                    14, SUB).move_to(lab12)
        self.say("one last freedom: Erdős lets a position SPLIT its weight — say 60% into A,\n"
                 "40% into B. The red HEIGHT of a column is its share in A", color=SUB)
        self.play(FadeOut(col48), FadeIn(col24), FadeOut(lab48), FadeIn(lab24), run_time=1.0)
        self.wait(2.0)

        # rung 4: refine to the record
        xs = np.linspace(-TALLY_W / 2, TALLY_W / 2, len(H))
        red_area = Polygon(*([[x, y0, 0] for x in xs]
                             + [[xs[i], y0 + (y1 - y0) * H[i], 0]
                                for i in range(len(H) - 1, -1, -1)]),
                           stroke_width=0, fill_color=RED, fill_opacity=0.75)
        blue_area = Polygon(*([[xs[i], y0 + (y1 - y0) * H[i], 0] for i in range(len(H))]
                              + [[x, y1, 0] for x in reversed(xs)]),
                            stroke_width=0, fill_color=BLUE, fill_opacity=0.55)
        frame = Rectangle(width=TALLY_W, height=y1 - y0, stroke_width=1.8, color=INK)
        frame.move_to([0, (y0 + y1) / 2, 0])
        strip = VGroup(red_area, blue_area, frame)
        striplab = txt("our record split — the same picture at 951 positions",
                       14, SUB).move_to(lab12)
        # The integer ticks go: the record's differences span the whole +-2n range, and leaving
        # -3..+3 under the continuous profile would caption it with a falsehood.
        self.say("those columns, 951 of them, ARE our record split. This is what evolution\n"
                 "shaped", color=SUB)
        self.play(FadeOut(col24), FadeIn(strip), FadeOut(lab24), FadeIn(striplab),
                  FadeOut(VGroup(*ax[1:-1])), run_time=1.2)
        self.wait(1.2)
        tl = strip

        self.say("and the tally becomes a CURVE — crowding per difference, for every\n"
                 "difference at once. This is the record split's tally", color=SUB)
        lx = np.linspace(-TALLY_W / 2, TALLY_W / 2, len(PROF))
        prof_line = VMobject(color=GREEN, stroke_width=2.6)
        prof_line.set_points_as_corners([[x, TALLY_Y + TALLY_H * v / 0.55, 0]
                                         for x, v in zip(lx, PROF)])
        self.play(Create(prof_line), run_time=1.6)
        lvl_lab = txt("Ψ = 0.380909 — nearly FLAT: no difference is crowded", 17, GREEN,
                      weight="BOLD").move_to([0, TALLY_Y + TALLY_H + 0.4, 0])
        self.play(FadeIn(lvl_lab), run_time=0.5)
        self.say("bursts of A at the edges, a plateau in the middle, mirror symmetry —\n"
                 "and no stack stands above the rest. Minimax, achieved", hold=2.6)

        # ---- act 5: scoreboard --------------------------------------------------
        board = para("flat split 0.5   ·   ours 0.380909   ·   AlphaEvolve 0.380924\n"
                     "Haugland 0.380927   ·   SimpleTES 0.380868   ·   theory floor ≈ 0.379",
                     17, SUB)
        fit(board, FULL_W - 2.4).move_to(CAP_AT)
        self.play(Transform(self.caption, board), run_time=0.6)
        self.wait(4.2)

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
