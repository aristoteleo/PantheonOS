# ===--------------------------------------------------------------------------------------===#
#
# Part of the CodeEvolve Project, under the Apache License v2.0.
# See https://github.com/inter-co/science-codeevolve/blob/main/LICENSE for license information.
# SPDX-License-Identifier: Apache-2.0
#
# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for the kissing number problem on dimension 11.
#
# ===--------------------------------------------------------------------------------------===#
#
# Some of the code in this file is adapted from:
#
# https://github.com/google-deepmind/alphaevolve_results:
# Licensed under the Apache License v2.0.
#
# ===--------------------------------------------------------------------------------------===#

import sys
import os
from importlib import __import__
import time
import json
import itertools
import numpy as np

DIM = 11
TOL = 1e-6
BENCHMARK = 593


def compute_squared_norm(point: list[int]) -> int:
    """Returns the squared norm of an integer vector using exact computation."""
    return sum(pow(int(x), 2) for x in point)


def verify_sphere_packing(sphere_centers: np.ndarray, tol: float = 1e-6):
    """Checks that after normalizing, the points correspond to a valid sphere packing for kissing numbers.

    Args:
        sphere_centers: the list of sphere centers, of shape [num_spheres, dimension].

    Raises:
        AssertionError: if the sphere packing is not a valid kissing configuration.
    """
    # Rounding to integers to guarantee exact computation throughout.
    sphere_centers = np.around(sphere_centers).astype(np.int64)
    squared_norms = [compute_squared_norm(list(center)) for center in sphere_centers]

    # Checks that the set doesn't contain 0.
    min_squared_norm = min(squared_norms)
    assert min_squared_norm > tol, f"Verification failed because the set contains 0."

    # Checks that the minimum pairwise distance between centers >= the maximum norm of the centers.
    max_squared_norm = max(squared_norms)
    min_squared_distance = min(
        compute_squared_norm(list(a - b)) for a, b in itertools.combinations(sphere_centers, 2)
    )
    assert (
        min_squared_distance >= max_squared_norm
    ), f"Verification failed because the minimum squared distance = {min_squared_distance} < {max_squared_norm} = maximum squared norm."


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
        out = _load_solution(workspace_path).kissing_number11()

        points = np.asarray(out)
        if points.ndim != 2 or points.shape[1] != DIM:
            raise ValueError(f"expected shape (m, {DIM}), got {points.shape}")
        verify_sphere_packing(points, TOL)
        metric = len(points); extra = {}
    except Exception as e:  # noqa: BLE001
        return _fail(f"{type(e).__name__}: {e}", t0)
    ratio = float(metric / BENCHMARK)
    return {"num_points": float(metric), "benchmark_ratio": ratio, "validity": 1.0,
            "combined_score": ratio, "eval_time": _time.time() - t0,
            "fitness_weights": {"combined_score": 1.0}, **extra}
