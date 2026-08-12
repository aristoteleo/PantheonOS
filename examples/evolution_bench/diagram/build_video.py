"""Render the videos to MP4. All manim now.

    uv run --with manim python build_video.py --out ~/Downloads

GIF is gone (256 colours, unscrubbable, per-frame delays clamped by every player), and so is the
frame-at-a-time matplotlib path -- the last matplotlib scene was the loop explainer, which the
framework video replaced.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MANIM_SCENES = [("evolution_00_framework", "manim_framework.py", "FrameworkRun"),
                ("evolution_01_agent_map_elites", "manim_agentmapelites.py",
                 "AgentMapElitesRun"),
                ("evolution_02_simpletes", "manim_simpletes.py", "SimpleTESRun"),
                ("evolution_03_annealed_idea_code", "manim_annealed.py", "AnnealedRun"),
                # The PROBLEM explainers: what each benchmark is, on its real artifacts.
                ("problem_00_erdos", "manim_prob_erdos.py", "ErdosProblem"),
                ("problem_01_circle_packing", "manim_prob_packing.py", "PackingProblem"),
                ("problem_02_ahc039", "manim_prob_ahc039.py", "AhcProblem")]


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

    print(f"\n-> {a.out}")
