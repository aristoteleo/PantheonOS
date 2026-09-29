# ===--------------------------------------------------------------------------------------===#
#
# Part of the CodeEvolve Project, under the Apache License v2.0.
# See https://github.com/inter-co/science-codeevolve/blob/main/LICENSE for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for the heilbronn problem for convex regions, with
# 13 points.
#
# ===--------------------------------------------------------------------------------------===#
#
# Some of the code in this file is adapted from:
#
# https://github.com/google-deepmind/alphaevolve_results:
# Licensed under the Apache License v2.0.
#
# ===--------------------------------------------------------------------------------------===#

import time
import numpy as np
import itertools
from scipy.spatial import ConvexHull
import json
import sys
import os
from importlib import __import__

BENCHMARK = 0.030936889034895654
NUM_POINTS = 13


def triangle_area(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    """Calculates the area of a triangle given its vertices p1, p2, and p3."""
    return abs(p1[0] * (p2[1] - p3[1]) + p2[0] * (p3[1] - p1[1]) + p3[0] * (p1[1] - p2[1])) / 2


# ---- bench-task edition ---------------------------------------------------------------------
# The verification helpers above are CodeEvolve's (Apache-2.0), themselves adapted from
# google-deepmind/alphaevolve_results; the checks and metrics below are the same ones their
# `evaluate(program_path, results_path)` computes, returned in the bench's contract. Fitness is
# CodeEvolve's `benchmark_ratio` (1.0 = AlphaEvolve's reported result), so scores compare across
# tasks and to their tables. No module-scope `__file__`: CodeEvaluator exec()s this source.
import importlib.util
import time as _time


def _load_solution(workspace_path):
    spec = importlib.util.spec_from_file_location("solution", os.path.join(workspace_path, "solution.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fail(reason, t0, **extra):
    return {"validity": 0.0, "combined_score": 0.0, "benchmark_ratio": 0.0, "invalid_reason": str(reason)[:300],
            "eval_time": _time.time() - t0, "fitness_weights": {"combined_score": 1.0}, **extra}


def evaluate(workspace_path, fidelity="full"):
    t0 = _time.time()
    try:
        out = _load_solution(workspace_path).heilbronn_convex13()

        points = np.asarray(out, dtype=float)
        if points.shape != (NUM_POINTS, 2):
            raise ValueError(f"expected shape {(NUM_POINTS, 2)}, got {points.shape}")
        min_triangle_area = min(triangle_area(p1, p2, p3) for p1, p2, p3 in itertools.combinations(points, 3))
        hull = ConvexHull(points)
        metric = min_triangle_area / hull.volume; extra = {"convex_hull_fraction": len(hull.vertices) / NUM_POINTS}
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}", t0)
    ratio = float(metric / BENCHMARK)
    return {"min_area_normalized": float(metric), "benchmark_ratio": ratio, "validity": 1.0,
            "combined_score": ratio, "eval_time": _time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}, **extra}
