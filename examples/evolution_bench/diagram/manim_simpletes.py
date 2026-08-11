"""SimpleTES as a narrated run.

    manim -qm --format=mp4 manim_simpletes.py SimpleTESRun

Four acts, because twenty-one identical steps explain nothing.

  1. what one step IS, with the camera close enough to read it
  2. how the parents are CHOSEN -- the chain laid out as the ranking the selector actually sees,
     with the three bands it draws from, and one real draw replayed against them
  3. the rest of that chain at speed, so the DAG accumulates and the arcs reach further back
  4. a pull-back: this was one of three independent chains, and here is what the run bought

Act 2 exists because every other act shows the draw only as an outcome -- four nodes light up. The
rule behind it is the one thing a viewer cannot infer from watching, and it is the lever the whole
family of algorithms turns.

The scores are invented. Everything about who gets picked, how many, and what commits comes from
`sim_simpletes`, which calls the real `Selector`.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, ApplyFunction, Arrow, Axes, Circle, Create,
                   CurvedArrow, Dot, FadeIn, FadeOut, Indicate, LaggedStart, Line, ManimColor,
                   MovingCameraScene, RoundedRectangle, Text, VGroup, Write, config,
                   interpolate_color)

from sim_simpletes import EVENTS, K, drew_phrase, tiers

config.background_color = ManimColor("#ffffff")
FONT = "PT Sans"
"""Pango's default here is a serif, which reads as a paper figure rather than a diagram.

PT Sans over Helvetica Neue and Avenir Next because manimpango sets both of those with noticeably
loose tracking at these sizes -- body lines come out airy and gappy, and the score labels inside
the nodes lose their fit. `Text` falls back silently if the family is missing.
"""

INK = ManimColor("#1f2328")
SUB = ManimColor("#57606a")
MUTED = ManimColor("#8c959f")
EDGE = ManimColor("#d0d7de")
PANEL = ManimColor("#f6f8fa")
ORANGE = ManimColor("#bc4c00")
GREEN = ManimColor("#1a7f37")
BLUE = ManimColor("#0969da")
LOW, HIGH = ManimColor("#deebf7"), ManimColor("#216eb4")

X0, XSTEP = -5.3, 1.66
"""Eight nodes have to fit the frame with room to the left for the chain labels, because act three
shows all three chains at once and a label that lands at x < -7.11 is simply not rendered."""
BAND_Y = [2.05, 0.0, -2.05]
NODE_R = 0.21
SQ, FAN_DY, FAN_DX, GATE_DX = 0.34, 0.50, 1.02, 0.58
"""Spacing is set by what has to fit BETWEEN two trunk nodes: the prompt dot, the fan, and an
arrowhead at each end that is not sitting on top of a node. The first pass put the prompt 0.29
from the previous node's edge with a 0.2-long arrowhead pointing at it, so every arrival overlapped
the node it was arriving next to."""
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


def _shrink(a, b, buff_a: float, buff_b: float):
    """Pull the endpoints in along the chord.

    `CurvedArrow` has no `buff`, so it runs centre to centre: the tail starts inside the source
    node and the head lands on top of the target. Trimming by each end's radius is what keeps an
    arrow next to a node instead of through it.
    """
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    d = b - a
    n = float(np.linalg.norm(d))
    if n < 1e-6:
        return a, b
    u = d / n
    return a + u * buff_a, b - u * buff_b


def arc(a, b, color, width=2.0, angle=-0.55, buff_a=0.0, buff_b=0.0) -> CurvedArrow:
    a, b = _shrink(a, b, buff_a, buff_b)
    return CurvedArrow(a, b, angle=angle, color=color, stroke_width=width, tip_length=0.085)


def spoke(a, b, color, width=1.8) -> Arrow:
    """Prompt to one candidate. Trimmed asymmetrically: clear of the prompt dot, clear of the box."""
    a, b = _shrink(a, b, 0.09, 0.235)
    return Arrow(a, b, buff=0.0, stroke_width=width, tip_length=0.075, color=color)


def fit(mob, limit: float):
    """Text set at a fixed font_size will happily run off both edges of the frame."""
    if mob.width > limit:
        mob.scale(limit / mob.width)
    return mob


def framing(chain: int, first: int, last: int, pad: float = 1.5, min_h: float = 0.0):
    """Centre the camera on the span actually being drawn.

    Aiming it at the newest node instead leaves the step half off one edge and empty canvas on the
    other, because a step reaches back from the seed to the fan, not forward from the node.

    `min_h` buys vertical room. The frame's height follows from its width, so a close-up on one
    step is not only narrow but SHORT, and a caption placed against the bottom edge lands on the
    lowest candidate of the fan. Widening is the only way to make room underneath.
    """
    lo = X0 + first * XSTEP - NODE_R
    hi = X0 + last * XSTEP + NODE_R
    w = max((hi - lo) + pad, min_h * FULL_W / config.frame_height)
    return np.array([(lo + hi) / 2, BAND_Y[chain], 0.0]), w


def _fade_stroke_and_tips(o: float):
    """Dim an arrow without filling it in.

    Two traps in one place. `set_opacity` sets fill as well as stroke, and an arc is an OPEN curve,
    so giving it fill paints the lens between it and its chord -- four parent arcs dimmed that way
    turn the step into an orange smear. But stroke alone leaves the ARROWHEADS at full strength,
    because a tip is a filled polygon, so a faded run still has solid orange heads scattered along
    it. Dim the stroke everywhere, and the fill only where there already was some.
    """
    def f(m):
        m.set_stroke(opacity=o)
        for sub in m.family_members_with_points():
            if sub.get_fill_opacity() > 0:
                sub.set_fill(opacity=o)
        return m
    return f


def dim(pairs, o: float = 0.3):
    """Fade finished work into the background."""
    return [ApplyFunction(_fade_stroke_and_tips(o), mob) if kind == "stroke"
            else mob.animate.set_opacity(o)
            for mob, kind in pairs]


def para(text: str, size: float, color, weight="NORMAL") -> VGroup:
    """Multi-line text whose lines are actually centred on each other.

    A `Text` with newlines left-aligns its lines inside one mobject, so a two-line caption centred
    on the frame still hangs a ragged edge off a centred first line. `Paragraph` centres them but
    inserts a gap after the first character of a line -- "They are peers" comes out "T hey are
    peers". One Text per line, arranged, avoids both.
    """
    lines = VGroup(*[Text(ln, font=FONT, font_size=size, color=color, weight=weight)
                     for ln in text.split("\n")])
    return lines.arrange(DOWN, buff=size * 0.0042)


class SimpleTESRun(MovingCameraScene):

    # ---- text that stays the same apparent size however far the camera is ----
    def cap(self, text: str, size: float = 26, color=SUB, weight="NORMAL"):
        t = para(text, size, color, weight=weight) if "\n" in text else \
            Text(text, font=FONT, font_size=size, color=color, weight=weight)
        t.scale(self.zoom)
        return fit(t, self.camera.frame.width - 0.8)

    @property
    def zoom(self) -> float:
        """How much the camera magnifies. Stroke widths and text both have to be divided by it,
        or a close-up renders 3x-thick rings around 3x-large labels."""
        return self.camera.frame.width / FULL_W

    def sw(self, base: float) -> float:
        return base * self.zoom

    def construct(self):
        self.dim_later: list = []
        self.chain_of = {c: [ev for ev in EVENTS if ev["chain"] == c] for c in range(3)}

        title = Text("SimpleTES", font=FONT, font_size=54, color=INK, weight="BOLD")
        sub = para("every selected node is a parent — one prompt fans out into k candidates,\n"
                   "and one of them continues the chain", 27, SUB)
        card = VGroup(title, sub).arrange(DOWN, buff=0.45)
        fit(card, FULL_W - 2.4).move_to(ORIGIN)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.15), run_time=1.4)
        self.wait(1.4)

        seeds = {}
        for c in range(3):
            seeds[c] = node_mob(EVENTS[0]["chains"][c][0].score, trunk_pos(0, BAND_Y[c]))

        # Clear the card, THEN cut to where the run starts. Animating the fade and a zoom together
        # dives into a close-up of nothing -- the title recedes into a corner of a detail shot that
        # has not been drawn yet. With the frame empty the camera move is invisible, so it is a cut.
        self.play(FadeOut(card), run_time=0.9)
        at, w = framing(0, 0, 1, pad=1.9, min_h=3.4)
        self.camera.frame.move_to(at).set(width=w)
        self.play(FadeIn(seeds[0]), run_time=0.8)

        self.built = {0: {0: seeds[0]}, 1: {0: seeds[1]}, 2: {0: seeds[2]}}
        self.act_one()
        self.act_selection()
        self.act_two()
        self.act_three(seeds)

    # ---------------------------------------------------------------- act 1 --
    def act_one(self):
        """What one step IS. Where its parents come from is a separate act.

        Those cannot be the same step. A chain's first step has only the seed to build on, so
        narrating "every selected node is a parent" over it shows a single parent and proves
        nothing. The chain needs a ranking before the selection is worth watching.
        """
        evs = self.chain_of[0]

        lab = self.cap("chain 1  ·  one of three", 24, MUTED)
        lab.next_to(self.built[0][0], UP, buff=0.4)
        self.play(FadeIn(lab), run_time=0.6)
        self.wait(1.0)
        self.play(FadeOut(lab), run_time=0.4)

        self.one_step(evs[0], 0, 1, beats=("fan", "commit"), slow=True)

        at, w = framing(0, 0, 4, pad=1.6, min_h=3.4)
        self.play(self.camera.frame.animate.move_to(at).set(width=w), run_time=1.1)
        for idx, ev in enumerate(evs[1:4], start=2):
            self.one_step(ev, 0, idx)

    # ------------------------------------------------------- the selection --
    def act_selection(self):
        """How the parents are chosen -- `Selector.pick`, laid out.

        Every other act shows the draw as an outcome: four nodes light up. The rule behind it is
        the one thing a viewer cannot infer from watching, and it is the lever the whole family of
        algorithms turns, so it gets its own act with the ranking made explicit.

        The chain is real and so are the picks: this is step 5's actual draw, replayed against the
        ranking it was drawn from.
        """
        ev = self.chain_of[0][4]
        pre = ev["chains"][0][:-1]                       # the chain as the selector saw it
        ranked = sorted(pre, key=lambda n: -n.score)
        n = len(ranked)
        elite_end, mid_start, mid_end = tiers(n)
        rank_of = {nd.order: i for i, nd in enumerate(ranked)}

        # Everything below the chain has to fit between it and the bottom edge: the heading, the
        # ranking, three brackets and a two-line rule. The frame is 8 units tall and the chain
        # takes the top 2.6 of them, so the budget is tight and worth writing down.
        self.play(self.camera.frame.animate.move_to([0, 0.1, 0]).set(width=FULL_W), run_time=1.3)
        head = Text("how the parents are chosen", font=FONT, font_size=31, color=INK)
        head.move_to([0, 1.05, 0])
        self.play(FadeIn(head), run_time=0.7)

        SPACING, ROW_Y = 1.32, -0.45

        def slot(i):
            return (i - (n - 1) / 2) * SPACING

        row = VGroup()
        for i, nd in enumerate(ranked):
            m = node_mob(nd.score, ORIGIN).scale(1.55).move_to([slot(i), ROW_Y, 0])
            row.add(m)
        sortnote = Text("the chain, sorted by score — best first",
                        font=FONT, font_size=21, color=SUB).move_to([0, 0.42, 0])
        self.play(LaggedStart(*[FadeIn(m, shift=DOWN * 0.25) for m in row], lag_ratio=0.12),
                  FadeIn(sortnote), run_time=1.6)
        self.wait(1.0)

        def band(lo: int, hi: int, y: float, label: str, color):
            """A bracket under ranks [lo, hi)."""
            x0, x1 = slot(lo) - 0.36, slot(hi - 1) + 0.36
            g = VGroup(Line([x0, y, 0], [x1, y, 0], color=color, stroke_width=3),
                       Line([x0, y, 0], [x0, y + 0.13, 0], color=color, stroke_width=3),
                       Line([x1, y, 0], [x1, y + 0.13, 0], color=color, stroke_width=3))
            g.add(Text(label, font=FONT, font_size=19, color=color)
                  .next_to(g, DOWN, buff=0.1))
            return g

        bands = VGroup(band(0, elite_end, -1.20, "the elite head", GREEN),
                       band(mid_start, mid_end, -1.95, "the middle", BLUE),
                       band(0, n, -2.70, "anywhere at all", MUTED))
        self.play(LaggedStart(*[FadeIn(b) for b in bands], lag_ratio=0.3), run_time=1.5)
        rule = para("the best node is always in; each of the others is drawn from the elite head\n"
                    "70% of the time, the middle 20%, and anywhere at all the remaining 10%.",
                    21, INK)
        fit(rule, FULL_W - 2.4).move_to([0, -3.42, 0])
        self.play(FadeIn(rule), run_time=0.7)
        self.wait(2.6)

        # the actual draw
        self.play(FadeOut(rule), run_time=0.4)
        phrase = {"elite": "drawn from the elite head", "middle": "drawn from the middle",
                  "tail": "drawn from the tail"}
        for j, (order, _) in enumerate(ev["picked"]):
            i = rank_of[order]
            tag = Text("always in — the incumbent" if j == 0 else phrase[ev["labels"][j]],
                       font=FONT, font_size=23, color=ORANGE).move_to([0, -3.42, 0])
            self.play(FadeIn(tag), Indicate(row[i], color=ORANGE, scale_factor=1.18),
                      row[i][0].animate.set_stroke(ORANGE, width=4.0), run_time=0.85)
            self.wait(0.75)
            self.play(FadeOut(tag), run_time=0.3)

        done = para(f"{len(ev['picked'])} nodes, and every one of them is a parent of what comes "
                    "next —\nnot one base program with the rest as decoration.", 23, INK)
        fit(done, FULL_W - 2.4).move_to([0, -3.42, 0])
        self.play(FadeIn(done), run_time=0.7)
        self.wait(2.4)
        self.play(FadeOut(head), FadeOut(sortnote), FadeOut(row), FadeOut(bands), FadeOut(done),
                  run_time=0.9)

    def one_step(self, ev, chain: int, order: int, *, beats=(), slow=False):
        band = BAND_Y[chain]
        gate, rt = gate_pos(order, band), 0.9 if slow else 0.28
        held = [None]

        def say(key: str, text: str, *, top=False, hold=1.7):
            if key not in beats:
                return
            c = self.cap(text, 25, INK)
            c.next_to(self.camera.frame.get_top() if top else self.camera.frame.get_bottom(),
                      DOWN if top else UP, buff=0.32)
            gone = [FadeOut(held[0])] if held[0] is not None else []
            self.play(FadeIn(c), *gone, run_time=0.6)
            held[0] = c
            self.wait(hold)

        # 1. the selector picks. Every selected node is a parent -- there is no distinguished one.
        picked = [self.built[chain][o][0] for o, _ in ev["picked"]]
        self.play(*[p.animate.set_stroke(ORANGE, width=self.sw(3.4)) for p in picked],
                  *dim(self.dim_later), run_time=rt)
        self.dim_later = []
        n = len(ev["picked"])
        say("select", f"the selector draws {n} node{'' if n == 1 else 's'} from this chain's "
                      f"ranking —\n{drew_phrase(ev['labels'])}", top=True)

        # 2. they all feed ONE prompt
        arcs = VGroup(*[arc(trunk_pos(o, band), gate, ORANGE, self.sw(2.4),
                            angle=-0.55 if o < order - 1 else -0.001,
                            buff_a=NODE_R + 0.03, buff_b=0.13)
                        for o, _ in ev["picked"]])
        dot = Dot(gate, radius=0.055, color=ORANGE)
        self.play(LaggedStart(*[Create(a) for a in arcs], lag_ratio=0.18 if slow else 0.05),
                  run_time=rt * 1.3)
        self.play(FadeIn(dot, scale=0.4), run_time=rt * 0.5)
        say("parents", "all of them are parents — the prompt asks for a NEW program,\n"
                       "not an edit of any one of them")

        # 3. one call, k candidates, drawn as peers
        fan = VGroup(*[cand_mob(s, cand_pos(order, j, band), j == ev["win"], True)
                       for j, s in enumerate(ev["cands"])])
        spokes = VGroup(*[spoke(gate, cand_pos(order, j, band), BLUE, self.sw(2.0))
                          for j in range(K)])
        self.play(LaggedStart(*[Create(s) for s in spokes], lag_ratio=0.12),
                  LaggedStart(*[FadeIn(f, scale=0.5) for f in fan], lag_ratio=0.12),
                  run_time=rt * 1.6)
        say("fan", f"ONE call produces all {K} of them — that is the cost profile.\n"
                   "They are peers; none of them is the parent's revision.", hold=1.9)

        # 4. best of k joins the chain -- and it commits even when it is worse
        win = ev["win"]
        # Indicate, not Flash: a flash draws its rays at a fixed radius, and around a box this
        # small they land well clear of it as loose green dashes belonging to nothing.
        self.play(Indicate(fan[win], color=GREEN, scale_factor=1.22), run_time=rt * 0.9)
        node = node_mob(ev["winner"], trunk_pos(order, band), GREEN, self.sw(3.0))
        link = arc(cand_pos(order, win, band), trunk_pos(order, band), GREEN, self.sw(2.6),
                   angle=0.32 * (win - 1) or -0.001,
                   buff_a=SQ * 0.62, buff_b=NODE_R + 0.03)
        self.play(Create(link), FadeIn(node, scale=0.6), run_time=rt * 1.2)
        say("commit",
            f"best of k = {ev['winner']:.2f} continues the chain — the other {K - 1} were "
            "measured\nand kept, but no later prompt will see them."
            + ("" if ev["improved"] else
               f"\nThis one is below its best parent ({ev['anchor']:.2f}) and it still commits."),
            hold=2.4)
        if held[0] is not None:
            self.play(FadeOut(held[0]), run_time=0.45)

        self.built[chain][order] = node
        self.play(*[p.animate.set_stroke(EDGE, width=self.sw(2.0)) for p in picked],
                  node[0].animate.set_stroke(EDGE, width=self.sw(2.0)), run_time=rt * 0.5)
        self.dim_later = [(arcs, "stroke"), (link, "stroke"), (dot, "all"), (spokes, "all"),
                          (fan, "all")]

    # ---------------------------------------------------------------- act 2 --
    def act_two(self):
        """The rest of chain 1 at speed, so the DAG accumulates and the arcs reach further back."""
        evs = self.chain_of[0]
        at, w = framing(0, 0, len(evs), pad=1.3)
        self.play(self.camera.frame.animate.move_to(at).set(width=w), run_time=1.4)

        # Step 5 is the draw the previous act just took apart, so it runs first and gets to say
        # what the parent set is FOR. The rest go at speed.
        self.one_step(evs[4], 0, 5, beats=("parents",), slow=True)

        note = self.cap("the arcs reach further back as the chain grows —\n"
                        "each one is a parent of the prompt it points at", 24, SUB)
        note.next_to(self.camera.frame.get_bottom(), UP, buff=0.35)
        self.play(FadeIn(note), run_time=0.6)
        for idx, ev in enumerate(evs[5:], start=6):
            self.one_step(ev, 0, idx)
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

        note = para("Chains do not compete. Each gets an equal share of the budget and they are "
                    "served in turn;\nthe selection happens inside one chain, over its own nodes.",
                    23, INK)
        fit(note, FULL_W - 2.0).move_to([0, -3.35, 0])
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
            g.add(arc(trunk_pos(o, band), gate, MUTED, 1.2,
                      angle=-0.55 if o < order - 1 else -0.001,
                      buff_a=NODE_R + 0.03, buff_b=0.13).set_stroke(opacity=0.4))
        for j, s in enumerate(ev["cands"]):
            at = cand_pos(order, j, band)
            g.add(spoke(gate, at, MUTED, 1.2).set_opacity(0.4))
            g.add(cand_mob(s, at, j == ev["win"], False).set_opacity(0.8))
        g.add(arc(cand_pos(order, ev["win"], band), trunk_pos(order, band), MUTED, 1.4,
                  angle=0.32 * (ev["win"] - 1) or -0.001,
                  buff_a=SQ * 0.62, buff_b=NODE_R + 0.03).set_stroke(opacity=0.5))
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
        head = fit(Text("every candidate that was measured, and the best so far",
                        font=FONT, font_size=28, color=INK), FULL_W - 2.0)
        head.next_to(ax, UP, buff=0.55)
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
        tag = para("The line only goes up, and on its own it suggests steady progress. The cloud "
                   "underneath is\nhow much of the budget went into candidates worse than what "
                   f"already existed — the run ended at {best:.2f}.", 21, SUB)
        fit(tag, FULL_W - 2.0).move_to([0, -3.25, 0])
        self.play(FadeIn(tag), Indicate(line, color=ORANGE, scale_factor=1.02), run_time=1.4)
