"""Shared drawing for the manim explainers.

Everything here was paid for once by `manim_simpletes.py`. The comments say what each helper is
working around, because every one of them replaced something that looked correct in the source and
wrong on screen -- and a second video written without them would reproduce the same four bugs.
"""
from __future__ import annotations

import numpy as np
from manim import (ApplyFunction, Arrow, Circle, CurvedArrow, ManimColor, Text, VGroup, config,
                   interpolate_color)

config.background_color = ManimColor("#ffffff")

FONT = "Helvetica Neue"
"""Pango's default here is a serif, which reads as a paper figure rather than a diagram. `Text`
falls back silently if the family is missing, so naming one costs nothing on another machine."""

BASE_FS = 72
"""Every label is typeset at this size and scaled down -- see `txt`."""

INK = ManimColor("#1f2328")
SUB = ManimColor("#57606a")
MUTED = ManimColor("#8c959f")
EDGE = ManimColor("#d0d7de")
PANEL = ManimColor("#f6f8fa")
ORANGE = ManimColor("#bc4c00")
GREEN = ManimColor("#1a7f37")
BLUE = ManimColor("#0969da")
PURPLE = ManimColor("#8250df")
RED = ManimColor("#cf222e")
LOW, HIGH = ManimColor("#deebf7"), ManimColor("#216eb4")

FULL_W = config.frame_width
FULL_H = config.frame_height


def txt(s: str, size: float, color=None, weight="NORMAL") -> Text:
    """Build the label large, then scale it down. Never set a small `font_size` directly.

    Pango hints glyph advances to whole pixels at whatever size it is asked to typeset. At
    font_size around 20 that quantisation is a sizeable fraction of a character's advance, so
    letters inside a word come out visibly unevenly spaced -- "the elite head" gets gaps that
    belong to no font -- and Manim then scales the resulting OUTLINE up, preserving the error
    exactly. Typesetting at 72 makes the rounding negligible, and scaling a vector costs nothing.
    """
    return Text(s, font=FONT, font_size=BASE_FS, color=INK if color is None else color,
                weight=weight).scale(size / BASE_FS)


def para(text: str, size: float, color=None, weight="NORMAL") -> VGroup:
    """Multi-line text whose lines are actually centred on each other.

    A `Text` with newlines left-aligns its lines inside one mobject. `Paragraph` centres them but
    inserts a gap after the first character of a line -- "They are peers" comes out "T hey are
    peers". One Text per line, arranged, avoids both.
    """
    from manim import DOWN

    lines = VGroup(*[txt(ln, size, color, weight) for ln in text.split("\n")])
    return lines.arrange(DOWN, buff=size * 0.0042)


def fit(mob, limit: float):
    """Text set at a fixed size will happily run off both edges of the frame."""
    if mob.width > limit:
        mob.scale(limit / mob.width)
    return mob


def score_color(s: float, lo: float = 0.35, hi: float = 0.85) -> ManimColor:
    return interpolate_color(LOW, HIGH, max(0.0, min(1.0, (s - lo) / max(hi - lo, 1e-6))))


def ink_on(s: float, lo: float = 0.35, hi: float = 0.85) -> ManimColor:
    mid = lo + 0.4 * (hi - lo)
    return ManimColor("#ffffff") if s > mid else INK


def fitted(label: str, width: float, color) -> Text:
    """A label sized to its container. `font_size` is absolute, so one that fits a circle of this
    radius spills out of a box of another size."""
    return txt(label, 24, color).scale_to_fit_width(width)


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


def arc(a, b, color, width=2.0, angle=-0.55, buff_a=0.0, buff_b=0.0, tip=0.085) -> CurvedArrow:
    a, b = _shrink(a, b, buff_a, buff_b)
    return CurvedArrow(a, b, angle=angle, color=color, stroke_width=width, tip_length=tip)


def spoke(a, b, color, width=1.8, buff_a=0.09, buff_b=0.235, tip=0.075) -> Arrow:
    """A straight arrow trimmed asymmetrically -- clear of its source, clear of its target."""
    a, b = _shrink(a, b, buff_a, buff_b)
    return Arrow(a, b, buff=0.0, stroke_width=width, tip_length=tip, color=color)


def arc_height(span: float, want: float = 0.9) -> float:
    """`rad` for an arc that rises `want` above its chord.

    An arc's height is `rad * span / 2`, so a fixed rad sends a long reach-back clean over whatever
    is above it. Hold the height instead and let rad fall away with distance.
    """
    return -min(0.5, 2.0 * want / max(span, 1e-6))


def _fade_stroke_and_tips(o: float):
    """Dim an arrow without filling it in.

    Two traps in one place. `set_opacity` sets fill as well as stroke, and an arc is an OPEN curve,
    so giving it fill paints the lens between it and its chord -- several arcs dimmed that way turn
    a step into a smear. But stroke alone leaves the ARROWHEADS at full strength, because a tip is
    a filled polygon. Dim the stroke everywhere, and the fill only where there already was some.
    """
    def f(m):
        m.set_stroke(opacity=o)
        for sub in m.family_members_with_points():
            if sub.get_fill_opacity() > 0:
                sub.set_fill(opacity=o)
        return m
    return f


def dim(pairs, o: float = 0.3):
    """Fade finished work into the background.

    `pairs` is `(mobject, "stroke" | "all")` -- "stroke" for anything containing an arc.
    """
    return [ApplyFunction(_fade_stroke_and_tips(o), mob) if kind == "stroke"
            else mob.animate.set_opacity(o)
            for mob, kind in pairs]


def node_mob(score: float, at, r: float = 0.21, ring=EDGE, width=2.0,
             lo: float = 0.35, hi: float = 0.85, label: str = None) -> VGroup:
    circ = Circle(radius=r, color=ring, stroke_width=width,
                  fill_color=score_color(score, lo, hi), fill_opacity=1.0).move_to(at)
    text = fitted(f"{score:.2f}" if label is None else label, r * 1.15, ink_on(score, lo, hi))
    return VGroup(circ, text.move_to(at))


class Kit:
    """Camera-aware helpers. Mix into a `MovingCameraScene`."""

    @property
    def zoom(self) -> float:
        """How much the camera magnifies. Stroke widths and text both have to be divided by it, or
        a close-up renders 3x-thick rings around 3x-large labels."""
        return self.camera.frame.width / FULL_W

    def sw(self, base: float) -> float:
        return base * self.zoom

    def cap(self, text: str, size: float = 25, color=SUB, weight="NORMAL"):
        """A caption sized for the current camera and clamped to what it can see."""
        t = para(text, size, color, weight) if "\n" in text else txt(text, size, color, weight)
        t.scale(self.zoom)
        return fit(t, self.camera.frame.width - 0.8)

    def frame_box(self, center, width: float):
        """`(left, right, top, bottom)` of the frame a camera at `(center, width)` would show.

        A frame's height follows from its width, so a close-up is not only narrow but SHORT, and a
        caption placed against the bottom edge lands on whatever is lowest in the picture.
        """
        h = width / FULL_W * FULL_H
        return (center[0] - width / 2, center[0] + width / 2,
                center[1] + h / 2, center[1] - h / 2)
