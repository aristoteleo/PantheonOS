"""AnnealedIdeaCode, as a narrated run.

    manim -qm --format=mp4 manim_annealed.py AnnealedRun

Three acts, one per thing that only a run shows:

  1. the loop -- every step draws an action from a mix, and the two branches cost different things:
     an idea is PREDICTED by the judge, a program is MEASURED by the verifier
  2. the schedule -- that mix sliding from proposing to implementing while the temperature falls,
     and the selection distribution collapsing from near-uniform onto one idea
  3. the judge learning -- its raw numbers all land in a narrow band, and the calibration is what
     maps them onto the scale the verifier actually reports

The run is real: `sim_annealed` drives the actual method, schedule, selection and calibration
through the actual loop. Only the model call inside the judge is stubbed.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, Axes, Circle, Create, Dot, FadeIn, FadeOut,
                   Indicate, LaggedStart, Line, MovingCameraScene, Rectangle, Transform, VGroup,
                   Write)

from manim_kit import (BLUE, EDGE, FULL_W, GREEN, INK, Kit, MUTED, ORANGE, PANEL, PURPLE, RED, SUB,
                       fit, para, txt)
from sim_annealed import EVENTS, IMPLS, RAW_HI, RAW_LO

ACTS = [("NEW", "propose an unrelated approach", PURPLE),
        ("REFINE", "vary one that exists", BLUE),
        ("IMPL", "implement one, and measure it", GREEN)]
BAR_W, BAR_Y = 9.0, 2.05


def mix_bar(mix, y=BAR_Y, w=BAR_W, h=0.46, lit=None) -> VGroup:
    """The action distribution as one divided bar -- three numbers that always sum to 1."""
    g = VGroup()
    x = -w / 2
    for i, (name, _, col) in enumerate(ACTS):
        seg_w = max(0.001, mix[i] * w)
        r = Rectangle(width=seg_w, height=h, stroke_width=2.0 if lit == i else 1.0,
                      color=INK if lit == i else EDGE, fill_color=col,
                      fill_opacity=0.9 if lit == i else 0.55)
        r.move_to([x + seg_w / 2, y, 0])
        g.add(r)
        if mix[i] > 0.07:
            g.add(txt(f"{name} {mix[i] * 100:.0f}%", 17, INK).move_to([x + seg_w / 2, y, 0]))
        x += seg_w
    return g


class AnnealedRun(Kit, MovingCameraScene):

    def construct(self):
        title = txt("AnnealedIdeaCode", 52, INK, weight="BOLD")
        sub = para("search the IDEAS, not just the programs — and spend the budget on proposing\n"
                   "early, on implementing late, with a judge that learns what an idea is worth",
                   27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(1.8)
        self.play(FadeOut(card), run_time=0.9)

        self.act_loop()
        self.act_schedule()
        self.act_judge()

    # ---- act 1 -----------------------------------------------------------
    def act_loop(self):
        head = txt("every step draws an action", 30, INK).move_to([0, 3.2, 0])
        first = EVENTS[0]
        bar = mix_bar(first["mix"])
        cap = txt("the three add up to one, and the split is what the schedule moves",
                  22, SUB).move_to([0, BAR_Y - 0.62, 0])
        self.play(FadeIn(head), FadeIn(bar), FadeIn(cap), run_time=1.1)
        self.wait(1.6)

        rows = VGroup()
        for name, what, col in ACTS:
            rows.add(VGroup(txt(name, 25, col, weight="BOLD"), txt(what, 22, SUB))
                     .arrange(RIGHT, buff=0.4))
        rows.arrange(DOWN, buff=0.42, aligned_edge=LEFT).move_to([0, 0.15, 0])
        self.play(LaggedStart(*[FadeIn(r, shift=RIGHT * 0.15) for r in rows], lag_ratio=0.3),
                  run_time=1.4)
        self.wait(1.6)

        cost = para("The two branches do not cost the same thing. An idea is PREDICTED by the "
                    "judge —\ncheap, and possibly wrong. A program is MEASURED by the verifier — "
                    "expensive, and true.", 22, INK)
        fit(cost, FULL_W - 2.0).move_to([0, -2.3, 0])
        self.play(FadeIn(cost), run_time=0.8)
        self.wait(3.0)
        self.play(FadeOut(head), FadeOut(rows), FadeOut(cost), FadeOut(cap), FadeOut(bar),
                  run_time=0.8)

    # ---- act 2 -----------------------------------------------------------
    def act_schedule(self):
        head = txt("the schedule: broad early, narrow late", 30, INK).move_to([0, 3.35, 0])
        bar = mix_bar(EVENTS[0]["mix"])
        tlab = txt("t = 0.00", 22, ORANGE).move_to([0, BAR_Y + 0.62, 0])
        self.play(FadeIn(head), FadeIn(bar), FadeIn(tlab), run_time=1.0)

        ax = Axes(x_range=[0, 1.02, 0.25], y_range=[0, 1.05, 0.5], x_length=8.6, y_length=2.5,
                  tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([0, -0.55, 0])
        ticks = VGroup(txt("t = 0", 17, MUTED).next_to(ax.c2p(0, 0), DOWN, buff=0.2),
                       txt("t = 1", 17, MUTED).next_to(ax.c2p(1.0, 0), DOWN, buff=0.2))
        ts = [e["t"] for e in EVENTS]
        curves = VGroup(
            ax.plot_line_graph(ts, [e["mix"][0] for e in EVENTS], line_color=PURPLE,
                               add_vertex_dots=False, stroke_width=4),
            ax.plot_line_graph(ts, [e["mix"][2] for e in EVENTS], line_color=GREEN,
                               add_vertex_dots=False, stroke_width=4),
            ax.plot_line_graph(ts, [e["temperature"] for e in EVENTS], line_color=ORANGE,
                               add_vertex_dots=False, stroke_width=3.4))
        key = VGroup(txt("propose", 19, PURPLE), txt("implement", 19, GREEN),
                     txt("temperature", 19, ORANGE)).arrange(RIGHT, buff=0.55)
        key.next_to(ax, UP, buff=0.12)
        self.play(Create(ax), FadeIn(ticks), FadeIn(key), run_time=0.9)
        self.play(Create(curves), run_time=2.2)

        # the bar walking the same schedule
        marker = Dot(ax.c2p(0, 0), radius=0.06, color=ORANGE)
        self.play(FadeIn(marker), run_time=0.3)
        for e in EVENTS[::3]:
            self.play(Transform(bar, mix_bar(e["mix"])),
                      Transform(tlab, txt(f"t = {e['t']:.2f}", 22, ORANGE)
                                .move_to([0, BAR_Y + 0.62, 0])),
                      marker.animate.move_to(ax.c2p(e["t"], e["temperature"])),
                      run_time=0.32)
        self.wait(1.0)

        tag = para("Proposing decays; implementing takes what it gives up. The temperature falls "
                   "with it,\nso the choice of WHICH idea to implement goes from near-uniform to "
                   "nearly always the leader.", 21, INK)
        fit(tag, FULL_W - 2.0).move_to([0, -2.95, 0])
        self.play(FadeIn(tag), run_time=0.8)
        self.wait(3.0)
        self.play(FadeOut(head), FadeOut(bar), FadeOut(tlab), FadeOut(ax), FadeOut(ticks),
                  FadeOut(key), FadeOut(curves), FadeOut(marker), FadeOut(tag), run_time=0.9)

    # ---- act 3 -----------------------------------------------------------
    def act_judge(self):
        head = txt("the judge, and what it has to learn", 30, INK).move_to([0, 3.35, 0])
        self.play(FadeIn(head), run_time=0.6)

        pairs = [(e["raw"], e["score"] - (e.get("idea_base") or 0.0))
                 for e in IMPLS if e.get("raw") is not None and e.get("score") is not None]
        # The BULK, not the extremes. `raw = prediction - base`, and the base climbs as the run
        # improves, so a couple of late ideas predict almost no gain and sit far from the rest.
        # A band drawn to the extremes would be labelled "0.46 wide" over a picture of a cluster --
        # the caption contradicting its own chart.
        rs = sorted(r for r, _ in pairs)
        k = max(1, len(rs) // 10)
        lo_r, hi_r = rs[k], rs[-k - 1]
        gs = sorted(g for _, g in pairs)
        lo_g, hi_g = gs[0], gs[-1]

        # BOTH axes on the same scale, and neither zoomed to its data. Zooming x to the band the
        # predictions occupy would make it look wide, which is the opposite of the point: the
        # judge's numbers span %.2f while the gains they are predicting span %.2f.
        ax = Axes(x_range=[0, 0.55, 0.25], y_range=[0, 0.55, 0.25], x_length=5.0, y_length=3.4,
                  tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([0.2, -0.2, 0])
        xlab = txt("what the judge said", 19, MUTED).next_to(ax, DOWN, buff=0.28)
        ylab = txt("what it actually gained", 19, MUTED).rotate(np.pi / 2).next_to(ax, LEFT,
                                                                                  buff=0.22)
        corner = VGroup(txt("0", 16, MUTED).next_to(ax.c2p(0, 0), DOWN + LEFT, buff=0.12),
                        txt("0.5", 16, MUTED).next_to(ax.c2p(0.5, 0), DOWN, buff=0.16),
                        txt("0.5", 16, MUTED).next_to(ax.c2p(0, 0.5), LEFT, buff=0.16))
        self.play(Create(ax), FadeIn(xlab), FadeIn(ylab), FadeIn(corner), run_time=1.0)

        band = Rectangle(width=abs(ax.c2p(hi_r, 0)[0] - ax.c2p(lo_r, 0)[0]),
                         height=3.4, stroke_width=0, fill_color=ORANGE, fill_opacity=0.14)
        band.move_to([(ax.c2p(lo_r, 0)[0] + ax.c2p(hi_r, 0)[0]) / 2, ax.get_center()[1], 0])
        bandlab = txt(f"8 in 10 predictions: {hi_r - lo_r:.2f} wide", 18, ORANGE)
        bandlab.next_to(band, UP, buff=0.1)
        spread = txt(f"their gains: {hi_g - lo_g:.2f} wide", 18, BLUE)
        spread.rotate(np.pi / 2).next_to(ax.c2p(0, (lo_g + hi_g) / 2), RIGHT, buff=0.05)
        dots = VGroup(*[Dot(ax.c2p(r, g), radius=0.055, color=BLUE).set_opacity(0.75)
                        for r, g in pairs])
        self.play(FadeIn(band), FadeIn(bandlab), run_time=0.7)
        self.play(LaggedStart(*[FadeIn(d, scale=0.4) for d in dots], lag_ratio=0.09), run_time=1.8)
        self.play(FadeIn(spread), run_time=0.5)

        why = para("Ranked about right, scaled completely wrong: the predictions bunch into a "
                   "band\na fraction of the width of the gains they are predicting. Only the ORDER "
                   "is trustworthy.", 21, INK)
        fit(why, FULL_W - 2.0).move_to([0, -3.15, 0])
        self.play(FadeIn(why), run_time=0.8)
        self.wait(3.2)

        # the isotonic fit: monotone, and free to move each step as far as it needs to
        # A running maximum: monotone by construction, and free to step as far as it needs to at
        # any point. That is what lets it stretch a 0.05-wide input onto a 0.33-wide output, which
        # no rescaling of a straight line can do.
        pts = sorted(pairs)
        fit_line = VGroup()
        run_y = None
        for i, (r, g) in enumerate(pts):
            run_y = g if run_y is None else max(run_y, g)
            x0 = ax.c2p(r, 0)[0]
            x1 = ax.c2p(pts[i + 1][0], 0)[0] if i + 1 < len(pts) else ax.c2p(0.55, 0)[0]
            y = ax.c2p(0, min(0.55, run_y))[1]
            fit_line.add(Line([x0, y, 0], [x1, y, 0], color=RED, stroke_width=4))
        done = para("So the judge is not asked for a number. It is asked for an ORDER, and a "
                    "monotone fit\nturns that order into the verifier's units — with the leftover "
                    "spread as its uncertainty.", 21, INK)
        fit(done, FULL_W - 2.0).move_to([0, -3.15, 0])
        self.play(FadeOut(why), FadeOut(spread), Create(fit_line), run_time=1.6)
        self.play(FadeIn(done), run_time=0.7)
        self.wait(3.4)
