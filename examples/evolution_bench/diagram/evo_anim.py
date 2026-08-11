"""Shared drawing for the method animations.

Two palettes. Set `EVO_DIAGRAM_THEME=dark` before importing for the dark one; light is the default
because these end up in documents and slides more often than on a terminal.
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle

THEME = os.environ.get("EVO_DIAGRAM_THEME", "light").lower()

if THEME == "dark":
    BG, PANEL, EDGE = "#1e242e", "#161b22", "#2d3643"
    TITLE, SUB, ARROW, GREY = "#e6edf3", "#8b949e", "#8b949e", "#6e7681"
    BLUE, ORANGE, GREEN, PURPLE, RED = "#4493f8", "#f0883e", "#3fb950", "#a371f7", "#e5534b"
    _LOW, _HIGH = (28, 48, 68), (62, 134, 156)      # grid cell fill, worst -> best
    INK = "#0d1117"                                  # text ON a coloured block
else:
    BG, PANEL, EDGE = "#ffffff", "#f6f8fa", "#d0d7de"
    TITLE, SUB, ARROW, GREY = "#1f2328", "#57606a", "#6e7781", "#8c959f"
    BLUE, ORANGE, GREEN, PURPLE, RED = "#0969da", "#bc4c00", "#1a7f37", "#8250df", "#cf222e"
    _LOW, _HIGH = (222, 235, 247), (33, 110, 180)
    INK = "#ffffff"

W, H, DPI = 15.0, 9.6, 96
plt.rcParams.update({"font.family": "DejaVu Sans"})


def score_fill(s: float | None) -> str:
    """Cell colour for a score in [0,1]. Empty cells take the panel colour."""
    if s is None:
        return PANEL
    s = max(0.0, min(1.0, float(s)))
    r, g, b = (int(a + (bb - a) * s) for a, bb in zip(_LOW, _HIGH))
    return f"#{r:02x}{g:02x}{b:02x}"


def score_ink(s: float | None) -> str:
    """Readable text colour on top of `score_fill(s)`."""
    if s is None:
        return SUB
    if THEME == "dark":
        return "#dbe4ee"
    return "#ffffff" if s > 0.45 else "#1f2328"


class Canvas:
    def __init__(self, title="", subtitle=""):
        self.fig = plt.figure(figsize=(W, H), dpi=DPI)
        self.fig.patch.set_facecolor(BG)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, 100)
        self.ax.set_ylim(0, 64)
        self.ax.axis("off")
        self.ax.set_facecolor(BG)
        if title:
            self.ax.text(4, 60.0, title, color=TITLE, fontsize=20, weight="bold", va="center")
        if subtitle:
            self.ax.text(4, 57.2, subtitle, color=SUB, fontsize=11.5, va="center")

    def box(self, x, y, w, h, title, sub="", *, model=False, alpha=1.0, bold=False,
            fill=None, edge=EDGE, tcol=TITLE, fs=13):
        self.ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.35,rounding_size=0.8",
            facecolor=PANEL if fill is None else fill, edgecolor=edge, linewidth=1.3,
            alpha=alpha, zorder=2))
        cy = y + h / 2 + (0.85 if sub else 0)
        self.ax.text(x + w / 2, cy, title, color=tcol, fontsize=fs,
                     weight="bold" if bold else "normal",
                     ha="center", va="center", alpha=alpha, zorder=3)
        if sub:
            self.ax.text(x + w / 2, y + h / 2 - 1.15, sub, color=SUB, fontsize=9.5,
                         ha="center", va="center", alpha=alpha, zorder=3)
        if model:
            self.ax.add_patch(Circle((x + 1.35, y + h - 1.25), 0.42, facecolor=ORANGE,
                                     edgecolor="none", alpha=alpha, zorder=4))
        return (x + w / 2, y + h / 2)

    def arrow(self, p0, p1, *, color=ARROW, alpha=1.0, rad=0.0, lw=1.5, dashed=False, label="",
              lcol=None, lsize=9.5, ldy=1.0):
        self.ax.add_patch(FancyArrowPatch(
            p0, p1, connectionstyle=f"arc3,rad={rad}",
            arrowstyle="-|>,head_width=3.2,head_length=6", color=color, lw=lw,
            alpha=alpha, zorder=1, linestyle="--" if dashed else "-"))
        if label:
            mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            self.ax.text(mx, my + ldy, label, color=lcol or color, fontsize=lsize,
                         ha="center", va="center", alpha=alpha, zorder=3)

    def region(self, x, y, w, h, label, note="", *, color=BLUE, alpha=1.0):
        self.ax.add_patch(Rectangle((x, y), w, h, facecolor="none", edgecolor=color,
                                    linewidth=1.4, linestyle=(0, (5, 4)), alpha=alpha, zorder=1))
        self.ax.text(x + 1.2, y + h - 1.6, label, color=SUB, fontsize=11,
                     va="center", alpha=alpha, zorder=3)
        if note:
            self.ax.text(x + 1.2, y + 1.4, note, color=SUB, fontsize=9.5,
                         va="center", alpha=alpha, zorder=3)

    def note(self, x, y, text, *, color=SUB, size=10, alpha=1.0, ha="left", weight="normal"):
        self.ax.text(x, y, text, color=color, fontsize=size, alpha=alpha,
                     ha=ha, va="center", zorder=3, weight=weight)

    def legend_model(self, x=4, y=2.2, extra=""):
        self.ax.add_patch(Circle((x, y), 0.42, facecolor=ORANGE, edgecolor="none", zorder=4))
        self.ax.text(x + 1.2, y, "calls a model" + extra, color=SUB, fontsize=10, va="center")

    def fitness(self, rect, steps, scores, best, total, *, title="fitness", ylim=None,
                scatter_label="every program measured"):
        """Scatter of every measurement, with the best-so-far on top.

        The two together are the honest picture: the line only ever goes up, and on its own it
        suggests steady progress. The cloud underneath shows how much of the budget went into
        attempts that were worse than what already existed -- which on some problems is most of it.
        """
        ax = self.fig.add_axes(rect)
        ax.set_facecolor(PANEL)
        for s in ax.spines.values():
            s.set_color(EDGE)
        if scores:
            ax.scatter(steps, scores, s=16, color=BLUE, alpha=0.55, linewidths=0, zorder=2,
                       label=scatter_label)
        if best:
            ax.plot(range(1, len(best) + 1), best, color=ORANGE, lw=2.0, zorder=3,
                    label="best so far")
            ax.scatter([len(best)], [best[-1]], s=34, color=ORANGE, zorder=4)
        ax.set_xlim(0.5, total + 0.5)
        if ylim:
            ax.set_ylim(*ylim)
        ax.tick_params(labelsize=8, colors=SUB, length=2)
        ax.set_xlabel("step", fontsize=8.5, color=SUB, labelpad=1)
        ax.set_title(title, fontsize=10, color=TITLE, loc="left", pad=4)
        ax.legend(fontsize=7.5, framealpha=0, labelcolor=SUB, loc="lower right")
        return ax

    def render(self):
        self.fig.canvas.draw()
        buf = np.asarray(self.fig.canvas.buffer_rgba())[:, :, :3].copy()
        plt.close(self.fig)
        return buf


def fade(step, at, span=1):
    """0 before `at`, ramping to 1 over `span` steps."""
    if step < at:
        return 0.0
    return min(1.0, (step - at + 1) / span)
