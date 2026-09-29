# ===--------------------------------------------------------------------------------------===#
#
# Part of the CodeEvolve Project, under the Apache License v2.0.
# See https://github.com/inter-co/science-codeevolve/blob/main/LICENSE for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for the heilbronn problem for triangles, with
# 11 points.
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
import json
import sys
import os
from importlib import __import__
import itertools
from scipy.spatial import ConvexHull

BENCHMARK = 0.036529889880030156
TOL = 1e-6
NUM_POINTS = 11


def check_inside_triangle_wtol(points: np.ndarray, tol: float = 1e-6):
    """Checks that all points are inside the triangle with vertices (0,0), (1,0), (0.5, sqrt(3)/2).

    Args:
        points: Array of 2D points to check
        tol: Tolerance for numerical errors
    """
    for x, y in points:
        cond1 = y >= -tol
        cond2 = np.sqrt(3) * x <= np.sqrt(3) - y + tol
        cond3 = y <= np.sqrt(3) * x + tol

        if not (cond1 and cond2 and cond3):
            raise ValueError(
                f"Point ({x}, {y}) is outside the equilateral triangle (tolerance: {tol})."
            )


def triangle_area(a: np.array, b: np.array, c: np.array) -> float:
    return np.abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])) / 2


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
        out = _load_solution(workspace_path).heilbronn_triangle11()

        points = np.asarray(out, dtype=float)
        if points.shape != (NUM_POINTS, 2):
            raise ValueError(f"expected shape {(NUM_POINTS, 2)}, got {points.shape}")
        check_inside_triangle_wtol(points, TOL)
        a, b, c = np.array([0, 0]), np.array([1, 0]), np.array([0.5, np.sqrt(3) / 2])
        min_triangle_area = min(triangle_area(p1, p2, p3) for p1, p2, p3 in itertools.combinations(points, 3))
        metric = min_triangle_area / triangle_area(a, b, c); extra = {}
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}", t0)
    ratio = float(metric / BENCHMARK)
    return {"min_area_normalized": float(metric), "benchmark_ratio": ratio, "validity": 1.0,
            "combined_score": ratio, "eval_time": _time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}, **extra}
