# ===--------------------------------------------------------------------------------------===#
#
# Part of the CodeEvolve Project, under the Apache License v2.0.
# See https://github.com/inter-co/science-codeevolve/blob/main/LICENSE for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for the circle packing problem on a rectangle
# of perimeter 4.
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
import sys
import json
import os
from importlib import __import__

BENCHMARK = 2.3658321334167627
NUM_CIRCLES = 21
TOL = 1e-6


def minimum_circumscribing_rectangle(circles: np.ndarray):
    """Returns the width and height of the minimum circumscribing rectangle.

    Args:
    circles: A numpy array of shape (num_circles, 3), where each row is of the
        form (x, y, radius), specifying a circle.

    Returns:
    A tuple (width, height) of the minimum circumscribing rectangle.
    """
    min_x = np.min(circles[:, 0] - circles[:, 2])
    max_x = np.max(circles[:, 0] + circles[:, 2])
    min_y = np.min(circles[:, 1] - circles[:, 2])
    max_y = np.max(circles[:, 1] + circles[:, 2])
    return max_x - min_x, max_y - min_y


def validate_packing_radii(radii: np.ndarray) -> None:
    n = len(radii)
    for i in range(n):
        if radii[i] < 0:
            raise ValueError(f"Circle {i} has negative radius {radii[i]}")
        elif np.isnan(radii[i]):
            raise ValueError(f"Circle {i} has nan radius")


def validate_packing_overlap_wtol(circles: np.ndarray, tol: float = 1e-6) -> None:
    n = len(circles)
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((circles[i, :2] - circles[j, :2]) ** 2))
            if dist < circles[i, 2] + circles[j, 2] - tol:
                raise ValueError(
                    f"Circles {i} and {j} overlap: dist={dist}, r1+r2={circles[i,2]+circles[j,2]}"
                )


def validate_packing_inside_rect_wtol(circles: np.array, tol: float = 1e-6) -> None:
    width, height = minimum_circumscribing_rectangle(circles)
    if width + height > (2 + tol):
        raise ValueError("Circles are not contained inside a rectangle of perimeter 4.")


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
        out = _load_solution(workspace_path).circle_packing21()

        circles = np.asarray(out, dtype=float)
        if circles.shape != (NUM_CIRCLES, 3):
            raise ValueError(f"expected shape {(NUM_CIRCLES, 3)}, got {circles.shape}")
        if np.isnan(circles).any():
            raise ValueError("nan entry")
        validate_packing_radii(circles[:, -1])
        validate_packing_overlap_wtol(circles, TOL)
        validate_packing_inside_rect_wtol(circles, TOL)
        metric = float(np.sum(circles[:, -1])); extra = {}
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}", t0)
    ratio = float(metric / BENCHMARK)
    return {"radii_sum": float(metric), "benchmark_ratio": ratio, "validity": 1.0,
            "combined_score": ratio, "eval_time": _time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}, **extra}
