"""SimpleTES as a narrated run.

    manim -qm --format=mp4 manim_simpletes.py SimpleTESRun

Four acts, because twenty-one identical steps explain nothing.

  1. what one step IS, with the camera close enough to read it
  2. how the parents are CHOSEN -- each node's score and its use count turned into a bar, the four
     tallest taken, and then the other two selectors, because the rule is a component and swapping
     it is the point
  3. the rest of that chain at speed, so the DAG accumulates and the arcs reach further back
  4. the whole run replayed with all three chains advancing in the same beat, the fitness curve
     growing as their measurements land

Act 2 exists because every other act shows the draw only as an outcome -- four nodes light up. The
rule behind it is the one thing a viewer cannot infer from watching, and it is the lever the whole
family of algorithms turns: `puct`, `balance` and `rpucg` differ in nothing else.

The scores are invented. Everything about who gets picked, how many, and what commits comes from
`sim_simpletes`, which calls the real selector.
"""
from __future__ import annotations

import numpy as np
from manim import (DOWN, LEFT, ORIGIN, RIGHT, UP, ApplyFunction, Arrow, Axes, Circle, Create,
                   CurvedArrow, Dot, FadeIn, FadeOut, Indicate, LaggedStart, Line, ManimColor,
                   MovingCameraScene, Rectangle, RoundedRectangle, Text, Transform, VGroup, Write,
                   config, interpolate_color)

from sim_simpletes import EVENTS, K

config.background_color = ManimColor("#ffffff")
FONT = "Helvetica Neue"
"""Pango's default here is a serif, which reads as a paper figure rather than a diagram.
`Text` falls back silently if the family is missing, so naming one costs nothing elsewhere."""

BASE_FS = 72
"""Every label is built at this size and scaled down -- see `txt`."""

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


def txt(s: str, size: float, color=None, weight="NORMAL") -> Text:
    """Build the label large, then scale it down. Never set a small `font_size` directly.

    Pango hints glyph advances to whole pixels at whatever size it is asked to typeset. At
    font_size around 20 that quantisation is a sizeable fraction of a character's advance, so
    letters inside a word come out visibly unevenly spaced -- "the elite head" gets gaps that
    belong to no font -- and Manim then scales the resulting OUTLINE up, preserving the error
    exactly. Typesetting at 72 makes the rounding negligible, and scaling a vector costs nothing.

    This also invalidated the first font comparison: Helvetica Neue and Avenir Next were rejected
    for "loose tracking" that was this bug, not the typeface.
    """
    return Text(s, font=FONT, font_size=BASE_FS, color=INK if color is None else color,
                weight=weight).scale(size / BASE_FS)


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
    return txt(f"{score:.2f}", 24, color).scale_to_fit_width(width)


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
    lines = VGroup(*[txt(ln, size, color, weight) for ln in text.split("\n")])
    return lines.arrange(DOWN, buff=size * 0.0042)


class SimpleTESRun(MovingCameraScene):

    # ---- text that stays the same apparent size however far the camera is ----
    def cap(self, text: str, size: float = 26, color=SUB, weight="NORMAL"):
        t = para(text, size, color, weight=weight) if "\n" in text else \
            txt(text, size, color, weight)
        t.scale(self.zoom)
        return fit(t, self.camera.frame.width - 0.8)

    @property
    def zoom(self) -> float:
        """How much the camera magnifies. Stroke widths and text both have to be divided by it,
        or a close-up renders 3x-thick rings around 3x-large labels."""
        return self.camera.frame.width / FULL_W

    def sw(self, base: float) -> float:
        return base * self.zoom

    # ---- the budget counter, pinned to the top-left of whatever the camera shows ----
    def chip_at(self, spent: int, center, width: float) -> Text:
        """A chain's share of the run, counting down.

        Real, and upstream's: `chain_prompt_count >= prompt_budget` drops a chain out of
        `_ready_chains`, so a chain that has spent its share stops even though the run continues.
        Without the counter on screen the chain just quietly stops producing steps.

        Positioned from an EXPLICIT centre and width so it can be moved in the same animation as
        the camera -- reading `self.camera.frame` here would place it where the camera still is.
        """
        z = width / FULL_W
        t = txt(f"chain 1   ·   {spent} / {self.chip_total} LLM calls", 24,
                MUTED if spent >= self.chip_total else ORANGE).scale(z)
        h = z * config.frame_height
        return t.move_to([center[0] - width / 2 + t.width / 2 + 0.25 * z,
                          center[1] + h / 2 - t.height / 2 - 0.25 * z, 0])

    def move_camera(self, at, w, *extra, run_time=1.2):
        anims = [self.camera.frame.animate.move_to(at).set(width=w), *extra]
        if self.chip is not None:
            anims.append(Transform(self.chip, self.chip_at(self.chip_spent, at, w)))
        self.play(*anims, run_time=run_time)

    def construct(self):
        self.dim_later: list = []
        self.chain_of = {c: [ev for ev in EVENTS if ev["chain"] == c] for c in range(3)}
        self.chip = None
        self.chip_spent = 0
        self.chip_total = len(self.chain_of[0])

        title = txt("SimpleTES", 54, INK, weight="BOLD")
        # What the thing IS. The mechanism sentence that used to sit here has moved to act 3,
        # where there is something on screen for it to describe.
        sub = para("an evolutionary search for programs: several chains of attempts advancing in\n"
                   "parallel, a language model as the only mutation operator, one score to sort by",
                   27, SUB)
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

        # Say what a "prompt" costs the first time the counter appears. The fan beat below explains
        # that one call returns all k candidates, but the counter is on screen before it.
        lab = self.cap("chain 1  ·  one of three\n"
                       f"its share of the run is {self.chip_total} prompts —\n"
                       "one prompt, one LLM call, k candidates back", 24, MUTED)
        lab.next_to(self.built[0][0], UP, buff=0.42)
        self.chip = self.chip_at(0, self.camera.frame.get_center(), self.camera.frame.width)
        self.play(FadeIn(lab), FadeIn(self.chip), run_time=0.6)
        self.wait(2.2)
        self.play(FadeOut(lab), run_time=0.4)

        self.one_step(evs[0], 0, 1, beats=("fan", "commit"), slow=True)

        # Five steps before the selection is explained, because the draw only becomes interesting
        # once the chain is longer than the number of parents a prompt takes: until then every
        # node is selected every time and there is no decision to look at.
        self.move_camera(*framing(0, 0, 5, pad=1.6, min_h=3.4), run_time=1.1)
        for idx, ev in enumerate(evs[1:5], start=2):
            self.one_step(ev, 0, idx)

    # ------------------------------------------------------- the selection --
    def act_selection(self):
        """How the parents are chosen -- PUCT, with the arithmetic on screen.

        Every other act shows the draw as an outcome: four nodes light up. The rule behind it is
        the one thing a viewer cannot infer from watching, and it is the lever the whole family of
        algorithms turns, so it gets its own act.

        PUCT rather than the default `balance` because its rule is arithmetic on two numbers the
        chain already carries -- a node's score and how many times it has been used -- so the picks
        can be shown being DERIVED. `balance` rolls a die against three bands: one sentence to
        state, nothing to watch.

        The chain is real, the visit counts are real, and so are the picks: this is step 6's actual
        draw, replayed against the numbers it was made from. Step 6 rather than an earlier one
        because a chain shorter than the parent count has every node selected every time.
        """
        ev = self.chain_of[0][5]
        ranking = ev["ranking"]                          # (order, score, visits, bonus), best first
        n = len(ranking)
        picked_orders = {o for o, _ in ev["picked"]}
        slot_of = {o: i for i, (o, _, _, _) in enumerate(ranking)}

        # Everything below the chain has to fit between it and the bottom edge: the heading, the
        # formula, the ranking, a bar per node and a closing line. The frame is 8 units tall and
        # the chain takes the top 2.6 of them, so the budget is tight and worth writing down.
        self.play(self.camera.frame.animate.move_to([0, 0.1, 0]).set(width=FULL_W),
                  FadeOut(self.chip), run_time=1.3)
        head = txt("how the parents are chosen", 31, INK)
        head.move_to([0, 1.08, 0])
        self.play(FadeIn(head), run_time=0.7)

        SPACING, ROW_Y, BASE_Y, BAR_H = 1.32, -0.35, -2.92, 1.35

        def slot(i):
            return (i - (n - 1) / 2) * SPACING

        row, seen_labels = VGroup(), VGroup()
        for i, (order, q, visits, _) in enumerate(ranking):
            row.add(node_mob(q, ORIGIN).scale(1.5).move_to([slot(i), ROW_Y, 0]))
            seen_labels.add(txt(f"used {visits}×", 17, MUTED).move_to([slot(i), -0.98, 0]))
        # Name the number before showing it. These circles carry each node's VALUE, which is not
        # the score the same node shows on the chain above -- a node whose children did well is
        # worth more than it scored, and a viewer who has just watched the chain will read the
        # circles as scores unless told otherwise.
        sortnote = txt("each node's value — its own score, or the best any of its children reached",
                       21, SUB).move_to([0, 0.48, 0])
        self.play(LaggedStart(*[FadeIn(m, shift=DOWN * 0.25) for m in row], lag_ratio=0.11),
                  FadeIn(sortnote), run_time=1.5)
        self.play(FadeIn(seen_labels), run_time=0.7)
        self.wait(1.4)

        # The rule, colour-coded to the bars it is about to draw. Spaces cannot do the spacing:
        # Pango trims them at the ends of a run, so "u  =  " sets as "u =" and the pieces collide.
        formula = VGroup(txt("u  =", 27, INK), txt("value", 27, BLUE), txt("+", 27, INK),
                         txt("c · range · prior · √(1+T) / (1 + used)", 27, ORANGE))
        formula.arrange(RIGHT, buff=0.17).move_to([0, 0.48, 0])
        fit(formula, FULL_W - 2.0)
        self.play(FadeOut(sortnote), FadeIn(formula), run_time=0.8)
        self.wait(1.8)

        scale = BAR_H / max(s + b for _, s, _, b in ranking)
        bars, tops = VGroup(), VGroup()
        for i, (order, score, _, bonus) in enumerate(ranking):
            q = Rectangle(width=0.62, height=score * scale, stroke_width=0,
                          fill_color=BLUE, fill_opacity=0.85)
            q.move_to([slot(i), BASE_Y + score * scale / 2, 0])
            e = Rectangle(width=0.62, height=bonus * scale, stroke_width=0,
                          fill_color=ORANGE, fill_opacity=0.85)
            e.move_to([slot(i), BASE_Y + score * scale + bonus * scale / 2, 0])
            bars.add(VGroup(q, e))
            tops.add(txt(f"{score + bonus:.2f}", 19, INK)
                     .move_to([slot(i), BASE_Y + (score + bonus) * scale + 0.2, 0]))
        axis = Line([slot(0) - 0.55, BASE_Y, 0], [slot(n - 1) + 0.55, BASE_Y, 0],
                    color=EDGE, stroke_width=2)

        self.play(FadeIn(axis), LaggedStart(*[FadeIn(b[0]) for b in bars], lag_ratio=0.1),
                  run_time=1.2)
        self.play(LaggedStart(*[FadeIn(b[1]) for b in bars], lag_ratio=0.1),
                  LaggedStart(*[FadeIn(t) for t in tops], lag_ratio=0.1), run_time=1.2)
        # Say what the bars actually show, which on a chain this short is not what a reader of the
        # formula would guess: a node's value is its own score OR the best any of its children
        # reached, whichever is higher, and nearly all of these were parents of the batch that set
        # the record -- so the blue is almost flat and the orange decides.
        why = para("Almost all of these were parents of the batch that set the record, so their "
                   "values are nearly flat.\nThe decision falls to the bonus — and the bonus "
                   "shrinks every time a node gets used.", 21, INK)
        fit(why, FULL_W - 2.0).move_to([0, -3.42, 0])
        self.play(FadeIn(why), run_time=0.7)
        self.wait(3.4)

        # the actual draw: the n highest u, no dice involved
        self.play(FadeOut(why), run_time=0.4)
        tag = txt(f"the {len(picked_orders)} highest u are the parents", 24, ORANGE)
        tag.move_to([0, -3.45, 0])
        lifts = []
        for order in picked_orders:
            i = slot_of[order]
            lifts += [row[i][0].animate.set_stroke(ORANGE, width=4.0),
                      bars[i].animate.set_stroke(ORANGE, width=2.5)]
        self.play(FadeIn(tag), *lifts,
                  *[Indicate(row[slot_of[o]], color=ORANGE, scale_factor=1.15)
                    for o in picked_orders], run_time=1.1)
        self.wait(2.0)

        done = para(f"{len(picked_orders)} nodes, and every one of them is a parent of what comes "
                    "next —\nnot one base program with the rest as decoration.", 23, INK)
        fit(done, FULL_W - 2.4).move_to([0, -3.45, 0])
        self.play(FadeOut(tag), FadeIn(done), run_time=0.7)
        self.wait(2.4)

        # PUCT is ONE selector. Swapping it is the axis SimpleTES varies to get six algorithms out
        # of one engine, and that is invisible if the act only ever shows one of them.
        # NOT sortnote: it was already faded when the formula replaced it, and `FadeOut` restores
        # a mobject's opacity when it finishes, so fading it a second time puts it back on screen
        # at full strength for the length of the animation -- on top of the formula.
        self.play(FadeOut(formula), FadeOut(row), FadeOut(seen_labels),
                  FadeOut(bars), FadeOut(tops), FadeOut(axis), FadeOut(done), FadeOut(head),
                  run_time=0.8)
        head2 = txt("that rule is a component, and swapping it is the point", 28, INK)
        head2.move_to([0, 0.92, 0])
        self.play(FadeIn(head2), run_time=0.6)

        picks = [("puct", "the score-plus-bonus rule just shown — what this run uses", BLUE),
                 ("balance", "no arithmetic: keep the best, then roll for each of the others —\n"
                             "70% from the elite head, 20% from the middle, 10% from anywhere",
                  GREEN),
                 ("rpucg", "value propagated back through the whole lineage and discounted\n"
                           "once per generation, ranked against the population, kin excluded",
                  MUTED)]
        rows = VGroup()
        for name, desc, col in picks:
            rows.add(VGroup(txt(name, 26, col, weight="BOLD"), para(desc, 20, SUB))
                     .arrange(DOWN, buff=0.14))
        rows.arrange(DOWN, buff=0.46).move_to([0, -1.18, 0])
        self.play(LaggedStart(*[FadeIn(r, shift=UP * 0.12) for r in rows], lag_ratio=0.35),
                  run_time=1.8)
        tail = para("Same chains, same prompts, same best-of-k — only the choice of parents "
                    "changes.\nThat is how one engine becomes a family of algorithms.", 22, INK)
        fit(tail, FULL_W - 2.4).move_to([0, -3.4, 0])
        self.play(FadeIn(tail), run_time=0.7)
        self.wait(3.0)
        self.play(FadeOut(head2), FadeOut(rows), FadeOut(tail), run_time=0.9)

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
        spend = []
        if self.chip is not None and chain == 0:
            self.chip_spent += 1
            spend.append(Transform(self.chip, self.chip_at(
                self.chip_spent, self.camera.frame.get_center(), self.camera.frame.width)))
        self.play(*[p.animate.set_stroke(EDGE, width=self.sw(2.0)) for p in picked],
                  node[0].animate.set_stroke(EDGE, width=self.sw(2.0)), *spend,
                  run_time=rt * 0.5)
        self.dim_later = [(arcs, "stroke"), (link, "stroke"), (dot, "all"), (spokes, "all"),
                          (fan, "all")]

    # ---------------------------------------------------------------- act 2 --
    def act_two(self):
        """The rest of chain 1 at speed, so the DAG accumulates and the arcs reach further back."""
        evs = self.chain_of[0]
        at, w = framing(0, 0, len(evs), pad=1.3)
        self.chip = self.chip_at(self.chip_spent, at, w)
        self.play(self.camera.frame.animate.move_to(at).set(width=w), FadeIn(self.chip),
                  run_time=1.4)

        # Step 6 is the draw the previous act just took apart, so it runs first and gets to say
        # what the parent set is FOR. The rest go at speed.
        self.one_step(evs[5], 0, 6, beats=("parents",), slow=True)

        note = self.cap("every selected node is a parent — one prompt fans out into k candidates,\n"
                        "and one of them continues the chain", 24, SUB)
        note.next_to(self.camera.frame.get_bottom(), UP, buff=0.35)
        self.play(FadeIn(note), run_time=0.6)
        for idx, ev in enumerate(evs[6:], start=7):
            self.one_step(ev, 0, idx)

        # The share is spent, so this chain is done -- while the run is not.
        stop = self.cap("its share of the budget is spent, so chain 1 stops here.\n"
                        "The run is not over: the other two still have theirs.", 24, INK)
        stop.next_to(self.camera.frame.get_bottom(), UP, buff=0.35)
        self.play(FadeOut(note), FadeIn(stop), Indicate(self.chip, color=MUTED, scale_factor=1.12),
                  run_time=0.9)
        self.wait(2.6)
        self.play(FadeOut(stop), run_time=0.5)

    # ---------------------------------------------------------------- act 4 --
    def act_three(self, seeds):
        """The whole run at once: three chains advancing together, the curve growing with them.

        Drawing chain 2 to completion and then chain 3 showed them as three things that happened,
        one after another. They did not: the chains are independent and run at the same time, and
        that parallelism is half of what the algorithm is. So the stage is cleared and the run is
        replayed round by round -- every chain takes its step in the same beat, and the fitness
        curve extends by those three measurements as they land.
        """
        self.play(*[FadeOut(m) for m in self.mobjects],
                  self.camera.frame.animate.move_to(ORIGIN).set(width=FULL_W), run_time=1.3)
        self.dim_later = []

        # The chains are drawn at their usual coordinates and then mapped as a block into the top
        # of the frame, so the plot has the bottom third to itself.
        SCALE, LIFT = 0.74, 1.35

        def place(m):
            return m.scale(SCALE, about_point=ORIGIN).shift(UP * LIFT)

        def place_pt(p):
            return np.asarray(p, dtype=float) * SCALE + np.array([0.0, LIFT, 0.0])

        head = txt("three chains, advancing together", 27, INK).move_to([0, 3.66, 0])
        stage = VGroup()
        for c in range(3):
            stage.add(place(node_mob(EVENTS[0]["chains"][c][0].score, trunk_pos(0, BAND_Y[c]))))
            stage.add(txt(f"chain {c + 1}", 17, SUB)
                      .move_to(place_pt([X0 - 1.15, BAND_Y[c] + 0.17, 0])))
            stage.add(txt(f"{len(self.chain_of[c])} LLM calls", 13, MUTED)
                      .move_to(place_pt([X0 - 1.15, BAND_Y[c] - 0.17, 0])))

        ax = Axes(x_range=[0, 22, 5], y_range=[0.3, 0.9, 0.2], x_length=8.4, y_length=2.15,
                  tips=False,
                  axis_config={"color": EDGE, "stroke_width": 2, "include_numbers": False})
        ax.move_to([0, -2.45, 0])
        ticks = VGroup()
        for v in (0.3, 0.5, 0.7, 0.9):
            ticks.add(txt(f"{v:.1f}", 17, MUTED).next_to(ax.c2p(0, v), LEFT, buff=0.16))
        for v in (5, 10, 15, 20):
            ticks.add(txt(str(v), 17, MUTED).next_to(ax.c2p(v, 0.3), DOWN, buff=0.18))
        ticks.add(txt("step", 17, MUTED).next_to(ax.c2p(22, 0.3), DOWN, buff=0.18))
        axis_note = txt("every candidate measured  ·  best so far", 19, MUTED)
        axis_note.next_to(ax, UP, buff=0.16).align_to(ax, LEFT)

        self.play(FadeIn(head), FadeIn(stage), Create(ax), FadeIn(ticks), FadeIn(axis_note),
                  run_time=1.4)

        pts = [ax.c2p(e["step"] + 1, e["best"]) for e in EVENTS]
        curve = VGroup(Dot(pts[0], radius=0.05, color=ORANGE))
        cloud = VGroup()
        self.add(curve, cloud)

        rounds = max(len(v) for v in self.chain_of.values())
        for r in range(rounds):
            anims = []
            for c in range(3):
                if r < len(self.chain_of[c]):
                    g = place(self.quiet_step(self.chain_of[c][r], c, r + 1))
                    stage.add(g)
                    anims.append(FadeIn(g))
            for i in range(3 * r, min(3 * r + 3, len(EVENTS))):
                for s in EVENTS[i]["cands"]:
                    d = Dot(ax.c2p(EVENTS[i]["step"] + 1, s), radius=0.045,
                            color=BLUE).set_opacity(0.55)
                    cloud.add(d)
                    anims.append(FadeIn(d, scale=0.35))
                if i:
                    seg = Line(pts[i - 1], pts[i], color=ORANGE, stroke_width=4)
                    curve.add(seg)
                    anims.append(Create(seg))
            self.play(*anims, run_time=1.15)

        note = para("Chains do not compete. Each gets an equal share of the budget, and the "
                    "selection\ninside one of them never looks at the others.", 23, INK)
        fit(note, FULL_W - 2.0).move_to([0, 3.6, 0])
        self.play(FadeOut(head), FadeIn(note), run_time=0.8)
        self.wait(2.6)

        plot = VGroup(ax, ticks, axis_note, cloud, curve)
        self.play(FadeOut(stage), FadeOut(note),
                  plot.animate.scale(1.42).move_to([0, 0.45, 0]), run_time=1.4)
        best = max(e["best"] for e in EVENTS)
        tag = para("The line only goes up, and on its own it suggests steady progress. The cloud "
                   "underneath is\nhow much of the budget went into candidates worse than what "
                   f"already existed — the run ended at {best:.2f}.", 21, SUB)
        fit(tag, FULL_W - 2.0).move_to([0, -3.1, 0])
        self.play(FadeIn(tag), Indicate(curve, color=ORANGE, scale_factor=1.02), run_time=1.4)
        self.wait(3.0)

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

