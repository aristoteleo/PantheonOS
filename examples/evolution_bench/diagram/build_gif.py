"""Render the method animations.

One GIF per method. Each is a simulated RUN, not a diagram that fades in: the state changes every
frame, and a line of text says what just happened and why. That is the only way the mechanisms
show themselves --

    MAP-Elites          a child worse than the run's best takes an empty bin, joins the lineage
                        tree, and becomes a parent
    SimpleTES           every selected node is a parent, so a chain grows as a DAG, and best-of-k
                        commits even when it is worse than what it was built from
    AnnealedIdeaCode    the budget slides from proposing to implementing while the selection
                        distribution collapses onto one idea

    python build_gif.py                 # -> ~/Downloads
    python build_gif.py --out DIR
    python build_gif.py --seconds 2.5   # slower
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import imageio.v2 as imageio  # noqa: E402

import run_annealed  # noqa: E402
import run_mapelites  # noqa: E402
import run_simpletes  # noqa: E402
import scene_loop  # noqa: E402

# The loop is not a method, but it is the frame the other three plug into, so it ships first.
BUILDS = [
    ("evolution_00_loop", [scene_loop], None),
    ("evolution_01_map_elites", None, run_mapelites),
    ("evolution_02_simpletes", None, run_simpletes),
    ("evolution_03_annealed_idea_code", None, run_annealed),
]


def build(path, scenes, run, step_s, hold_s):
    # imageio takes per-frame duration in MILLISECONDS. Passing seconds silently produces a GIF
    # whose delays are below every player's floor, so it runs at the clamped minimum -- about
    # 0.1s a frame -- and a request to slow it down changes nothing visible.
    step_s, hold_s = step_s * 1000.0, hold_s * 1000.0
    frames, durations = [], []
    if scenes:
        for mod in scenes:
            for i in range(mod.STEPS):
                frames.append(mod.frame(i))
                durations.append(hold_s if i == mod.STEPS - 1 else step_s)
    else:
        for i in range(run.FRAMES):
            frames.append(run.frame(i))
            durations.append(hold_s if i == run.FRAMES - 1 else step_s)
    imageio.mimsave(path, frames, duration=durations, loop=0)
    return len(frames), os.path.getsize(path)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    p.add_argument("--seconds", type=float, default=2.0, help="per step")
    p.add_argument("--hold", type=float, default=5.0, help="on the final frame")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)

    for name, scenes, run in BUILDS:
        path = os.path.join(a.out, f"{name}.gif")
        n, size = build(path, scenes, run, a.seconds, a.hold)
        print(f"{name + '.gif':40} {n:>3} frames  {n * a.seconds:>5.0f}s  {size / 1e6:>5.2f} MB")
    print(f"\n-> {a.out}")
