"""SimpleTES as a narrated run.

    manim -qm --format=mp4 manim_simpletes.py SimpleTESRun

Three acts, because twenty-one identical steps explain nothing. The first walks two steps slowly
with the camera close enough to read them; the second runs the rest of that chain at speed so the
DAG accumulates; the third pulls back to show that this was one of three independent chains and
what the whole run bought.

The scores are invented. Everything about who gets picked, how many, and what commits comes from
`sim_simpletes`, which calls the real `Selector`.
"""
from __future__ import annotations

import numpy as np
from manim import (BLACK, DOWN, LEFT, RIGHT, UP, Arrow, Axes, Circle, Create, CurvedArrow, Dot,
                   FadeIn, FadeOut, Flash, Indicate, LaggedStart, ManimColor, MovingCameraScene,
                   RoundedRectangle, Text, VGroup, Write, config, interpolate_color)

from sim_simpletes import EVENTS, K, drew_phrase

config.background_color = ManimColor("#ffffff")
FONT = "Helvetica Neue"
"""Pango's default here is a serif, which reads as a paper figure rather than a diagram. Set it
once; `Text` silently falls back if the family is missing, so this costs nothing on another box."""

INK = ManimColor("#1f2328")
SUB = ManimColor("#57606a")
MUTED = ManimColor("#8c959f")
EDGE = ManimColor("#d0d7de")
PANEL = ManimColor("#f6f8fa")
ORANGE = ManimColor("#bc4c00")
GREEN = ManimColor("#1a7f37")
BLUE = ManimColor("#0969da")
LOW, HIGH = ManimColor("#deebf7"), ManimColor("#216eb4")

X0, XSTEP = -5.35, 1.62
"""Eight nodes have to fit the frame with room to the left for the chain labels, because act three
shows all three chains at once and a label that lands at x < -7.11 is simply not rendered."""
BAND_Y = [2.05, 0.0, -2.05]
NODE_R = 0.23
SQ, FAN_DY, FAN_DX, GATE_DX = 0.36, 0.52, 0.98, 0.52
FULL_W = config.frame_width


def score_color(s: float) -> ManimColor:
    return interpolate_color(LOW, HIGH, max(0.0, min(1.0, (s - 0.35) / 0.5)))


def ink_on(s: float) -> ManimColor:
    return ManimColor("#ffffff") if s > 0.55 else INK


def trunk_pos(order: int, band: float) -> np.ndarray:
    return np.array([X0 + order * XSTEP, band, 0.0])


def gate_pos(order: int, band: float) -> np.ndarray:
    """Where the selected parents converge: one prompt, just left of its candidates."""
    return np.array([X0 + (order - 1) * XSTEP + GATE_DX, band, 0.0])


def cand_pos(order: int, j: int, band: float) -> np.ndarray:
    return np.array([X0 + (order - 1) * XSTEP + FAN_DX, band + (1 - j) * FAN_DY, 0.0])


def _fitted(score: float, width: float, color) -> Text:
    """A score label sized to its container. `font_size` is absolute, so a label that fits the
    trunk circles here spills out of them at another radius."""
    return Text(f"{score:.2f}", font=FONT, font_size=24, color=color).scale_to_fit_width(width)


def node_mob(score: float, at: np.ndarray, ring=EDGE, width=2.0) -> VGroup:
    circ = Circle(radius=NODE_R, color=ring, stroke_width=width,
                  fill_color=score_color(score), fill_opacity=1.0).move_to(at)
    return VGroup(circ, _fitted(score, NODE_R * 1.15, ink_on(score)).move_to(at))


def cand_mob(score: float, at: np.ndarray, won: bool, lit: bool) -> VGroup:
    # The losers need a visible outline. At EDGE on white they vanish, and a candidate you cannot
    # see is not a peer of the one that committed -- it is back to being an empty slot.
    box = RoundedRectangle(
        width=SQ, height=SQ * 0.78, corner_radius=0.06,
        stroke_width=(3.0 if lit else 2.0) if won else 1.8,
        color=GREEN if won else MUTED,
        fill_color=score_color(score) if won else PANEL, fill_opacity=1.0).move_to(at)
    label = _fitted(score, SQ * 0.62, ink_on(score) if won else SUB).move_to(at)
    return VGroup(box, label)


def arc(a: np.ndarray, b: np.ndarray, color, width=2.0, angle=-0.55) -> CurvedArrow:
    return CurvedArrow(a, b, angle=angle, color=color, stroke_width=width,
                       tip_length=0.11)


def framing(chain: int, first: int, last: int, pad: float = 1.5):
    """Centre the camera on the span actually being drawn.

    Aiming it at the newest node instead leaves the step half off one edge and empty canvas on the
    other, because a step reaches back from the seed to the fan, not forward from the node.
    """
    lo = X0 + first * XSTEP - NODE_R
    hi = X0 + last * XSTEP + NODE_R
    return np.array([(lo + hi) / 2, BAND_Y[chain], 0.0]), (hi - lo) + pad


def dim(pairs, o: float = 0.3):
    """Fade finished work into the background.

    `set_opacity` sets fill as well as stroke, and an arc is an OPEN curve -- giving it fill paints
    the lens between it and its chord. Four parent arcs dimmed that way turn the step into an
    orange smear. Arcs get stroke only; anything with a real interior gets both.
    """
    out = []
    for mob, kind in pairs:
        out.append(mob.animate.set_stroke(opacity=o) if kind == "stroke"
                   else mob.animate.set_opacity(o))
    return out


class SimpleTESRun(MovingCameraScene):

    # ---- text that stays the same apparent size however far the camera is ----
    def cap(self, text: str, size: float = 26, color=SUB, weight="NORMAL") -> Text:
        t = Text(text, font=FONT, font_size=size, color=color, weight=weight)
        return t.scale(self.camera.frame.width / FULL_W)

    def construct(self):
        self.dim_later: list = []
        self.chain_of = {c: [ev for ev in EVENTS if ev["chain"] == c] for c in range(3)}

        title = Text("SimpleTES", font=FONT, font_size=44, color=INK, weight="BOLD").to_edge(UP, buff=0.45)
        sub = Text("every selected node is a parent — one prompt fans out into k candidates, "
                   "and one of them continues the chain",
                   font=FONT, font_size=21, color=SUB).next_to(title, DOWN, buff=0.22)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(0.8)

        seeds = {}
        for c in range(3):
            seeds[c] = node_mob(EVENTS[0]["chains"][c][0].score, trunk_pos(0, BAND_Y[c]))
        at, w = framing(0, 0, 1, pad=1.9)
        self.play(FadeOut(title), FadeOut(sub), FadeIn(seeds[0]),
                  self.camera.frame.animate.move_to(at).set(width=w), run_time=1.6)

        self.built = {0: {0: seeds[0]}, 1: {0: seeds[1]}, 2: {0: seeds[2]}}
        self.act_one()
        self.act_two()
        self.act_three(seeds)

    # ---------------------------------------------------------------- act 1 --
    def act_one(self):
        """Two steps, slowly, with every part named as it appears."""
        band = BAND_Y[0]
        evs = self.chain_of[0]

        lab = self.cap("chain 1  ·  one of three", 24, MUTED)
        lab.next_to(self.built[0][0], UP, buff=0.3)
        self.play(FadeIn(lab), run_time=0.6)

        for idx, ev in enumerate(evs[:2]):
            order = idx + 1
            if idx == 1:
                at, w = framing(0, 0, order, pad=1.9)
                self.play(FadeOut(lab),
                          self.camera.frame.animate.move_to(at).set(width=w), run_time=1.2)
            self.one_step(ev, 0, order, narrate=idx == 0, slow=True)

    def one_step(self, ev, chain: int, order: int, *, narrate=False, slow=False):
        band = BAND_Y[chain]
        gate, rt = gate_pos(order, band), 0.9 if slow else 0.28

        # 1. the selector picks. Every selected node is a parent -- there is no distinguished one.
        picked = [self.built[chain][o][0] for o, _ in ev["picked"]]
        self.play(*[p.animate.set_stroke(ORANGE, width=4.0) for p in picked],
                  *dim(self.dim_later), run_time=rt)
        self.dim_later = []
        if narrate:
            n = len(ev["picked"])
            c1 = self.cap(f"the selector draws {n} nodes from this chain's ranking —\n"
                          f"{drew_phrase(ev['labels'])}", 25, INK)
            c1.next_to(self.camera.frame.get_top(), DOWN, buff=0.35)
            self.play(FadeIn(c1), run_time=0.7)
            self.wait(1.5)

        # 2. they all feed ONE prompt
        arcs = VGroup(*[arc(self.built[chain][o][0].get_center(), gate + LEFT * 0.12, ORANGE,
                            2.0 if slow else 1.6,
                            angle=-0.55 if o < order - 1 else -0.001)
                        for o, _ in ev["picked"]])
        dot = Dot(gate, radius=0.06, color=ORANGE)
        self.play(LaggedStart(*[Create(a) for a in arcs], lag_ratio=0.18 if slow else 0.05),
                  run_time=rt * 1.3)
        self.play(FadeIn(dot, scale=0.4), run_time=rt * 0.5)
        if narrate:
            c2 = self.cap("all of them are parents — the prompt asks for a NEW program,\n"
                          "not an edit of any one of them", 25, INK)
            c2.next_to(self.camera.frame.get_bottom(), UP, buff=0.35)
            self.play(FadeOut(c1), FadeIn(c2), run_time=0.7)
            self.wait(1.6)

        # 3. one call, k candidates, drawn as peers
        fan = VGroup(*[cand_mob(s, cand_pos(order, j, band), j == ev["win"], True)
                       for j, s in enumerate(ev["cands"])])
        spokes = VGroup(*[Arrow(gate, cand_pos(order, j, band) + LEFT * SQ / 2,
                                buff=0.04, stroke_width=1.8, tip_length=0.09, color=BLUE)
                          for j in range(K)])
        self.play(LaggedStart(*[Create(s) for s in spokes], lag_ratio=0.12),
                  LaggedStart(*[FadeIn(f, scale=0.5) for f in fan], lag_ratio=0.12),
                  run_time=rt * 1.6)
        if narrate:
            c3 = self.cap(f"ONE call produces all {K} of them — that is the cost profile.\n"
                          "They are peers; none is the parent's revision.", 25, INK)
            c3.next_to(self.camera.frame.get_bottom(), UP, buff=0.35)
            self.play(FadeOut(c2), FadeIn(c3), run_time=0.7)
            self.wait(1.8)

        # 4. best of k joins the chain -- and it commits even when it is worse
        win = ev["win"]
        self.play(Flash(fan[win].get_center(), color=GREEN, line_length=0.12,
                        flash_radius=0.28, num_lines=10), run_time=rt * 0.8)
        node = node_mob(ev["winner"], trunk_pos(order, band), GREEN, 3.2)
        link = arc(cand_pos(order, win, band) + RIGHT * SQ / 2, trunk_pos(order, band),
                   GREEN, 2.4, angle=0.35 * (win - 1) or -0.001)
        self.play(Create(link), FadeIn(node, scale=0.6), run_time=rt * 1.2)
        if narrate:
            c4 = self.cap(
                f"best of k = {ev['winner']:.2f} continues the chain."
                + ("" if ev["improved"] else
                   f"\nIt is below its best parent ({ev['anchor']:.2f}) and it still commits — "
                   "a chain\nrecords what was tried, it is not a ratchet."), 25, INK)
            c4.next_to(self.camera.frame.get_bottom(), UP, buff=0.35)
            self.play(FadeOut(c3), FadeIn(c4), run_time=0.7)
            self.wait(2.2)
            self.play(FadeOut(c4), run_time=0.5)

        self.built[chain][order] = node
        self.play(*[p.animate.set_stroke(EDGE, width=2.0) for p in picked],
                  node[0].animate.set_stroke(EDGE, width=2.0), run_time=rt * 0.5)
        self.dim_later = [(arcs, "stroke"), (link, "stroke"), (dot, "all"), (spokes, "all"),
                          (fan, "all")]

    # ---------------------------------------------------------------- act 2 --
    def act_two(self):
        """The rest of chain 1 at speed, so the DAG accumulates and the arcs reach further back."""
        evs = self.chain_of[0]
        at, w = framing(0, 0, 5, pad=1.4)
        self.play(self.camera.frame.animate.move_to(at).set(width=w), run_time=1.4)

        note = self.cap("the arcs reach back further as the chain gets longer —\n"
                        "most parents come from the top of the ranking, some from the tail",
                        24, SUB)
        note.next_to(self.camera.frame.get_bottom(), UP, buff=0.3)
        self.play(FadeIn(note), run_time=0.6)

        for idx, ev in enumerate(evs[2:], start=3):
            if idx == 6:
                at, w = framing(0, 0, len(evs), pad=1.4)
                self.play(self.camera.frame.animate.move_to(at).set(width=w),
                          note.animate.scale(w / self.camera.frame.width).move_to(
                              at + DOWN * 1.55), run_time=0.9)
            self.one_step(ev, 0, idx, slow=False)
        self.play(FadeOut(note), run_time=0.5)

    # ---------------------------------------------------------------- act 3 --
    def act_three(self, seeds):
        """Pull back: this was one of three, and here is what the whole run bought."""
        self.play(*dim(self.dim_later),
                  self.camera.frame.animate.move_to([0, 0.25, 0]).set(width=FULL_W),
                  run_time=1.8)
        self.dim_later = []

        labels = VGroup()
        for c in range(3):
            t = Text(f"chain {c + 1}", font=FONT, font_size=17, color=SUB)
            t.move_to([X0 - 1.1, BAND_Y[c] + 0.16, 0])
            b = Text(f"{len(self.chain_of[c])} prompts", font=FONT, font_size=13, color=MUTED)
            b.move_to([X0 - 1.1, BAND_Y[c] - 0.18, 0])
            labels.add(VGroup(t, b))
        self.play(FadeIn(labels[0]), run_time=0.5)

        # the other two chains, all at once
        for c in (1, 2):
            self.play(FadeIn(seeds[c]), FadeIn(labels[c]), run_time=0.4)
            batch = [self.quiet_step(ev, c, idx)
                     for idx, ev in enumerate(self.chain_of[c], start=1)]
            self.play(LaggedStart(*[FadeIn(g) for g in batch], lag_ratio=0.22), run_time=2.6)

        note = Text("Chains do not compete. Each gets an equal share of the budget and they are "
                    "served in turn;\nthe selection happens inside one chain, over its own nodes.",
                    font=FONT, font_size=21, color=INK, line_spacing=0.85)
        note.move_to([0, -3.35, 0])
        self.play(FadeIn(note), run_time=0.8)
        self.wait(2.4)

        # Clear the stage before the summary. Three full chains and a plot in one frame is how the
        # first version of this looked, and the plot landed on top of chain 3.
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.0)
        self.fitness()
        self.wait(2.5)

    def quiet_step(self, ev, chain: int, order: int) -> VGroup:
        """A whole step as one static group -- for the chains the camera is not following."""
        band = BAND_Y[chain]
        gate = gate_pos(order, band)
        g = VGroup()
        for o, _ in ev["picked"]:
            g.add(arc(trunk_pos(o, band), gate + LEFT * 0.12, MUTED, 1.2,
                      angle=-0.55 if o < order - 1 else -0.001).set_stroke(opacity=0.4))
        for j, s in enumerate(ev["cands"]):
            at = cand_pos(order, j, band)
            g.add(Arrow(gate, at + LEFT * SQ / 2, buff=0.04, stroke_width=1.2, tip_length=0.07,
                        color=MUTED).set_opacity(0.4))
            g.add(cand_mob(s, at, j == ev["win"], False).set_opacity(0.75))
        g.add(arc(cand_pos(order, ev["win"], band) + RIGHT * SQ / 2, trunk_pos(order, band),
                  MUTED, 1.4, angle=0.35 * (ev["win"] - 1) or -0.001).set_stroke(opacity=0.5))
        node = node_mob(ev["winner"], trunk_pos(order, band))
        g.add(node)
        self.built[chain][order] = node
        return g

    def fitness(self):
        # `include_numbers` renders each tick through MathTex, so the numbers come out in LaTeX's
        # serif while every other label on screen is Helvetica -- and the y ticks collapse on top
        # of each other. Plain Text, placed by hand.
        ax = Axes(x_range=[0, 22, 5], y_range=[0.3, 0.9, 0.2], x_length=8.2, y_length=3.6,
                  tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([0, -0.35, 0])
        ticks = VGroup()
        for v in (0.3, 0.5, 0.7, 0.9):
            ticks.add(Text(f"{v:.1f}", font=FONT, font_size=17, color=MUTED)
                      .next_to(ax.c2p(0, v), LEFT, buff=0.16))
        for v in (5, 10, 15, 20):
            ticks.add(Text(str(v), font=FONT, font_size=17, color=MUTED)
                      .next_to(ax.c2p(v, 0.3), DOWN, buff=0.18))
        ticks.add(Text("step", font=FONT, font_size=17, color=MUTED)
                  .next_to(ax.c2p(22, 0.3), DOWN, buff=0.18))
        head = Text("every candidate that was measured, and the best so far",
                    font=FONT, font_size=26, color=INK).next_to(ax, UP, buff=0.55)
        dots = VGroup(*[Dot(ax.c2p(ev["step"] + 1, s), radius=0.045, color=BLUE).set_opacity(0.55)
                        for ev in EVENTS for s in ev["cands"]])
        line = ax.plot_line_graph([ev["step"] + 1 for ev in EVENTS],
                                  [ev["best"] for ev in EVENTS],
                                  line_color=ORANGE, add_vertex_dots=False, stroke_width=4.5)
        self.play(Create(ax), FadeIn(head), FadeIn(ticks), run_time=1.2)
        self.play(LaggedStart(*[FadeIn(d, scale=0.3) for d in dots], lag_ratio=0.006),
                  run_time=2.2)
        self.play(Create(line), run_time=1.8)

        best = max(ev["best"] for ev in EVENTS)
        tag = Text("The line only goes up, and on its own it suggests steady progress. The cloud "
                   "underneath is\nhow much of the budget went into candidates worse than what "
                   f"already existed — the run ended at {best:.2f}.",
                   font=FONT, font_size=19, color=SUB, line_spacing=0.85)
        tag.move_to([0, -3.25, 0])
        self.play(FadeIn(tag), Indicate(line, color=ORANGE, scale_factor=1.02), run_time=1.4)
