"""Render the method animations to MP4.

    uv run --with manim --with imageio --with imageio-ffmpeg --with matplotlib \
        python build_video.py --out ~/Downloads

Two kinds of source, one output format:

  - Manim scenes, which are narrated and use a moving camera. This is where the explanatory ones
    are heading; `SimpleTESRun` is the first.
  - Frame-at-a-time matplotlib runs, still here for the methods not ported yet. Each frame is held
    for `--seconds`, which means repeating it at the output frame rate -- a video has one duration
    per file, not one per frame the way a GIF does.

GIF is gone. It capped colours at 256, could not be scrubbed, and its per-frame delays were quietly
clamped by every player to a floor of about 0.1s, so asking for a slower animation changed nothing.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FPS = 30

MANIM_SCENES = [("evolution_01_agent_map_elites", "manim_agentmapelites.py", "AgentMapElitesRun"),
                ("evolution_02_simpletes", "manim_simpletes.py", "SimpleTESRun"),
                ("evolution_03_annealed_idea_code", "manim_annealed.py",
                 "AnnealedRun")]
MPL_RUNS = [("evolution_00_loop", "scene_loop", True)]


def even(frame):
    """libx264 needs both dimensions even; matplotlib will happily hand back an odd height."""
    h, w = frame.shape[:2]
    return frame[: h - (h % 2), : w - (w % 2)]


def render_mpl(path, module, is_scene, step_s, hold_s):
    import imageio.v2 as imageio

    mod = __import__(module)
    n = mod.STEPS if is_scene else mod.FRAMES
    writer = imageio.get_writer(path, fps=FPS, codec="libx264", quality=8,
                               macro_block_size=None, ffmpeg_log_level="error")
    try:
        for i in range(n):
            frame = even(mod.frame(i))
            hold = hold_s if i == n - 1 else step_s
            for _ in range(max(1, round(hold * FPS))):
                writer.append_data(frame)
    finally:
        writer.close()
    return n


QUALITY = {"low": ("-ql", "480p15"), "medium": ("-qm", "720p30"), "high": ("-qh", "1080p60")}
"""Named rather than passed through as `-ql`: argparse reads a value that starts with a dash as
the next option and rejects the call."""


def render_manim(path, source, scene, quality, media):
    flag, tag = QUALITY[quality]
    cmd = ["manim", flag, "--format=mp4", "--media_dir", media,
           os.path.join(HERE, source), scene]
    subprocess.run(cmd, check=True, cwd=HERE,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    built = os.path.join(media, "videos", os.path.splitext(source)[0], tag, f"{scene}.mp4")
    shutil.copyfile(built, path)
    return built


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.expanduser("~/Downloads"))
    p.add_argument("--seconds", type=float, default=2.0, help="per step, matplotlib runs")
    p.add_argument("--hold", type=float, default=4.0, help="on the final frame")
    p.add_argument("--quality", default="high", choices=sorted(QUALITY),
                   help="manim scenes; 'low' for a quick look")
    p.add_argument("--media", default=os.path.join(HERE, ".manim"),
                   help="manim's scratch directory")
    p.add_argument("--only", default="", help="substring filter on the output name")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)

    def wanted(name):
        return a.only in name

    for name, source, scene in MANIM_SCENES:
        if not wanted(name):
            continue
        path = os.path.join(a.out, f"{name}.mp4")
        render_manim(path, source, scene, a.quality, a.media)
        print(f"{name + '.mp4':40} manim {a.quality:6} {os.path.getsize(path) / 1e6:>6.2f} MB")

    for name, module, is_scene in MPL_RUNS:
        if not wanted(name):
            continue
        path = os.path.join(a.out, f"{name}.mp4")
        n = render_mpl(path, module, is_scene, a.seconds, a.hold)
        print(f"{name + '.mp4':40} {n:>3} steps      {os.path.getsize(path) / 1e6:>6.2f} MB")

    print(f"\n-> {a.out}")
